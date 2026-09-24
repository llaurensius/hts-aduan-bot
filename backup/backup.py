#!/usr/bin/env python3
"""Standalone SQLite database backup script for HTS Ticket Monitor.

Executes via external cron job (not inside the main application loop).
Uses Python sqlite3 online backup API (hot backup safe during live concurrent writes).
"""

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from typing import Optional

logger = logging.getLogger("backup")


def backup(db_path: str, backup_dir: str, retain_days: int = 7) -> str:
    """Perform a hot online backup of the SQLite database with retention and integrity checks.

    Args:
        db_path: Path to the active SQLite database file.
        backup_dir: Directory where backups will be stored.
        retain_days: Number of days to retain backup files before deletion.

    Returns:
        str: Absolute or resolved path to the created backup file.

    Raises:
        FileNotFoundError: If db_path does not exist.
        RuntimeError: If the backup fails SQLite integrity verification.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Source database not found at: {db_path}")

    Path(backup_dir).mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"hts_monitor_{ts}.db"
    backup_path = os.path.join(backup_dir, backup_filename)

    # 1. Hot online backup via Python sqlite3 backup API
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()

    # 2. Set strict file permissions (owner read/write only)
    try:
        os.chmod(backup_path, 0o600)
    except OSError:
        pass

    # 3. Integrity verification
    chk_conn = sqlite3.connect(backup_path)
    try:
        result = chk_conn.execute("PRAGMA integrity_check;").fetchone()
    finally:
        chk_conn.close()

    if not result or result[0].lower() != "ok":
        if os.path.exists(backup_path):
            os.remove(backup_path)
        raise RuntimeError(f"Integrity check failed on backup: {result[0] if result else 'no response'}")

    # 4. Clean up backups older than retain_days
    cutoff = datetime.now() - timedelta(days=retain_days)
    for file_path in Path(backup_dir).glob("hts_monitor_*.db"):
        ts_str = file_path.stem.replace("hts_monitor_", "")
        try:
            file_dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
            if file_dt < cutoff:
                file_path.unlink(missing_ok=True)
                logger.info("Purged expired backup: %s", file_path)
        except ValueError:
            # Ignore files that don't match the expected timestamp format
            pass

    # 5. Record BACKUP_COMPLETE into system_events if available
    try:
        audit_conn = sqlite3.connect(db_path)
        audit_conn.execute(
            """
            INSERT INTO system_events (event_type, description, metadata, created_at)
            VALUES (?, ?, ?, ?);
            """,
            (
                "BACKUP_COMPLETE",
                f"Database backup successfully created: {backup_filename}",
                json.dumps({
                    "backup_path": backup_path,
                    "size_bytes": os.path.getsize(backup_path),
                }),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        audit_conn.commit()
        audit_conn.close()
    except Exception:
        # If system_events table does not exist or DB is locked, do not fail backup
        pass

    logger.info("Backup successfully completed: %s", backup_path)
    return backup_path


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    source_db = os.getenv("DB_PATH", "data/hts_monitor.db")
    target_backup_dir = os.getenv("BACKUP_DIR", "data/backups")
    retention = int(os.getenv("BACKUP_RETAIN_DAYS", "7"))

    try:
        created_file = backup(source_db, target_backup_dir, retain_days=retention)
        print(f"Backup OK: {created_file}")
    except Exception as err:
        print(f"Backup FAILED: {err}", file=sys.stderr)
        sys.exit(1)
