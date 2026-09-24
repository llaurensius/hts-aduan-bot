#!/usr/bin/env python3
"""Database initialization script.

Initializes the SQLite database with the full schema defined in app/database/schema.sql.
Safe to run multiple times (idempotent).
"""

import logging
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.config import load_config
from app.database.db import DatabaseManager
from app.utils.log_utils import setup_logging


def main() -> int:
    """Initialize database and verify schema creation."""
    setup_logging(log_level="INFO", log_file=None)
    logger = logging.getLogger("init_db")

    # Load configuration
    db_path = os.getenv("DB_PATH", "data/hts_monitor.db")
    logger.info("Initializing database at: %s", db_path)

    db_manager = DatabaseManager(db_path)
    try:
        conn = db_manager.connect()

        # Check integrity
        if not db_manager.integrity_check():
            logger.error("Database integrity check failed!")
            return 1

        # List created tables
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        )
        tables = [row[0] for row in cursor.fetchall()]
        logger.info("Database initialized successfully. Tables found (%d): %s", len(tables), ", ".join(tables))

        expected_tables = {"tickets", "ticket_snapshots", "ticket_events", "notifications", "system_events"}
        missing = expected_tables - set(tables)
        if missing:
            logger.error("Missing expected tables: %s", missing)
            return 1

        return 0
    except Exception as e:
        logger.exception("Failed to initialize database: %s", e)
        return 1
    finally:
        db_manager.close()


if __name__ == "__main__":
    sys.exit(main())
