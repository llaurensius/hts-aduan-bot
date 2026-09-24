"""HTTP client and connectivity layer for HTS."""

import logging
from typing import Any, Dict, Generator, List, Optional
import requests
from app.config import AppConfig
from app.hts.exceptions import (
    HTSConnectionError,
    HTSError,
    HTSSessionExpiredError,
)
from app.hts.parser import TicketData, parse_api_response

logger = logging.getLogger(__name__)


def create_http_session(config: Optional[AppConfig] = None) -> requests.Session:
    """Create requests.Session with standard browser-like headers for HTS.

    Args:
        config: Optional application configuration.

    Returns:
        requests.Session: Configured session for connection reuse.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    })
    return session


def is_session_expired(response: requests.Response, base_url: Optional[str] = None) -> bool:
    """Detect if an HTTP response from HTS indicates that session has expired.

    Verified indications:
    1. URL redirected to '/login' or equals base_url (when requesting protected page)
    2. Response HTML body contains login form markers ('id="userEmail"' or 'captchaimg')
    3. HTTP status code 401 or 403

    Args:
        response: requests.Response object.
        base_url: Optional base URL of HTS for redirect comparison.

    Returns:
        bool: True if session is expired/unauthenticated, False otherwise.
    """
    # Check 1: HTTP Status Codes
    if response.status_code in (401, 403):
        return True

    # Check 2: URL redirection
    resp_url = (response.url or "").rstrip("/")
    if resp_url.endswith("/login"):
        return True

    if base_url:
        clean_base = base_url.rstrip("/")
        if resp_url == clean_base:
            return True

    # Check 3: HTML content markers
    text = response.text or ""
    if 'id="userEmail"' in text or "captchaimg" in text:
        return True

    return False


class HTSClient:
    """HTTP client responsible for communication with HTS."""

    def __init__(self, config: AppConfig, http_session: Optional[requests.Session] = None) -> None:
        self.config = config
        self.session = http_session or create_http_session(config)
        self.base_url = config.hts_base_url.rstrip("/")

    def _make_api_request(
        self,
        page: int = 1,
        limit: int = 100,
        status: str = "pending",
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:
        """Make POST request to /get_aduan_data.

        Args:
            page: Page number (1-indexed).
            limit: Number of items per page.
            status: 'pending' | 'solved' | 'all' | 'unsubmitted'.
            extra_payload: Additional key-value pairs to include in payload.

        Returns:
            requests.Response: Raw response from HTS.

        Raises:
            HTSConnectionError: On network connection errors or timeouts.
            HTSSessionExpiredError: If response indicates expired session.
            HTSError: On other unexpected HTTP failure codes.
        """
        url = f"{self.base_url}/get_aduan_data"
        headers = {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        payload: Dict[str, Any] = {
            "page": page,
            "limit": limit,
            "status": status,
        }
        if extra_payload:
            payload.update(extra_payload)

        try:
            resp = self.session.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.config.request_timeout,
            )
        except requests.Timeout as e:
            logger.error("Request to HTS timed out: %s", e)
            raise HTSConnectionError(f"HTS request timed out after {self.config.request_timeout}s") from e
        except requests.ConnectionError as e:
            logger.error("Connection error while reaching HTS: %s", e)
            raise HTSConnectionError(f"Failed to connect to HTS: {e}") from e
        except requests.RequestException as e:
            logger.error("Unexpected requests exception: %s", e)
            raise HTSConnectionError(f"Network error: {e}") from e

        if is_session_expired(resp, base_url=self.base_url):
            logger.warning("HTS session expired detected from response (status=%d, url=%s)", resp.status_code, resp.url)
            raise HTSSessionExpiredError("HTS session is expired or invalid")

        if resp.status_code != 200:
            raise HTSError(f"HTS returned HTTP {resp.status_code}: {resp.text[:200]}")

        return resp

    def fetch_tickets_page(
        self,
        page: int = 1,
        limit: int = 50,
        status: str = "pending",
    ) -> Dict[str, Any]:
        """Fetch a single page of tickets from /get_aduan_data.

        Args:
            page: Page number (1-indexed).
            limit: Number of items per page.
            status: 'pending' | 'solved' | 'all' | 'unsubmitted'.

        Returns:
            dict: Raw parsed JSON response dict.

        Raises:
            HTSConnectionError: On network connection errors or timeouts.
            HTSSessionExpiredError: If response indicates expired session.
            HTSError: If HTTP status is non-200 or response is not valid JSON.
        """
        resp = self._make_api_request(page=page, limit=limit, status=status)
        try:
            return resp.json()
        except ValueError as e:
            logger.error("Failed to parse JSON response from /get_aduan_data: %s", e)
            raise HTSError(f"Invalid JSON response from HTS: {e}") from e

    def fetch_all_tickets(
        self,
        status: str = "all",
        limit: int = 50,
    ) -> Generator[TicketData, None, None]:
        """Generator that fetches all tickets across all pages for a given status.

        Args:
            status: 'all' | 'pending' | 'solved' | 'unsubmitted'.
            limit: Page size (default 50).

        Yields:
            TicketData: Parsed ticket domain object.
        """
        page = 1
        while True:
            raw_response = self.fetch_tickets_page(page=page, limit=limit, status=status)
            tickets, pagination = parse_api_response(raw_response)

            for ticket in tickets:
                yield ticket

            total_pages = pagination.get("total_pages", 1)
            total = pagination.get("total", 0)

            # If no items or current page reached or exceeded total_pages, break
            if total == 0 or page >= total_pages or len(tickets) == 0:
                break

            page += 1

    def fetch_active_tickets(self, limit: int = 50) -> List[TicketData]:
        """Fetch all currently active/pending tickets along with recent tickets.

        Includes recent tickets from page 1 of status='all' so that solved
        and modified tickets are immediately detected in real-time.

        Returns:
            List[TicketData]: List of active and recently modified tickets.
        """
        seen: set = set()
        combined: List[TicketData] = []

        # 1. Fetch all pending tickets
        for t in self.fetch_all_tickets(status="pending", limit=limit):
            if t.nomor_aduan not in seen:
                seen.add(t.nomor_aduan)
                combined.append(t)

        # 2. Also fetch recent tickets from page 1 of status="all"
        try:
            raw_page1 = self.fetch_tickets_page(page=1, limit=limit, status="all")
            page1_tickets, _ = parse_api_response(raw_page1)
            for t in page1_tickets:
                if t.nomor_aduan not in seen:
                    seen.add(t.nomor_aduan)
                    combined.append(t)
        except (HTSSessionExpiredError, HTSConnectionError):
            raise
        except Exception as e:
            logger.warning("Failed to fetch page 1 of status='all': %s", e)

        return combined

    def fetch_ticket_by_nomor(
        self, nomor_aduan: str, limit: int = 50
    ) -> Optional[TicketData]:
        """Fetch a specific ticket by scanning through all tickets.
        
        Since HTS API does not support filtering by ticket number directly,
        this method iterates through all tickets until a match is found.
        This can be slow (5-10s) if the ticket is old or the system has many tickets.
        
        Args:
            nomor_aduan: The ticket number to find.
            limit: Page size for API requests.
            
        Returns:
            TicketData if found, None otherwise.
        """
        for ticket in self.fetch_all_tickets(status="all", limit=limit):
            if ticket.nomor_aduan == nomor_aduan:
                return ticket
        return None
