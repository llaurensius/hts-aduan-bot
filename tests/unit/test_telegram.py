"""Unit tests for TelegramNotifier (Milestone M9)."""

import logging
import pytest
import requests
from app.config import AppConfig
from app.notifications.telegram import (
    TelegramError,
    TelegramNotifier,
    TelegramRateLimitError,
)


@pytest.fixture
def mock_config():
    return AppConfig(
        hts_base_url="https://hts.diskomdigi.jatengprov.go.id",
        hts_username="operator_test",
        hts_password="secret_password",
        telegram_bot_token="123456:SECRET_BOT_TOKEN_XYZ",
        telegram_chat_id="-100987654321",
        request_timeout=5,
    )


def test_send_success(mock_config, monkeypatch):
    """Test successful message sending returns message dict."""
    notifier = TelegramNotifier(mock_config)

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp._content = b'{"ok": true, "result": {"message_id": 9988}}'

    captured_requests = []

    def mock_post(url, json=None, timeout=None):
        captured_requests.append({"url": url, "json": json})
        return mock_resp

    monkeypatch.setattr(notifier.session, "post", mock_post)

    result = notifier.send("Test message content")
    assert result["ok"] is True
    assert result["result"]["message_id"] == 9988
    assert len(captured_requests) == 1
    assert captured_requests[0]["json"]["chat_id"] == "-100987654321"
    assert captured_requests[0]["json"]["text"] == "Test message content"


def test_send_rate_limit(mock_config, monkeypatch):
    """Test HTTP 429 raises TelegramRateLimitError with retry_after."""
    notifier = TelegramNotifier(mock_config)

    mock_resp = requests.Response()
    mock_resp.status_code = 429
    mock_resp.headers["Retry-After"] = "45"
    mock_resp._content = b'{"ok": false, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 45}}'

    monkeypatch.setattr(notifier.session, "post", lambda *args, **kwargs: mock_resp)

    with pytest.raises(TelegramRateLimitError) as exc_info:
        notifier.send("Too frequent")
    assert exc_info.value.retry_after == 45
    assert exc_info.value.status_code == 429


def test_send_bad_request(mock_config, monkeypatch):
    """Test HTTP 400 raises TelegramError with status code 400."""
    notifier = TelegramNotifier(mock_config)

    mock_resp = requests.Response()
    mock_resp.status_code = 400
    mock_resp._content = b'{"ok": false, "error_code": 400, "description": "Bad Request: chat not found"}'

    monkeypatch.setattr(notifier.session, "post", lambda *args, **kwargs: mock_resp)

    with pytest.raises(TelegramError) as exc_info:
        notifier.send("Invalid chat")
    assert exc_info.value.status_code == 400
    assert "Bad Request" in exc_info.value.body


def test_send_server_error(mock_config, monkeypatch):
    """Test HTTP 500 raises TelegramError with status code 500."""
    notifier = TelegramNotifier(mock_config)

    mock_resp = requests.Response()
    mock_resp.status_code = 500
    mock_resp._content = b"Internal Server Error"

    monkeypatch.setattr(notifier.session, "post", lambda *args, **kwargs: mock_resp)

    with pytest.raises(TelegramError) as exc_info:
        notifier.send("Server boom")
    assert exc_info.value.status_code == 500


def test_token_not_in_log(mock_config, monkeypatch, caplog):
    """Test that Telegram bot token is never logged by TelegramNotifier."""
    notifier = TelegramNotifier(mock_config)

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp._content = b'{"ok": true}'

    monkeypatch.setattr(notifier.session, "post", lambda *args, **kwargs: mock_resp)

    with caplog.at_level(logging.DEBUG):
        notifier.send("A harmless message")

    for record in caplog.records:
        assert mock_config.telegram_bot_token not in record.message


def test_send_truncates_long_message(mock_config, monkeypatch):
    """Test message longer than MAX_TELEGRAM_MESSAGE_LENGTH is truncated."""
    notifier = TelegramNotifier(mock_config)

    captured = []
    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp._content = b'{"ok": true, "result": {"message_id": 1}}'

    def mock_post(url, json=None, timeout=None):
        captured.append(json)
        return mock_resp

    monkeypatch.setattr(notifier.session, "post", mock_post)

    long_msg = "A" * 5000
    notifier.send(long_msg)

    assert len(captured) == 1
    sent_text = captured[0]["text"]
    assert len(sent_text) <= 4096
    assert sent_text.endswith("...[dipotong]")


def test_send_connection_exception(mock_config, monkeypatch):
    """Test network connection error raises TelegramError."""
    notifier = TelegramNotifier(mock_config)

    def mock_post_fail(*args, **kwargs):
        raise requests.ConnectionError("DNS failure")

    monkeypatch.setattr(notifier.session, "post", mock_post_fail)

    with pytest.raises(TelegramError) as exc_info:
        notifier.send("Test network error")
    assert exc_info.value.status_code == 0
    assert "Connection error" in exc_info.value.body


def test_send_rate_limit_from_json(mock_config, monkeypatch):
    """Test rate limit parsing from json body parameters when header is absent."""
    notifier = TelegramNotifier(mock_config)

    mock_resp = requests.Response()
    mock_resp.status_code = 429
    mock_resp._content = b'{"ok": false, "description": "Too many requests", "parameters": {"retry_after": 75}}'

    monkeypatch.setattr(notifier.session, "post", lambda *args, **kwargs: mock_resp)

    with pytest.raises(TelegramRateLimitError) as exc_info:
        notifier.send("Rate limited json")
    assert exc_info.value.retry_after == 75


def test_send_invalid_json_fallback(mock_config, monkeypatch):
    """Test response with non-JSON content returns ok fallback dictionary."""
    notifier = TelegramNotifier(mock_config)

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp._content = b"Not valid JSON at all"

    monkeypatch.setattr(notifier.session, "post", lambda *args, **kwargs: mock_resp)

    res = notifier.send("Test fallback")
    assert res == {"ok": True, "result": {}}

