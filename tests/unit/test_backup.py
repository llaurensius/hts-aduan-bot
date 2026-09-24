"""Unit tests for the SQLite Database Backup Service (Milestone M15)."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import sys
from unittest.mock import MagicMock, patch
import pytest

from backup.backup import backup


@pytest.fixture
def sample_db(tmp_path):
    """Create a temporary SQLite database with valid tables and data."""
    db_file = tmp_path / "source_hts.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """
        CREATE TABLE system_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            description TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nomor_aduan TEXT UNIQUE NOT NULL,
            keluhan TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO tickets (nomor_aduan, keluhan) VALUES (?, ?);",
        ("HTS-BACKUP-001", "Gangguan server"),
    )
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture
def backup_dir(tmp_path):
    """Temporary directory for storing backups."""
    b_dir = tmp_path / "backups"
    b_dir.mkdir(parents=True, exist_ok=True)
    return b_dir


def test_backup_creates_file(sample_db, backup_dir):
    """Verify that backup() produces a non-empty SQLite file in the target directory."""
    created_path = backup(str(sample_db), str(backup_dir), retain_days=7)

    assert os.path.exists(created_path)
    assert os.path.getsize(created_path) > 0
    assert "hts_monitor_" in os.path.basename(created_path)


def test_backup_integrity_check(sample_db, backup_dir):
    """Verify that a newly created backup passes PRAGMA integrity_check."""
    created_path = backup(str(sample_db), str(backup_dir), retain_days=7)

    conn = sqlite3.connect(created_path)
    try:
        row = conn.execute("PRAGMA integrity_check;").fetchone()
        assert row is not None
        assert row[0].lower() == "ok"

        # Verify data was copied intact
        ticket = conn.execute("SELECT nomor_aduan, keluhan FROM tickets WHERE nomor_aduan = 'HTS-BACKUP-001';").fetchone()
        assert ticket == ("HTS-BACKUP-001", "Gangguan server")
    finally:
        conn.close()


def test_backup_retention(sample_db, backup_dir):
    """Verify that backups older than retain_days are purged while fresh ones are kept."""
    # Create an old expired backup file (e.g. 30 days old)
    old_ts = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d_%H%M%S")
    old_backup = backup_dir / f"hts_monitor_{old_ts}.db"
    old_backup.write_text("dummy old backup data")

    # Create a non-matching file which should be preserved
    preserved_file = backup_dir / "preserve_me.txt"
    preserved_file.write_text("important log")

    assert old_backup.exists()

    # Run backup with 7 days retention
    fresh_backup = backup(str(sample_db), str(backup_dir), retain_days=7)

    # Old backup must be deleted
    assert not old_backup.exists()

    # Preserved file and fresh backup must remain
    assert preserved_file.exists()
    assert os.path.exists(fresh_backup)


def test_backup_permission(sample_db, backup_dir):
    """Verify that file permissions are set strictly (0o600 on POSIX)."""
    created_path = backup(str(sample_db), str(backup_dir), retain_days=7)

    assert os.path.exists(created_path)
    if os.name != "nt":
        file_mode = oct(os.stat(created_path).st_mode & 0o777)
        assert file_mode == "0o600"
    else:
        # On Windows, verify file is readable and writable
        assert os.access(created_path, os.R_OK | os.W_OK)


def test_backup_source_not_found(backup_dir):
    """Verify FileNotFoundError is raised if source database does not exist."""
    non_existent = str(backup_dir / "does_not_exist.db")
    with pytest.raises(FileNotFoundError):
        backup(non_existent, str(backup_dir), retain_days=7)


def test_backup_integrity_failure_removes_file(sample_db, backup_dir, monkeypatch):
    """Verify that a failing integrity check raises RuntimeError and cleans up the corrupt backup."""

    class CorruptConnection:
        def execute(self, query):
            return self

        def fetchone(self):
            return ("database disk image is malformed",)

        def close(self):
            pass

    original_connect = sqlite3.connect
    calls = 0

    def mock_connect(database, *args, **kwargs):
        nonlocal calls
        if "hts_monitor_" in str(database) and "backups" in str(database):
            calls += 1
            if calls >= 2:
                return CorruptConnection()
        return original_connect(database, *args, **kwargs)

    # Let src.backup succeed, but mock the verification step
    with patch("backup.backup.sqlite3.connect", side_effect=mock_connect):
        with pytest.raises(RuntimeError, match="Integrity check failed"):
            backup(str(sample_db), str(backup_dir), retain_days=7)

    # Corrupt backup must not remain in the directory
    backups = list(backup_dir.glob("hts_monitor_*.db"))
    assert len(backups) == 0


def test_backup_records_system_event(sample_db, backup_dir):
    """Verify that a successful backup logs BACKUP_COMPLETE to system_events table."""
    created_path = backup(str(sample_db), str(backup_dir), retain_days=7)

    conn = sqlite3.connect(str(sample_db))
    try:
        row = conn.execute(
            "SELECT event_type, description, metadata FROM system_events WHERE event_type = 'BACKUP_COMPLETE';"
        ).fetchone()
        assert row is not None
        event_type, description, metadata_json = row
        assert event_type == "BACKUP_COMPLETE"
        metadata = json.loads(metadata_json)
        assert metadata["backup_path"] == created_path
        assert metadata["size_bytes"] > 0
    finally:
        conn.close()
