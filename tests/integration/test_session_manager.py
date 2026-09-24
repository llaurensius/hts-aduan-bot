"""Integration tests for SessionManager and HTS session lifecycle."""

import threading
import pytest
import requests
from app.config import AppConfig
from app.database.db import DatabaseManager
from app.hts.session import SessionManager, SessionState


class MockNotifier:
    """Mock notifier recording sent messages."""

    def __init__(self):
        self.sent_messages = []

    def send(self, text: str):
        self.sent_messages.append(text)
        return "mock_msg_id_123"


@pytest.fixture
def mock_config():
    return AppConfig(
        hts_base_url="https://hts.diskomdigi.jatengprov.go.id",
        hts_username="test_user",
        hts_password="test_password",
        telegram_bot_token="123:TOKEN",
        telegram_chat_id="12345",
        captcha_reminder_interval=1800,
        session_check_interval=1,
    )


@pytest.fixture
def db_manager(tmp_path):
    db_file = tmp_path / "test_session_db.db"
    manager = DatabaseManager(str(db_file))
    manager.connect()
    yield manager
    manager.close()


def test_check_validity_authenticated(mock_config, db_manager, monkeypatch):
    """Test that a normal response from /list_aduan sets state to AUTHENTICATED and returns True."""
    notifier = MockNotifier()
    sm = SessionManager(config=mock_config, db=db_manager, notifier=notifier)

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp.url = "https://hts.diskomdigi.jatengprov.go.id/list_aduan"
    mock_resp._content = b"<html><title>Aduan List</title><body><table></table></body></html>"

    monkeypatch.setattr(sm.http, "get", lambda *args, **kwargs: mock_resp)

    valid = sm.check_session_validity()
    assert valid is True
    assert sm.state == SessionState.AUTHENTICATED


def test_check_validity_expired(mock_config, db_manager, monkeypatch):
    """Test that redirect to /login marks session as expired and returns False."""
    notifier = MockNotifier()
    sm = SessionManager(config=mock_config, db=db_manager, notifier=notifier)

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp.url = "https://hts.diskomdigi.jatengprov.go.id/login"
    mock_resp._content = b'<html><body><img src="captchaimg" /></body></html>'

    monkeypatch.setattr(sm.http, "get", lambda *args, **kwargs: mock_resp)

    valid = sm.check_session_validity()
    assert valid is False
    assert sm.state == SessionState.SESSION_EXPIRED


def test_initialize_already_valid(mock_config, db_manager, monkeypatch):
    """Test initialize() returns True immediately when session is already valid."""
    notifier = MockNotifier()
    sm = SessionManager(config=mock_config, db=db_manager, notifier=notifier)

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp.url = "https://hts.diskomdigi.jatengprov.go.id/list_aduan"
    mock_resp._content = b"<html><table>Valid Session</table></html>"

    monkeypatch.setattr(sm.http, "get", lambda *args, **kwargs: mock_resp)

    result = sm.initialize()
    assert result is True
    assert sm.state == SessionState.AUTHENTICATED
    assert len(notifier.sent_messages) == 0  # No CAPTCHA alert needed


def test_send_captcha_alert_rate_limited(mock_config, db_manager):
    """Test that consecutive calls to _send_captcha_alert are rate-limited."""
    notifier = MockNotifier()
    sm = SessionManager(config=mock_config, db=db_manager, notifier=notifier)

    first_call = sm._send_captcha_alert()
    second_call = sm._send_captcha_alert()

    assert first_call is True
    assert second_call is False
    assert len(notifier.sent_messages) == 1
    assert "LOGIN MANUAL DIPERLUKAN" in notifier.sent_messages[0]


def test_get_csrf_token(mock_config, db_manager, monkeypatch):
    """Test parsing csrf token from login page."""
    notifier = MockNotifier()
    sm = SessionManager(config=mock_config, db=db_manager, notifier=notifier)

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp.url = "https://hts.diskomdigi.jatengprov.go.id/"
    mock_resp._content = b'<html><form><input type="hidden" name="csrf_test_name" value="token_abc_123" /></form></html>'

    monkeypatch.setattr(sm.http, "get", lambda *args, **kwargs: mock_resp)

    token = sm._get_csrf_token()
    assert token == "token_abc_123"


def test_wait_for_manual_login_recovers(mock_config, db_manager, monkeypatch):
    """Test wait_for_manual_login detects session recovery."""
    notifier = MockNotifier()
    sm = SessionManager(config=mock_config, db=db_manager, notifier=notifier)

    # First call to check_session_validity is expired, second call becomes valid
    calls = []

    def mock_get(url, *args, **kwargs):
        mock_resp = requests.Response()
        mock_resp.status_code = 200
        if len(calls) == 0:
            mock_resp.url = "https://hts.diskomdigi.jatengprov.go.id/login"
            mock_resp._content = b'<html><img src="captchaimg" /></html>'
        else:
            mock_resp.url = "https://hts.diskomdigi.jatengprov.go.id/list_aduan"
            mock_resp._content = b"<html><table>Valid List</table></html>"
        calls.append(url)
        return mock_resp

    monkeypatch.setattr(sm.http, "get", mock_get)

    shutdown_event = threading.Event()

    # In thread 1 wait, in thread 2 let loop proceed once
    result = sm.wait_for_manual_login(shutdown_event=shutdown_event)
    assert result is True
    assert sm.state == SessionState.AUTHENTICATED


def test_get_csrf_token_exception(mock_config, db_manager, monkeypatch):
    """Test get_csrf_token returns None on network exception."""
    sm = SessionManager(config=mock_config, db=db_manager)

    def mock_get_fail(*args, **kwargs):
        raise requests.ConnectionError("Connection timed out")

    monkeypatch.setattr(sm.http, "get", mock_get_fail)
    assert sm._get_csrf_token() is None


def test_check_session_validity_exception(mock_config, db_manager, monkeypatch):
    """Test check_session_validity returns False on network exception."""
    sm = SessionManager(config=mock_config, db=db_manager)

    def mock_get_fail(*args, **kwargs):
        raise requests.ConnectionError("Connection broken")

    monkeypatch.setattr(sm.http, "get", mock_get_fail)
    assert sm.check_session_validity() is False


def test_send_captcha_alert_exceptions_swallowed(mock_config, db_manager, monkeypatch):
    """Test _send_captcha_alert handles exceptions in notifier and db gracefully."""
    class FailingNotifier:
        def send(self, text):
            raise RuntimeError("Telegram failure")

    sm = SessionManager(config=mock_config, db=db_manager, notifier=FailingNotifier())

    def mock_insert(*args, **kwargs):
        raise RuntimeError("DB failure")

    monkeypatch.setattr(db_manager.models, "insert_system_event", mock_insert)

    # Should not raise exception
    res = sm._send_captcha_alert()
    assert res is True
    assert sm.state == SessionState.CAPTCHA_REQUIRED


def test_wait_for_manual_login_shutdown_event_already_set(mock_config, db_manager):
    """Test wait_for_manual_login returns False immediately if shutdown_event is set."""
    sm = SessionManager(config=mock_config, db=db_manager)
    shutdown_event = threading.Event()
    shutdown_event.set()

    result = sm.wait_for_manual_login(shutdown_event=shutdown_event)
    assert result is False


def test_wait_for_manual_login_without_shutdown_event(mock_config, db_manager, monkeypatch):
    """Test wait_for_manual_login returns False after single probe if shutdown_event is None."""
    sm = SessionManager(config=mock_config, db=db_manager)
    monkeypatch.setattr(sm, "check_session_validity", lambda: False)

    result = sm.wait_for_manual_login(shutdown_event=None)
    assert result is False


def test_initialize_calls_wait_for_manual_login(mock_config, db_manager, monkeypatch):
    """Test initialize() delegates to wait_for_manual_login if session invalid."""
    sm = SessionManager(config=mock_config, db=db_manager)

    monkeypatch.setattr(sm, "check_session_validity", lambda: False)
    monkeypatch.setattr(sm, "wait_for_manual_login", lambda shutdown_event=None: True)

    assert sm.initialize() is True


def test_load_persisted_cookies(mock_config, db_manager, tmp_path):
    """Test loading cookies from JSON file into session."""
    cookie_file = tmp_path / "cookies.json"
    import json
    cookie_file.write_text(json.dumps({"ci_session": "sess_12345", "TS0128f648": "waf_67890"}))

    sm = SessionManager(config=mock_config, db=db_manager)
    loaded = sm.load_persisted_cookies(str(cookie_file))
    assert loaded is True
    assert sm.http.cookies.get("ci_session") == "sess_12345"
    assert sm.http.cookies.get("TS0128f648") == "waf_67890"

    # Non-existent file
    assert sm.load_persisted_cookies(str(tmp_path / "non_existent.json")) is False


