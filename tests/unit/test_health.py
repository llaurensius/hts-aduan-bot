"""Unit and functional tests for the Health Check HTTP Endpoint (Milestone M14)."""

import json
import urllib.error
import urllib.request
import pytest

from app.config import AppConfig
from app.health.endpoint import HealthState, start_health_server


@pytest.fixture
def health_server_env():
    """Start an ephemeral Health HTTP server for testing."""
    config = AppConfig(
        hts_base_url="https://hts.example.com",
        hts_username="operator_user",
        hts_password="super_secret_password_999",
        telegram_bot_token="987654321:TOKEN_SECRET_XYZ",
        telegram_chat_id="12345",
        health_host="127.0.0.1",
        health_port=0,  # Bind to ephemeral port
    )
    state = HealthState()
    thread = start_health_server(config, state)
    port = thread.server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"

    yield config, state, base_url

    thread.server.shutdown()
    thread.server.server_close()


def test_health_healthy(health_server_env):
    """Verify that normal operational state returns 200 with status 'healthy'."""
    config, state, base_url = health_server_env
    state.app_state = "MONITORING"
    state.hts_state = "ok"
    state.failed_notifications = 0
    state.consecutive_failures = 0

    with urllib.request.urlopen(f"{base_url}/health") as response:
        assert response.status == 200
        data = json.loads(response.read().decode("utf-8"))
        assert data["status"] == "healthy"
        assert data["app"]["state"] == "MONITORING"
        assert data["hts"]["state"] == "ok"


def test_health_degraded(health_server_env):
    """Verify that failed notifications or minor errors return 200 with status 'degraded'."""
    config, state, base_url = health_server_env
    state.app_state = "MONITORING"
    state.failed_notifications = 2
    state.consecutive_failures = 0

    with urllib.request.urlopen(f"{base_url}/health") as response:
        assert response.status == 200
        data = json.loads(response.read().decode("utf-8"))
        assert data["status"] == "degraded"
        assert data["telegram"]["failed_notifications"] == 2


def test_health_unhealthy_hts_down(health_server_env):
    """Verify that HTS_UNAVAILABLE or CAPTCHA_REQUIRED returns 503 with status 'unhealthy'."""
    config, state, base_url = health_server_env

    # 1. HTS_UNAVAILABLE
    state.app_state = "HTS_UNAVAILABLE"
    state.hts_state = "down"

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/health")
    assert exc_info.value.code == 503
    data = json.loads(exc_info.value.read().decode("utf-8"))
    assert data["status"] == "unhealthy"

    # 2. CAPTCHA_REQUIRED
    state.app_state = "CAPTCHA_REQUIRED"
    with pytest.raises(urllib.error.HTTPError) as exc_info2:
        urllib.request.urlopen(f"{base_url}/health")
    assert exc_info2.value.code == 503
    data2 = json.loads(exc_info2.value.read().decode("utf-8"))
    assert data2["status"] == "unhealthy"


def test_health_no_credentials(health_server_env):
    """Verify that no passwords, bot tokens, or credentials appear anywhere in the response."""
    config, state, base_url = health_server_env

    with urllib.request.urlopen(f"{base_url}/health") as response:
        raw_body = response.read().decode("utf-8")
        assert config.hts_password not in raw_body
        assert config.telegram_bot_token not in raw_body
        assert config.hts_username not in raw_body
        assert "password" not in raw_body.lower()
        assert "cookie" not in raw_body.lower()


def test_health_404_other_path(health_server_env):
    """Verify that requests to endpoints other than /health return 404."""
    config, state, base_url = health_server_env

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/unknown-endpoint")
    assert exc_info.value.code == 404


def test_health_response_json_valid(health_server_env):
    """Verify that the /health response is valid JSON and contains all required structure keys."""
    config, state, base_url = health_server_env

    with urllib.request.urlopen(f"{base_url}/health") as response:
        assert response.headers.get("Content-Type") == "application/json"
        data = json.loads(response.read().decode("utf-8"))

        # Verify top-level keys
        assert "status" in data
        assert "timestamp" in data
        assert "hts" in data
        assert "session" in data
        assert "telegram" in data
        assert "database" in data
        assert "app" in data

        # Verify nested keys
        assert "uptime_seconds" in data["app"]
        assert isinstance(data["app"]["uptime_seconds"], int)
        assert data["app"]["uptime_seconds"] >= 0
        assert data["app"]["version"] == "1.0.0"
