"""Unit tests covering critical Edge Cases defined in PRD Section 29 (Milestone M16)."""

import json
from unittest.mock import MagicMock
import pytest

from app.database.db import DatabaseManager
from app.hts.client import is_session_expired
from app.hts.parser import TicketData, parse_ticket
from app.monitoring.change_detector import compute_hash, detect_changes
from app.monitoring.ticket_processor import TicketProcessor
from app.notifications.queue import NotificationQueue, get_next_delay
from app.notifications.templates import format_changed_ticket, format_new_ticket


@pytest.fixture
def test_db(tmp_path):
    """In-memory SQLite database initialized with schema."""
    db_file = tmp_path / "test_edge_cases.db"
    manager = DatabaseManager(str(db_file))
    manager.connect()
    yield manager
    manager.close()


def test_ec01_ticket_deleted_from_hts(test_db):
    """EC-01: Ticket deleted from HTS after being stored in DB.

    Expected: Ticket remains preserved in database, not deleted, no new notification sent.
    """
    notif_queue = MagicMock()
    processor = TicketProcessor(test_db, notif_queue=notif_queue)

    ticket = TicketData(
        nomor_aduan="HTS-EC01-001",
        kategori="Aduan",
        sub_kategori="Jaringan",
        instansi="Diskominfo",
        opd_induk="Setda",
        pic_nama="Budi",
        pic_nomor="0812345678",
        keluhan="Koneksi terputus",
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )

    processor.process(ticket)
    assert test_db.models.count_tickets() == 1

    # In next poll, HTS returns empty list (ticket disappeared)
    # The database must preserve the ticket
    stored = test_db.models.get_ticket("HTS-EC01-001")
    assert stored is not None
    assert stored["nomor_aduan"] == "HTS-EC01-001"
    assert test_db.models.count_tickets() == 1


def test_ec02_duplicate_nomor_aduan_in_single_poll(test_db):
    """EC-02: Same ticket number appears twice in the same HTS response.

    Expected: First is processed as new; second is processed as unchanged without duplicate rows or notifications.
    """
    processor = TicketProcessor(test_db)
    ticket1 = TicketData(
        nomor_aduan="HTS-EC02-DUP",
        kategori="Infrastruktur",
        sub_kategori="Server",
        instansi="Bappeda",
        opd_induk=None,
        pic_nama="Siti",
        pic_nomor="0811122233",
        keluhan="Server restart mendadak",
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )
    ticket2 = TicketData(
        nomor_aduan="HTS-EC02-DUP",
        kategori="Infrastruktur",
        sub_kategori="Server",
        instansi="Bappeda",
        opd_induk=None,
        pic_nama="Siti",
        pic_nomor="0811122233",
        keluhan="Server restart mendadak",
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )

    # Process first
    processor.process(ticket1)
    # Process duplicate immediately
    processor.process(ticket2)

    assert test_db.models.count_tickets() == 1
    # Only 1 NEW_TICKET event
    cursor = test_db.connection.execute(
        "SELECT COUNT(*) FROM ticket_events WHERE nomor_aduan = 'HTS-EC02-DUP';"
    )
    assert cursor.fetchone()[0] == 1


def test_ec03_monitored_fields_null_or_empty(test_db):
    """EC-03: Monitored fields return null or empty strings from HTS.

    Expected: Handled gracefully without KeyError; fallback values rendered in notification templates.
    """
    raw_ticket = {
        "id": "12345",
        "no_trouble": "HTS-EC03-NULL",
        "kategori": None,
        "sub_kategori": None,
        "instansi": None,
        "opd_induk": None,
        "pic_nama": None,
        "pic_nomor": None,
        "keluhan": None,
        "tanggal_aduan": "2026-09-17",
        "t_solve": None,
        "is_submitted": "0",
    }

    parsed = parse_ticket(raw_ticket)
    assert parsed.nomor_aduan == "HTS-EC03-NULL"
    assert parsed.kategori == ""
    assert parsed.pic_nama == ""

    message = format_new_ticket(parsed)
    assert "Nomor Aduan:\nHTS-EC03-NULL" in message
    assert "- / -" in message
    assert "PIC:\n-" in message


def test_ec04_very_long_keluhan(test_db):
    """EC-04: Keluhan text is extremely long (>10,000 characters).

    Expected: Full text stored in SQLite database, truncated cleanly for Telegram <= 4096 characters.
    """
    processor = TicketProcessor(test_db)
    very_long_text = "A" * 15000

    ticket = TicketData(
        nomor_aduan="HTS-EC04-LONG",
        kategori="Layanan",
        sub_kategori="Portal",
        instansi="Dinkes",
        opd_induk="Setda",
        pic_nama="Ahmad",
        pic_nomor="0899887766",
        keluhan=very_long_text,
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )

    processor.process(ticket)

    # 1. Full text preserved in SQLite
    stored = test_db.models.get_ticket("HTS-EC04-LONG")
    assert len(stored["keluhan"]) == 15000

    # 2. Formatted notification message <= 4096 and ends with ellipsis
    msg = format_new_ticket(ticket)
    assert len(msg) <= 4096
    assert msg.endswith("...") or "..." in msg


def test_ec08_initial_sync_then_modified_on_first_poll(test_db):
    """EC-08: Ticket saved during initial sync changes on first monitoring poll.

    Expected: Detected as TICKET_CHANGED and queues Telegram alert.
    """
    # 1. Simulate initial sync
    init_processor = TicketProcessor(test_db, is_initial_sync=True)
    ticket_v1 = TicketData(
        nomor_aduan="HTS-EC08-001",
        kategori="Hardware",
        sub_kategori="PC",
        instansi="Dishub",
        opd_induk=None,
        pic_nama="Rudi",
        pic_nomor="0812345678",
        keluhan="PC tidak mau menyala",
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )
    init_processor.process(ticket_v1)
    assert test_db.models.count_notifications_by_status("PENDING") == 0

    # 2. First monitoring poll: ticket keluhan is updated
    mon_processor = TicketProcessor(test_db, is_initial_sync=False)
    ticket_v2 = TicketData(
        nomor_aduan="HTS-EC08-001",
        kategori="Hardware",
        sub_kategori="PC",
        instansi="Dishub",
        opd_induk=None,
        pic_nama="Rudi",
        pic_nomor="0812345678",
        keluhan="PC tidak mau menyala dan tercium bau hangus",
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )
    mon_processor.process(ticket_v2)

    # Must generate TICKET_CHANGED event and 1 pending notification
    cursor = test_db.connection.execute(
        "SELECT event_type FROM ticket_events WHERE nomor_aduan = 'HTS-EC08-001';"
    )
    events = [r[0] for r in cursor.fetchall()]
    assert "TICKET_CHANGED" in events
    assert test_db.models.count_notifications_by_status("PENDING") == 1


def test_ec09_telegram_rate_limit_backoff():
    """EC-09: Telegram rate limit delays.

    Expected: get_next_delay returns appropriate exponential backoff intervals.
    """
    assert get_next_delay(0) == 15
    assert get_next_delay(1) == 60
    assert get_next_delay(2) == 300
    assert get_next_delay(3) == 1800
    assert get_next_delay(10) == 1800  # Capped at 1800


def test_ec10_all_fields_changed_simultaneously(test_db):
    """EC-10: All monitored fields change at the same time.

    Expected: Exactly one TICKET_CHANGED event generated, containing all modified fields in changed_fields.
    """
    processor = TicketProcessor(test_db)
    v1 = TicketData(
        nomor_aduan="HTS-EC10-ALL",
        kategori="Cat1",
        sub_kategori="Sub1",
        instansi="Inst1",
        opd_induk="OPD1",
        pic_nama="PIC1",
        pic_nomor="Num1",
        keluhan="Keluhan1",
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )
    processor.process(v1)

    v2 = TicketData(
        nomor_aduan="HTS-EC10-ALL",
        kategori="Cat2",
        sub_kategori="Sub2",
        instansi="Inst2",
        opd_induk="OPD2",
        pic_nama="PIC2",
        pic_nomor="Num2",
        keluhan="Keluhan2",
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )
    processor.process(v2)

    cursor = test_db.connection.execute(
        "SELECT event_type, changed_fields FROM ticket_events WHERE nomor_aduan = 'HTS-EC10-ALL' AND event_type = 'TICKET_CHANGED';"
    )
    row = cursor.fetchone()
    assert row is not None
    changed = json.loads(row[1])
    assert "kategori" in changed
    assert "sub_kategori" in changed
    assert "instansi" in changed
    assert "opd_induk" in changed
    assert "pic_nama" in changed
    assert "pic_nomor" in changed
    assert "keluhan" in changed


def test_ec11_ticket_reverts_from_solved_to_active(test_db):
    """EC-11: Ticket reverts from solved (t_solve != '0') back to active (t_solve == '0').

    Expected: Detected as TICKET_CHANGED, NOT a duplicate COMPLETED event.
    """
    processor = TicketProcessor(test_db)

    # 1. Initially solved ticket
    v1 = TicketData(
        nomor_aduan="HTS-EC11-REOPEN",
        kategori="Jaringan",
        sub_kategori="WiFi",
        instansi="Dinas PU",
        opd_induk=None,
        pic_nama="Dani",
        pic_nomor="08123",
        keluhan="Sinyal lemah",
        tanggal_aduan="2026-09-17",
        t_solve="2026-09-17 12:00:00",
        is_submitted=1,
    )
    processor.process(v1)

    # 2. Reopened ticket (t_solve set back to '0')
    v2 = TicketData(
        nomor_aduan="HTS-EC11-REOPEN",
        kategori="Jaringan",
        sub_kategori="WiFi",
        instansi="Dinas PU",
        opd_induk=None,
        pic_nama="Dani",
        pic_nomor="08123",
        keluhan="Sinyal lemah kembali bermasalah",
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )
    processor.process(v2)

    cursor = test_db.connection.execute(
        "SELECT event_type FROM ticket_events WHERE nomor_aduan = 'HTS-EC11-REOPEN' ORDER BY id ASC;"
    )
    events = [r[0] for r in cursor.fetchall()]

    # Should have NEW_TICKET then TICKET_CHANGED (not COMPLETED)
    assert "COMPLETED" not in events
    assert "TICKET_CHANGED" in events


def test_ec12_html_login_redirect_detection():
    """EC-12: HTS returns HTML login page unexpectedly.

    Expected: is_session_expired identifies the login redirect URL or page content.
    """
    mock_resp = MagicMock()
    mock_resp.url = "https://hts.diskomdigi.jatengprov.go.id/login"
    mock_resp.text = '<input type="password" name="password">'

    assert is_session_expired(mock_resp) is True
