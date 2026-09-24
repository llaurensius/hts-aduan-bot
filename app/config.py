"""Application configuration dataclass and loader."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    """Application configuration container."""

    # Required settings
    hts_base_url: str
    hts_username: str
    hts_password: str
    telegram_bot_token: str
    telegram_chat_id: str

    # Polling & Network settings
    poll_interval: int = 5
    request_timeout: int = 10
    retry_initial_delay: int = 15
    max_retry_delay: int = 1800

    # Telegram notification settings
    max_telegram_retry: int = 0
    initial_sync: bool = True

    # Logging & Database settings
    log_level: str = "INFO"
    log_file: str = "logs/hts_monitor.log"
    db_path: str = "data/hts_monitor.db"

    # Backup settings
    backup_enabled: bool = True
    backup_dir: str = "data/backups"
    backup_retain_days: int = 7

    # Health server settings
    health_port: int = 8080
    health_host: str = "127.0.0.1"

    # Interval & Timing settings
    change_debounce_seconds: int = 0
    captcha_reminder_interval: int = 1800
    session_check_interval: int = 60
    cookie_file: str = "data/cookies.json"
    petugas_nama: str = "Laurensius Liquori"
    petugas_role: str = "Helpdesk DC"


def _parse_bool(value: Optional[str], default: bool) -> bool:
    """Parse string representation of boolean."""
    if value is None:
        return default
    return value.strip().lower() in ("true", "1", "yes", "y", "on")


def _parse_int(value: Optional[str], default: int, var_name: str) -> int:
    """Parse string representation of integer."""
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError:
        sys.stderr.write(f"ERROR: Configuration variable {var_name} must be an integer, got: {value!r}\n")
        sys.exit(1)


def load_config(env_file: Optional[str] = None) -> AppConfig:
    """Load configuration from environment variables (optionally loading a .env file).

    Args:
        env_file: Optional path to .env file to load. If None, default search path is used.

    Returns:
        AppConfig: Immutable configuration object.

    Raises:
        SystemExit: If any required configuration variables are missing or invalid.
    """
    if env_file:
        load_dotenv(dotenv_path=env_file, override=True)
    else:
        # Default load_dotenv looks for .env in current directory or parents
        load_dotenv(override=False)

    required_vars = [
        "HTS_BASE_URL",
        "HTS_USERNAME",
        "HTS_PASSWORD",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ]

    missing: List[str] = []
    for var in required_vars:
        val = os.getenv(var)
        if val is None or not val.strip():
            missing.append(var)

    if missing:
        sys.stderr.write(
            f"ERROR: Missing required environment variable(s): {', '.join(missing)}\n"
            f"Please check your .env file or environment settings against .env.example.\n"
        )
        sys.exit(1)

    return AppConfig(
        hts_base_url=os.environ["HTS_BASE_URL"].strip().rstrip("/"),
        hts_username=os.environ["HTS_USERNAME"].strip(),
        hts_password=os.environ["HTS_PASSWORD"].strip(),
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"].strip(),
        telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"].strip(),
        poll_interval=_parse_int(os.getenv("POLL_INTERVAL"), 5, "POLL_INTERVAL"),
        request_timeout=_parse_int(os.getenv("REQUEST_TIMEOUT"), 10, "REQUEST_TIMEOUT"),
        retry_initial_delay=_parse_int(os.getenv("RETRY_INITIAL_DELAY"), 15, "RETRY_INITIAL_DELAY"),
        max_retry_delay=_parse_int(os.getenv("MAX_RETRY_DELAY"), 1800, "MAX_RETRY_DELAY"),
        max_telegram_retry=_parse_int(os.getenv("MAX_TELEGRAM_RETRY"), 0, "MAX_TELEGRAM_RETRY"),
        initial_sync=_parse_bool(os.getenv("INITIAL_SYNC"), True),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        log_file=os.getenv("LOG_FILE", "logs/hts_monitor.log").strip(),
        db_path=os.getenv("DB_PATH", "data/hts_monitor.db").strip(),
        backup_enabled=_parse_bool(os.getenv("BACKUP_ENABLED"), True),
        backup_dir=os.getenv("BACKUP_DIR", "data/backups").strip(),
        backup_retain_days=_parse_int(os.getenv("BACKUP_RETAIN_DAYS"), 7, "BACKUP_RETAIN_DAYS"),
        health_port=_parse_int(os.getenv("HEALTH_PORT"), 8080, "HEALTH_PORT"),
        health_host=os.getenv("HEALTH_HOST", "127.0.0.1").strip(),
        change_debounce_seconds=_parse_int(os.getenv("CHANGE_DEBOUNCE_SECONDS"), 0, "CHANGE_DEBOUNCE_SECONDS"),
        captcha_reminder_interval=_parse_int(os.getenv("CAPTCHA_REMINDER_INTERVAL"), 1800, "CAPTCHA_REMINDER_INTERVAL"),
        session_check_interval=_parse_int(os.getenv("SESSION_CHECK_INTERVAL"), 60, "SESSION_CHECK_INTERVAL"),
        cookie_file=os.getenv("COOKIE_FILE", "data/cookies.json").strip(),
        petugas_nama=os.getenv("PETUGAS_NAMA", "Laurensius Liquori").strip(),
        petugas_role=os.getenv("PETUGAS_ROLE", "Helpdesk DC").strip(),
    )

