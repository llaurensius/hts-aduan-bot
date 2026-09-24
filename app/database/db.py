"""Database manager handling SQLite connection lifecycle, WAL mode, and transactions."""

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


class DatabaseManager:
    """Thread-safe SQLite connection manager with WAL mode and foreign keys enabled."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the active connection, opening it if not already connected."""
        if self._conn is None:
            self.connect()
        assert self._conn is not None
        return self._conn

    @property
    def models(self):
        """Return ModelLayer instance bound to current connection."""
        from app.database.models import ModelLayer
        return ModelLayer(self.connection)

    def connect(self) -> sqlite3.Connection:
        """Open connection; create parent directories if needed; configure PRAGMAs and schema."""
        if self._conn is not None:
            return self._conn

        # Create parent directory if path is a file on disk (skip if memory)
        if self.db_path != ":memory:":
            db_file = Path(self.db_path)
            db_file.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # Configure PRAGMAs
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=30000;")  # 30s busy timeout for concurrent multi-process safety

        self._conn = conn
        self._run_migrations()
        logger.debug("Database connection established: %s", self.db_path)
        return self._conn

    def _run_migrations(self) -> None:
        """Run schema.sql if tables do not exist (idempotent via IF NOT EXISTS)."""
        assert self._conn is not None
        if not SCHEMA_PATH.exists():
            raise FileNotFoundError(f"Schema file not found at {SCHEMA_PATH}")

        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        self._conn.executescript(schema_sql)

        # Ensure incremental columns exist on existing databases
        try:
            cursor = self._conn.execute("PRAGMA table_info(tickets);")
            cols = {row[1] for row in cursor.fetchall()}
            if cols and "penjelasan" not in cols:
                self._conn.execute("ALTER TABLE tickets ADD COLUMN penjelasan TEXT DEFAULT '';")
            if cols and "pic_kominfo" not in cols:
                self._conn.execute("ALTER TABLE tickets ADD COLUMN pic_kominfo TEXT DEFAULT '';")
        except Exception as e:
            logger.warning("Column migration check warning: %s", e)

    def close(self) -> None:
        """Commit pending changes and close connection."""
        if self._conn is not None:
            try:
                self._conn.commit()
            except Exception as e:
                logger.warning("Error committing on close: %s", e)
            finally:
                self._conn.close()
                self._conn = None
                logger.debug("Database connection closed: %s", self.db_path)

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for atomic transaction block.

        Yields:
            sqlite3.Connection: Active database connection.

        Raises:
            Exception: Re-raises any exception after performing a rollback.
        """
        conn = self.connection
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def integrity_check(self) -> bool:
        """Run PRAGMA integrity_check. Returns True if OK, False otherwise."""
        conn = self.connection
        cursor = conn.execute("PRAGMA integrity_check;")
        rows = cursor.fetchall()
        if not rows:
            return False
        first_result = rows[0][0] if isinstance(rows[0], (sqlite3.Row, tuple, list)) else rows[0]
        return str(first_result).strip().lower() == "ok"
