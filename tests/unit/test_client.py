"""Unit tests for HTSClient connectivity, headers, error conversion, and session expiry."""

import pytest
import requests
from app.config import AppConfig
from app.hts.client import HTSClient, create_http_session, is_session_expired
from app.hts.exceptions import HTSConnectionError, HTSError, HTSSessionExpiredError


@pytest.fixture
def mock_config():
    return AppConfig(
        hts_base_url="https://hts.diskomdigi.jatengprov.go.id",
        hts_username="operator_test",
        hts_password="secret_password",
        telegram_bot_token="123:TOKEN",
        telegram_chat_id="12345",
        request_timeout=5,
    )


def test_http_session_headers(mock_config):
    """Test that create_http_session sets proper User-Agent and Accept headers."""
    session = create_http_session(mock_config)
    assert "User-Agent" in session.headers
    assert "Mozilla" in session.headers["User-Agent"]
    assert "Accept" in session.headers
    assert "application/json" in session.headers["Accept"]


def test_connection_error_raises(mock_config, monkeypatch):
    """Test requests.ConnectionError is caught and raised as HTSConnectionError."""
    client = HTSClient(mock_config)

    def mock_post(*args, **kwargs):
        raise requests.ConnectionError("Connection refused")

    monkeypatch.setattr(client.session, "post", mock_post)

    with pytest.raises(HTSConnectionError) as exc_info:
        client._make_api_request(page=1, limit=10, status="pending")
    assert "Failed to connect" in str(exc_info.value) or "Connection refused" in str(exc_info.value)


def test_timeout_raises(mock_config, monkeypatch):
    """Test requests.Timeout is caught and raised as HTSConnectionError."""
    client = HTSClient(mock_config)

    def mock_post(*args, **kwargs):
        raise requests.Timeout("Request timed out")

    monkeypatch.setattr(client.session, "post", mock_post)

    with pytest.raises(HTSConnectionError) as exc_info:
        client._make_api_request(page=1, limit=10, status="pending")
    assert "timed out" in str(exc_info.value)


def test_session_expired_url_redirect(mock_config):
    """Test that response redirected to /login returns is_session_expired=True."""
    resp = requests.Response()
    resp.status_code = 200
    resp.url = "https://hts.diskomdigi.jatengprov.go.id/login"
    resp._content = b"<html>Some page</html>"

    assert is_session_expired(resp, base_url=mock_config.hts_base_url) is True

    # Test redirected to root base_url
    resp.url = "https://hts.diskomdigi.jatengprov.go.id"
    assert is_session_expired(resp, base_url=mock_config.hts_base_url) is True


def test_session_expired_html_content(mock_config):
    """Test that response containing captchaimg or userEmail returns is_session_expired=True."""
    resp = requests.Response()
    resp.status_code = 200
    resp.url = "https://hts.diskomdigi.jatengprov.go.id/list_aduan"
    resp._content = b'<html><body><img src="captchaimg" /><input id="userEmail" /></body></html>'

    assert is_session_expired(resp) is True

    # Test status code 401 and 403
    resp_401 = requests.Response()
    resp_401.status_code = 401
    resp_401.url = "https://hts.diskomdigi.jatengprov.go.id/list_aduan"
    resp_401._content = b"Unauthorized"
    assert is_session_expired(resp_401) is True


def test_session_not_expired():
    """Test that a normal authenticated response returns is_session_expired=False."""
    resp = requests.Response()
    resp.status_code = 200
    resp.url = "https://hts.diskomdigi.jatengprov.go.id/list_aduan"
    resp._content = b'{"data": [{"no_trouble": "T123"}], "pagination": {"page": 1}}'

    assert is_session_expired(resp) is False


def test_make_api_request_success(mock_config, monkeypatch):
    """Test that a valid 200 JSON response is returned cleanly."""
    client = HTSClient(mock_config)

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp.url = "https://hts.diskomdigi.jatengprov.go.id/get_aduan_data"
    mock_resp._content = b'{"data": [{"no_trouble": "T-001"}], "pagination": {"page": 1, "total_pages": 1}}'

    monkeypatch.setattr(client.session, "post", lambda *args, **kwargs: mock_resp)

    resp = client._make_api_request(page=1, limit=50, status="pending")
    assert resp.status_code == 200
    json_data = resp.json()
    assert len(json_data["data"]) == 1
    assert json_data["data"][0]["no_trouble"] == "T-001"


def test_make_api_request_session_expired_raises(mock_config, monkeypatch):
    """Test that receiving an expired response raises HTSSessionExpiredError."""
    client = HTSClient(mock_config)

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp.url = "https://hts.diskomdigi.jatengprov.go.id/get_aduan_data"
    mock_resp._content = b'<html><img src="captchaimg" /></html>'

    monkeypatch.setattr(client.session, "post", lambda *args, **kwargs: mock_resp)

    with pytest.raises(HTSSessionExpiredError):
        client._make_api_request(page=1, limit=50, status="pending")


def test_fetch_page_success(mock_config, monkeypatch):
    """Test fetch_tickets_page returns parsed JSON dict."""
    client = HTSClient(mock_config)

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp.url = "https://hts.diskomdigi.jatengprov.go.id/get_aduan_data"
    mock_resp._content = b'{"data": [{"no_trouble": "T-100"}], "pagination": {"page": 1, "total_pages": 1}}'

    monkeypatch.setattr(client.session, "post", lambda *args, **kwargs: mock_resp)

    data = client.fetch_tickets_page(page=1, limit=10, status="pending")
    assert isinstance(data, dict)
    assert len(data["data"]) == 1
    assert data["data"][0]["no_trouble"] == "T-100"


def test_fetch_page_session_expired(mock_config, monkeypatch):
    """Test fetch_tickets_page raises HTSSessionExpiredError when body contains captchaimg."""
    client = HTSClient(mock_config)

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp.url = "https://hts.diskomdigi.jatengprov.go.id/get_aduan_data"
    mock_resp._content = b'<html><img src="captchaimg" /></html>'

    monkeypatch.setattr(client.session, "post", lambda *args, **kwargs: mock_resp)

    with pytest.raises(HTSSessionExpiredError):
        client.fetch_tickets_page(page=1, limit=10, status="pending")


def test_fetch_page_http_error(mock_config, monkeypatch):
    """Test fetch_tickets_page raises HTSError when status is 500."""
    client = HTSClient(mock_config)

    mock_resp = requests.Response()
    mock_resp.status_code = 500
    mock_resp.url = "https://hts.diskomdigi.jatengprov.go.id/get_aduan_data"
    mock_resp._content = b"Internal Server Error"

    monkeypatch.setattr(client.session, "post", lambda *args, **kwargs: mock_resp)

    with pytest.raises(HTSError):
        client.fetch_tickets_page(page=1, limit=10, status="pending")


def test_fetch_all_tickets_pagination(mock_config, monkeypatch):
    """Test fetch_all_tickets automatically iterates pages until total_pages reached."""
    client = HTSClient(mock_config)

    page_calls = []

    def mock_post(url, json=None, *args, **kwargs):
        req_page = (json or {}).get("page", 1)
        page_calls.append(req_page)

        mock_resp = requests.Response()
        mock_resp.status_code = 200
        mock_resp.url = url

        if req_page == 1:
            mock_resp._content = (
                b'{"data": [{"no_trouble": "T-01"}], "pagination": {"page": 1, "total_pages": 2, "total": 2}}'
            )
        else:
            mock_resp._content = (
                b'{"data": [{"no_trouble": "T-02"}], "pagination": {"page": 2, "total_pages": 2, "total": 2}}'
            )
        return mock_resp

    monkeypatch.setattr(client.session, "post", mock_post)

    tickets = list(client.fetch_all_tickets(status="all", limit=1))
    assert len(tickets) == 2
    assert tickets[0].nomor_aduan == "T-01"
    assert tickets[1].nomor_aduan == "T-02"
    assert page_calls == [1, 2]


def test_fetch_all_tickets_empty(mock_config, monkeypatch):
    """Test fetch_all_tickets returns empty generator when total is 0."""
    client = HTSClient(mock_config)

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp.url = "https://hts.diskomdigi.jatengprov.go.id/get_aduan_data"
    mock_resp._content = b'{"data": [], "pagination": {"page": 1, "total_pages": 0, "total": 0}}'

    monkeypatch.setattr(client.session, "post", lambda *args, **kwargs: mock_resp)

    tickets = list(client.fetch_all_tickets(status="all"))
    assert len(tickets) == 0


def test_fetch_active_tickets(mock_config, monkeypatch):
    """Test fetch_active_tickets queries status='pending'."""
    client = HTSClient(mock_config)

    captured_payloads = []

    def mock_post(url, json=None, *args, **kwargs):
        captured_payloads.append(json)
        mock_resp = requests.Response()
        mock_resp.status_code = 200
        mock_resp.url = url
        mock_resp._content = b'{"data": [{"no_trouble": "T-ACT-01"}], "pagination": {"page": 1, "total_pages": 1, "total": 1}}'
        return mock_resp

    monkeypatch.setattr(client.session, "post", mock_post)

    tickets = client.fetch_active_tickets()
    assert len(tickets) == 1
    assert tickets[0].nomor_aduan == "T-ACT-01"
    assert len(captured_payloads) == 2
    assert captured_payloads[0]["status"] == "pending"
    assert captured_payloads[1]["status"] == "all"

