"""Unit and integration tests for application entrypoint (app/main.py)."""

from unittest.mock import MagicMock, patch
import pytest

from app.config import AppConfig
from app.main import main


@pytest.fixture
def mock_app_config():
    """Mock valid AppConfig instance."""
    return AppConfig(
        hts_base_url="https://hts.example.com",
        hts_username="operator_user",
        hts_password="test_secret_password",
        telegram_bot_token="123456:SECRET_BOT_TOKEN",
        telegram_chat_id="12345",
        poll_interval=5,
        db_path=":memory:",
        health_host="127.0.0.1",
        health_port=0,
    )


def test_main_startup_and_clean_run(mock_app_config):
    """Verify that main() initializes all components and runs the orchestrator."""
    mock_orchestrator = MagicMock()
    mock_db = MagicMock()
    mock_health_thread = MagicMock()
    mock_health_thread.server = MagicMock()

    with patch("app.main.load_config", return_value=mock_app_config), \
         patch("app.main.setup_logging") as mock_setup_log, \
         patch("app.main.DatabaseManager", return_value=mock_db), \
         patch("app.main.start_health_server", return_value=mock_health_thread), \
         patch("app.main.Orchestrator", return_value=mock_orchestrator):

        exit_code = main()

        assert exit_code == 0
        mock_setup_log.assert_called_once()
        mock_db.connect.assert_called_once()
        mock_orchestrator.run.assert_called_once()
        mock_db.close.assert_called_once()
        mock_health_thread.server.shutdown.assert_called_once()


def test_main_handles_keyboard_interrupt(mock_app_config):
    """Verify that main() catches KeyboardInterrupt, stops orchestrator, and shuts down cleanly."""
    mock_orchestrator = MagicMock()
    mock_orchestrator.run.side_effect = KeyboardInterrupt
    mock_db = MagicMock()
    mock_health_thread = MagicMock()
    mock_health_thread.server = MagicMock()

    with patch("app.main.load_config", return_value=mock_app_config), \
         patch("app.main.setup_logging"), \
         patch("app.main.DatabaseManager", return_value=mock_db), \
         patch("app.main.start_health_server", return_value=mock_health_thread), \
         patch("app.main.Orchestrator", return_value=mock_orchestrator):

        exit_code = main()

        assert exit_code == 0
        mock_orchestrator.stop.assert_called_once()
        mock_db.close.assert_called_once()
        mock_health_thread.server.shutdown.assert_called_once()
