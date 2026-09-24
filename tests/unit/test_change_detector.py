"""Unit tests for Change Detector (Milestone M7)."""

import json
import pytest
from app.hts.parser import TicketData
from app.monitoring.change_detector import (
    ChangeResult,
    compute_hash,
    detect_changes,
)


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


def test_compute_hash_deterministic(base_ticket):
    """Test compute_hash produces identical SHA256 string for identical ticket data."""
    hash1 = compute_hash(base_ticket)
    hash2 = compute_hash(base_ticket)
    assert hash1 == hash2
    assert len(hash1) == 64


def test_compute_hash_different(base_ticket):
    """Test changing any monitored field produces a different hash."""
    hash1 = compute_hash(base_ticket)

    modified = TicketData(
        nomor_aduan=base_ticket.nomor_aduan,
        kategori=base_ticket.kategori,
        sub_kategori=base_ticket.sub_kategori,
        instansi=base_ticket.instansi,
        opd_induk=base_ticket.opd_induk,
        pic_nama=base_ticket.pic_nama,
        pic_nomor=base_ticket.pic_nomor,
        keluhan="Jaringan mati total di lantai 2.",  # Modified
        tanggal_aduan=base_ticket.tanggal_aduan,
        t_solve=base_ticket.t_solve,
        is_submitted=base_ticket.is_submitted,
    )
    hash2 = compute_hash(modified)
    assert hash1 != hash2


def test_detect_no_change(base_ticket):
    """Test identical data yields is_changed=False and empty changed_fields."""
    snapshot_json = json.dumps(base_ticket.to_monitored_dict())
    result = detect_changes(base_ticket, snapshot_json, was_completed=False)

    assert isinstance(result, ChangeResult)
    assert result.is_new is False
    assert result.is_changed is False
    assert result.is_completed is False
    assert result.changed_fields == {}
    assert result.previous_data is not None


def test_detect_keluhan_changed(base_ticket):
    """Test modifying keluhan is detected with proper old/new mapping."""
    snapshot_json = json.dumps(base_ticket.to_monitored_dict())

    updated_ticket = TicketData(
        nomor_aduan=base_ticket.nomor_aduan,
        kategori=base_ticket.kategori,
        sub_kategori=base_ticket.sub_kategori,
        instansi=base_ticket.instansi,
        opd_induk=base_ticket.opd_induk,
        pic_nama=base_ticket.pic_nama,
        pic_nomor=base_ticket.pic_nomor,
        keluhan="Kabel FO putus di depan kantor.",
        tanggal_aduan=base_ticket.tanggal_aduan,
        t_solve=base_ticket.t_solve,
        is_submitted=base_ticket.is_submitted,
    )

    result = detect_changes(updated_ticket, snapshot_json, was_completed=False)

    assert result.is_changed is True
    assert "keluhan" in result.changed_fields
    assert result.changed_fields["keluhan"]["old"] == "Jaringan tidak bisa diakses."
    assert result.changed_fields["keluhan"]["new"] == "Kabel FO putus di depan kantor."


def test_detect_multiple_fields(base_ticket):
    """Test modifying multiple fields yields all entries in changed_fields."""
    snapshot_json = json.dumps(base_ticket.to_monitored_dict())

    updated_ticket = TicketData(
        nomor_aduan=base_ticket.nomor_aduan,
        kategori="APLIKASI",
        sub_kategori=base_ticket.sub_kategori,
        instansi=base_ticket.instansi,
        opd_induk=base_ticket.opd_induk,
        pic_nama="Budi Santoso",
        pic_nomor=base_ticket.pic_nomor,
        keluhan=base_ticket.keluhan,
        tanggal_aduan=base_ticket.tanggal_aduan,
        t_solve=base_ticket.t_solve,
        is_submitted=base_ticket.is_submitted,
    )

    result = detect_changes(updated_ticket, snapshot_json, was_completed=False)

    assert result.is_changed is True
    assert len(result.changed_fields) == 2
    assert "kategori" in result.changed_fields
    assert "pic_nama" in result.changed_fields


def test_detect_completed(base_ticket):
    """Test transition from t_solve='0' to timestamp sets is_completed=True."""
    snapshot_json = json.dumps(base_ticket.to_monitored_dict())

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

    result = detect_changes(completed_ticket, snapshot_json, was_completed=False)

    assert result.is_changed is True
    assert result.is_completed is True
    assert "t_solve" in result.changed_fields
    assert "status_display" in result.changed_fields
    assert result.changed_fields["status_display"]["new"] == "Sudah Ditangani"


def test_detect_already_completed(base_ticket):
    """Test ticket that was already completed does NOT trigger is_completed=True again."""
    completed_ticket = TicketData(
        nomor_aduan=base_ticket.nomor_aduan,
        kategori=base_ticket.kategori,
        sub_kategori=base_ticket.sub_kategori,
        instansi=base_ticket.instansi,
        opd_induk=base_ticket.opd_induk,
        pic_nama=base_ticket.pic_nama,
        pic_nomor=base_ticket.pic_nomor,
        keluhan="Catatan tambahan setelah selesai.",
        tanggal_aduan=base_ticket.tanggal_aduan,
        t_solve="1726527600",
        is_submitted=base_ticket.is_submitted,
    )

    snapshot_json = json.dumps(completed_ticket.to_monitored_dict())

    # was_completed is True
    result = detect_changes(completed_ticket, snapshot_json, was_completed=True)

    assert result.is_completed is False


def test_detect_pic_changed(base_ticket):
    """Test pic_nomor change detection."""
    snapshot_json = json.dumps(base_ticket.to_monitored_dict())

    updated = TicketData(
        nomor_aduan=base_ticket.nomor_aduan,
        kategori=base_ticket.kategori,
        sub_kategori=base_ticket.sub_kategori,
        instansi=base_ticket.instansi,
        opd_induk=base_ticket.opd_induk,
        pic_nama=base_ticket.pic_nama,
        pic_nomor="08999999999",
        keluhan=base_ticket.keluhan,
        tanggal_aduan=base_ticket.tanggal_aduan,
        t_solve=base_ticket.t_solve,
        is_submitted=base_ticket.is_submitted,
    )

    result = detect_changes(updated, snapshot_json, was_completed=False)
    assert result.is_changed is True
    assert "pic_nomor" in result.changed_fields


def test_detect_all_fields_unchanged(base_ticket):
    """Test exact dictionary match returns is_changed=False."""
    dict_copy = base_ticket.to_monitored_dict()
    result = detect_changes(base_ticket, json.dumps(dict_copy), was_completed=False)
    assert result.is_changed is False
    assert len(result.changed_fields) == 0


def test_is_ticket_reused(base_ticket):
    """Test is_ticket_reused detects replaced tickets by date, agency, or resurrection."""
    from app.monitoring.change_detector import is_ticket_reused

    existing_row = {
        "tanggal_aduan": "2026-09-17",
        "instansi": "Dinas Kesehatan",
        "keluhan": "Jaringan lambat",
        "is_completed": 0,
    }

    # Same date & instansi -> not reused
    assert is_ticket_reused(base_ticket, existing_row) is False

    # Different tanggal_aduan -> reused
    t_diff_date = TicketData(
        nomor_aduan=base_ticket.nomor_aduan,
        kategori=base_ticket.kategori,
        sub_kategori=base_ticket.sub_kategori,
        instansi="Dinas Kesehatan",
        opd_induk=None,
        pic_nama="Siti",
        pic_nomor="08123",
        keluhan="Jaringan lambat",
        tanggal_aduan="2026-09-20",  # Different date
        t_solve="0",
        is_submitted=1,
    )
    assert is_ticket_reused(t_diff_date, existing_row) is True

    # Marked as deleted in DB -> reused
    deleted_row = {
        "tanggal_aduan": "2026-09-17",
        "instansi": "Dinas Kesehatan",
        "keluhan": "Jaringan lambat",
        "is_completed": 0,
        "is_deleted": 1,
    }
    assert is_ticket_reused(base_ticket, deleted_row) is True


