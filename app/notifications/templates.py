"""Telegram notification templates and text formatters."""

import os
from typing import Any, Dict, Optional
from app.hts.parser import TicketData

# Telegram maximum message length limit
MAX_TELEGRAM_MESSAGE_LENGTH = 4096

TEMPLATE_NEW_TICKET = """\
Menginformasikan Tiket Masuk:

Nomor Aduan:
{nomor_aduan}

Kategori / Sub Kategori
{kategori} / {sub_kategori}

Instansi:
{instansi}

OPD Induk:
{opd_induk}

PIC:
{pic_line}

Keluhan:
{keluhan}

Status:
{status_display}\
"""

TEMPLATE_CHANGED = """\
Menginformasikan Perubahan Aduan:

Nomor Aduan:
{nomor_aduan}

Kategori / Sub Kategori
{kategori} / {sub_kategori}

Instansi:
{instansi}

OPD Induk:
{opd_induk}

PIC:
{pic_line}

Keluhan:
{keluhan}

Penjelasan Penanganan:
{penjelasan}

Status:
{status_display}

{footer}\
"""

TEMPLATE_COMPLETED = """\
Menginformasikan Tiket Selesai:

Nomor Aduan:
{nomor_aduan}

Kategori / Sub Kategori
{kategori} / {sub_kategori}

Instansi:
{instansi}

OPD Induk:
{opd_induk}

PIC:
{pic_line}

Keluhan:
{keluhan}

Penjelasan Penanganan:
{penjelasan}

Status:
{status_display}

{footer}\
"""

TEMPLATE_HTS_DOWN = """\
⚠️ HTS CONNECTION ERROR

HTS tidak dapat diakses sejak:
{timestamp}

Monitoring sementara dihentikan.
Retry otomatis akan dilakukan.\
"""

TEMPLATE_HTS_RECOVERED = """\
✅ HTS CONNECTION RECOVERED

Koneksi HTS kembali normal.
{timestamp}\
"""

TEMPLATE_CAPTCHA = """\
🔐 LOGIN MANUAL DIPERLUKAN

HTS memerlukan login manual (CAPTCHA).
Timestamp: {timestamp}

Silakan login secara manual ke:
{hts_base_url}

Monitoring akan dilanjutkan secara otomatis setelah sesi valid.\
"""

TEMPLATE_SESSION_EXPIRED = """\
⚠️ SESSION EXPIRED

Sesi HTS telah berakhir.
Timestamp: {timestamp}

Diperlukan login manual ke:
{hts_base_url}\
"""


def _truncate(text: str, max_len: int = 1000) -> str:
    """Truncate long text to avoid exceeding message length boundaries."""
    if not text:
        return ""
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _format_pic(pic_nama: Optional[str], pic_nomor: Optional[str]) -> str:
    """Format PIC line. If pic_nomor is provided, formats as 'Name (Number)', else 'Name'."""
    nama = (pic_nama or "").strip()
    nomor = (pic_nomor or "").strip()
    if nama and nomor:
        return f"{nama} ({nomor})"
    if nama:
        return nama
    if nomor:
        return f"({nomor})"
    return "-"


def _format_opd_induk(opd_induk: Optional[str]) -> str:
    """Format OPD induk string. Blank string if None or empty."""
    return (opd_induk or "").strip()


def format_new_ticket(ticket: TicketData) -> str:
    """Format notification for a newly entered ticket."""
    return TEMPLATE_NEW_TICKET.format(
        nomor_aduan=ticket.nomor_aduan or "-",
        kategori=ticket.kategori or "-",
        sub_kategori=ticket.sub_kategori or "-",
        instansi=ticket.instansi or "-",
        opd_induk=_format_opd_induk(ticket.opd_induk),
        pic_line=_format_pic(ticket.pic_nama, ticket.pic_nomor),
        keluhan=_truncate(ticket.keluhan or "-"),
        status_display=ticket.status_display,
    )


def _format_penjelasan(penjelasan: Optional[str]) -> str:
    """Format penjelasan penanganan. Falls back to '-' if empty or whitespace."""
    val = (penjelasan or "").strip()
    return _truncate(val) if val and val != "-" else "-"


def _format_footer(
    petugas_nama: Optional[str] = None,
    petugas_role: Optional[str] = None,
) -> str:
    """Format the closing footer."""
    nama = (petugas_nama if petugas_nama is not None else os.getenv("PETUGAS_NAMA", "Laurensius Liquori")).strip()
    role = (petugas_role if petugas_role is not None else os.getenv("PETUGAS_ROLE", "Helpdesk DC")).strip()

    lines = ["Terima kasih atas perhatian dan kerjasamanya."]
    if nama:
        lines.append(nama)
    if role:
        lines.append(role)
    return "\n".join(lines)


def format_changed_ticket(
    ticket: TicketData,
    changed_fields: Optional[Dict[str, Any]] = None,
    petugas_nama: Optional[str] = None,
    petugas_role: Optional[str] = None,
) -> str:
    """Format notification for a ticket that has been modified."""
    return TEMPLATE_CHANGED.format(
        nomor_aduan=ticket.nomor_aduan or "-",
        kategori=ticket.kategori or "-",
        sub_kategori=ticket.sub_kategori or "-",
        instansi=ticket.instansi or "-",
        opd_induk=_format_opd_induk(ticket.opd_induk),
        pic_line=_format_pic(ticket.pic_nama, ticket.pic_nomor),
        keluhan=_truncate(ticket.keluhan or "-"),
        penjelasan=_format_penjelasan(ticket.penjelasan),
        status_display=ticket.status_display,
        footer=_format_footer(petugas_nama, petugas_role),
    )


def format_completed_ticket(
    ticket: TicketData,
    petugas_nama: Optional[str] = None,
    petugas_role: Optional[str] = None,
) -> str:
    """Format notification for a ticket that is completed/resolved."""
    return TEMPLATE_COMPLETED.format(
        nomor_aduan=ticket.nomor_aduan or "-",
        kategori=ticket.kategori or "-",
        sub_kategori=ticket.sub_kategori or "-",
        instansi=ticket.instansi or "-",
        opd_induk=_format_opd_induk(ticket.opd_induk),
        pic_line=_format_pic(ticket.pic_nama, ticket.pic_nomor),
        keluhan=_truncate(ticket.keluhan or "-"),
        penjelasan=_format_penjelasan(ticket.penjelasan),
        status_display=ticket.status_display,
        footer=_format_footer(petugas_nama, petugas_role),
    )


def format_hts_down(timestamp: str) -> str:
    """Format alert notification when HTS connection is down."""
    return TEMPLATE_HTS_DOWN.format(timestamp=timestamp)


def format_hts_recovered(timestamp: str) -> str:
    """Format alert notification when HTS connection has recovered."""
    return TEMPLATE_HTS_RECOVERED.format(timestamp=timestamp)


def format_captcha_alert(timestamp: str, base_url: str) -> str:
    """Format alert notification when manual login / CAPTCHA is required."""
    return TEMPLATE_CAPTCHA.format(timestamp=timestamp, hts_base_url=base_url)


def format_session_expired(timestamp: str, base_url: str) -> str:
    """Format alert notification when an active HTS session expires."""
    return TEMPLATE_SESSION_EXPIRED.format(timestamp=timestamp, hts_base_url=base_url)


# ─────────────────────────────────────────────────────────────────────────────
# Rekap Laporan Template
# ─────────────────────────────────────────────────────────────────────────────

def _format_category_block(cat) -> str:
    """Format satu blok kategori dalam rekap."""
    emoji = "🔴" if cat.belum_selesai > 0 else "🟢"
    header = (
        f"{emoji} {cat.display_name} "
        f"(Tiket Masuk {cat.masuk} / Selesai {cat.selesai} / Belum Selesai {cat.belum_selesai})"
    )
    if not cat.tickets:
        return header
    lines = [header]
    for i, t in enumerate(cat.tickets, start=1):
        lines.append(f"{i}. {t.nomor} - {t.keluhan}")
    return "\n".join(lines)


def format_rekap(result) -> str:
    """Format RekapResult menjadi teks rekap final."""
    cat_blocks = "\n\n".join(_format_category_block(c) for c in result.categories)
    footer_text = _format_footer(petugas_nama=result.nama_petugas)

    return (
        f"Rekap Laporan Aduan dan Permohonan Layanan "
        f"{result.tanggal} (Shift {result.shift_name})\n"
        f"\n"
        f"Jumlah Tiket Masuk = {result.total_masuk}\n"
        f"Jumlah Tiket Selesai = {result.total_selesai}\n"
        f"Jumlah Tiket Belum Selesai = {result.total_belum_selesai}\n"
        f"\n"
        f"Dengan Rincian Sebagai Berikut :\n"
        f"\n"
        f"{cat_blocks}\n"
        f"\n"
        f"{footer_text}"
    )
