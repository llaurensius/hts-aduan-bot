"""Reconciliation and Initial Synchronization service."""

import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional, Protocol
from app.database.db import DatabaseManager
from app.hts.client import HTSClient
from app.hts.parser import TicketData
from app.monitoring.change_detector import compute_hash

logger = logging.getLogger(__name__)


class TicketProcessorProtocol(Protocol):
    """Protocol representing a ticket processor."""
    is_initial_sync: bool
    def process(self, ticket: TicketData, sync_source: str = "initial") -> Any: ...


class InitialSyncTicketProcessor:
    """Default processor used during initial synchronization.

    Saves tickets and initial snapshots to the database without generating
    any notification events or Telegram alerts.
    """

    def __init__(self, db: DatabaseManager, is_initial_sync: bool = True) -> None:
        self.db = db
        self.is_initial_sync = is_initial_sync

    def process(self, ticket: TicketData, sync_source: str = "initial") -> int:
        """Process a ticket during initial sync: insert ticket and initial snapshot.

        Idempotent via INSERT OR IGNORE.

        Args:
            ticket: TicketData object.
            sync_source: 'initial' (default).

        Returns:
            int: ticket ID in database.
        """
        ticket_hash = compute_hash(ticket)

        with self.db.transaction() as conn:
            ticket_id = self.db.models.insert_ticket(
                conn=conn,
                ticket=ticket,
                sync_source=sync_source,
                last_hash=ticket_hash,
            )

            # Insert initial snapshot if not already present
            existing_snap = self.db.models.get_latest_snapshot(ticket.nomor_aduan, conn=conn)
            if not existing_snap:
                self.db.models.insert_snapshot(
                    conn=conn,
                    ticket_id=ticket_id,
                    nomor_aduan=ticket.nomor_aduan,
                    data=ticket.to_monitored_dict(),
                    hash_val=ticket_hash,
                    snapshot_type="initial",
                )

        return ticket_id


def run_initial_sync(
    client: HTSClient,
    processor: Optional[Any] = None,
    db: Optional[DatabaseManager] = None,
) -> int:
    """Fetch all tickets from HTS and store them without generating notifications.

    Args:
        client: HTSClient instance.
        processor: Processor instance with `process()` method and `is_initial_sync` flag.
        db: DatabaseManager instance for recording system events.

    Returns:
        int: Number of tickets processed during initial sync.
    """
    logger.info("Starting Initial Synchronization...")
    if db:
        db.models.insert_system_event("INITIAL_SYNC_START")

    proc = processor or (InitialSyncTicketProcessor(db) if db else None)
    if proc is None:
        raise ValueError("Either processor or db must be provided to run_initial_sync")

    proc.is_initial_sync = True
    count = 0

    try:
        for ticket in client.fetch_all_tickets(status="all"):
            proc.process(ticket, sync_source="initial")
            count += 1
            if count % 100 == 0:
                logger.info("Initial sync progress: %d tickets processed", count)
    finally:
        proc.is_initial_sync = False

    logger.info("Initial Synchronization completed: %d total tickets processed", count)
    if db:
        db.models.insert_system_event(
            "INITIAL_SYNC_COMPLETE",
            metadata={"ticket_count": count},
        )

    return count


def run_reconciliation(
    client: HTSClient,
    db: DatabaseManager,
    processor: Any,
) -> Dict[str, Any]:
    """Run reconciliation between local database state and current HTS data.

    Called after startup, restart, session recovery, or HTS recovery.
    Fetches all tickets (status='all') from HTS:
    - Tickets in HTS but not in DB -> processed as new (generates event & notification)
    - Tickets in DB with changed hash -> processed as changed (generates event & notification)
    - Tickets in DB with identical hash -> update last_seen only (no notification)

    Args:
        client: HTSClient instance.
        db: DatabaseManager instance.
        processor: TicketProcessor instance.

    Returns:
        dict: Reconciliation statistics {"new": int, "changed": int, "unchanged": int, "duration_ms": float}
    """
    logger.info("Starting Reconciliation service...")
    db.models.insert_system_event("RECONCILIATION_START")
    start = time.time()

    if hasattr(processor, "is_initial_sync"):
        processor.is_initial_sync = False

    stats: Dict[str, Any] = {"new": 0, "changed": 0, "unchanged": 0}

    for ticket in client.fetch_all_tickets(status="all"):
        existing = db.models.get_ticket(ticket.nomor_aduan)
        if existing is None:
            processor.process(ticket, sync_source="reconciliation")
            stats["new"] += 1
        else:
            current_hash = compute_hash(ticket)
            if existing["last_hash"] != current_hash:
                processor.process(ticket, sync_source="reconciliation")
                stats["changed"] += 1
            else:
                db.models.update_last_seen(ticket.nomor_aduan)
                stats["unchanged"] += 1

    duration_ms = round((time.time() - start) * 1000, 2)
    stats["duration_ms"] = duration_ms
    logger.info(
        "Reconciliation completed in %.2fms: %d new, %d changed, %d unchanged",
        duration_ms,
        stats["new"],
        stats["changed"],
        stats["unchanged"],
    )
    db.models.insert_system_event("RECONCILIATION_COMPLETE", metadata=stats)
    return stats

