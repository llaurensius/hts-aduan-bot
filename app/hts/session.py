from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os

import threading
from typing import Any, Optional, Protocol
import requests
from app.config import AppConfig
from app.database.db import DatabaseManager
from app.hts.client import create_http_session, is_session_expired
from app.hts.exceptions import HTSConnectionError
from app.hts.parser import parse_csrf_token
from app.utils.time_utils import utcnow_iso

logger = logging.getLogger(__name__)


class SessionState(str, Enum):

    """Lifecycle states of the HTS Session."""
    UNAUTHENTICATED = "UNAUTHENTICATED"
    AUTHENTICATING = "AUTHENTICATING"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    AUTHENTICATED = "AUTHENTICATED"
    SESSION_EXPIRED = "SESSION_EXPIRED"


class TelegramNotifierProtocol(Protocol):
    """Protocol for sending alerts to Telegram."""
    def send(self, text: str) -> Any: ...


TEMPLATE_CAPTCHA = """\
🔐 LOGIN MANUAL DIPERLUKAN

HTS memerlukan login manual (CAPTCHA).
Timestamp: {timestamp}

Silakan login secara manual ke:
{hts_base_url}

Monitoring akan dilanjutkan secara otomatis setelah sesi valid.\
"""


class SessionManager:
    """Manages HTS session state, manual login waiting, and validity checks.

    Since HTS login forms always require CAPTCHA, auto-relogin is not possible.
    Manual login alerts are dispatched to operators, and the manager periodically
    probes for valid session cookies.
    """

    def __init__(
        self,
        config: AppConfig,
        http_session: Optional[requests.Session] = None,
        db: Optional[DatabaseManager] = None,
        notifier: Optional[TelegramNotifierProtocol] = None,
    ) -> None:
        self.config = config
        self.http = http_session or create_http_session(config)
        self.db = db
        self.notifier = notifier
        self.state = SessionState.UNAUTHENTICATED
        self._last_captcha_alert: Optional[datetime] = None

    def _get_csrf_token(self) -> Optional[str]:
        """Fetch login page and extract CSRF token (csrf_test_name)."""
        try:
            resp = self.http.get(
                f"{self.config.hts_base_url}/",
                timeout=self.config.request_timeout,
            )
            return parse_csrf_token(resp.text)
        except Exception as e:
            logger.warning("Failed to get CSRF token: %s", e)
            return None

    def check_session_validity(self) -> bool:
        """Probe HTS /list_aduan to verify if session is authenticated.

        Returns:
            bool: True if session is authenticated, False if expired/unauthenticated.
        """
        self.last_check_was_network_error = False
        url = f"{self.config.hts_base_url}/list_aduan"
        try:
            resp = self.http.get(
                url,
                timeout=self.config.request_timeout,
                allow_redirects=True,
            )
            expired = is_session_expired(resp, base_url=self.config.hts_base_url)
            if expired:
                logger.info("Session check: Session is expired or unauthenticated.")
                self.state = SessionState.SESSION_EXPIRED
                return False

            logger.debug("Session check: Session is valid.")
            self.state = SessionState.AUTHENTICATED
            return True
        except (requests.RequestException, HTSConnectionError) as e:
            logger.warning("Session check connection failure: %s", e)
            self.last_check_was_network_error = True
            return False

    def _send_captcha_alert(self) -> bool:
        """Send manual login / CAPTCHA alert to operator (rate-limited).

        Returns:
            bool: True if alert was sent, False if throttled.
        """
        now = datetime.now(timezone.utc)
        if self._last_captcha_alert is not None:
            elapsed = (now - self._last_captcha_alert).total_seconds()
            if elapsed < self.config.captcha_reminder_interval:
                logger.debug("CAPTCHA alert throttled (elapsed: %.1fs < %ds)", elapsed, self.config.captcha_reminder_interval)
                return False

        message = TEMPLATE_CAPTCHA.format(
            timestamp=utcnow_iso(),
            hts_base_url=self.config.hts_base_url,
        )

        if self.notifier:
            try:
                self.notifier.send(message)
                logger.info("CAPTCHA alert sent via Telegram.")
            except Exception as e:
                logger.error("Failed to send CAPTCHA alert via notifier: %s", e)

        if self.db:
            try:
                self.db.models.insert_system_event(
                    "CAPTCHA_REQUIRED",
                    description="Manual login required (CAPTCHA present)",
                    metadata={"hts_base_url": self.config.hts_base_url},
                )
            except Exception as e:
                logger.warning("Failed to record system event: %s", e)

        self._last_captcha_alert = now
        self.state = SessionState.CAPTCHA_REQUIRED
        return True

    def wait_for_manual_login(self, shutdown_event: Optional[threading.Event] = None) -> bool:
        """Loop checking session validity until operator logs in or shutdown requested.

        Args:
            shutdown_event: Optional threading.Event to signal shutdown.

        Returns:
            bool: True if session became valid, False if interrupted/shutdown.
        """
        logger.info("Waiting for operator to login manually...")
        self.state = SessionState.CAPTCHA_REQUIRED
        self._send_captcha_alert()

        check_interval = max(1, self.config.session_check_interval)

        while True:
            if shutdown_event and shutdown_event.is_set():
                logger.info("Shutdown requested while waiting for manual login.")
                return False

            # Reload cookies from persistent storage if updated
            self.load_persisted_cookies()

            try:
                is_valid = self.check_session_validity()
            except HTSConnectionError:
                is_valid = False

            if is_valid:
                logger.info("Valid session detected! Transitioning to AUTHENTICATED.")
                self.state = SessionState.AUTHENTICATED
                if self.db:
                    try:
                        self.db.models.insert_system_event(
                            "SESSION_RECOVERED",
                            description="Session recovered after manual login",
                        )
                    except Exception as e:
                        logger.warning("Failed to record SESSION_RECOVERED event: %s", e)
                return True

            # Send periodic reminder if due
            self._send_captcha_alert()

            # Wait before next probe
            if shutdown_event:
                if shutdown_event.wait(check_interval):
                    return False
            else:
                # If no shutdown_event provided, probe once and return
                return False

    def load_persisted_cookies(self, cookie_file: Optional[str] = None) -> bool:
        """Load session cookies from JSON file into self.http if present.

        Args:
            cookie_file: Path to JSON cookie file (defaults to config.cookie_file).

        Returns:
            bool: True if valid cookies were loaded, False otherwise.
        """
        path = cookie_file or getattr(self.config, "cookie_file", "data/cookies.json")
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            if not isinstance(cookies, dict):
                return False

            domain = self.config.hts_base_url.split("//")[-1].split("/")[0].split(":")[0]
            loaded_count = 0
            for name, val in cookies.items():
                if name != "updated_at" and val:
                    self.http.cookies.set(name, str(val), domain=domain)
                    loaded_count += 1
            if loaded_count > 0:
                logger.debug("Loaded %d persisted cookie(s) from %s", loaded_count, path)
                return True
        except Exception as e:
            logger.warning("Failed to load cookies from %s: %s", path, e)
            return False

    def save_session_cookies(self, cookie_file: Optional[str] = None) -> bool:
        """Save current session cookies to JSON file."""
        path = cookie_file or getattr(self.config, "cookie_file", "data/cookies.json")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cookies = self.http.cookies.get_dict()
            cookies["updated_at"] = datetime.now(timezone.utc).isoformat()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2)
            logger.info("Saved %d cookie(s) to %s", len(cookies) - 1, path)
            return True
        except Exception as e:
            logger.warning("Failed to save cookies to %s: %s", path, e)
            return False

    def login(self) -> bool:
        """Attempt automated login using configured credentials."""
        if not self.config.hts_username or not self.config.hts_password:
            return False
        csrf = self._get_csrf_token()
        data = {
            "csrf_test_name": csrf or "",
            "email": self.config.hts_username,
            "password": self.config.hts_password,
            "captcha_code": "",
            "authCheck": "1",
        }
        try:
            resp = self.http.post(
                f"{self.config.hts_base_url}/login",
                data=data,
                timeout=self.config.request_timeout,
                allow_redirects=True,
            )
            if self.check_session_validity():
                logger.info("Automated login succeeded!")
                self.save_session_cookies()
                self.state = SessionState.AUTHENTICATED
                return True
            return False
        except Exception as e:
            logger.warning("Automated login attempt failed: %s", e)
            return False

    def initialize(self, shutdown_event: Optional[threading.Event] = None) -> bool:
        """Initialize session. Probes existing cookies, tries automated login, or waits for manual login if needed.

        Args:
            shutdown_event: Optional threading.Event to signal graceful shutdown.

        Returns:
            bool: True if authenticated and ready, False otherwise.
        """
        logger.info("Initializing HTS session...")
        self.load_persisted_cookies()
        try:
            if self.check_session_validity():
                logger.info("Existing HTS session is valid.")
                self.state = SessionState.AUTHENTICATED
                return True
        except HTSConnectionError as e:
            logger.warning("Network error during initial session check: %s", e)

        # Coba auto login jika kredensial ada
        if self.config.hts_username and self.config.hts_password:
            logger.info("Attempting automated login using credentials...")
            if self.login():
                return True

        logger.info("No valid session found. Manual login required.")
        return self.wait_for_manual_login(shutdown_event=shutdown_event)

