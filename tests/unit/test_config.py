"""Unit tests for configuration loading and sensitive data logging filter."""

import logging
import os
import pytest
from app.config import AppConfig, load_config
from app.utils.log_utils import SensitiveDataFilter, setup_logging


@pytest.fixture
def valid_env_vars(monkeypatch):
    """Set up all required environment variables for test."""
    env = {
        "HTS_BASE_URL": "https://hts.diskomdigi.jatengprov.go.id",
        "HTS_USERNAME": "test_operator",
        "HTS_PASSWORD": "super_secret_password_123",
        "TELEGRAM_BOT_TOKEN": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
        "TELEGRAM_CHAT_ID": "-1001234567890",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


def test_load_config_success(valid_env_vars):
    """Test that valid required environment variables load correctly into AppConfig."""
    config = load_config()
    assert isinstance(config, AppConfig)
    assert config.hts_base_url == "https://hts.diskomdigi.jatengprov.go.id"
    assert config.hts_username == "test_operator"
    assert config.hts_password == "super_secret_password_123"
    assert config.telegram_bot_token == "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
    assert config.telegram_chat_id == "-1001234567890"


def test_load_config_missing_required(monkeypatch):
    """Test that missing required variables trigger SystemExit(1)."""
    monkeypatch.setattr("app.config.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("HTS_BASE_URL", raising=False)
    monkeypatch.delenv("HTS_USERNAME", raising=False)
    monkeypatch.delenv("HTS_PASSWORD", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        load_config()
    assert exc_info.value.code == 1



def test_load_config_defaults(valid_env_vars, monkeypatch):
    """Test that default values are assigned properly when optional vars are absent."""
    monkeypatch.setattr("app.config.load_dotenv", lambda *args, **kwargs: None)
    # Ensure optional vars are not set
    for opt_var in [
        "POLL_INTERVAL",
        "REQUEST_TIMEOUT",
        "RETRY_INITIAL_DELAY",
        "MAX_RETRY_DELAY",
        "MAX_TELEGRAM_RETRY",
        "INITIAL_SYNC",
        "LOG_LEVEL",
        "LOG_FILE",
        "DB_PATH",
        "BACKUP_ENABLED",
        "BACKUP_DIR",
        "BACKUP_RETAIN_DAYS",
        "HEALTH_PORT",
        "HEALTH_HOST",
        "CHANGE_DEBOUNCE_SECONDS",
        "CAPTCHA_REMINDER_INTERVAL",
        "SESSION_CHECK_INTERVAL",
        "PETUGAS_NAMA",
        "PETUGAS_ROLE",
    ]:
        monkeypatch.delenv(opt_var, raising=False)

    config = load_config()
    assert config.poll_interval == 5
    assert config.request_timeout == 10
    assert config.retry_initial_delay == 15
    assert config.max_retry_delay == 1800
    assert config.max_telegram_retry == 0
    assert config.initial_sync is True
    assert config.log_level == "INFO"
    assert config.log_file == "logs/hts_monitor.log"
    assert config.db_path == "data/hts_monitor.db"
    assert config.backup_enabled is True
    assert config.backup_dir == "data/backups"
    assert config.backup_retain_days == 7
    assert config.health_port == 8080
    assert config.health_host == "127.0.0.1"
    assert config.change_debounce_seconds == 0
    assert config.captcha_reminder_interval == 1800
    assert config.session_check_interval == 60
    assert config.petugas_nama == "Laurensius Liquori"
    assert config.petugas_role == "Helpdesk DC"


def test_config_types(valid_env_vars, monkeypatch):
    """Test that integer and boolean environment variables are parsed into correct types."""
    monkeypatch.setenv("POLL_INTERVAL", "12")
    monkeypatch.setenv("REQUEST_TIMEOUT", "20")
    monkeypatch.setenv("INITIAL_SYNC", "false")
    monkeypatch.setenv("BACKUP_ENABLED", "0")

    config = load_config()
    assert isinstance(config.poll_interval, int)
    assert config.poll_interval == 12
    assert isinstance(config.request_timeout, int)
    assert config.request_timeout == 20
    assert isinstance(config.initial_sync, bool)
    assert config.initial_sync is False
    assert isinstance(config.backup_enabled, bool)
    assert config.backup_enabled is False


def test_sensitive_filter(tmp_path):
    """Test that SensitiveDataFilter scrubs sensitive passwords and tokens from logs."""
    secret_pass = "top_secret_pass_xyz"
    secret_token = "987654:ABC-Token"
    log_file = tmp_path / "test.log"

    logger = setup_logging(
        log_level="INFO",
        log_file=str(log_file),
        sensitive_patterns={secret_pass, secret_token},
    )

    logger.info("Connecting with password %s and token %s", secret_pass, secret_token)
    logger.info("Direct message with %s included", secret_pass)

    # Flush handlers
    for handler in logger.handlers:
        handler.flush()

    content = log_file.read_text(encoding="utf-8")
    assert secret_pass not in content
    assert secret_token not in content
    assert "[REDACTED]" in content


def test_sensitive_filter_advanced():
    """Test SensitiveDataFilter with add_pattern, dict args, and tuple args."""
    filt = SensitiveDataFilter()
    assert filt.filter(logging.LogRecord("test", logging.INFO, "test.py", 10, "Normal message", (), None)) is True

    filt.add_pattern("SECRET123")
    filt.add_pattern("   ")  # Empty/whitespace ignored

    record_dict = logging.LogRecord("test", logging.INFO, "test.py", 10, "Format: %(val)s", (), None)
    record_dict.args = {"val": "My SECRET123 value", "num": 123}
    filt.filter(record_dict)
    assert record_dict.args["val"] == "My [REDACTED] value"
    assert record_dict.args["num"] == 123

    record_tuple = logging.LogRecord("test", logging.INFO, "test.py", 10, "Format: %s", (), None)
    record_tuple.args = ("SECRET123 and more",)
    filt.filter(record_tuple)
    assert record_tuple.args == ("[REDACTED] and more",)

