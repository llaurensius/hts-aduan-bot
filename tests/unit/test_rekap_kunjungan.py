"""Unit tests for RekapService with Kunjungan category."""

from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from app.hts.rekap_client import HTSRekapClient
from app.monitoring.rekap_service import RekapService, SHIFTS, WIB


def test_parse_t_solve_datetime_string():
    service = RekapService(MagicMock())
    dt = service._parse_t_solve("2026-09-22 17:01:44")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 9
    assert dt.day == 22
    assert dt.hour == 17
    assert dt.minute == 1
    assert dt.second == 44
    assert dt.tzinfo == WIB


def test_parse_t_solve_unix_timestamp():
    service = RekapService(MagicMock())
    dt = service._parse_t_solve("1789963200")
    assert dt is not None
    assert dt.tzinfo == WIB


def test_parse_t_solve_empty_and_null():
    service = RekapService(MagicMock())
    assert service._parse_t_solve(None) is None
    assert service._parse_t_solve("") is None
    assert service._parse_t_solve("0") is None
    assert service._parse_t_solve("null") is None


def test_rekap_kunjungan_shift_pagi_selesai_and_belum_selesai():
    """Test rekap calculation for Kunjungan on Shift Pagi (07:00 - 15:00)."""
    mock_client = MagicMock(spec=HTSRekapClient)
    
    # 2 kunjungan tickets:
    # 1. KJG-01: Masuk jam 08:00, checkout jam 11:00 (Selesai di shift pagi)
    # 2. KJG-02: Masuk jam 09:00, belum checkout (Belum Selesai di shift pagi)
    mock_kunjungan_data = [
        {
            "id_kunjung": "1",
            "no_kunjung": "KJG-20260923-0001",
            "tujuan": "Perbaikan Server",
            "instansi": "Dinas Kesehatan",
            "created_at": "2026-09-23 08:00:00",
            "checkout_datetime": "2026-09-23 11:00:00",
            "visit_status": "1",
        },
        {
            "id_kunjung": "2",
            "no_kunjung": "KJG-20260923-0002",
            "tujuan": "Konsultasi Jaringan",
            "instansi": "Bappeda",
            "created_at": "2026-09-23 09:00:00",
            "checkout_datetime": None,
            "visit_status": "0",
        },
    ]

    mock_client.fetch_all_categories.return_value = {
        "aduan": [],
        "kunjungan": mock_kunjungan_data,
        "permohonan_layanan": [],
        "vps_domain": [],
        "rekomtek": [],
        "_errors": {},
    }

    service = RekapService(mock_client)
    res = service.generate(tanggal="23/09/2026", shift_key="pagi", nama_petugas="Ori")

    assert res.total_masuk == 2
    assert res.total_selesai == 1
    assert res.total_belum_selesai == 1

    # Check Kunjungan category stats
    kjg_cat = next(c for c in res.categories if c.display_name == "Kunjungan")
    assert kjg_cat.masuk == 2
    assert kjg_cat.selesai == 1
    assert kjg_cat.belum_selesai == 1

    # Only unresolved tickets should be listed
    assert len(kjg_cat.tickets) == 1
    assert kjg_cat.tickets[0].nomor == "KJG-20260923-0002"
    assert "Konsultasi Jaringan (Bappeda)" in kjg_cat.tickets[0].keluhan

    # Verify formatted text output
    assert "🔴 Kunjungan (Tiket Masuk 2 / Selesai 1 / Belum Selesai 1)" in res.rekap_text
    assert "1. KJG-20260923-0002 - Konsultasi Jaringan (Bappeda)" in res.rekap_text
    assert "KJG-20260923-0001" not in res.rekap_text  # Selesai ticket must NOT be listed


def test_rekap_kunjungan_carry_over():
    """Test that visits entered in a previous shift and still not checked out are counted."""
    mock_client = MagicMock(spec=HTSRekapClient)

    # KJG-00: Dibuat kemarin jam 14:00, checkout hari ini jam 10:00 (diselesaikan di shift pagi)
    # KJG-01: Dibuat kemarin jam 16:00, belum checkout sampai sekarang
    mock_kunjungan_data = [
        {
            "id_kunjung": "10",
            "no_kunjung": "KJG-20260922-PREV1",
            "tujuan": "Pemasangan Fiber",
            "instansi": "Disdik",
            "created_at": "2026-09-22 14:00:00",
            "checkout_datetime": "2026-09-23 10:00:00",
            "visit_status": "1",
        },
        {
            "id_kunjung": "11",
            "no_kunjung": "KJG-20260922-PREV2",
            "tujuan": "Instalasi Router",
            "instansi": "BPKAD",
            "created_at": "2026-09-22 16:00:00",
            "checkout_datetime": None,
            "visit_status": "0",
        },
    ]

    mock_client.fetch_all_categories.return_value = {
        "aduan": [],
        "kunjungan": mock_kunjungan_data,
        "permohonan_layanan": [],
        "vps_domain": [],
        "rekomtek": [],
        "_errors": {},
    }

    service = RekapService(mock_client)
    res = service.generate(tanggal="23/09/2026", shift_key="pagi", nama_petugas="Ori")

    kjg_cat = next(c for c in res.categories if c.display_name == "Kunjungan")
    assert kjg_cat.masuk == 2
    assert kjg_cat.selesai == 1
    assert kjg_cat.belum_selesai == 1
    assert len(kjg_cat.tickets) == 1
    assert kjg_cat.tickets[0].nomor == "KJG-20260922-PREV2"
