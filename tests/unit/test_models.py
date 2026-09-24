"""Unit tests for ModelLayer CRUD operations."""

import sqlite3
import pytest
from app.database.db import DatabaseManager
from app.database.models import ModelLayer


@pytest.fixture
def db_conn(tmp_path):
    """Provide a fresh SQLite test database with schema."""
    db_file = tmp_path / "test_models.db"
    db_manager = DatabaseManager(str(db_file))
    conn = db_manager.connect()
    yield conn
    db_manager.close()


@pytest.fixture
def model_layer(db_conn):
    return ModelLayer(db_conn)


@pytest.fixture
def sample_ticket_dict():
    return {
        "nomor_aduan": "T-2026-0001",
        "kategori": "Jaringan",
        "sub_kategori": "Internet Mati",
        "instansi": "Diskominfo",
        "opd_induk": "Setda",
        "pic_nama": "Budi",
        "pic_nomor": "08123456789",
        "keluhan": "Koneksi internet kantor mati total",
        "tanggal_aduan": "2026-09-17 08:00:00",
        "t_solve": "0",
        "is_submitted": 1,
        "status_display": "Belum Ditangani",
    }


def test_insert_ticket_new(model_layer, sample_ticket_dict):
    """Test inserting a new ticket creates a row in tickets table."""
    row_id = model_layer.insert_ticket(
        conn=None,
        ticket=sample_ticket_dict,
        sync_source="initial",
        last_hash="hash123",
    )
    assert row_id > 0

    ticket = model_layer.get_ticket("T-2026-0001")
    assert ticket is not None
    assert ticket["nomor_aduan"] == "T-2026-0001"
    assert ticket["instansi"] == "Diskominfo"
    assert ticket["is_completed"] == 0
    assert ticket["last_hash"] == "hash123"


def test_insert_ticket_duplicate(model_layer, sample_ticket_dict):
    """Test INSERT OR IGNORE behavior: no error and no duplicate rows."""
    row_id_1 = model_layer.insert_ticket(
        conn=None,
        ticket=sample_ticket_dict,
        sync_source="initial",
    )
    row_id_2 = model_layer.insert_ticket(
        conn=None,
        ticket=sample_ticket_dict,
        sync_source="monitoring",
    )

    assert row_id_1 == row_id_2
    assert model_layer.count_tickets() == 1


def test_get_ticket_not_found(model_layer):
    """Test querying non-existent ticket returns None."""
    ticket = model_layer.get_ticket("NON-EXISTENT")
    assert ticket is None


def test_update_last_seen(model_layer, sample_ticket_dict):
    """Test updating last_seen timestamp."""
    model_layer.insert_ticket(conn=None, ticket=sample_ticket_dict, sync_source="initial")
    ticket_before = model_layer.get_ticket("T-2026-0001")

    new_time = "2026-09-17T12:00:00+00:00"
    model_layer.update_last_seen("T-2026-0001", last_seen=new_time)

    ticket_after = model_layer.get_ticket("T-2026-0001")
    assert ticket_after["last_seen"] == new_time
    assert ticket_after["last_seen"] != ticket_before["last_seen"]


def test_insert_snapshot_and_get_latest(model_layer, sample_ticket_dict):
    """Test snapshot creation and retrieving the latest snapshot."""
    ticket_id = model_layer.insert_ticket(conn=None, ticket=sample_ticket_dict, sync_source="initial")

    snap1_id = model_layer.insert_snapshot(
        conn=None,
        ticket_id=ticket_id,
        nomor_aduan="T-2026-0001",
        data={"keluhan": "Aduan awal", "status_display": "Belum Ditangani"},
        hash_val="hash_snap_1",
        snapshot_type="initial",
    )
    assert snap1_id > 0

    snap2_id = model_layer.insert_snapshot(
        conn=None,
        ticket_id=ticket_id,
        nomor_aduan="T-2026-0001",
        data={"keluhan": "Aduan diupdate", "status_display": "Sudah Ditangani"},
        hash_val="hash_snap_2",
        snapshot_type="update",
    )
    assert snap2_id > snap1_id

    latest = model_layer.get_latest_snapshot("T-2026-0001")
    assert latest is not None
    assert latest["id"] == snap2_id
    assert latest["snapshot_hash"] == "hash_snap_2"
    assert latest["snapshot_type"] == "update"


def test_insert_event(model_layer, sample_ticket_dict):
    """Test inserting a ticket event."""
    ticket_id = model_layer.insert_ticket(conn=None, ticket=sample_ticket_dict, sync_source="initial")
    event_rowid = model_layer.insert_event(
        conn=None,
        ticket_id=ticket_id,
        nomor_aduan="T-2026-0001",
        event_type="NEW_TICKET",
        changed_fields={"status_display": {"old": None, "new": "Belum Ditangani"}},
    )
    assert event_rowid > 0


def test_notification_lifecycle(model_layer):
    """Test notification lifecycle from PENDING to SENT and status counts."""
    notif_id_str = "notif-uuid-1234"
    model_layer.insert_notification(
        conn=None,
        message_text="Ada aduan baru T-2026-0001",
        notification_id=notif_id_str,
    )

    # Check pending
    pending = model_layer.get_pending_notifications()
    assert len(pending) == 1
    assert pending[0]["notification_id"] == notif_id_str
    assert pending[0]["status"] == "PENDING"
    assert model_layer.count_notifications_by_status("PENDING") == 1

    # Mark as sent
    model_layer.mark_notification_sent(notif_id_str, telegram_msg_id="998877")
    assert model_layer.count_notifications_by_status("PENDING") == 0
    assert model_layer.count_notifications_by_status("SENT") == 1


def test_transaction_rollback(tmp_path):
    """Test that exception raised inside transaction() triggers rollback."""
    db_file = tmp_path / "test_rollback.db"
    db_manager = DatabaseManager(str(db_file))
    db_manager.connect()
    model = ModelLayer(db_manager.connection)

    with pytest.raises(RuntimeError):
        with db_manager.transaction():
            model.insert_system_event("APP_START", description="First event")
            # Force an error
            raise RuntimeError("Deliberate failure during transaction")

    # Verify that the insert was rolled back
    cursor = db_manager.connection.execute("SELECT COUNT(*) FROM system_events;")
    assert cursor.fetchone()[0] == 0

    db_manager.close()


def test_update_ticket_with_dict(model_layer):
    """Test update_ticket with a dictionary rather than a TicketData instance."""
    # First insert ticket
    model_layer.insert_ticket(
        None,
        {
            "nomor_aduan": "T-DICT-01",
            "kategori": "Network",
            "sub_kategori": "LAN",
            "instansi": "Dinas Kominfo",
            "opd_induk": None,
            "pic_nama": "John",
            "pic_nomor": "081234",
            "keluhan": "Slow internet",
            "tanggal_aduan": "2026-09-17",
            "t_solve": "0",
            "is_submitted": 1,
            "status_display": "Belum Ditangani",
            "is_completed": 0,
        },
        sync_source="monitoring",
        last_hash="hash1",
    )

    # Update using dictionary
    update_dict = {
        "nomor_aduan": "T-DICT-01",
        "kategori": "Network",
        "sub_kategori": "WIFI",
        "instansi": "Dinas Kominfo",
        "opd_induk": "Setda",
        "pic_nama": "John Doe",
        "pic_nomor": "08123499",
        "keluhan": "Resolved wifi issue",
        "tanggal_aduan": "2026-09-17",
        "t_solve": "1726527999",
        "is_submitted": 1,
        "status_display": "Sudah Ditangani",
    }
    model_layer.update_ticket(None, update_dict, new_hash="hash2")

    updated = model_layer.get_ticket("T-DICT-01")
    assert updated is not None
    assert updated["sub_kategori"] == "WIFI"
    assert updated["opd_induk"] == "Setda"
    assert updated["pic_nama"] == "John Doe"
    assert updated["t_solve"] == "1726527999"
    assert updated["is_completed"] == 1
    assert updated["status_display"] == "Sudah Ditangani"


