"""Ticket processing and event generation engine."""

import json
import logging
import uuid
from typing import Any, Dict, Optional
from app.database.db import DatabaseManager
from app.hts.parser import TicketData
from app.monitoring.change_detector import compute_hash, detect_changes, is_ticket_reused
from app.notifications.queue import NotificationQueue

from app.notifications.templates import (
    format_changed_ticket,
    format_completed_ticket,
    format_new_ticket,
)
from app.utils.time_utils import utcnow_iso

logger = logging.getLogger(__name__)


class TicketProcessor:
    """Processes incoming tickets from HTS, detects changes, and manages persistence and events."""

    def __init__(
        self,
        db: DatabaseManager,
        notif_queue: Optional[NotificationQueue] = None,
        is_initial_sync: bool = False,
    ) -> None:
        self.db = db
        self.notif_queue = notif_queue or NotificationQueue(db)
        self.is_initial_sync = is_initial_sync

    def process(self, ticket: TicketData, sync_source: str = "monitoring") -> None:
        """Process a single ticket item against current database state.

        Args:
            ticket: Current TicketData object.
            sync_source: 'initial' | 'monitoring' | 'reconciliation'.
        """
        existing = self.db.models.get_ticket(ticket.nomor_aduan)
        if existing is None:
            self._handle_new_ticket(ticket, sync_source=sync_source)
        else:
            self._handle_existing_ticket(ticket, existing=existing)

    def _handle_new_ticket(self, ticket: TicketData, sync_source: str) -> None:
        """Handle ingestion of a newly discovered ticket."""
        now = utcnow_iso()
        ticket_hash = compute_hash(ticket)

        with self.db.transaction() as conn:
            ticket_id = self.db.models.insert_ticket(
                conn=conn,
                ticket=ticket,
                sync_source=sync_source,
                last_hash=ticket_hash,
                now_iso=now,
            )

            snapshot_id = self.db.models.insert_snapshot(
                conn=conn,
                ticket_id=ticket_id,
                nomor_aduan=ticket.nomor_aduan,
                data=ticket.to_monitored_dict(),
                hash_val=ticket_hash,
                snapshot_type="initial",
                created_at=now,
            )

            # If NOT initial sync, create NEW_TICKET event and queue notification
            if not self.is_initial_sync:
                event_id = str(uuid.uuid4())
                self.db.models.insert_event(
                    conn=conn,
                    event_id=event_id,
                    ticket_id=ticket_id,
                    nomor_aduan=ticket.nomor_aduan,
                    event_type="NEW_TICKET",
                    current_snapshot_id=snapshot_id,
                    event_timestamp=now,
                    created_at=now,
                )

                self.notif_queue.queue(
                    conn=conn,
                    event_id=event_id,
                    message=format_new_ticket(ticket),
                )
                logger.info("New ticket processed: %s (event_id=%s)", ticket.nomor_aduan, event_id)

    def _handle_existing_ticket(self, ticket: TicketData, existing: Any) -> None:
        """Handle an existing ticket, checking for modifications or completion."""
        now = utcnow_iso()
        current_hash = compute_hash(ticket)

        # Quick hash comparison
        if existing["last_hash"] == current_hash:
            with self.db.transaction() as conn:
                self.db.models.update_last_seen(ticket.nomor_aduan, last_seen=now, conn=conn)
            logger.debug("Unchanged ticket %s: last_seen updated", ticket.nomor_aduan)
            return

        # Check if this nomor_aduan was reused for a brand new ticket
        if is_ticket_reused(ticket, existing):
            logger.info("Detected reused ticket number for %s: treating as NEW_TICKET.", ticket.nomor_aduan)
            self._handle_reused_ticket(ticket, existing=existing, current_hash=current_hash, now=now)
            return

        # Fetch latest snapshot to inspect changed fields

        last_snapshot = self.db.models.get_latest_snapshot(ticket.nomor_aduan)
        last_snapshot_json = last_snapshot["snapshot_data"] if last_snapshot else None
        was_completed = bool(existing["is_completed"])

        change_res = detect_changes(
            current=ticket,
            last_snapshot_json=last_snapshot_json,
            was_completed=was_completed,
        )

        if not change_res.is_changed and not change_res.is_completed:
            # Hash changed due to non-monitored discrepancy (or identical)
            with self.db.transaction() as conn:
                self.db.models.update_last_seen(ticket.nomor_aduan, last_seen=now, conn=conn)
            return

        with self.db.transaction() as conn:
            # Update ticket row
            self.db.models.update_ticket(
                conn=conn,
                ticket=ticket,
                new_hash=current_hash,
                last_seen=now,
            )

            prev_snapshot_id = last_snapshot["id"] if last_snapshot else None
            new_snapshot_id = self.db.models.insert_snapshot(
                conn=conn,
                ticket_id=existing["id"],
                nomor_aduan=ticket.nomor_aduan,
                data=ticket.to_monitored_dict(),
                hash_val=current_hash,
                snapshot_type="update",
                created_at=now,
            )

            # TICKET_CHANGED event (if there are changed fields)
            if change_res.is_changed:
                event_id = str(uuid.uuid4())
                self.db.models.insert_event(
                    conn=conn,
                    event_id=event_id,
                    ticket_id=existing["id"],
                    nomor_aduan=ticket.nomor_aduan,
                    event_type="TICKET_CHANGED",
                    changed_fields=change_res.changed_fields,
                    previous_snapshot_id=prev_snapshot_id,
                    current_snapshot_id=new_snapshot_id,
                    event_timestamp=now,
                    created_at=now,
                )
                self.notif_queue.queue(
                    conn=conn,
                    event_id=event_id,
                    message=format_changed_ticket(ticket, change_res.changed_fields),
                )
                logger.info("Ticket changed: %s (event_id=%s)", ticket.nomor_aduan, event_id)

            # COMPLETED event (only if newly completed and not previously completed)
            if change_res.is_completed:
                comp_event_id = str(uuid.uuid4())
                self.db.models.insert_event(
                    conn=conn,
                    event_id=comp_event_id,
                    ticket_id=existing["id"],
                    nomor_aduan=ticket.nomor_aduan,
                    event_type="COMPLETED",
                    current_snapshot_id=new_snapshot_id,
                    event_timestamp=now,
                    created_at=now,
                )
                self.notif_queue.queue(
                    conn=conn,
                    event_id=comp_event_id,
                    message=format_completed_ticket(ticket),
                )
                logger.info("Ticket completed: %s (event_id=%s)", ticket.nomor_aduan, comp_event_id)

    def _handle_reused_ticket(
        self,
        ticket: TicketData,
        existing: Any,
        current_hash: str,
        now: str,
    ) -> None:
        """Handle a ticket reusing a previously recorded nomor_aduan as a brand new ticket."""
        with self.db.transaction() as conn:
            # 1. Update ticket row with the new ticket's data
            self.db.models.update_ticket(
                conn=conn,
                ticket=ticket,
                new_hash=current_hash,
                last_seen=now,
            )

            # 2. Insert new initial snapshot for this new ticket iteration
            snapshot_id = self.db.models.insert_snapshot(
                conn=conn,
                ticket_id=existing["id"],
                nomor_aduan=ticket.nomor_aduan,
                data=ticket.to_monitored_dict(),
                hash_val=current_hash,
                snapshot_type="reused_initial",
                created_at=now,
            )

            # 3. Create NEW_TICKET event and queue "Ada Aduan Baru" notification
            if not self.is_initial_sync:
                event_id = str(uuid.uuid4())
                self.db.models.insert_event(
                    conn=conn,
                    event_id=event_id,
                    ticket_id=existing["id"],
                    nomor_aduan=ticket.nomor_aduan,
                    event_type="NEW_TICKET",
                    current_snapshot_id=snapshot_id,
                    event_timestamp=now,
                    created_at=now,
                )
                self.notif_queue.queue(
                    conn=conn,
                    event_id=event_id,
                    message=format_new_ticket(ticket),
                )
                logger.info(
                    "Reused ticket %s processed as NEW_TICKET (event_id=%s)",
                    ticket.nomor_aduan,
                    event_id,
                )

