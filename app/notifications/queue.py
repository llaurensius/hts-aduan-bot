"""Notification queue service for managing AT-LEAST-ONCE delivery and retry backoff."""

from datetime import datetime, timedelta, timezone
import logging
import sqlite3
import time
import uuid
from typing import Any, List, Optional
from app.config import AppConfig
from app.database.db import DatabaseManager
from app.notifications.telegram import (
    TelegramError,
    TelegramNotifier,
    TelegramRateLimitError,
)
from app.utils.time_utils import utcnow_iso

logger = logging.getLogger(__name__)

# Standard retry delays in seconds: 15s, 60s, 300s (5m), 1800s (30m)
RETRY_DELAYS = [15, 60, 300, 1800]


def get_next_delay(attempt_count: int) -> int:
    """Calculate exponential backoff delay based on attempt count.

    Args:
        attempt_count: Number of failed attempts so far (0-indexed).

    Returns:
        int: Delay in seconds.
    """
    idx = max(0, min(attempt_count, len(RETRY_DELAYS) - 1))
    return RETRY_DELAYS[idx]


class NotificationQueue:
    """Manages the persistence, queuing, and resilient delivery of outgoing notifications."""

    def __init__(self, db: DatabaseManager, config: Optional[AppConfig] = None) -> None:
        self.db = db
        self.config = config

    def queue(
        self,
        conn: sqlite3.Connection,
        *,
        event_id: Optional[str] = None,
        message: str,
        channel: str = "telegram",
        notification_id: Optional[str] = None,
    ) -> int:
        """Enqueue an outgoing notification inside caller's transaction.

        Args:
            conn: Active sqlite3.Connection transaction.
            event_id: Foreign key string pointing to ticket_events.event_id.
            message: Formatted notification message string.
            channel: 'telegram' (default).
            notification_id: Optional UUID4 string.

        Returns:
            int: Inserted notification record id (rowid).
        """
        # Cek apakah Mode Bisu aktif
        import json
        import os
        is_muted = False
        try:
            if os.path.exists("data/settings.json"):
                with open("data/settings.json", "r") as f:
                    is_muted = json.load(f).get("is_muted", False)
        except Exception:
            pass

        notif_id = notification_id or str(uuid.uuid4())
        initial_status = "CANCELLED" if is_muted else "PENDING"
        
        row_id = self.db.models.insert_notification(
            conn=conn,
            message_text=message,
            event_id=event_id,
            channel=channel,
            status=initial_status,
            notification_id=notif_id,
        )
        
        if is_muted:
            logger.info("Mute Mode is ON: Notification %s automatically CANCELLED", notif_id)
        else:
            logger.debug("Notification queued: id=%d, notif_id=%s, event_id=%s", row_id, notif_id, event_id)
            
        return row_id


    def queue_system_alert(
        self,
        alert_type: str,
        message: str,
        channel: str = "telegram",
    ) -> int:
        """Queue a system alert notification (event_id=None) in its own transaction.

        Args:
            alert_type: System alert type (e.g. CAPTCHA, HTS_DOWN).
            message: Formatted message string.
            channel: 'telegram'.

        Returns:
            int: Inserted notification rowid.
        """
        with self.db.transaction() as conn:
            return self.queue(conn, event_id=None, message=message, channel=channel)

    def process_pending(
        self,
        notifier: TelegramNotifier,
        now_iso: Optional[str] = None,
    ) -> int:
        """Process and send all pending and eligible failed notifications.

        Args:
            notifier: TelegramNotifier instance used to send messages.
            now_iso: Optional ISO timestamp to evaluate retry readiness.

        Returns:
            int: Number of notifications successfully sent.
        """
        current_now = now_iso or utcnow_iso()
        notifications = self.db.models.get_pending_notifications(now_iso=current_now)

        if not notifications:
            return 0

        logger.info("Processing %d pending/retryable notifications", len(notifications))
        sent_count = 0

        for notif in notifications:
            notif_id = notif["notification_id"]
            message_text = notif["message_text"]
            current_attempts = notif["attempt_count"]

            # Double-check jika notifikasi dibatalkan via dashboard saat loop ini sedang berjalan
            with self.db.transaction() as conn:
                check = conn.execute("SELECT status FROM notifications WHERE notification_id = ?", (notif_id,)).fetchone()
                if check and check["status"] == "CANCELLED":
                    logger.info("Notifikasi %s dibatalkan via dashboard (mid-loop), skip pengiriman.", notif_id)
                    continue

            try:
                result = notifier.send(message_text)
                msg_id = None
                if isinstance(result, dict) and "result" in result:
                    msg_id = result["result"].get("message_id")

                with self.db.transaction() as conn:
                    self.db.models.mark_notification_sent(notif_id, telegram_msg_id=msg_id, conn=conn)
                sent_count += 1
                logger.info("Notification %s successfully sent (telegram_msg_id=%s)", notif_id, msg_id)

            except TelegramRateLimitError as e:
                logger.warning("Notification %s hit Telegram rate limit (retry_after=%ds)", notif_id, e.retry_after)
                next_retry = (datetime.now(timezone.utc) + timedelta(seconds=e.retry_after)).isoformat()
                with self.db.transaction() as conn:
                    self.db.models.mark_notification_failed(notif_id, error=str(e), next_retry_at=next_retry, conn=conn)
                # Rate limited, break batch to respect Telegram throttle
                break

            except TelegramError as e:
                if e.status_code == 400:
                    logger.error("Notification %s failed permanently with HTTP 400: %s", notif_id, e.body)
                    with self.db.transaction() as conn:
                        self.db.models.mark_notification_exhausted(notif_id, conn=conn)
                else:
                    delay = get_next_delay(current_attempts)
                    next_retry = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                    logger.warning("Notification %s failed (status=%d), next retry in %ds", notif_id, e.status_code, delay)
                    with self.db.transaction() as conn:
                        self.db.models.mark_notification_failed(notif_id, error=str(e), next_retry_at=next_retry, conn=conn)

            except Exception as e:
                delay = get_next_delay(current_attempts)
                next_retry = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                logger.warning("Notification %s error: %s, next retry in %ds", notif_id, e, delay)
                with self.db.transaction() as conn:
                    self.db.models.mark_notification_failed(notif_id, error=str(e), next_retry_at=next_retry, conn=conn)

        return sent_count

    def flush(self, notifier: TelegramNotifier, timeout: int = 10) -> int:
        """Process all pending notifications up to a timeout threshold.

        Useful during graceful application shutdown.
        """
        start_time = time.time()
        total_sent = 0

        while (time.time() - start_time) < timeout:
            sent = self.process_pending(notifier)
            total_sent += sent
            if sent == 0:
                break
            time.sleep(0.5)

        return total_sent
