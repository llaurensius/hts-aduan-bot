"""Unit tests for NotificationQueue (Milestone M10)."""

from datetime import datetime, timedelta, timezone
import pytest
from app.config import AppConfig
from app.database.db import DatabaseManager
from app.notifications.queue import NotificationQueue, get_next_delay
from app.notifications.telegram import (
    TelegramError,
    TelegramNotifier,
    TelegramRateLimitError,
)


class MockNotifier:
    """Mock notifier for testing process_pending."""

    def __init__(self, behavior="success"):
        self.behavior = behavior
        self.sent_calls = []

    def send(self, text: str):
        self.sent_calls.append(text)
        if self.behavior == "success":
            return {"ok": True, "result": {"message_id": 12345}}
        elif self.behavior == "rate_limit":
            raise TelegramRateLimitError(retry_after=60, body="Too Many Requests")
        elif self.behavior == "bad_request":
            raise TelegramError(status_code=400, body="Bad Request")
        elif self.behavior == "server_error":
            raise TelegramError(status_code=500, body="Server Error")
        else:
            raise RuntimeError("Unexpected error")


@pytest.fixture
def db_manager(tmp_path):
    db_file = tmp_path / "test_notif_queue.db"
    manager = DatabaseManager(str(db_file))
    manager.connect()
    yield manager
    manager.close()


@pytest.fixture
def queue(db_manager):
    return NotificationQueue(db=db_manager)


def test_queue_creates_pending_row(queue, db_manager):
    """Test queue() creates row in notifications with status PENDING."""
    with db_manager.transaction() as conn:
        notif_id = queue.queue(conn=conn, message="Hello world", notification_id="notif-1")

    assert notif_id > 0
    cursor = db_manager.connection.execute("SELECT * FROM notifications WHERE notification_id = 'notif-1';")
    row = cursor.fetchone()
    assert row is not None
    assert row["status"] == "PENDING"
    assert row["message_text"] == "Hello world"


def test_process_pending_success(queue, db_manager):
    """Test process_pending successfully updates status to SENT."""
    with db_manager.transaction() as conn:
        queue.queue(conn=conn, message="Test msg", notification_id="notif-succ")

    notifier = MockNotifier(behavior="success")
    sent = queue.process_pending(notifier)

    assert sent == 1
    cursor = db_manager.connection.execute("SELECT * FROM notifications WHERE notification_id = 'notif-succ';")
    row = cursor.fetchone()
    assert row["status"] == "SENT"
    assert row["telegram_message_id"] == "12345"


def test_process_pending_rate_limit(queue, db_manager):
    """Test TelegramRateLimitError sets status=FAILED and next_retry_at."""
    with db_manager.transaction() as conn:
        queue.queue(conn=conn, message="Rate limit test", notification_id="notif-rl")

    notifier = MockNotifier(behavior="rate_limit")
    sent = queue.process_pending(notifier)

    assert sent == 0
    cursor = db_manager.connection.execute("SELECT * FROM notifications WHERE notification_id = 'notif-rl';")
    row = cursor.fetchone()
    assert row["status"] == "FAILED"
    assert row["next_retry_at"] is not None
    assert row["attempt_count"] == 1


def test_process_pending_bad_request(queue, db_manager):
    """Test HTTP 400 immediately marks notification as RETRY_EXHAUSTED."""
    with db_manager.transaction() as conn:
        queue.queue(conn=conn, message="Bad request test", notification_id="notif-bad")

    notifier = MockNotifier(behavior="bad_request")
    sent = queue.process_pending(notifier)

    assert sent == 0
    cursor = db_manager.connection.execute("SELECT * FROM notifications WHERE notification_id = 'notif-bad';")
    row = cursor.fetchone()
    assert row["status"] == "RETRY_EXHAUSTED"


def test_process_pending_retry_delay():
    """Test get_next_delay returns exponential intervals [15, 60, 300, 1800]."""
    assert get_next_delay(0) == 15
    assert get_next_delay(1) == 60
    assert get_next_delay(2) == 300
    assert get_next_delay(3) == 1800
    assert get_next_delay(10) == 1800


def test_process_pending_respects_retry_at(queue, db_manager):
    """Test that a failed notification with next_retry_at in the future is skipped."""
    future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    with db_manager.transaction() as conn:
        queue.queue(conn=conn, message="Future retry", notification_id="notif-future")
        db_manager.models.mark_notification_failed(
            notification_id="notif-future",
            error="Connection error",
            next_retry_at=future_time,
            conn=conn,
        )

    notifier = MockNotifier(behavior="success")
    sent = queue.process_pending(notifier)

    # Must skip future retry notification
    assert sent == 0
    assert len(notifier.sent_calls) == 0


def test_at_least_once(queue, db_manager):
    """Test crash/failure during or before mark_sent leaves notification retryable."""
    with db_manager.transaction() as conn:
        queue.queue(conn=conn, message="At least once msg", notification_id="notif-alo")

    # First attempt fails due to server error
    fail_notifier = MockNotifier(behavior="server_error")
    queue.process_pending(fail_notifier)

    cursor = db_manager.connection.execute("SELECT * FROM notifications WHERE notification_id = 'notif-alo';")
    row = cursor.fetchone()
    assert row["status"] == "FAILED"
    assert row["attempt_count"] == 1

    # Simulate retry time has arrived
    past_time = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    db_manager.connection.execute(
        "UPDATE notifications SET next_retry_at = ? WHERE notification_id = 'notif-alo';", (past_time,)
    )

    # Second attempt succeeds
    succ_notifier = MockNotifier(behavior="success")
    sent = queue.process_pending(succ_notifier)
    assert sent == 1

    cursor = db_manager.connection.execute("SELECT * FROM notifications WHERE notification_id = 'notif-alo';")
    row_final = cursor.fetchone()
    assert row_final["status"] == "SENT"


def test_queue_system_alert(queue, db_manager):
    """Test queue_system_alert queues notification with NULL event_id."""
    rowid = queue.queue_system_alert("HTS_DOWN", "HTS System is Down")
    assert rowid > 0

    cursor = db_manager.connection.execute("SELECT * FROM notifications WHERE message_text = 'HTS System is Down';")
    row = cursor.fetchone()
    assert row is not None
    assert row["event_id"] is None
    assert row["status"] == "PENDING"


def test_process_pending_generic_exception(queue, db_manager):
    """Test generic unexpected exception marks notification as failed with retry delay."""
    with db_manager.transaction() as conn:
        queue.queue(conn=conn, message="Crash test", notification_id="notif-crash")

    notifier = MockNotifier(behavior="crash")
    sent = queue.process_pending(notifier)

    assert sent == 0
    cursor = db_manager.connection.execute("SELECT * FROM notifications WHERE notification_id = 'notif-crash';")
    row = cursor.fetchone()
    assert row["status"] == "FAILED"
    assert row["attempt_count"] == 1
    assert "Unexpected error" in row["last_error"]


def test_flush_notifications(queue, db_manager):
    """Test flush processes pending notifications up to empty queue."""
    with db_manager.transaction() as conn:
        queue.queue(conn=conn, message="Flush msg 1", notification_id="flush-1")
        queue.queue(conn=conn, message="Flush msg 2", notification_id="flush-2")

    notifier = MockNotifier(behavior="success")
    total_sent = queue.flush(notifier, timeout=2)
    assert total_sent == 2

    # Calling flush again when queue is empty returns 0 immediately
    total_sent_empty = queue.flush(notifier, timeout=2)
    assert total_sent_empty == 0

