"""Service layer untuk generate rekap laporan aduan per shift."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.hts.rekap_client import HTSRekapClient, DISPLAY_NAMES

logger = logging.getLogger(__name__)

WIB = ZoneInfo("Asia/Jakarta")

# ─── Definisi Shift ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ShiftDefinition:
    name: str
    start_hour: int
    end_hour: int
    crosses_midnight: bool


SHIFTS: Dict[str, ShiftDefinition] = {
    "pagi":  ShiftDefinition("Pagi",  7,  15, False),
    "siang": ShiftDefinition("Siang", 15, 23, False),
    "malam": ShiftDefinition("Malam", 23,  7, True),
}

# ─── Result Dataclasses ───────────────────────────────────────────────────────

@dataclass
class TicketSummary:
    nomor: str
    keluhan: str


@dataclass
class CategoryStats:
    display_name: str
    masuk: int = 0
    selesai: int = 0
    belum_selesai: int = 0
    tickets: List[TicketSummary] = field(default_factory=list)


@dataclass
class RekapResult:
    tanggal: str
    shift_name: str
    nama_petugas: str
    total_masuk: int
    total_selesai: int
    total_belum_selesai: int
    categories: List[CategoryStats]
    errors: Dict[str, str]
    rekap_text: str


# ─── Service ──────────────────────────────────────────────────────────────────

class RekapService:
    """Orkestrasi generate rekap: fetch → filter → hitung → format."""

    def __init__(self, rekap_client: HTSRekapClient) -> None:
        self.client = rekap_client

    def generate(self, tanggal: str, shift_key: str, nama_petugas: str) -> RekapResult:
        """
        Generate rekap laporan untuk tanggal dan shift tertentu.
        tanggal format: "DD/MM/YYYY"
        shift_key: "pagi" | "siang" | "malam"
        """
        from app.notifications.templates import format_rekap

        shift = SHIFTS[shift_key]
        tanggal_date = datetime.strptime(tanggal, "%d/%m/%Y").date()

        # Fetch semua kategori dari HTS
        raw_data = self.client.fetch_all_categories()
        errors = raw_data.pop("_errors", {})

        # Proses tiap kategori
        category_keys = ["aduan", "kunjungan", "permohonan_layanan", "vps_domain", "rekomtek"]
        categories: List[CategoryStats] = []

        for cat_key in category_keys:
            display = DISPLAY_NAMES[cat_key]
            raw_tickets = raw_data.get(cat_key, [])

            if cat_key in errors:
                stats = CategoryStats(display_name=display)
                categories.append(stats)
                continue

            filtered, _shift_start, shift_end = self._filter_tickets(raw_tickets, tanggal_date, shift)
            stats = self._compute_stats(filtered, display, shift_end)
            categories.append(stats)

        total_masuk = sum(c.masuk for c in categories)
        total_selesai = sum(c.selesai for c in categories)
        total_belum = sum(c.belum_selesai for c in categories)

        result = RekapResult(
            tanggal=tanggal,
            shift_name=shift.name,
            nama_petugas=nama_petugas,
            total_masuk=total_masuk,
            total_selesai=total_selesai,
            total_belum_selesai=total_belum,
            categories=categories,
            errors=errors,
            rekap_text="",
        )
        result.rekap_text = format_rekap(result)
        return result

    def _parse_tanggal_aduan(self, raw: Any) -> Optional[datetime]:
        """Parse field tanggal/tgltshoot/created_at dari HTS ke datetime (WIB-aware)."""
        if not raw:
            return None
        raw_str = str(raw).strip()
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
            "%d/%m/%Y",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(raw_str, fmt)
                return dt.replace(tzinfo=WIB)
            except ValueError:
                continue
        logger.debug("Tidak bisa parse tanggal: %r", raw_str)
        return None

    def _parse_t_solve(self, raw: Any) -> Optional[datetime]:
        """Parse field t_solve / checkout_datetime ke datetime (WIB-aware).
        
        Mendukung format UNIX timestamp integer/string maupun string datetime standard.
        Returns None jika kosong, '0', 'null', atau tidak valid.
        """
        if not raw:
            return None
        raw_str = str(raw).strip()
        if not raw_str or raw_str.lower() in ("0", "none", "null"):
            return None
        
        # Coba parse sebagai UNIX timestamp terlebih dahulu
        try:
            ts = int(raw_str)
            if ts == 0:
                return None
            return datetime.fromtimestamp(ts, tz=WIB)
        except (ValueError, OSError, OverflowError):
            pass

        # Coba parse sebagai string datetime
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(raw_str, fmt)
                return dt.replace(tzinfo=WIB)
            except ValueError:
                continue

        logger.debug("Tidak bisa parse t_solve/checkout: %r", raw_str)
        return None

    def _get_shift_window(self, tanggal: date, shift: ShiftDefinition) -> tuple:
        """Kembalikan (shift_start, shift_end) sebagai datetime WIB-aware."""
        if shift.crosses_midnight:
            # Shift Malam: start (tanggal) 23:00 s.d. (tanggal+1) 07:00
            shift_start = datetime(tanggal.year, tanggal.month, tanggal.day,
                                   shift.start_hour, 0, 0, tzinfo=WIB)
            next_day = tanggal + timedelta(days=1)
            shift_end = datetime(next_day.year, next_day.month, next_day.day,
                                 shift.end_hour, 0, 0, tzinfo=WIB)
        else:
            shift_start = datetime(tanggal.year, tanggal.month, tanggal.day,
                                   shift.start_hour, 0, 0, tzinfo=WIB)
            shift_end = datetime(tanggal.year, tanggal.month, tanggal.day,
                                 shift.end_hour, 0, 0, tzinfo=WIB)
        return shift_start, shift_end

    def _should_include_ticket(
        self,
        dt_start: datetime,
        raw_t_solve: Any,
        shift_start: datetime,
        shift_end: datetime,
    ) -> bool:
        """Tentukan apakah tiket harus dimasukkan dalam rekap shift ini.

        Logika:
        - Tiket yang DIBUAT >= shift_end: EXCLUDE (tiket shift berikutnya)
        - Tiket yang DIBUAT dalam window shift (shift_start <= dt < shift_end): INCLUDE
        - Tiket yang DIBUAT sebelum shift (carry-over / menunggak):
            - Jika belum selesai (t_solve=0/None): INCLUDE
            - Jika selesai SEBELUM shift dimulai (dt_solve < shift_start): EXCLUDE
            - Jika selesai SAAT atau SETELAH shift dimulai (dt_solve >= shift_start): INCLUDE
        """
        if dt_start >= shift_end:
            # Tiket dari shift berikutnya, tidak relevan
            return False

        if dt_start >= shift_start:
            # Tiket masuk dalam window shift ini: selalu include
            return True

        # Carry-over: tiket dibuat sebelum shift ini dimulai
        dt_solve = self._parse_t_solve(raw_t_solve)
        if dt_solve is None:
            # Belum selesai sama sekali: INCLUDE sebagai tiket menunggak
            return True
        # Selesai setelah atau tepat saat shift dimulai: INCLUDE
        return dt_solve >= shift_start

    def _filter_tickets(
        self, tickets: List[dict], tanggal: date, shift: ShiftDefinition
    ) -> tuple:
        """Filter tiket yang relevan untuk shift ini.
        
        Mengembalikan tuple (filtered_tickets, shift_start, shift_end) agar
        _compute_stats bisa menentukan status selesai secara historis.
        """
        shift_start, shift_end = self._get_shift_window(tanggal, shift)
        result = []
        for t in tickets:
            raw_tgl = t.get("created_at") or t.get("tgltshoot") or t.get("tanggal_aduan")
            if not raw_tgl and t.get("tgl_kjg"):
                raw_tgl = f"{t.get('tgl_kjg')} {t.get('jam_kjg', '00:00:00')}"

            dt = self._parse_tanggal_aduan(raw_tgl)
            if not dt:
                continue

            # Tentukan nilai solve untuk evaluasi include
            if "checkout_datetime" in t or "visit_status" in t:
                # Kunjungan: hanya jika visit_status == 1 maka checkout_datetime diakui
                if str(t.get("visit_status", "")).strip() == "1":
                    raw_solve = t.get("checkout_datetime")
                else:
                    raw_solve = None
            else:
                raw_solve = t.get("t_solve")

            if self._should_include_ticket(
                dt, raw_solve, shift_start, shift_end
            ):
                result.append(t)
        return result, shift_start, shift_end

    def _compute_stats(
        self, tickets: List[dict], display_name: str, shift_end: datetime
    ) -> CategoryStats:
        """Hitung statistik dan buat daftar tiket.
        
        Sebuah tiket dianggap 'Selesai' pada shift ini jika t_solve-nya
        valid DAN jatuh dalam atau sebelum akhir shift (shift_end).
        Tiket yang diselesaikan SETELAH shift berakhir dihitung Belum Selesai
        untuk laporan historis shift ini.
        """
        masuk = len(tickets)
        selesai = 0
        ticket_summaries = []

        for t in tickets:
            if "checkout_datetime" in t or "visit_status" in t:
                # Kunjungan
                is_visited = str(t.get("visit_status", "")).strip() == "1"
                dt_solve = self._parse_t_solve(t.get("checkout_datetime"))
                is_selesai = is_visited and dt_solve is not None and dt_solve <= shift_end
            else:
                # Aduan / umum
                dt_solve = self._parse_t_solve(t.get("t_solve"))
                is_selesai = dt_solve is not None and dt_solve <= shift_end
            
            if is_selesai:
                selesai += 1
            else:
                # Hanya masukkan tiket yang BELUM SELESAI ke daftar rincian
                nomor = str(t.get("no_kunjung") or t.get("no_trouble") or t.get("nomor_aduan", "")).strip()
                tujuan = str(t.get("keluhan") or t.get("tujuan") or t.get("deskripsi", "")).strip()
                instansi = str(t.get("instansi", "")).strip()
                if instansi and tujuan and instansi not in tujuan:
                    desc_full = f"{tujuan} ({instansi})"
                else:
                    desc_full = tujuan
                desc = desc_full[:100] + "..." if len(desc_full) > 100 else desc_full
                ticket_summaries.append(TicketSummary(nomor=nomor, keluhan=desc))

        return CategoryStats(
            display_name=display_name,
            masuk=masuk,
            selesai=selesai,
            belum_selesai=masuk - selesai,
            tickets=ticket_summaries,
        )
