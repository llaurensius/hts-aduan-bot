"""HTTP health check endpoint and daemon server for system monitoring."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import threading
from typing import Any, Optional

from app.config import AppConfig
from app.utils.time_utils import utcnow_iso

logger = logging.getLogger(__name__)


@dataclass
class HealthState:
    """Shared mutable state between main monitoring loop and health server thread."""

    app_state: str = "STARTING"
    hts_state: str = "unknown"
    last_poll: Optional[str] = None
    last_successful_poll: Optional[str] = None
    consecutive_failures: int = 0
    session_state: str = "UNAUTHENTICATED"
    pending_notifications: int = 0
    failed_notifications: int = 0
    last_telegram_sent: Optional[str] = None
    total_tickets: int = 0
    database_state: str = "ok"
    version: str = "1.0.0"
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class HealthHTTPHandler(BaseHTTPRequestHandler):
    """Handle HTTP requests for health check."""

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.path != "/health":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Not found"}).encode("utf-8"))
            return

        state: HealthState = getattr(self.server, "health_state", HealthState())

        # Determine overall health status
        if state.app_state in ("HTS_UNAVAILABLE", "CAPTCHA_REQUIRED") or state.database_state != "ok":
            status = "unhealthy"
            http_code = 503
        elif state.failed_notifications > 0 or state.consecutive_failures > 0:
            status = "degraded"
            http_code = 200
        else:
            status = "healthy"
            http_code = 200

        # Calculate uptime
        now = datetime.now(timezone.utc)
        if state.start_time.tzinfo is None:
            uptime = int((datetime.utcnow() - state.start_time).total_seconds())
        else:
            uptime = int((now - state.start_time).total_seconds())

        response_body = {
            "status": status,
            "timestamp": utcnow_iso(),
            "hts": {
                "state": state.hts_state,
                "last_poll": state.last_poll,
                "last_successful_poll": state.last_successful_poll,
                "consecutive_failures": state.consecutive_failures,
            },
            "session": {
                "state": state.session_state,
            },
            "telegram": {
                "last_sent": state.last_telegram_sent,
                "pending_notifications": state.pending_notifications,
                "failed_notifications": state.failed_notifications,
            },
            "database": {
                "state": state.database_state,
                "total_tickets": state.total_tickets,
            },
            "app": {
                "state": state.app_state,
                "uptime_seconds": max(0, uptime),
                "version": getattr(state, "version", "1.0.0"),
            },
        }

        body_bytes = json.dumps(response_body, indent=2).encode("utf-8")

        self.send_response(http_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default access log spam from HTTP server."""
        pass


def start_health_server(config: AppConfig, health_state: HealthState) -> threading.Thread:
    """Start the health HTTP server in a daemon thread.

    Args:
        config: AppConfig instance containing health_host and health_port.
        health_state: Shared HealthState instance.

    Returns:
        threading.Thread: Daemon thread running the server, with attached `.server` attribute.
    """
    host = getattr(config, "health_host", "127.0.0.1")
    port = getattr(config, "health_port", 8080)

    server = HTTPServer((host, port), HealthHTTPHandler)
    server.health_state = health_state  # type: ignore[attr-defined]

    thread = threading.Thread(
        target=server.serve_forever,
        name="HealthServerThread",
        daemon=True,
    )
    thread.server = server  # type: ignore[attr-defined]
    thread.start()

    logger.info("Health server listening on http://%s:%d/health", host, server.server_address[1])
    return thread
