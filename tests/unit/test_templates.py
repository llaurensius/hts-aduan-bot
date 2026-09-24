"""Unit tests for notification templates and message formatters (Milestone M9)."""

import pytest
from app.hts.parser import TicketData
from app.notifications.templates import (
    MAX_TELEGRAM_MESSAGE_LENGTH,
    _format_pic,
    _truncate,
    format_captcha_alert,
    format_changed_ticket,
    format_completed_ticket,
    format_hts_down,
    format_hts_recovered,
    format_new_ticket,
    format_session_expired,
)


@pytest.fixture
def sample_ticket():
    return TicketData(
        nomor_aduan="1977-TShoot-2026-jateng-09",
        kategori="troubleshoot",
        sub_kategori="CORE NETWORK",
        instansi="Dinas Kesehatan",
        opd_induk="Setda",
        pic_nama="Siti Rahayu",
        pic_nomor="081234567891",
        keluhan="Jaringan tidak bisa diakses.",
        tanggal_aduan="2026-09-17",
        t_solve="0",
        is_submitted=1,
    )


def test_format_new_ticket_full(sample_ticket):
    """Test format_new_ticket produces exact layout with all fields present."""
    msg = format_new_ticket(sample_ticket)
    assert "Menginformasikan Tiket Masuk:" in msg
    assert "Nomor Aduan:\n1977-TShoot-2026-jateng-09" in msg
    assert "Kategori / Sub Kategori\ntroubleshoot / CORE NETWORK" in msg
    assert "Instansi:\nDinas Kesehatan" in msg
    assert "OPD Induk:\nSetda" in msg
    assert "PIC:\nSiti Rahayu (081234567891)" in msg
    assert "Keluhan:\nJaringan tidak bisa diakses." in msg
    assert "Status:\nBelum Ditangani" in msg


def test_format_new_ticket_no_opd(sample_ticket):
    """Test format_new_ticket leaves OPD Induk line blank when opd_induk is None."""
    sample_ticket.opd_induk = None
    msg = format_new_ticket(sample_ticket)
    assert "OPD Induk:\n\nPIC:" in msg or "OPD Induk:\n\n" in msg


def test_format_new_ticket_no_pic_nomor(sample_ticket):
    """Test format_new_ticket prints name only when pic_nomor is empty."""
    sample_ticket.pic_nomor = ""
    msg = format_new_ticket(sample_ticket)
    assert "PIC:\nSiti Rahayu\n" in msg


def test_format_changed_ticket(sample_ticket):
    """Test format_changed_ticket produces expected header, penjelasan, and footer."""
    msg = format_changed_ticket(sample_ticket, {"keluhan": {"old": "A", "new": "B"}})
    assert "Menginformasikan Perubahan Aduan:" in msg
    assert "Nomor Aduan:\n1977-TShoot-2026-jateng-09" in msg
    assert "Penjelasan Penanganan:\n-" in msg
    assert "Terima kasih atas perhatian dan kerjasamanya." in msg
    assert "Laurensius Liquori" in msg
    assert "Helpdesk DC" in msg


def test_format_completed_ticket(sample_ticket):
    """Test format_completed_ticket produces expected header, penjelasan, and footer."""
    sample_ticket.t_solve = "1726527600"
    sample_ticket.penjelasan = "Sudah dilakukan perbaikan kabel fiber."
    msg = format_completed_ticket(sample_ticket, petugas_nama="Budi Santoso", petugas_role="Teknisi Jaringan")
    assert "Menginformasikan Tiket Selesai:" in msg
    assert "Status:\nSudah Ditangani" in msg
    assert "Penjelasan Penanganan:\nSudah dilakukan perbaikan kabel fiber." in msg
    assert "Terima kasih atas perhatian dan kerjasamanya." in msg
    assert "Budi Santoso" in msg
    assert "Teknisi Jaringan" in msg


def test_keluhan_truncation():
    """Test that keluhan longer than 1000 characters is truncated with '...'."""
    long_text = "A" * 1500
    truncated = _truncate(long_text, max_len=1000)
    assert len(truncated) == 1000
    assert truncated.endswith("...")


def test_format_captcha_alert():
    """Test format_captcha_alert includes timestamp and base URL."""
    msg = format_captcha_alert("2026-09-17T03:00:00+00:00", "https://hts.diskomdigi.jatengprov.go.id")
    assert "LOGIN MANUAL DIPERLUKAN" in msg
    assert "2026-09-17T03:00:00+00:00" in msg
    assert "https://hts.diskomdigi.jatengprov.go.id" in msg


def test_format_session_expired():
    """Test format_session_expired includes timestamp and base URL."""
    msg = format_session_expired("2026-09-17T03:00:00+00:00", "https://hts.diskomdigi.jatengprov.go.id")
    assert "SESSION EXPIRED" in msg
    assert "2026-09-17T03:00:00+00:00" in msg


def test_format_hts_down_and_recovered():
    """Test format_hts_down and format_hts_recovered."""
    down_msg = format_hts_down("2026-09-17T03:00:00+00:00")
    assert "HTS CONNECTION ERROR" in down_msg

    rec_msg = format_hts_recovered("2026-09-17T03:15:00+00:00")
    assert "HTS CONNECTION RECOVERED" in rec_msg


def test_message_within_telegram_limit(sample_ticket):
    """Test generated message fits within Telegram's 4096 character limit."""
    sample_ticket.keluhan = "X" * 1000
    msg = format_new_ticket(sample_ticket)
    assert len(msg) <= MAX_TELEGRAM_MESSAGE_LENGTH
