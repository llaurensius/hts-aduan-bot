"""Integration tests for TicketProcessor and event processing lifecycle (Milestone M8)."""

import json
import sqlite3
import pytest
from app.database.db import DatabaseManager
from app.hts.parser import TicketData
from app.monitoring.change_detector import compute_hash
from app.monitoring.ticket_processor import TicketProcessor
from app.notifications.queue import NotificationQueue


@pytest.fixture
def db_manager(tmp_path):
    db_file = tmp_path / "test_processor.db"
    manager = DatabaseManager(str(db_file))
    manager.connect()
    yield manager
    manager.close()


@pytest.fixture
def base_ticket():
    return TicketData(
        nomor_aduan="1977-TShoot-2026-jateng-09",
        kategori="troubleshoot",
        sub_kategori="CORE NETWORK",
        instansi="Dinas Kesehatan",
        opd_induk=None,
        pic_nama="Siti Rahayu",
        pic_nomor="081234567891",
        keluhan="Jaringan tidak bisa diakses.",
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )


def test_process_new_ticket_monitoring(db_manager, base_ticket):
    """Test new ticket in monitoring mode creates ticket row, snapshot, NEW_TICKET event, and notification."""
    processor = TicketProcessor(db=db_manager, is_initial_sync=False)
    processor.process(base_ticket, sync_source="monitoring")

    # 1. Verify ticket row
    ticket = db_manager.models.get_ticket(base_ticket.nomor_aduan)
    assert ticket is not None
    assert ticket["nomor_aduan"] == base_ticket.nomor_aduan
    assert ticket["sync_source"] == "monitoring"

    # 2. Verify snapshot row
    snapshot = db_manager.models.get_latest_snapshot(base_ticket.nomor_aduan)
    assert snapshot is not None
    assert snapshot["snapshot_type"] == "initial"

    # 3. Verify event row
    cursor = db_manager.connection.execute(
        "SELECT * FROM ticket_events WHERE nomor_aduan = ?;", (base_ticket.nomor_aduan,)
    )
    events = cursor.fetchall()
    assert len(events) == 1
    assert events[0]["event_type"] == "NEW_TICKET"

    # 4. Verify notification queued
    cursor = db_manager.connection.execute("SELECT * FROM notifications;")
    notifs = cursor.fetchall()
    assert len(notifs) == 1
    assert notifs[0]["status"] == "PENDING"
    assert notifs[0]["event_id"] == events[0]["event_id"]


def test_process_new_ticket_initial_sync(db_manager, base_ticket):
    """Test new ticket with is_initial_sync=True creates NO events and NO notifications."""
    processor = TicketProcessor(db=db_manager, is_initial_sync=True)
    processor.process(base_ticket, sync_source="initial")

    # Ticket and snapshot must be stored
    assert db_manager.models.get_ticket(base_ticket.nomor_aduan) is not None
    assert db_manager.models.get_latest_snapshot(base_ticket.nomor_aduan) is not None

    # Events and notifications must NOT be created
    cursor = db_manager.connection.execute("SELECT COUNT(*) FROM ticket_events;")
    assert cursor.fetchone()[0] == 0

    cursor = db_manager.connection.execute("SELECT COUNT(*) FROM notifications;")
    assert cursor.fetchone()[0] == 0


def test_process_unchanged_ticket(db_manager, base_ticket):
    """Test processing an unchanged ticket only updates last_seen timestamp."""
    processor = TicketProcessor(db=db_manager, is_initial_sync=False)
    processor.process(base_ticket, sync_source="monitoring")

    ticket_before = db_manager.models.get_ticket(base_ticket.nomor_aduan)
    first_last_seen = ticket_before["last_seen"]

    # Process again without changes
    processor.process(base_ticket, sync_source="monitoring")

    ticket_after = db_manager.models.get_ticket(base_ticket.nomor_aduan)
    assert ticket_after["last_seen"] >= first_last_seen

    # Event count must still be 1 (only the initial NEW_TICKET)
    cursor = db_manager.connection.execute("SELECT COUNT(*) FROM ticket_events;")
    assert cursor.fetchone()[0] == 1


def test_process_changed_keluhan(db_manager, base_ticket):
    """Test modifying keluhan generates TICKET_CHANGED event with correct changed_fields."""
    processor = TicketProcessor(db=db_manager, is_initial_sync=False)
    processor.process(base_ticket, sync_source="monitoring")

    updated_ticket = TicketData(
        nomor_aduan=base_ticket.nomor_aduan,
        kategori=base_ticket.kategori,
        sub_kategori=base_ticket.sub_kategori,
        instansi=base_ticket.instansi,
        opd_induk=base_ticket.opd_induk,
        pic_nama=base_ticket.pic_nama,
        pic_nomor=base_ticket.pic_nomor,
        keluhan="Kabel switch utama terbakar.",  # Changed
        tanggal_aduan=base_ticket.tanggal_aduan,
        t_solve=base_ticket.t_solve,
        is_submitted=base_ticket.is_submitted,
    )

    processor.process(updated_ticket, sync_source="monitoring")

    # Verify event generated
    cursor = db_manager.connection.execute(
        "SELECT * FROM ticket_events WHERE event_type = 'TICKET_CHANGED';"
    )
    events = cursor.fetchall()
    assert len(events) == 1

    changed_fields = json.loads(events[0]["changed_fields"])
    assert "keluhan" in changed_fields
    assert changed_fields["keluhan"]["old"] == "Jaringan tidak bisa diakses."
    assert changed_fields["keluhan"]["new"] == "Kabel switch utama terbakar."


def test_process_completed(db_manager, base_ticket):
    """Test ticket marked completed generates both TICKET_CHANGED and COMPLETED events."""
    processor = TicketProcessor(db=db_manager, is_initial_sync=False)
    processor.process(base_ticket, sync_source="monitoring")

    completed_ticket = TicketData(
        nomor_aduan=base_ticket.nomor_aduan,
        kategori=base_ticket.kategori,
        sub_kategori=base_ticket.sub_kategori,
        instansi=base_ticket.instansi,
        opd_induk=base_ticket.opd_induk,
        pic_nama=base_ticket.pic_nama,
        pic_nomor=base_ticket.pic_nomor,
        keluhan=base_ticket.keluhan,
        tanggal_aduan=base_ticket.tanggal_aduan,
        t_solve="1726527600",  # Changed to solved
        is_submitted=base_ticket.is_submitted,
    )

    processor.process(completed_ticket, sync_source="monitoring")

    cursor = db_manager.connection.execute(
        "SELECT event_type FROM ticket_events ORDER BY id ASC;"
    )
    event_types = [row[0] for row in cursor.fetchall()]

    assert "NEW_TICKET" in event_types
    assert "TICKET_CHANGED" in event_types
    assert "COMPLETED" in event_types


def test_process_no_duplicate_completed(db_manager, base_ticket):
    """Test ticket already marked completed does not generate duplicate COMPLETED event."""
    processor = TicketProcessor(db=db_manager, is_initial_sync=False)
    processor.process(base_ticket, sync_source="monitoring")

    completed_ticket = TicketData(
        nomor_aduan=base_ticket.nomor_aduan,
        kategori=base_ticket.kategori,
        sub_kategori=base_ticket.sub_kategori,
        instansi=base_ticket.instansi,
        opd_induk=base_ticket.opd_induk,
        pic_nama=base_ticket.pic_nama,
        pic_nomor=base_ticket.pic_nomor,
        keluhan=base_ticket.keluhan,
        tanggal_aduan=base_ticket.tanggal_aduan,
        t_solve="1726527600",
        is_submitted=base_ticket.is_submitted,
    )

    # First completion
    processor.process(completed_ticket, sync_source="monitoring")

    # Second cycle: change keluhan on an already completed ticket
    modified_completed = TicketData(
        nomor_aduan=base_ticket.nomor_aduan,
        kategori=base_ticket.kategori,
        sub_kategori=base_ticket.sub_kategori,
        instansi=base_ticket.instansi,
        opd_induk=base_ticket.opd_induk,
        pic_nama=base_ticket.pic_nama,
        pic_nomor=base_ticket.pic_nomor,
        keluhan="Catatan tambahan pasca perbaikan.",
        tanggal_aduan=base_ticket.tanggal_aduan,
        t_solve="1726527600",
        is_submitted=base_ticket.is_submitted,
    )
    processor.process(modified_completed, sync_source="monitoring")

    # Check COMPLETED events count
    cursor = db_manager.connection.execute(
        "SELECT COUNT(*) FROM ticket_events WHERE event_type = 'COMPLETED';"
    )
    assert cursor.fetchone()[0] == 1  # Exactly 1 COMPLETED event


def test_process_atomicity(db_manager, base_ticket, monkeypatch):
    """Test that a DB exception inside transaction triggers rollback without partial state."""
    processor = TicketProcessor(db=db_manager, is_initial_sync=False)

    # Cause notif_queue.queue to fail
    def mock_queue_fail(*args, **kwargs):
        raise sqlite3.OperationalError("Simulated write failure")

    monkeypatch.setattr(processor.notif_queue, "queue", mock_queue_fail)

    with pytest.raises(sqlite3.OperationalError):
        processor.process(base_ticket, sync_source="monitoring")

    # Due to transaction rollback, neither ticket nor snapshot nor event should exist
    assert db_manager.models.get_ticket(base_ticket.nomor_aduan) is None
    assert db_manager.models.get_latest_snapshot(base_ticket.nomor_aduan) is None

    cursor = db_manager.connection.execute("SELECT COUNT(*) FROM ticket_events;")
    assert cursor.fetchone()[0] == 0


def test_process_reused_ticket_number(db_manager, base_ticket):
    """Test when a ticket number is reused with a different date/agency, it emits NEW_TICKET."""
    processor = TicketProcessor(db=db_manager, is_initial_sync=False)

    # 1. Process original ticket
    processor.process(base_ticket, sync_source="monitoring")

    # Verify 1 NEW_TICKET event
    cursor = db_manager.connection.execute(
        "SELECT event_type FROM ticket_events WHERE nomor_aduan = ?;", (base_ticket.nomor_aduan,)
    )
    assert [r[0] for r in cursor.fetchall()] == ["NEW_TICKET"]

    # 2. Reused ticket: Same nomor_aduan, but different instansi & tanggal_aduan
    reused_ticket = TicketData(
        nomor_aduan=base_ticket.nomor_aduan,
        kategori="troubleshoot",
        sub_kategori="INTERNET",
        instansi="Dinas Pendidikan",  # Different OPD
        opd_induk="Setda",
        pic_nama="Budi Santoso",
        pic_nomor="0855555555",
        keluhan="Internet sekolah mati.",
        tanggal_aduan="2026-09-20",  # Different date
        t_solve="0",
        is_submitted=1,
    )

    processor.process(reused_ticket, sync_source="monitoring")

    # Should have a SECOND NEW_TICKET event (not TICKET_CHANGED)
    cursor = db_manager.connection.execute(
        "SELECT event_type FROM ticket_events WHERE nomor_aduan = ? ORDER BY id ASC;",
        (base_ticket.nomor_aduan,),
    )
    event_types = [r[0] for r in cursor.fetchall()]
    assert event_types == ["NEW_TICKET", "NEW_TICKET"]

    # Latest snapshot should have snapshot_type 'reused_initial'
    snap = db_manager.models.get_latest_snapshot(base_ticket.nomor_aduan)
    assert snap is not None
    assert snap["snapshot_type"] == "reused_initial"

    # Ticket row should reflect the new ticket's properties
    current_row = db_manager.models.get_ticket(base_ticket.nomor_aduan)
    assert current_row["instansi"] == "Dinas Pendidikan"
    assert current_row["tanggal_aduan"] == "2026-09-20"

