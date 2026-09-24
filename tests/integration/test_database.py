"""Integration tests for DatabaseManager and full database lifecycles."""

import sqlite3
import pytest
from app.database.db import DatabaseManager
from app.database.models import ModelLayer


@pytest.fixture
def db_manager(tmp_path):
    """Provide a real SQLite on-disk DatabaseManager."""
    db_file = tmp_path / "integration_test.db"
    manager = DatabaseManager(str(db_file))
    manager.connect()
    yield manager
    manager.close()


def test_db_wal_mode(db_manager):
    """Verify PRAGMA journal_mode is wal."""
    cursor = db_manager.connection.execute("PRAGMA journal_mode;")
    mode = cursor.fetchone()[0]
    assert mode.lower() == "wal"


def test_db_foreign_keys(db_manager):
    """Verify PRAGMA foreign_keys is enabled (1)."""
    cursor = db_manager.connection.execute("PRAGMA foreign_keys;")
    fk_enabled = cursor.fetchone()[0]
    assert fk_enabled == 1


def test_integrity_check(db_manager):
    """Verify PRAGMA integrity_check returns True for fresh database."""
    assert db_manager.integrity_check() is True


def test_foreign_key_enforcement(db_manager):
    """Verify that foreign key constraints are strictly enforced."""
    conn = db_manager.connection
    # Inserting into ticket_snapshots with non-existent ticket_id should fail
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO ticket_snapshots (ticket_id, nomor_aduan, snapshot_data, snapshot_type, created_at)
            VALUES (999999, 'INVALID', '{}', 'initial', '2026-09-17T00:00:00+00:00');
            """
        )


def test_full_ticket_lifecycle(db_manager):
    """Verify full ticket lifecycle: ticket -> snapshot -> event -> notification in a single atomic transaction."""
    model = ModelLayer(db_manager.connection)

    ticket_data = {
        "nomor_aduan": "T-INT-0001",
        "kategori": "Hardware",
        "sub_kategori": "PC Rusak",
        "instansi": "Bappeda",
        "opd_induk": None,
        "pic_nama": "Agus",
        "pic_nomor": "081999888777",
        "keluhan": "Monitor tidak menyala",
        "tanggal_aduan": "2026-09-17 09:30:00",
        "t_solve": "0",
        "is_submitted": 1,
        "status_display": "Belum Ditangani",
    }

    event_id_str = "event-lifecycle-uuid-001"
    notif_id_str = "notif-lifecycle-uuid-001"

    # Execute all 4 related operations atomically within a single transaction
    with db_manager.transaction() as conn:
        ticket_id = model.insert_ticket(
            conn=conn,
            ticket=ticket_data,
            sync_source="monitoring",
            last_hash="hash_initial_lifecycle",
        )

        snap_id = model.insert_snapshot(
            conn=conn,
            ticket_id=ticket_id,
            nomor_aduan="T-INT-0001",
            data=ticket_data,
            hash_val="hash_initial_lifecycle",
            snapshot_type="initial",
        )

        event_id = model.insert_event(
            conn=conn,
            ticket_id=ticket_id,
            nomor_aduan="T-INT-0001",
            event_type="NEW_TICKET",
            event_id=event_id_str,
            current_snapshot_id=snap_id,
        )

        notif_id = model.insert_notification(
            conn=conn,
            event_id=event_id_str,
            message_text="[NEW TICKET] T-INT-0001 Monitor tidak menyala",
            notification_id=notif_id_str,
        )

    # Verify all records persisted after transaction committed
    ticket = model.get_ticket("T-INT-0001")
    assert ticket is not None
    assert ticket["id"] == ticket_id

    snapshot = model.get_latest_snapshot("T-INT-0001")
    assert snapshot is not None
    assert snapshot["id"] == snap_id

    cursor = db_manager.connection.execute("SELECT * FROM ticket_events WHERE event_id = ?;", (event_id_str,))
    evt_row = cursor.fetchone()
    assert evt_row is not None
    assert evt_row["current_snapshot_id"] == snap_id

    pending_notifs = model.get_pending_notifications()
    assert len(pending_notifs) == 1
    assert pending_notifs[0]["notification_id"] == notif_id_str
    assert pending_notifs[0]["event_id"] == event_id_str
