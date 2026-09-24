"""Data models and CRUD access layer for SQLite database."""

import json
import sqlite3
import uuid
from typing import Any, Dict, List, Optional
from app.utils.time_utils import utcnow_iso


class ModelLayer:
    """Data access and manipulation layer for all HTS tables."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ─────────────────────────────────────────────────────────────────────────
    # Tickets CRUD
    # ─────────────────────────────────────────────────────────────────────────

    def insert_ticket(
        self,
        conn: Optional[sqlite3.Connection],
        ticket: Any,
        sync_source: str,
        last_hash: Optional[str] = None,
        now_iso: Optional[str] = None,
    ) -> int:
        """Insert a ticket with INSERT OR IGNORE for idempotency.

        Args:
            conn: Optional connection to use (or self.conn).
            ticket: TicketData object or dict containing ticket fields.
            sync_source: 'initial' / 'monitoring' / 'reconciliation'.
            last_hash: SHA256 hash string.
            now_iso: Timestamp string (defaults to utcnow_iso()).

        Returns:
            int: The ticket id (rowid). If ignored, returns the existing ticket id.
        """
        c = conn or self.conn
        now = now_iso or utcnow_iso()

        # Handle both dataclass and dict
        if hasattr(ticket, "nomor_aduan"):
            nomor_aduan = ticket.nomor_aduan
            kategori = ticket.kategori
            sub_kategori = ticket.sub_kategori
            instansi = ticket.instansi
            opd_induk = ticket.opd_induk
            pic_nama = ticket.pic_nama
            pic_nomor = ticket.pic_nomor
            keluhan = ticket.keluhan
            tanggal_aduan = ticket.tanggal_aduan
            t_solve = ticket.t_solve
            is_submitted = ticket.is_submitted
            status_display = ticket.status_display
            is_completed = int(ticket.is_completed)
            penjelasan = getattr(ticket, "penjelasan", "") or ""
            pic_kominfo = getattr(ticket, "pic_kominfo", "") or ""
        else:
            nomor_aduan = ticket["nomor_aduan"]
            kategori = ticket.get("kategori")
            sub_kategori = ticket.get("sub_kategori")
            instansi = ticket.get("instansi")
            opd_induk = ticket.get("opd_induk")
            pic_nama = ticket.get("pic_nama")
            pic_nomor = ticket.get("pic_nomor")
            keluhan = ticket.get("keluhan")
            tanggal_aduan = ticket.get("tanggal_aduan")
            t_solve = ticket.get("t_solve", "0")
            is_submitted = int(ticket.get("is_submitted", 0))
            status_display = ticket.get("status_display")
            is_completed = 1 if t_solve not in ("0", "", None) else 0
            penjelasan = ticket.get("penjelasan", "") or ""
            pic_kominfo = ticket.get("pic_kominfo", "") or ""

        cursor = c.execute(
            """
            INSERT OR IGNORE INTO tickets (
                nomor_aduan, kategori, sub_kategori, instansi, opd_induk,
                pic_nama, pic_nomor, keluhan, tanggal_aduan, t_solve,
                is_submitted, status_display, is_completed, sync_source,
                penjelasan, pic_kominfo,
                first_seen, last_seen, last_hash, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?, ?
            );
            """,
            (
                nomor_aduan,
                kategori,
                sub_kategori,
                instansi,
                opd_induk,
                pic_nama,
                pic_nomor,
                keluhan,
                tanggal_aduan,
                t_solve,
                is_submitted,
                status_display,
                is_completed,
                sync_source,
                penjelasan,
                pic_kominfo,
                now,
                now,
                last_hash,
                now,
                now,
            ),
        )

        if cursor.rowcount > 0:
            return cursor.lastrowid
        else:
            # Row existed, fetch existing id
            existing = self.get_ticket(nomor_aduan, conn=c)
            return existing["id"] if existing else 0

    def get_ticket(
        self, nomor_aduan: str, conn: Optional[sqlite3.Connection] = None
    ) -> Optional[sqlite3.Row]:
        """Fetch a ticket by business key (nomor_aduan)."""
        c = conn or self.conn
        cursor = c.execute("SELECT * FROM tickets WHERE nomor_aduan = ?;", (nomor_aduan,))
        return cursor.fetchone()

    def update_ticket(
        self,
        conn: Optional[sqlite3.Connection],
        ticket: Any,
        new_hash: str,
        last_seen: Optional[str] = None,
    ) -> None:
        """Update monitored fields, last_seen, hash, and updated_at of a ticket."""
        c = conn or self.conn
        now = last_seen or utcnow_iso()

        if hasattr(ticket, "nomor_aduan"):
            nomor_aduan = ticket.nomor_aduan
            kategori = ticket.kategori
            sub_kategori = ticket.sub_kategori
            instansi = ticket.instansi
            opd_induk = ticket.opd_induk
            pic_nama = ticket.pic_nama
            pic_nomor = ticket.pic_nomor
            keluhan = ticket.keluhan
            tanggal_aduan = ticket.tanggal_aduan
            t_solve = ticket.t_solve
            is_submitted = ticket.is_submitted
            status_display = ticket.status_display
            is_completed = int(ticket.is_completed)
            penjelasan = getattr(ticket, "penjelasan", "") or ""
            pic_kominfo = getattr(ticket, "pic_kominfo", "") or ""
        else:
            nomor_aduan = ticket["nomor_aduan"]
            kategori = ticket.get("kategori")
            sub_kategori = ticket.get("sub_kategori")
            instansi = ticket.get("instansi")
            opd_induk = ticket.get("opd_induk")
            pic_nama = ticket.get("pic_nama")
            pic_nomor = ticket.get("pic_nomor")
            keluhan = ticket.get("keluhan")
            tanggal_aduan = ticket.get("tanggal_aduan")
            t_solve = ticket.get("t_solve", "0")
            is_submitted = int(ticket.get("is_submitted", 0))
            status_display = ticket.get("status_display")
            is_completed = 1 if t_solve not in ("0", "", None) else 0
            penjelasan = ticket.get("penjelasan", "") or ""
            pic_kominfo = ticket.get("pic_kominfo", "") or ""

        c.execute(
            """
            UPDATE tickets SET
                kategori = ?,
                sub_kategori = ?,
                instansi = ?,
                opd_induk = ?,
                pic_nama = ?,
                pic_nomor = ?,
                keluhan = ?,
                tanggal_aduan = ?,
                t_solve = ?,
                is_submitted = ?,
                status_display = ?,
                is_completed = ?,
                penjelasan = CASE WHEN ? != '' THEN ? ELSE penjelasan END,
                pic_kominfo = CASE WHEN ? != '' THEN ? ELSE pic_kominfo END,
                last_seen = ?,
                last_hash = ?,
                updated_at = ?
            WHERE nomor_aduan = ?;
            """,
            (
                kategori,
                sub_kategori,
                instansi,
                opd_induk,
                pic_nama,
                pic_nomor,
                keluhan,
                tanggal_aduan,
                t_solve,
                is_submitted,
                status_display,
                is_completed,
                penjelasan,
                penjelasan,
                pic_kominfo,
                pic_kominfo,
                now,
                new_hash,
                now,
                nomor_aduan,
            ),
        )

    def update_last_seen(
        self, nomor_aduan: str, last_seen: Optional[str] = None, conn: Optional[sqlite3.Connection] = None
    ) -> None:
        """Update last_seen timestamp of an unchanged ticket."""
        c = conn or self.conn
        now = last_seen or utcnow_iso()
        c.execute("UPDATE tickets SET last_seen = ? WHERE nomor_aduan = ?;", (now, nomor_aduan))

    def count_tickets(self, conn: Optional[sqlite3.Connection] = None) -> int:
        """Count total tickets in database."""
        c = conn or self.conn
        cursor = c.execute("SELECT COUNT(*) FROM tickets;")
        return cursor.fetchone()[0]

    # ─────────────────────────────────────────────────────────────────────────
    # Ticket Snapshots CRUD
    # ─────────────────────────────────────────────────────────────────────────

    def insert_snapshot(
        self,
        conn: Optional[sqlite3.Connection],
        ticket_id: int,
        nomor_aduan: str,
        data: Dict[str, Any],
        hash_val: str,
        snapshot_type: str,
        created_at: Optional[str] = None,
    ) -> int:
        """Insert a snapshot of ticket state.

        Args:
            conn: Connection.
            ticket_id: Foreign key to tickets.id.
            nomor_aduan: Redundant business key.
            data: Monitored fields dictionary.
            hash_val: SHA256 string.
            snapshot_type: 'initial' / 'update'.
            created_at: Optional timestamp string.

        Returns:
            int: Inserted snapshot id.
        """
        c = conn or self.conn
        now = created_at or utcnow_iso()
        data_json = json.dumps(data, sort_keys=True)

        cursor = c.execute(
            """
            INSERT INTO ticket_snapshots (
                ticket_id, nomor_aduan, snapshot_data, snapshot_hash, snapshot_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?);
            """,
            (ticket_id, nomor_aduan, data_json, hash_val, snapshot_type, now),
        )
        return cursor.lastrowid

    def get_latest_snapshot(
        self, nomor_aduan: str, conn: Optional[sqlite3.Connection] = None
    ) -> Optional[sqlite3.Row]:
        """Fetch the most recent snapshot for a ticket."""
        c = conn or self.conn
        cursor = c.execute(
            """
            SELECT * FROM ticket_snapshots
            WHERE nomor_aduan = ?
            ORDER BY id DESC LIMIT 1;
            """,
            (nomor_aduan,),
        )
        return cursor.fetchone()

    # ─────────────────────────────────────────────────────────────────────────
    # Ticket Events CRUD
    # ─────────────────────────────────────────────────────────────────────────

    def insert_event(
        self,
        conn: Optional[sqlite3.Connection] = None,
        *,
        ticket_id: int,
        nomor_aduan: str,
        event_type: str,
        event_id: Optional[str] = None,
        changed_fields: Optional[Dict[str, Any]] = None,
        previous_snapshot_id: Optional[int] = None,
        current_snapshot_id: Optional[int] = None,
        event_timestamp: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> int:
        """Insert a ticket event record."""
        c = conn or self.conn
        now = created_at or utcnow_iso()
        evt_ts = event_timestamp or now
        evt_id = event_id or str(uuid.uuid4())
        fields_json = json.dumps(changed_fields) if changed_fields is not None else None

        cursor = c.execute(
            """
            INSERT INTO ticket_events (
                event_id, ticket_id, nomor_aduan, event_type,
                changed_fields, previous_snapshot_id, current_snapshot_id,
                event_timestamp, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                evt_id,
                ticket_id,
                nomor_aduan,
                event_type,
                fields_json,
                previous_snapshot_id,
                current_snapshot_id,
                evt_ts,
                now,
            ),
        )
        return cursor.lastrowid

    # ─────────────────────────────────────────────────────────────────────────
    # Notifications CRUD
    # ─────────────────────────────────────────────────────────────────────────

    def insert_notification(
        self,
        conn: Optional[sqlite3.Connection] = None,
        *,
        message_text: str,
        event_id: Optional[str] = None,
        channel: str = "telegram",
        status: str = "PENDING",
        notification_id: Optional[str] = None,
        max_attempts: int = 0,
        next_retry_at: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> int:
        """Insert a notification queue record."""
        c = conn or self.conn
        now = created_at or utcnow_iso()
        notif_id = notification_id or str(uuid.uuid4())

        cursor = c.execute(
            """
            INSERT INTO notifications (
                notification_id, event_id, channel, message_text,
                status, attempt_count, max_attempts, next_retry_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?);
            """,
            (
                notif_id,
                event_id,
                channel,
                message_text,
                status,
                max_attempts,
                next_retry_at,
                now,
                now,
            ),
        )
        return cursor.lastrowid

    def get_pending_notifications(
        self,
        now_iso: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> List[sqlite3.Row]:
        """Fetch pending and retry-ready failed notifications sorted by creation time.

        Args:
            now_iso: Optional ISO timestamp to compare against next_retry_at.
            conn: Optional sqlite3.Connection.

        Returns:
            List[sqlite3.Row]: List of notification rows ready to be processed.
        """
        c = conn or self.conn
        now = now_iso or utcnow_iso()
        cursor = c.execute(
            """
            SELECT * FROM notifications
            WHERE status = 'PENDING'
               OR (status = 'FAILED' AND (next_retry_at IS NULL OR next_retry_at <= ?))
            ORDER BY created_at ASC, id ASC;
            """,
            (now,),
        )
        return cursor.fetchall()

    def mark_notification_sent(
        self,
        notification_id: str,
        telegram_msg_id: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """Mark notification as successfully sent."""
        c = conn or self.conn
        now = utcnow_iso()
        c.execute(
            """
            UPDATE notifications SET
                status = 'SENT',
                telegram_message_id = ?,
                sent_at = ?,
                updated_at = ?
            WHERE notification_id = ?;
            """,
            (str(telegram_msg_id) if telegram_msg_id else None, now, now, notification_id),
        )

    def mark_notification_failed(
        self,
        notification_id: str,
        error: str,
        next_retry_at: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """Increment attempt count and update error on failed notification attempt."""
        c = conn or self.conn
        now = utcnow_iso()
        c.execute(
            """
            UPDATE notifications SET
                status = 'FAILED',
                attempt_count = attempt_count + 1,
                last_error = ?,
                next_retry_at = ?,
                updated_at = ?
            WHERE notification_id = ?;
            """,
            (error, next_retry_at, now, notification_id),
        )

    def mark_notification_exhausted(
        self, notification_id: str, conn: Optional[sqlite3.Connection] = None
    ) -> None:
        """Mark notification as retry exhausted."""
        c = conn or self.conn
        now = utcnow_iso()
        c.execute(
            """
            UPDATE notifications SET
                status = 'RETRY_EXHAUSTED',
                updated_at = ?
            WHERE notification_id = ?;
            """,
            (now, notification_id),
        )

    def count_notifications_by_status(
        self, status: str, conn: Optional[sqlite3.Connection] = None
    ) -> int:
        """Count notifications matching a given status."""
        c = conn or self.conn
        cursor = c.execute("SELECT COUNT(*) FROM notifications WHERE status = ?;", (status,))
        return cursor.fetchone()[0]

    # ─────────────────────────────────────────────────────────────────────────
    # System Events CRUD
    # ─────────────────────────────────────────────────────────────────────────

    def insert_system_event(
        self,
        event_type: str,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        event_timestamp: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """Insert a system event record."""
        c = conn or self.conn
        now = utcnow_iso()
        evt_ts = event_timestamp or now
        meta_json = json.dumps(metadata) if metadata is not None else None

        cursor = c.execute(
            """
            INSERT INTO system_events (
                event_type, description, metadata, event_timestamp, created_at
            ) VALUES (?, ?, ?, ?, ?);
            """,
            (event_type, description, meta_json, evt_ts, now),
        )
        return cursor.lastrowid

    def get_tickets_by_filter(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> List[sqlite3.Row]:
        """Fetch tickets based on status.

        Args:
            status: "pending" (is_completed=0), "completed" (is_completed=1), or None for all.
            limit: Optional limit for the number of tickets to return.
            conn: Optional sqlite3 connection.
            
        Returns:
            List of matching ticket rows.
        """
        c = conn or self.conn
        query = "SELECT * FROM tickets"
        params = []
        
        if status == "pending":
            query += " WHERE is_completed = 0"
        elif status == "completed":
            query += " WHERE is_completed = 1"
            
        query += " ORDER BY id DESC"
        
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
            
        cursor = c.execute(query, tuple(params))
        return cursor.fetchall()

    def delete_ticket_cascade(
        self, nomor_aduan: str, conn: Optional[sqlite3.Connection] = None
    ) -> bool:
        """Delete a ticket and all related records (snapshots, events, notifications).
        
        Returns:
            bool: True if a ticket was deleted, False otherwise.
        """
        c = conn or self.conn
        
        # Check if ticket exists and get its ID
        ticket = self.get_ticket(nomor_aduan, conn=c)
        if not ticket:
            return False
            
        ticket_id = ticket["id"]
        
        # 1. Delete notifications tied to events of this ticket
        # First, find the event IDs
        cursor = c.execute("SELECT event_id FROM ticket_events WHERE ticket_id = ?;", (ticket_id,))
        event_ids = [row["event_id"] for row in cursor.fetchall()]
        
        if event_ids:
            placeholders = ",".join("?" for _ in event_ids)
            c.execute(f"DELETE FROM notifications WHERE event_id IN ({placeholders});", tuple(event_ids))
            
        # 2. Delete events
        c.execute("DELETE FROM ticket_events WHERE ticket_id = ?;", (ticket_id,))
        
        # 3. Delete snapshots
        c.execute("DELETE FROM ticket_snapshots WHERE ticket_id = ?;", (ticket_id,))
        
        # 4. Delete the ticket itself
        c.execute("DELETE FROM tickets WHERE id = ?;", (ticket_id,))
        
        return True

    def get_dashboard_stats(self, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
        """Fetch summary counts for dashboard metrics."""
        c = conn or self.conn
        
        total_tickets = c.execute("SELECT COUNT(*) FROM tickets;").fetchone()[0]
        pending_tickets = c.execute("SELECT COUNT(*) FROM tickets WHERE is_completed = 0;").fetchone()[0]
        completed_tickets = c.execute("SELECT COUNT(*) FROM tickets WHERE is_completed = 1;").fetchone()[0]
        
        notif_sent = c.execute("SELECT COUNT(*) FROM notifications WHERE status = 'SENT';").fetchone()[0]
        notif_failed = c.execute("SELECT COUNT(*) FROM notifications WHERE status IN ('FAILED', 'RETRY_EXHAUSTED');").fetchone()[0]
        notif_pending = c.execute("SELECT COUNT(*) FROM notifications WHERE status = 'PENDING';").fetchone()[0]
        
        today_tickets = c.execute(
            "SELECT COUNT(*) FROM tickets WHERE date(tanggal_aduan) = date('now') OR date(created_at) = date('now');"
        ).fetchone()[0]
        
        return {
            "total_tickets": total_tickets,
            "pending_tickets": pending_tickets,
            "completed_tickets": completed_tickets,
            "notifications_sent": notif_sent,
            "notifications_failed": notif_failed,
            "notifications_pending": notif_pending,
            "today_tickets": today_tickets,
        }

    def get_tickets_paginated(
        self,
        page: int = 1,
        limit: int = 15,
        status: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: Optional[str] = "id",
        order: Optional[str] = "desc",
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        """Fetch paginated tickets with filtering, search, and sorting."""
        c = conn or self.conn
        page = max(1, page)
        limit = max(1, min(100, limit))
        offset = (page - 1) * limit
        
        conditions = []
        params: List[Any] = []
        
        if status == "pending":
            conditions.append("is_completed = 0")
        elif status == "completed":
            conditions.append("is_completed = 1")
            
        if search and search.strip():
            kw = f"%{search.strip()}%"
            conditions.append(
                "(nomor_aduan LIKE ? OR pic_nama LIKE ? OR keluhan LIKE ? OR instansi LIKE ? OR kategori LIKE ?)"
            )
            params.extend([kw, kw, kw, kw, kw])
            
        where_clause = ""
        if conditions:
            where_clause = " WHERE " + " AND ".join(conditions)
            
        count_query = f"SELECT COUNT(*) FROM tickets{where_clause};"
        total = c.execute(count_query, tuple(params)).fetchone()[0]

        ALLOWED_SORT_FIELDS = {
            "nomor_aduan": "nomor_aduan",
            "status": "is_completed",
            "instansi": "instansi COLLATE NOCASE",
            "kategori": "kategori COLLATE NOCASE",
            "pic": "pic_nama COLLATE NOCASE",
            "pic_nama": "pic_nama COLLATE NOCASE",
            "keluhan": "keluhan COLLATE NOCASE",
            "tanggal_aduan": "tanggal_aduan",
            "id": "id",
        }

        sort_key = (sort_by or "id").lower()
        if sort_key not in ALLOWED_SORT_FIELDS:
            sort_key = "id"

        direction = (order or "desc").lower()
        if direction not in ("asc", "desc"):
            direction = "desc"

        sql_col = ALLOWED_SORT_FIELDS[sort_key]
        if sort_key == "id":
            order_clause = f"id {direction.upper()}"
        else:
            order_clause = f"{sql_col} {direction.upper()}, id {direction.upper()}"
        
        query = f"SELECT * FROM tickets{where_clause} ORDER BY {order_clause} LIMIT ? OFFSET ?;"
        query_params = list(params) + [limit, offset]
        cursor = c.execute(query, tuple(query_params))
        items = [dict(row) for row in cursor.fetchall()]
        
        total_pages = (total + limit - 1) // limit if total > 0 else 1
        
        return {
            "items": items,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "sort_by": sort_key,
            "order": direction,
        }

    def get_ticket_details(
        self, nomor_aduan: str, conn: Optional[sqlite3.Connection] = None
    ) -> Optional[Dict[str, Any]]:
        """Fetch full details for a ticket including snapshots, events, and notifications."""
        c = conn or self.conn
        t_row = self.get_ticket(nomor_aduan, conn=c)
        if not t_row:
            return None
            
        ticket_dict = dict(t_row)
        ticket_id = ticket_dict["id"]
        
        # Snapshots
        snap_cursor = c.execute(
            "SELECT * FROM ticket_snapshots WHERE ticket_id = ? ORDER BY id DESC;",
            (ticket_id,)
        )
        snapshots = []
        for s in snap_cursor.fetchall():
            s_dict = dict(s)
            try:
                s_dict["snapshot_data"] = json.loads(s_dict["snapshot_data"])
            except Exception:
                pass
            snapshots.append(s_dict)
            
        # Events & their notifications
        evt_cursor = c.execute(
            "SELECT * FROM ticket_events WHERE ticket_id = ? ORDER BY id DESC;",
            (ticket_id,)
        )
        events = []
        event_ids = []
        for e in evt_cursor.fetchall():
            e_dict = dict(e)
            if e_dict.get("changed_fields"):
                try:
                    e_dict["changed_fields"] = json.loads(e_dict["changed_fields"])
                except Exception:
                    pass
            events.append(e_dict)
            event_ids.append(e_dict["event_id"])
            
        # Notifications
        notifications = []
        if event_ids:
            placeholders = ",".join("?" for _ in event_ids)
            notif_cursor = c.execute(
                f"SELECT * FROM notifications WHERE event_id IN ({placeholders}) ORDER BY id DESC;",
                tuple(event_ids)
            )
            notifications = [dict(n) for n in notif_cursor.fetchall()]
            
        ticket_dict["snapshots"] = snapshots
        ticket_dict["events"] = events
        ticket_dict["notifications"] = notifications
        
        return ticket_dict

    def get_recent_events(
        self, limit: int = 10, conn: Optional[sqlite3.Connection] = None
    ) -> List[Dict[str, Any]]:
        """Fetch most recent ticket events for activity feed."""
        c = conn or self.conn
        cursor = c.execute(
            """
            SELECT e.*, t.instansi, t.pic_nama, t.kategori, t.status_display
            FROM ticket_events e
            LEFT JOIN tickets t ON e.ticket_id = t.id
            ORDER BY e.id DESC
            LIMIT ?;
            """,
            (limit,)
        )
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            if item.get("changed_fields"):
                try:
                    item["changed_fields"] = json.loads(item["changed_fields"])
                except Exception:
                    pass
            results.append(item)
        return results
