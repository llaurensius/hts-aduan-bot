"""Integration tests for Initial Synchronization (Milestone M6)."""

import json
from pathlib import Path
import pytest
import requests
from app.config import AppConfig
from app.database.db import DatabaseManager
from app.hts.client import HTSClient
from app.hts.parser import TicketData
from app.monitoring.reconciler import (
    InitialSyncTicketProcessor,
    run_initial_sync,
    run_reconciliation,
)
from app.monitoring.ticket_processor import TicketProcessor

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def mock_config():
    return AppConfig(
        hts_base_url="https://hts.diskomdigi.jatengprov.go.id",
        hts_username="operator_test",
        hts_password="secret_password",
        telegram_bot_token="123:TOKEN",
        telegram_chat_id="12345",
    )


@pytest.fixture
def db_manager(tmp_path):
    db_file = tmp_path / "test_initial_sync.db"
    manager = DatabaseManager(str(db_file))
    manager.connect()
    yield manager
    manager.close()


def _create_mock_tickets(count: int = 10):
    """Generate a list of mock TicketData objects."""
    tickets = []
    for i in range(1, count + 1):
        tickets.append(
            TicketData(
                nomor_aduan=f"T-SYNC-{i:04d}",
                kategori="troubleshoot",
                sub_kategori="NETWORK",
                instansi=f"OPD {i}",
                opd_induk=None,
                pic_nama=f"PIC {i}",
                pic_nomor="0812345678",
                keluhan=f"Keluhan gangguan internet {i}",
                tanggal_aduan="2026-09-17",
                t_solve="0",
                is_submitted=1,
            )
        )
    return tickets


def test_initial_sync_stores_tickets(mock_config, db_manager, monkeypatch):
    """Test initial sync stores 10 tickets in database with sync_source='initial'."""
    client = HTSClient(mock_config)
    mock_tickets = _create_mock_tickets(10)

    # Mock fetch_all_tickets generator
    def mock_fetch_all(status="all", limit=50):
        for t in mock_tickets:
            yield t

    monkeypatch.setattr(client, "fetch_all_tickets", mock_fetch_all)

    processor = InitialSyncTicketProcessor(db_manager)
    count = run_initial_sync(client, processor=processor, db=db_manager)

    assert count == 10
    assert db_manager.models.count_tickets() == 10

    # Verify a stored ticket
    stored = db_manager.models.get_ticket("T-SYNC-0001")
    assert stored is not None
    assert stored["sync_source"] == "initial"
    assert stored["nomor_aduan"] == "T-SYNC-0001"

    # Verify snapshot was stored
    snapshot = db_manager.models.get_latest_snapshot("T-SYNC-0001")
    assert snapshot is not None
    assert snapshot["snapshot_type"] == "initial"


def test_initial_sync_no_notifications(mock_config, db_manager, monkeypatch):
    """Test initial sync generates 0 notification records in database."""
    client = HTSClient(mock_config)
    mock_tickets = _create_mock_tickets(10)

    monkeypatch.setattr(client, "fetch_all_tickets", lambda status="all", limit=50: iter(mock_tickets))

    processor = InitialSyncTicketProcessor(db_manager)
    run_initial_sync(client, processor=processor, db=db_manager)

    # Notifications count must be strictly 0
    cursor = db_manager.connection.execute("SELECT COUNT(*) FROM notifications;")
    notif_count = cursor.fetchone()[0]
    assert notif_count == 0

    # Events count must also be 0 (no ticket events generated)
    cursor = db_manager.connection.execute("SELECT COUNT(*) FROM ticket_events;")
    event_count = cursor.fetchone()[0]
    assert event_count == 0


def test_initial_sync_idempotent(mock_config, db_manager, monkeypatch):
    """Test running initial sync twice leaves total tickets count unchanged (no duplicates)."""
    client = HTSClient(mock_config)
    mock_tickets = _create_mock_tickets(10)

    monkeypatch.setattr(client, "fetch_all_tickets", lambda status="all", limit=50: iter(mock_tickets))

    processor = InitialSyncTicketProcessor(db_manager)

    # First run
    count1 = run_initial_sync(client, processor=processor, db=db_manager)
    assert count1 == 10
    assert db_manager.models.count_tickets() == 10

    # Second run (e.g. after crash / restart)
    count2 = run_initial_sync(client, processor=processor, db=db_manager)
    assert count2 == 10
    assert db_manager.models.count_tickets() == 10


def test_initial_sync_system_events(mock_config, db_manager, monkeypatch):
    """Test INITIAL_SYNC_START and INITIAL_SYNC_COMPLETE are logged in system_events."""
    client = HTSClient(mock_config)
    mock_tickets = _create_mock_tickets(5)

    monkeypatch.setattr(client, "fetch_all_tickets", lambda status="all", limit=50: iter(mock_tickets))

    processor = InitialSyncTicketProcessor(db_manager)
    run_initial_sync(client, processor=processor, db=db_manager)

    cursor = db_manager.connection.execute("SELECT event_type, metadata FROM system_events ORDER BY id ASC;")
    events = cursor.fetchall()
    event_types = [e["event_type"] for e in events]

    assert "INITIAL_SYNC_START" in event_types
    assert "INITIAL_SYNC_COMPLETE" in event_types

    # Find complete event and check ticket_count
    complete_event = next(e for e in events if e["event_type"] == "INITIAL_SYNC_COMPLETE")
    metadata = json.loads(complete_event["metadata"])
    assert metadata["ticket_count"] == 5


def test_reconcile_new_tickets(mock_config, db_manager, monkeypatch):
    """Test reconciliation detects new tickets, inserts with sync_source='reconciliation', and queues notifications."""
    client = HTSClient(mock_config)
    tickets = _create_mock_tickets(2)

    monkeypatch.setattr(client, "fetch_all_tickets", lambda status="all", limit=50: iter(tickets))

    processor = TicketProcessor(db_manager)
    stats = run_reconciliation(client, db=db_manager, processor=processor)

    assert stats["new"] == 2
    assert stats["changed"] == 0
    assert stats["unchanged"] == 0
    assert db_manager.models.count_tickets() == 2

    # Check sync_source is reconciliation
    t1 = db_manager.models.get_ticket("T-SYNC-0001")
    assert t1["sync_source"] == "reconciliation"

    # Check NEW_TICKET events created
    cursor = db_manager.connection.execute("SELECT event_type FROM ticket_events ORDER BY id ASC;")
    events = [r[0] for r in cursor.fetchall()]
    assert events == ["NEW_TICKET", "NEW_TICKET"]

    # Check notifications created
    assert db_manager.models.count_notifications_by_status("PENDING") == 2


def test_reconcile_changed_tickets(mock_config, db_manager, monkeypatch):
    """Test reconciliation detects changed tickets and creates TICKET_CHANGED event."""
    client = HTSClient(mock_config)
    original_tickets = _create_mock_tickets(1)
    processor = TicketProcessor(db_manager)

    # Pre-populate DB with original ticket
    processor.process(original_tickets[0])

    # Change keluhan
    changed_ticket = TicketData(
        nomor_aduan="T-SYNC-0001",
        kategori="troubleshoot",
        sub_kategori="NETWORK",
        instansi="OPD 1",
        opd_induk=None,
        pic_nama="PIC 1",
        pic_nomor="0812345678",
        keluhan="Keluhan telah diperbarui secara signifikan",
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )

    monkeypatch.setattr(client, "fetch_all_tickets", lambda status="all", limit=50: iter([changed_ticket]))

    stats = run_reconciliation(client, db=db_manager, processor=processor)

    assert stats["new"] == 0
    assert stats["changed"] == 1
    assert stats["unchanged"] == 0

    cursor = db_manager.connection.execute(
        "SELECT event_type FROM ticket_events WHERE nomor_aduan = 'T-SYNC-0001' ORDER BY id ASC;"
    )
    events = [r[0] for r in cursor.fetchall()]
    assert "TICKET_CHANGED" in events


def test_reconcile_unchanged_tickets(mock_config, db_manager, monkeypatch):
    """Test reconciliation updates last_seen without generating new events for unchanged tickets."""
    client = HTSClient(mock_config)
    original_tickets = _create_mock_tickets(2)
    processor = TicketProcessor(db_manager)

    for t in original_tickets:
        processor.process(t)

    # Record last_seen and event count before reconcile
    cursor = db_manager.connection.execute("SELECT COUNT(*) FROM ticket_events;")
    initial_event_count = cursor.fetchone()[0]

    # Explicitly set last_seen in past
    old_time = "2020-01-01T00:00:00+00:00"
    db_manager.connection.execute("UPDATE tickets SET last_seen = ?;", (old_time,))
    db_manager.connection.commit()

    monkeypatch.setattr(client, "fetch_all_tickets", lambda status="all", limit=50: iter(original_tickets))

    stats = run_reconciliation(client, db=db_manager, processor=processor)

    assert stats["new"] == 0
    assert stats["changed"] == 0
    assert stats["unchanged"] == 2

    # last_seen should be updated
    t1 = db_manager.models.get_ticket("T-SYNC-0001")
    assert t1["last_seen"] > old_time

    # No additional events created
    cursor = db_manager.connection.execute("SELECT COUNT(*) FROM ticket_events;")
    assert cursor.fetchone()[0] == initial_event_count


def test_reconcile_mixed(mock_config, db_manager, monkeypatch):
    """Test reconciliation handles mix of new, changed, and unchanged tickets."""
    client = HTSClient(mock_config)
    tickets = _create_mock_tickets(2)
    processor = TicketProcessor(db_manager)

    # Pre-populate T-SYNC-0001 and T-SYNC-0002
    for t in tickets:
        processor.process(t)

    # In HTS:
    # T-SYNC-0001: unchanged
    # T-SYNC-0002: changed
    # T-SYNC-0003: new
    t1_unchanged = tickets[0]
    t2_changed = TicketData(
        nomor_aduan="T-SYNC-0002",
        kategori="troubleshoot",
        sub_kategori="NETWORK",
        instansi="OPD 2",
        opd_induk=None,
        pic_nama="PIC 2",
        pic_nomor="0812345678",
        keluhan="Keluhan T-SYNC-0002 diubah",
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )
    t3_new = TicketData(
        nomor_aduan="T-SYNC-0003",
        kategori="troubleshoot",
        sub_kategori="SERVER",
        instansi="OPD 3",
        opd_induk=None,
        pic_nama="PIC 3",
        pic_nomor="0812345679",
        keluhan="Tiket baru T-SYNC-0003",
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )

    monkeypatch.setattr(
        client, "fetch_all_tickets", lambda status="all", limit=50: iter([t1_unchanged, t2_changed, t3_new])
    )

    stats = run_reconciliation(client, db=db_manager, processor=processor)

    assert stats["new"] == 1
    assert stats["changed"] == 1
    assert stats["unchanged"] == 1
    assert db_manager.models.count_tickets() == 3


def test_reconcile_system_events(mock_config, db_manager, monkeypatch):
    """Test RECONCILIATION_START and RECONCILIATION_COMPLETE are logged with stats metadata."""
    client = HTSClient(mock_config)
    tickets = _create_mock_tickets(1)

    monkeypatch.setattr(client, "fetch_all_tickets", lambda status="all", limit=50: iter(tickets))

    processor = TicketProcessor(db_manager)
    run_reconciliation(client, db=db_manager, processor=processor)

    cursor = db_manager.connection.execute("SELECT event_type, metadata FROM system_events ORDER BY id ASC;")
    events = cursor.fetchall()
    event_types = [e["event_type"] for e in events]

    assert "RECONCILIATION_START" in event_types
    assert "RECONCILIATION_COMPLETE" in event_types

    complete_event = next(e for e in events if e["event_type"] == "RECONCILIATION_COMPLETE")
    metadata = json.loads(complete_event["metadata"])
    assert metadata["new"] == 1
    assert metadata["changed"] == 0
    assert metadata["unchanged"] == 0
    assert "duration_ms" in metadata


def test_reconcile_stats(mock_config, db_manager, monkeypatch):
    """Test reconciliation returns correct stats dictionary structure."""
    client = HTSClient(mock_config)
    monkeypatch.setattr(client, "fetch_all_tickets", lambda status="all", limit=50: iter([]))

    processor = TicketProcessor(db_manager)
    stats = run_reconciliation(client, db=db_manager, processor=processor)

    assert isinstance(stats, dict)
    assert stats["new"] == 0
    assert stats["changed"] == 0
    assert stats["unchanged"] == 0
    assert isinstance(stats["duration_ms"], float)

