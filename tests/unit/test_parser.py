"""Unit tests for HTS Data Parser and Field Mapping (Milestone M4)."""

import json
from pathlib import Path
import pytest
from app.hts.exceptions import HTSParseError
from app.hts.parser import (
    TicketData,
    normalize_opd_induk,
    normalize_str,
    normalize_t_solve,
    parse_api_response,
    parse_ticket,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def ticket_new_raw():
    return json.loads((FIXTURES_DIR / "ticket_new.json").read_text(encoding="utf-8"))


@pytest.fixture
def ticket_pending_raw():
    return json.loads((FIXTURES_DIR / "ticket_pending.json").read_text(encoding="utf-8"))


@pytest.fixture
def ticket_completed_raw():
    return json.loads((FIXTURES_DIR / "ticket_completed.json").read_text(encoding="utf-8"))


@pytest.fixture
def ticket_empty_opd_raw():
    return json.loads((FIXTURES_DIR / "ticket_empty_opd.json").read_text(encoding="utf-8"))


@pytest.fixture
def ticket_invalid_raw():
    return json.loads((FIXTURES_DIR / "ticket_invalid.json").read_text(encoding="utf-8"))


@pytest.fixture
def api_response_page1_raw():
    return json.loads((FIXTURES_DIR / "api_response_page1.json").read_text(encoding="utf-8"))


@pytest.fixture
def api_response_empty_raw():
    return json.loads((FIXTURES_DIR / "api_response_empty.json").read_text(encoding="utf-8"))


def test_parse_ticket_full(ticket_new_raw):
    """Test parsing a full valid ticket item into TicketData."""
    ticket = parse_ticket(ticket_new_raw)
    assert isinstance(ticket, TicketData)
    assert ticket.nomor_aduan == "1977-TShoot-2026-jateng-09"
    assert ticket.kategori == "troubleshoot"
    assert ticket.sub_kategori == "CORE NETWORK"
    assert ticket.instansi == "Dinas Kesehatan"
    assert ticket.opd_induk is None
    assert ticket.pic_nama == "Siti Rahayu"
    assert ticket.pic_nomor == "081234567891"
    assert ticket.keluhan == "Jaringan tidak bisa diakses."
    assert ticket.tanggal_aduan == "2026-09-17"
    assert ticket.t_solve == "0"
    assert ticket.is_submitted == 1
    assert ticket.is_completed is False
    assert ticket.status_display == "Belum Ditangani"


def test_parse_ticket_opd_induk_dash(ticket_new_raw):
    """Test that induk_opd_nama='-' normalizes to None."""
    ticket_new_raw["induk_opd_nama"] = "-"
    ticket = parse_ticket(ticket_new_raw)
    assert ticket.opd_induk is None


def test_parse_ticket_opd_induk_empty(ticket_empty_opd_raw):
    """Test that empty string or whitespace induk_opd_nama normalizes to None."""
    ticket = parse_ticket(ticket_empty_opd_raw)
    assert ticket.opd_induk is None


def test_parse_ticket_opd_induk_populated(ticket_pending_raw):
    """Test that valid induk_opd_nama is preserved."""
    ticket = parse_ticket(ticket_pending_raw)
    assert ticket.opd_induk == "Setda Provinsi Jawa Tengah"


def test_parse_ticket_t_solve_zero(ticket_new_raw):
    """Test that t_solve='0' yields is_completed=False."""
    ticket = parse_ticket(ticket_new_raw)
    assert ticket.t_solve == "0"
    assert ticket.is_completed is False


def test_parse_ticket_t_solve_nonzero(ticket_completed_raw):
    """Test that non-zero t_solve timestamp yields is_completed=True."""
    ticket = parse_ticket(ticket_completed_raw)
    assert ticket.t_solve == "1726527600"
    assert ticket.is_completed is True


def test_status_display_pending(ticket_new_raw):
    """Test status_display when t_solve='0' and is_submitted=1."""
    ticket = parse_ticket(ticket_new_raw)
    assert ticket.status_display == "Belum Ditangani"


def test_status_display_completed(ticket_completed_raw):
    """Test status_display when ticket is completed."""
    ticket = parse_ticket(ticket_completed_raw)
    assert ticket.status_display == "Sudah Ditangani"


def test_status_display_unsubmitted(ticket_empty_opd_raw):
    """Test status_display when t_solve='0' and is_submitted=0."""
    ticket = parse_ticket(ticket_empty_opd_raw)
    assert ticket.status_display == "Belum Disubmit"


def test_to_monitored_dict_keys(ticket_new_raw):
    """Test to_monitored_dict contains exact monitored fields."""
    ticket = parse_ticket(ticket_new_raw)
    monitored = ticket.to_monitored_dict()

    expected_keys = {
        "is_submitted",
        "kategori",
        "keluhan",
        "opd_induk",
        "pic_nama",
        "pic_nomor",
        "status_display",
        "sub_kategori",
        "t_solve",
        "instansi",
    }
    assert set(monitored.keys()) == expected_keys


def test_parse_missing_optional_field():
    """Test parsing a dict with only no_trouble provides proper normalized defaults."""
    item = {"no_trouble": "TEST-123"}
    ticket = parse_ticket(item)
    assert ticket.nomor_aduan == "TEST-123"
    assert ticket.kategori == ""
    assert ticket.sub_kategori == ""
    assert ticket.instansi == ""
    assert ticket.opd_induk is None
    assert ticket.pic_nama == ""
    assert ticket.pic_nomor == ""
    assert ticket.keluhan == ""
    assert ticket.tanggal_aduan == ""
    assert ticket.t_solve == "0"
    assert ticket.is_submitted == 0


def test_parse_ticket_missing_required(ticket_invalid_raw):
    """Test that missing no_trouble raises HTSParseError."""
    with pytest.raises(HTSParseError):
        parse_ticket(ticket_invalid_raw)


def test_parse_api_response_success(api_response_page1_raw):
    """Test parse_api_response returning list of TicketData and pagination."""
    tickets, pagination = parse_api_response(api_response_page1_raw)
    assert len(tickets) == 2
    assert tickets[0].nomor_aduan == "1977-TShoot-2026-jateng-09"
    assert tickets[1].nomor_aduan == "1978-TShoot-2026-jateng-09"
    assert pagination["page"] == 1
    assert pagination["total"] == 2


def test_parse_api_response_empty(api_response_empty_raw):
    """Test parse_api_response with empty list."""
    tickets, pagination = parse_api_response(api_response_empty_raw)
    assert len(tickets) == 0
    assert pagination["total"] == 0


def test_parse_csrf_token():
    """Test parse_csrf_token handles None, found token, missing token, and invalid HTML."""
    from app.hts.parser import parse_csrf_token

    assert parse_csrf_token("") is None
    assert parse_csrf_token(None) is None

    html_valid = '<form><input type="hidden" name="csrf_test_name" value="csrf12345" /></form>'
    assert parse_csrf_token(html_valid) == "csrf12345"

    html_none = '<div>No CSRF field here</div>'
    assert parse_csrf_token(html_none) is None


def test_parse_csrf_token_exception(monkeypatch):
    """Test parse_csrf_token raises HTSParseError on unexpected parser failure."""
    from app.hts.parser import parse_csrf_token
    import app.hts.parser as p

    def mock_soup(*args, **kwargs):
        raise RuntimeError("Soup crash")

    monkeypatch.setattr(p, "BeautifulSoup", mock_soup)
    with pytest.raises(HTSParseError):
        parse_csrf_token("<p>test</p>")


def test_parse_ticket_invalid_types():
    """Test parse_ticket validation against non-dict and invalid is_submitted."""
    with pytest.raises(HTSParseError):
        parse_ticket("not_a_dict")

    # non-integer is_submitted defaults to 0
    item = {"no_trouble": "TICKET-1", "is_submitted": "invalid_num"}
    ticket = parse_ticket(item)
    assert ticket.is_submitted == 0


def test_parse_api_response_invalid_structures():
    """Test parse_api_response validation against malformed payload structures."""
    with pytest.raises(HTSParseError):
        parse_api_response("not_a_dict")

    with pytest.raises(HTSParseError):
        parse_api_response({"data": "not_a_list"})

    # Non-dict pagination defaults to empty dict, invalid items are skipped
    resp = {
        "data": [
            {"no_trouble": "VALID-01"},
            {"no_trouble": ""},  # invalid, missing business key
        ],
        "pagination": "not_a_dict",
    }
    tickets, pagination = parse_api_response(resp)
    assert len(tickets) == 1
    assert tickets[0].nomor_aduan == "VALID-01"
    assert pagination == {}

