"""Change detection logic and hashing for HTS tickets."""

from dataclasses import dataclass, field
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional
from app.hts.parser import TicketData

logger = logging.getLogger(__name__)

# Deterministic list of fields monitored for changes
MONITORED_FIELDS: List[str] = [
    "instansi",
    "is_submitted",
    "kategori",
    "keluhan",
    "opd_induk",
    "pic_nama",
    "pic_nomor",
    "status_display",
    "sub_kategori",
    "t_solve",
]


def compute_hash(ticket: TicketData) -> str:
    """Compute deterministic SHA256 hash of all monitored fields of a ticket.

    Uses sort_keys=True and ensure_ascii=False to ensure consistency.
    """
    data = json.dumps(ticket.to_monitored_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass
class ChangeResult:
    """Result of change detection comparison."""
    is_new:         bool
    is_changed:     bool
    is_completed:   bool                          # True only if t_solve just became non-zero
    changed_fields: Dict[str, Dict[str, Any]]     # {"field": {"old": ..., "new": ...}}
    previous_data:  Optional[Dict[str, Any]]      # parsed previous snapshot data
    current:        TicketData                    # current ticket data


def detect_changes(
    current: TicketData,
    last_snapshot_json: Optional[str] = None,
    was_completed: bool = False,
) -> ChangeResult:
    """Compare current ticket against last snapshot JSON.

    Args:
        current: Current TicketData instance.
        last_snapshot_json: Raw JSON string of previous snapshot.
        was_completed: Boolean indicating if ticket was already completed previously.

    Returns:
        ChangeResult: Detailed comparison result.
    """
    if last_snapshot_json is None:
        # No previous snapshot exists -> ticket is new
        is_completed = current.is_completed and not was_completed
        return ChangeResult(
            is_new=True,
            is_changed=False,
            is_completed=is_completed,
            changed_fields={},
            previous_data=None,
            current=current,
        )

    try:
        previous_data = json.loads(last_snapshot_json)
        if not isinstance(previous_data, dict):
            previous_data = {}
    except Exception as e:
        logger.warning("Failed to parse last_snapshot_json for %s: %s", current.nomor_aduan, e)
        previous_data = {}

    current_monitored = current.to_monitored_dict()
    changed_fields: Dict[str, Dict[str, Any]] = {}

    for field_name in MONITORED_FIELDS:
        old_val = previous_data.get(field_name)
        new_val = current_monitored.get(field_name)

        # Normalize string comparison if both are strings
        if old_val != new_val:
            changed_fields[field_name] = {
                "old": old_val,
                "new": new_val,
            }

    is_changed = len(changed_fields) > 0

    # is_completed is True only if:
    # 1. current ticket is completed (t_solve != '0')
    # 2. previous status was NOT completed (was_completed is False)
    # 3. and either t_solve was in changed_fields or old t_solve in ('0', None, '')
    is_completed = False
    if current.is_completed and not was_completed:
        is_completed = True

    return ChangeResult(
        is_new=False,
        is_changed=is_changed,
        is_completed=is_completed,
        changed_fields=changed_fields,
        previous_data=previous_data,
        current=current,
    )


def is_ticket_reused(
    current: TicketData,
    existing_ticket_row: Any,
) -> bool:
    """Determine if a ticket with the same nomor_aduan is a reused/replaced ticket.

    Conditions for a reused ticket:
    1. Different tanggal_aduan (the complaint was submitted on a different date).
    2. Explicitly marked as deleted (is_deleted=1) in previous polling/reconciliation.

    Args:
        current: Incoming TicketData.
        existing_ticket_row: sqlite3.Row or dict representing currently stored ticket.

    Returns:
        bool: True if this ticket is a replacement/reused ticket, False if regular update.
    """
    if existing_ticket_row is None:
        return False

    # 1. Check if marked as deleted in DB
    if "is_deleted" in existing_ticket_row.keys() and existing_ticket_row["is_deleted"]:
        return True

    # 2. Check tanggal_aduan
    old_tanggal = existing_ticket_row["tanggal_aduan"] if "tanggal_aduan" in existing_ticket_row.keys() else None
    if old_tanggal and current.tanggal_aduan and old_tanggal != current.tanggal_aduan:
        return True

    return False


