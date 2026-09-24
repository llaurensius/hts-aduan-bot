"""HTML, CSRF, and ticket JSON parser for HTS."""

from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Optional, Tuple
from bs4 import BeautifulSoup
from app.hts.exceptions import HTSParseError

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Normalization Helpers
# ─────────────────────────────────────────────────────────────────────────────

def normalize_str(val: Any) -> str:
    """Strip leading/trailing whitespace and convert None to empty string."""
    if val is None:
        return ""
    return str(val).strip()


def normalize_opd_induk(val: Any) -> Optional[str]:
    """Normalize opd_induk / induk_opd_nama. Return None if '-', empty, or None."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s == "-":
        return None
    return s


def normalize_t_solve(val: Any) -> str:
    """Normalize t_solve to string. None/empty becomes '0'."""
    if val is None:
        return "0"
    s = str(val).strip()
    return s if s else "0"


# ─────────────────────────────────────────────────────────────────────────────
# TicketData Domain Model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TicketData:
    """Internal representation of a single HTS ticket.

    Derived from JSON payload of POST /get_aduan_data.
    All string fields are whitespace-stripped.
    """
    nomor_aduan:    str            # no_trouble — business key, UNIQUE
    kategori:       str            # kategori
    sub_kategori:   str            # sub_kategori
    instansi:       str            # opd
    opd_induk:      Optional[str]  # induk_opd_nama (None if '-' or empty)
    pic_nama:       str            # pic
    pic_nomor:      str            # wa
    keluhan:        str            # keluhan
    tanggal_aduan:  str            # tgltshoot
    t_solve:        str            # raw: '0' or UNIX timestamp string
    is_submitted:   int            # 0 or 1
    penjelasan:     str = ""       # penjelasan penanganan dari HTS
    pic_kominfo:    str = ""       # teknisi/helpdesk HTS yang menangani

    @property
    def is_completed(self) -> bool:
        """True if t_solve != '0' and not empty."""
        return self.t_solve not in ("0", "", None)

    @property
    def status_display(self) -> str:
        """Human-readable status string for notification templates."""
        if self.is_completed:
            return "Sudah Ditangani"
        if self.is_submitted == 1:
            return "Belum Ditangani"
        return "Belum Disubmit"

    def to_monitored_dict(self) -> Dict[str, Any]:
        """Dict of monitored fields for change detection and snapshot hashing.

        Key ordering is deterministic.
        """
        return {
            "is_submitted":  self.is_submitted,
            "kategori":      self.kategori,
            "keluhan":       self.keluhan,
            "opd_induk":     self.opd_induk,
            "pic_nama":      self.pic_nama,
            "pic_nomor":     self.pic_nomor,
            "status_display": self.status_display,
            "sub_kategori":  self.sub_kategori,
            "t_solve":       self.t_solve,
            "instansi":      self.instansi,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Parser Functions
# ─────────────────────────────────────────────────────────────────────────────

def parse_csrf_token(html_content: str) -> Optional[str]:
    """Parse csrf_test_name token value from HTS login page HTML."""
    if not html_content:
        return None

    try:
        try:
            soup = BeautifulSoup(html_content, "lxml")
        except Exception:
            soup = BeautifulSoup(html_content, "html.parser")
        token_input = soup.find("input", {"name": "csrf_test_name"})
        if token_input and token_input.get("value"):
            return str(token_input["value"]).strip()
        return None
    except Exception as e:
        logger.error("Failed to parse CSRF token from HTML: %s", e)
        raise HTSParseError(f"Failed to parse HTML for CSRF token: {e}") from e


def parse_ticket(item: Dict[str, Any]) -> TicketData:
    """Map raw JSON dictionary from HTS API to a TicketData object.

    Args:
        item: Raw item dictionary from /get_aduan_data response.

    Returns:
        TicketData: Normalized and validated TicketData instance.

    Raises:
        HTSParseError: If required business key (no_trouble) is missing.
    """
    if not isinstance(item, dict):
        raise HTSParseError(f"Expected dict for ticket item, got {type(item).__name__}")

    raw_no_trouble = item.get("no_trouble")
    if raw_no_trouble is None or not str(raw_no_trouble).strip():
        raise HTSParseError("Missing required field 'no_trouble' in ticket JSON")

    nomor_aduan = str(raw_no_trouble).strip()

    try:
        raw_is_submitted = item.get("is_submitted", 0)
        is_submitted = int(raw_is_submitted) if raw_is_submitted is not None else 0
    except (ValueError, TypeError):
        is_submitted = 0

    return TicketData(
        nomor_aduan=nomor_aduan,
        kategori=normalize_str(item.get("kategori")),
        sub_kategori=normalize_str(item.get("sub_kategori")),
        instansi=normalize_str(item.get("opd")),
        opd_induk=normalize_opd_induk(item.get("induk_opd_nama")),
        pic_nama=normalize_str(item.get("pic")),
        pic_nomor=normalize_str(item.get("wa")),
        keluhan=normalize_str(item.get("keluhan")),
        tanggal_aduan=normalize_str(item.get("tgltshoot")),
        t_solve=normalize_t_solve(item.get("t_solve")),
        is_submitted=is_submitted,
        penjelasan=normalize_str(item.get("penjelasan")),
        pic_kominfo=normalize_str(item.get("pic_kominfo")),
    )


def parse_api_response(response_json: Dict[str, Any]) -> Tuple[List[TicketData], Dict[str, Any]]:
    """Parse complete API JSON response from /get_aduan_data.

    Args:
        response_json: Parsed JSON response dictionary.

    Returns:
        Tuple[List[TicketData], Dict[str, Any]]: List of TicketData and pagination dictionary.

    Raises:
        HTSParseError: If response format is invalid.
    """
    if not isinstance(response_json, dict):
        raise HTSParseError(f"Expected dict for API response, got {type(response_json).__name__}")

    raw_items = response_json.get("data", [])
    if not isinstance(raw_items, list):
        raise HTSParseError(f"Expected list for 'data', got {type(raw_items).__name__}")

    pagination = response_json.get("pagination", {})
    if not isinstance(pagination, dict):
        pagination = {}

    tickets: List[TicketData] = []
    for idx, raw_item in enumerate(raw_items):
        try:
            tickets.append(parse_ticket(raw_item))
        except HTSParseError as e:
            logger.warning("Skipping invalid ticket item at index %d: %s", idx, e)

    return tickets, pagination
