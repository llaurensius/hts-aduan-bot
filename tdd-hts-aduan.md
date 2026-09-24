# Technical Design Document
# HTS Ticket Monitor — v1.0

| Metadata | Value |
|---|---|
| Document Version | 1.0 |
| Status | READY FOR IMPLEMENTATION |
| Derived From | PRD HTS Ticket Monitor v1.0 |
| Created | 2026-09-17 |
| Last Updated | 2026-09-17 |
| Target Environment | Ubuntu / WSL + Python 3.11+ + PM2 |

---

## Table of Contents

1. [Overview & Scope](#1-overview--scope)
2. [Architecture Overview](#2-architecture-overview)
3. [Technology Stack](#3-technology-stack)
4. [Project File Structure](#4-project-file-structure)
5. [Configuration Layer](#5-configuration-layer)
6. [Database Design](#6-database-design)
7. [HTS Client — Authentication & Session](#7-hts-client--authentication--session)
8. [HTS Client — Data Fetching (API)](#8-hts-client--data-fetching-api)
9. [Ticket Processor & Change Detector](#9-ticket-processor--change-detector)
10. [Event System](#10-event-system)
11. [Initial Sync & Reconciliation](#11-initial-sync--reconciliation)
12. [Notification Service — Telegram](#12-notification-service--telegram)
13. [Health Endpoint](#13-health-endpoint)
14. [Orchestrator — Main Loop & State Machine](#14-orchestrator--main-loop--state-machine)
15. [Logging Design](#15-logging-design)
16. [Error Handling Strategy](#16-error-handling-strategy)
17. [Security Implementation](#17-security-implementation)
18. [Graceful Shutdown](#18-graceful-shutdown)
19. [Backup Strategy](#19-backup-strategy)
20. [PM2 Deployment](#20-pm2-deployment)
21. [Dependencies (`requirements.txt`)](#21-dependencies-requirementstxt)
22. [Implementation Sequence](#22-implementation-sequence)

---

## 1. Overview & Scope

**HTS Ticket Monitor** adalah daemon Python yang memantau sistem pengaduan HTS milik Diskominfo Jawa Tengah.

### 1.1 Core Verified Facts (dari Empirical Verification)

| Aspek | Nilai Terverifikasi |
|---|---|
| Base URL | `https://hts.diskomdigi.jatengprov.go.id` |
| Login Endpoint | `POST /login` |
| Data API Endpoint | `POST /get_aduan_data` |
| Auth Mechanism | CodeIgniter CSRF + `ci_session` cookie |
| WAF | F5 BIG-IP (`TS0128f648` cookie wajib dipertahankan) |
| Session Timeout | 7200 detik (2 jam) |
| CAPTCHA | Selalu muncul di halaman login |
| Login Fields | `csrf_test_name`, `email`, `password`, `captcha_code`, `authCheck` |
| API Request | `Content-Type: application/json`, `X-Requested-With: XMLHttpRequest` |
| Pagination | `{page, limit, status}` → response: `{pagination: {total_pages, total}}` |
| Status Selesai | `item.t_solve != '0'` |
| Status Baru/Pending | `item.t_solve == '0'` && `item.is_submitted == 1` |
| Response Time | ~150–350ms |

### 1.2 Scope V1

- Polling periodik ke API `POST /get_aduan_data`
- Deteksi tiket baru, perubahan field, dan penyelesaian tiket
- Notifikasi Telegram dengan template siap copy-paste WhatsApp
- Penanganan session expired & CAPTCHA (manual operator)
- Initial sync (tanpa notifikasi) dan reconciliation (dengan notifikasi)
- Daemon via PM2 dengan health endpoint

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        HTS TICKET MONITOR                             │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                    config.py (AppConfig)                         │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                │                                      │
│  ┌─────────────────────────────▼─────────────────────────────────┐    │
│  │              orchestrator.py (Main Loop + State Machine)       │    │
│  └──┬──────────────┬──────────────────┬──────────────────────────┘    │
│     │              │                  │                               │
│  ┌──▼──────────┐  ┌▼─────────────┐  ┌▼──────────────────────────┐   │
│  │session.py   │  │client.py     │  │ticket_processor.py         │   │
│  │(Auth FSM)   │  │(HTS API)     │  │(Detect + Events)           │   │
│  └─────────────┘  └──────────────┘  └────────────────────────────┘   │
│                                                │                      │
│  ┌─────────────────────────────────────────────▼──────────────────┐   │
│  │                  database/db.py (SQLite WAL)                    │   │
│  │    tickets | ticket_snapshots | ticket_events | notifications   │   │
│  │    system_events                                                │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                │                                      │
│  ┌─────────────────────────────▼─────────────────────────────────┐    │
│  │           notifications/queue.py + telegram.py                 │    │
│  │           (AT-LEAST-ONCE, exponential backoff retry)           │    │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │              health/endpoint.py (GET /health — localhost only)   │  │
│  └─────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
         │                                        │
         ▼                                        ▼
  HTS System (External)                Telegram Bot API (External)
```

### 2.1 Prinsip Desain

1. **Simplicity over cleverness** — hindari overengineering; kode harus mudah dibaca dan di-debug
2. **No data loss** — event yang gagal kirim tetap tersimpan di DB dan di-retry
3. **AT-LEAST-ONCE delivery** — lebih baik duplikat notifikasi daripada tiket terlewat
4. **Fail gracefully** — error pada satu komponen tidak boleh mematikan daemon
5. **No CAPTCHA bypass** — kebijakan keamanan tidak bisa dikompromikan

---

## 3. Technology Stack

| Komponen | Teknologi | Versi |
|---|---|---|
| Runtime | Python | 3.11+ |
| HTTP Client | `requests` | 2.31+ |
| HTML Parsing (fallback) | `beautifulsoup4` + `lxml` | 4.12+ |
| Database | SQLite (built-in) | WAL mode |
| Config | `python-dotenv` | 1.0+ |
| Health Server | `http.server` (stdlib) | — |
| Process Manager | PM2 | latest |
| OS Target | Ubuntu 22.04+ / WSL2 | — |

**Tidak ada framework web berat** (Flask, FastAPI, dsb.) — health endpoint cukup menggunakan `http.server` stdlib untuk meminimalkan dependency.

---

## 4. Project File Structure

```
hts-aduan-bot/
├── main.py                        # Entry point — wiring semua komponen
├── config.py                      # AppConfig: load & validate .env
│
├── database/
│   ├── __init__.py
│   ├── db.py                      # Koneksi, migrasi schema, WAL setup
│   └── models.py                  # CRUD operations (tickets, events, notif, system)
│
├── hts/
│   ├── __init__.py
│   ├── client.py                  # GET/POST ke HTS API, timeout, session reuse
│   ├── session.py                 # Login, CSRF, session detection, state machine
│   └── parser.py                  # Mapping JSON response → internal TicketData
│
├── monitoring/
│   ├── __init__.py
│   ├── orchestrator.py            # Main loop, application state machine
│   ├── ticket_processor.py        # Koordinasi process_ticket()
│   ├── change_detector.py         # Hash check + field-by-field diff
│   └── reconciler.py              # Reconciliation logic (dipanggil setelah recovery)
│
├── notifications/
│   ├── __init__.py
│   ├── telegram.py                # Bot API wrapper, send_message()
│   ├── templates.py               # Format template NEW/CHANGED/COMPLETED/SYSTEM
│   └── queue.py                   # Notification queue processor + retry
│
├── health/
│   ├── __init__.py
│   └── endpoint.py                # Threaded HTTP server GET /health
│
├── backup/
│   └── backup.py                  # Standalone backup script (via cron)
│
├── logs/                          # Gitignored; runtime log files
├── data/                          # Gitignored; SQLite DB + backups
│   └── backups/
│
├── .env                           # Credentials & config (gitignored)
├── .env.example                   # Template dengan placeholder
├── .gitignore
├── requirements.txt               # Pinned dependencies
├── ecosystem.config.js            # PM2 config
└── README.md                      # Setup, deployment, secret rotation docs
```

---

## 5. Configuration Layer

### 5.1 `config.py` — AppConfig

```python
# config.py
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

@dataclass
class AppConfig:
    # HTS
    hts_base_url: str
    hts_username: str
    hts_password: str

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # Polling
    poll_interval: int = 5
    request_timeout: int = 10

    # Retry
    retry_initial_delay: int = 15
    max_retry_delay: int = 1800
    max_telegram_retry: int = 0   # 0 = unlimited

    # App behavior
    initial_sync: bool = True
    log_level: str = "INFO"
    log_file: str = "logs/hts_monitor.log"
    db_path: str = "data/hts_monitor.db"

    # Backup
    backup_enabled: bool = True
    backup_dir: str = "data/backups"
    backup_retain_days: int = 7

    # Health
    health_port: int = 8080
    health_host: str = "127.0.0.1"

    # Optional
    change_debounce_seconds: int = 0
    captcha_reminder_interval: int = 1800
    session_check_interval: int = 60


def load_config() -> AppConfig:
    """Load and validate configuration from .env file.
    
    Raises SystemExit with clear message if required vars are missing.
    Credentials are NEVER printed to stdout/log.
    """
    load_dotenv()

    REQUIRED = ["HTS_BASE_URL", "HTS_USERNAME", "HTS_PASSWORD",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        print(f"[FATAL] Missing required env vars: {', '.join(missing)}")
        raise SystemExit(1)

    return AppConfig(
        hts_base_url=os.environ["HTS_BASE_URL"].rstrip("/"),
        hts_username=os.environ["HTS_USERNAME"],
        hts_password=os.environ["HTS_PASSWORD"],
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
        poll_interval=int(os.getenv("POLL_INTERVAL", 5)),
        request_timeout=int(os.getenv("REQUEST_TIMEOUT", 10)),
        retry_initial_delay=int(os.getenv("RETRY_INITIAL_DELAY", 15)),
        max_retry_delay=int(os.getenv("MAX_RETRY_DELAY", 1800)),
        max_telegram_retry=int(os.getenv("MAX_TELEGRAM_RETRY", 0)),
        initial_sync=os.getenv("INITIAL_SYNC", "true").lower() == "true",
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_file=os.getenv("LOG_FILE", "logs/hts_monitor.log"),
        db_path=os.getenv("DB_PATH", "data/hts_monitor.db"),
        backup_enabled=os.getenv("BACKUP_ENABLED", "true").lower() == "true",
        backup_dir=os.getenv("BACKUP_DIR", "data/backups"),
        backup_retain_days=int(os.getenv("BACKUP_RETAIN_DAYS", 7)),
        health_port=int(os.getenv("HEALTH_PORT", 8080)),
        health_host=os.getenv("HEALTH_HOST", "127.0.0.1"),
        change_debounce_seconds=int(os.getenv("CHANGE_DEBOUNCE_SECONDS", 0)),
        captcha_reminder_interval=int(os.getenv("CAPTCHA_REMINDER_INTERVAL", 1800)),
        session_check_interval=int(os.getenv("SESSION_CHECK_INTERVAL", 60)),
    )
```

### 5.2 File `.env.example`

```ini
# ─────────────────────────────────────────────
# HTS Configuration
# ─────────────────────────────────────────────
HTS_BASE_URL=https://hts.diskomdigi.jatengprov.go.id
HTS_USERNAME=your_username_here
HTS_PASSWORD=your_password_here

# ─────────────────────────────────────────────
# Telegram Configuration
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

# ─────────────────────────────────────────────
# Polling Configuration
# ─────────────────────────────────────────────
POLL_INTERVAL=5
REQUEST_TIMEOUT=10

# ─────────────────────────────────────────────
# Retry Configuration
# ─────────────────────────────────────────────
RETRY_INITIAL_DELAY=15
MAX_RETRY_DELAY=1800
MAX_TELEGRAM_RETRY=0

# ─────────────────────────────────────────────
# Application Configuration
# ─────────────────────────────────────────────
INITIAL_SYNC=true
LOG_LEVEL=INFO
LOG_FILE=logs/hts_monitor.log

# ─────────────────────────────────────────────
# Database Configuration
# ─────────────────────────────────────────────
DB_PATH=data/hts_monitor.db

# ─────────────────────────────────────────────
# Backup Configuration
# ─────────────────────────────────────────────
BACKUP_ENABLED=true
BACKUP_DIR=data/backups
BACKUP_RETAIN_DAYS=7

# ─────────────────────────────────────────────
# Health Endpoint
# ─────────────────────────────────────────────
HEALTH_PORT=8080
HEALTH_HOST=127.0.0.1

# ─────────────────────────────────────────────
# Optional
# ─────────────────────────────────────────────
CHANGE_DEBOUNCE_SECONDS=0
CAPTCHA_REMINDER_INTERVAL=1800
SESSION_CHECK_INTERVAL=60
```

---

## 6. Database Design

### 6.1 Teknologi & Pengaturan

- **SQLite 3** dengan **WAL mode** (`PRAGMA journal_mode=WAL`)
- **Foreign keys** aktif (`PRAGMA foreign_keys=ON`)
- **Semua timestamps** dalam format ISO 8601 UTC: `2026-09-17T02:30:00+00:00`
- **Semua operasi write** yang berkaitan (ticket + snapshot + event + notification) dalam **satu transaction**

### 6.2 Schema SQL Lengkap

```sql
-- database/schema.sql

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ─────────────────────────────────────────────
-- Tabel: tickets
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nomor_aduan     TEXT    UNIQUE NOT NULL,   -- Business key; no_trouble dari API
    kategori        TEXT,
    sub_kategori    TEXT,
    instansi        TEXT,                      -- opd dari API
    opd_induk       TEXT,                      -- induk_opd_nama dari API, nullable
    pic_nama        TEXT,                      -- pic dari API
    pic_nomor       TEXT,                      -- wa dari API
    keluhan         TEXT,
    tanggal_aduan   TEXT,                      -- tgltshoot dari API
    t_solve         TEXT    DEFAULT '0',       -- Raw value dari API
    is_submitted    INTEGER DEFAULT 0,         -- Raw value dari API
    status_display  TEXT,                      -- "Belum Ditangani" / "Sudah Ditangani" / etc.
    is_completed    INTEGER DEFAULT 0,         -- 1 jika t_solve != '0'
    sync_source     TEXT    NOT NULL,          -- 'initial' / 'monitoring' / 'reconciliation'
    first_seen      TEXT    NOT NULL,
    last_seen       TEXT    NOT NULL,
    last_hash       TEXT,                      -- SHA256 hash semua monitored fields
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_nomor_aduan ON tickets(nomor_aduan);
CREATE INDEX IF NOT EXISTS idx_tickets_is_completed ON tickets(is_completed);
CREATE INDEX IF NOT EXISTS idx_tickets_status_display ON tickets(status_display);

-- ─────────────────────────────────────────────
-- Tabel: ticket_snapshots
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ticket_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id       INTEGER NOT NULL REFERENCES tickets(id),
    nomor_aduan     TEXT    NOT NULL,          -- Redundant untuk query efficiency
    snapshot_data   TEXT    NOT NULL,          -- JSON: semua monitored fields
    snapshot_hash   TEXT,                      -- SHA256 snapshot_data
    snapshot_type   TEXT    NOT NULL,          -- 'initial' / 'update'
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_ticket_id ON ticket_snapshots(ticket_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_nomor_aduan ON ticket_snapshots(nomor_aduan);
CREATE INDEX IF NOT EXISTS idx_snapshots_created_at ON ticket_snapshots(created_at);

-- ─────────────────────────────────────────────
-- Tabel: ticket_events
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ticket_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id             TEXT    UNIQUE NOT NULL,     -- UUID4
    ticket_id            INTEGER NOT NULL REFERENCES tickets(id),
    nomor_aduan          TEXT    NOT NULL,
    event_type           TEXT    NOT NULL,
    -- NEW_TICKET | TICKET_CHANGED | STATUS_CHANGED | COMPLETED
    changed_fields       TEXT,                        -- JSON: {"field": {"old": ..., "new": ...}}
    previous_snapshot_id INTEGER REFERENCES ticket_snapshots(id),
    current_snapshot_id  INTEGER REFERENCES ticket_snapshots(id),
    event_timestamp      TEXT    NOT NULL,
    created_at           TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_event_id ON ticket_events(event_id);
CREATE INDEX IF NOT EXISTS idx_events_ticket_id ON ticket_events(ticket_id);
CREATE INDEX IF NOT EXISTS idx_events_nomor_aduan ON ticket_events(nomor_aduan);
CREATE INDEX IF NOT EXISTS idx_events_event_type ON ticket_events(event_type);

-- ─────────────────────────────────────────────
-- Tabel: notifications
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notifications (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id      TEXT    UNIQUE NOT NULL,     -- UUID4
    event_id             TEXT    REFERENCES ticket_events(event_id),
    -- NULL untuk system notifications (HTS down, CAPTCHA, dsb.)
    channel              TEXT    NOT NULL DEFAULT 'telegram',
    message_text         TEXT    NOT NULL,
    status               TEXT    NOT NULL DEFAULT 'PENDING',
    -- PENDING | SENT | FAILED | RETRY_EXHAUSTED
    attempt_count        INTEGER NOT NULL DEFAULT 0,
    max_attempts         INTEGER NOT NULL DEFAULT 0,  -- 0 = unlimited
    next_retry_at        TEXT,
    sent_at              TEXT,
    telegram_message_id  TEXT,
    last_error           TEXT,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_notif_notification_id ON notifications(notification_id);
CREATE INDEX IF NOT EXISTS idx_notif_event_id ON notifications(event_id);
CREATE INDEX IF NOT EXISTS idx_notif_status ON notifications(status);
CREATE INDEX IF NOT EXISTS idx_notif_next_retry_at ON notifications(next_retry_at);

-- ─────────────────────────────────────────────
-- Tabel: system_events
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS system_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT    NOT NULL,
    -- APP_START | APP_STOP | HTS_LOGIN | HTS_LOGOUT | CAPTCHA_REQUIRED
    -- SESSION_EXPIRED | SESSION_RECOVERED | HTS_UNAVAILABLE | HTS_RECOVERED
    -- INITIAL_SYNC_START | INITIAL_SYNC_COMPLETE | RECONCILIATION_START
    -- RECONCILIATION_COMPLETE | BACKUP_START | BACKUP_COMPLETE | BACKUP_FAILED | DB_ERROR
    description     TEXT,
    metadata        TEXT,                      -- JSON optional context
    event_timestamp TEXT    NOT NULL,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sysevents_event_type ON system_events(event_type);
CREATE INDEX IF NOT EXISTS idx_sysevents_event_timestamp ON system_events(event_timestamp);
```

### 6.3 `database/db.py` — Database Manager

```python
# database/db.py (design)

class DatabaseManager:
    """Thread-safe SQLite connection manager with WAL mode."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self):
        """Buka koneksi; buat direktori jika belum ada. Jalankan schema migration."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")   # 5s timeout jika DB locked
        self._run_migrations()

    def _run_migrations(self):
        """Jalankan schema.sql jika tabel belum ada (idempoten via IF NOT EXISTS)."""

    def close(self):
        """Commit pending changes dan tutup koneksi."""

    @contextmanager
    def transaction(self):
        """Context manager untuk atomic transaction."""
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def integrity_check(self) -> bool:
        """Jalankan PRAGMA integrity_check. Return True jika OK."""
```

### 6.4 Data Types Internal

```python
# hts/parser.py

from dataclasses import dataclass

@dataclass
class TicketData:
    """Internal representation dari satu tiket HTS."""
    nomor_aduan:    str      # no_trouble
    kategori:       str      # kategori
    sub_kategori:   str      # sub_kategori
    instansi:       str      # opd
    opd_induk:      str | None  # induk_opd_nama
    pic_nama:       str      # pic
    pic_nomor:      str      # wa
    keluhan:        str      # keluhan
    tanggal_aduan:  str      # tgltshoot
    t_solve:        str      # raw: '0' atau UNIX timestamp
    is_submitted:   int      # 0 atau 1
    
    @property
    def is_completed(self) -> bool:
        return self.t_solve not in ('0', '', None)
    
    @property
    def status_display(self) -> str:
        if self.is_completed:
            return "Sudah Ditangani"
        if self.is_submitted == 1:
            return "Belum Ditangani"
        return "Belum Disubmit"

    def to_monitored_dict(self) -> dict:
        """Field yang dimonitor untuk change detection."""
        return {
            "kategori": self.kategori,
            "sub_kategori": self.sub_kategori,
            "instansi": self.instansi,
            "opd_induk": self.opd_induk,
            "pic_nama": self.pic_nama,
            "pic_nomor": self.pic_nomor,
            "keluhan": self.keluhan,
            "t_solve": self.t_solve,
            "is_submitted": self.is_submitted,
            "status_display": self.status_display,
        }
```

---

## 7. HTS Client — Authentication & Session

### 7.1 Session Manager State Machine

```
UNAUTHENTICATED
      │ (login attempt)
      ▼
AUTHENTICATING
      │
      ├── CAPTCHA always present on /login form
      │   → parse CSRF token from form
      │   → send Telegram alert dengan URL login manual
      │   → masuk state CAPTCHA_REQUIRED
      │
CAPTCHA_REQUIRED
      │ (operator login manual, session valid terdeteksi)
      │  atau
      ├── session recovered via periodic check → AUTHENTICATED
      │
AUTHENTICATED
      │ (session detected as expired during polling)
      ▼
SESSION_EXPIRED
      │
      │ (always CAPTCHA on HTS login → no auto-relogin possible)
      ▼
CAPTCHA_REQUIRED
      │ (operator completes login)
      ▼
RECONCILING → MONITORING
```

> **Catatan Penting**: Berdasarkan hasil verifikasi empiris, CAPTCHA **selalu muncul** pada form login HTS. Ini berarti **tidak ada jalur re-login otomatis** — setiap session expired selalu memerlukan intervensi manual operator. Implementasi harus merefleksikan ini.

### 7.2 Session Detection

Session dianggap expired jika:

```python
def is_session_expired(response: requests.Response) -> bool:
    """
    Deteksi session expired dari response HTS.
    
    Indikasi verified:
    1. URL redirect ke '/' atau '/login' (response.url)
    2. Response body mengandung string khas halaman login
    3. HTTP 401 atau 403
    """
    # Cek 1: URL redirect
    if response.url.rstrip('/').endswith(('/login', HTS_BASE_URL)):
        return True
    
    # Cek 2: HTML contains login form marker
    if 'id="userEmail"' in response.text or 'captchaimg' in response.text:
        return True
    
    # Cek 3: HTTP status
    if response.status_code in (401, 403):
        return True
    
    return False
```

### 7.3 `hts/session.py` — Design

```python
# hts/session.py (design)

class SessionState(Enum):
    UNAUTHENTICATED  = "UNAUTHENTICATED"
    AUTHENTICATING   = "AUTHENTICATING"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    AUTHENTICATED    = "AUTHENTICATED"
    SESSION_EXPIRED  = "SESSION_EXPIRED"

class SessionManager:
    """
    Mengelola siklus hidup session HTS.
    
    Karena CAPTCHA selalu hadir di form login HTS,
    tidak ada auto-relogin. Setiap startup dan session
    expired memerlukan login manual operator.
    """
    
    def __init__(self, config: AppConfig, http_session: requests.Session,
                 db: DatabaseManager, notifier: TelegramNotifier):
        self.config = config
        self.http = http_session
        self.db = db
        self.notifier = notifier
        self.state = SessionState.UNAUTHENTICATED
        self._last_captcha_alert: datetime | None = None
    
    def initialize(self) -> bool:
        """
        Cek apakah ada session yang masih valid (mis. via cookie persistence).
        Jika tidak, kirim Telegram alert untuk login manual.
        Return True jika authenticated, False jika perlu menunggu.
        """
    
    def check_session_validity(self) -> bool:
        """
        Lakukan lightweight request ke /list_aduan untuk cek session.
        Kembalikan True jika session masih valid.
        Jika expired, ubah state dan notify.
        """
    
    def wait_for_manual_login(self):
        """
        Loop dengan SESSION_CHECK_INTERVAL.
        Setiap CAPTCHA_REMINDER_INTERVAL kirim reminder ke Telegram.
        Return ketika session valid terdeteksi.
        """
    
    def _send_captcha_alert(self):
        """Kirim alert login manual ke operator. Rate-limited."""
        now = datetime.utcnow()
        if (self._last_captcha_alert is None or
            (now - self._last_captcha_alert).seconds >= self.config.captcha_reminder_interval):
            self.notifier.send_system_alert("CAPTCHA_REQUIRED", ...)
            self._last_captcha_alert = now
    
    def _get_csrf_token(self) -> str | None:
        """GET halaman login, parse csrf_test_name dari form."""
        resp = self.http.get(f"{self.config.hts_base_url}/", timeout=self.config.request_timeout)
        soup = BeautifulSoup(resp.text, "lxml")
        token_input = soup.find("input", {"name": "csrf_test_name"})
        return token_input["value"] if token_input else None
```

### 7.4 HTTP Session Setup

```python
# Dalam hts/client.py atau inisialisasi di main.py

def create_http_session(config: AppConfig) -> requests.Session:
    """
    Buat requests.Session dengan header default yang dibutuhkan HTS.
    Session ini di-reuse sepanjang lifetime daemon (connection reuse).
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/html,*/*",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    })
    return session
```

---

## 8. HTS Client — Data Fetching (API)

### 8.1 Endpoint Details (Verified)

| Parameter | Nilai |
|---|---|
| Method | `POST` |
| URL | `https://hts.diskomdigi.jatengprov.go.id/get_aduan_data` |
| Headers | `Content-Type: application/json`, `X-Requested-With: XMLHttpRequest` |
| Body | JSON `{page, limit, status, ...}` |
| Auth | Cookie `ci_session` + `TS0128f648` (via requests.Session) |

### 8.2 Strategi Fetch Seluruh Tiket

Karena total tiket bisa besar (~1894+), ada dua strategi fetch:

**A. Fetch Status Pending saja (untuk monitoring normal)**
```json
{"page": 1, "limit": 100, "status": "pending"}
```
- Polling normal hanya perlu memantau tiket yang belum selesai
- Efisien: hanya ~8 tiket aktif saat ini

**B. Fetch "all" (untuk initial sync & reconciliation)**
```json
{"page": 1, "limit": 50, "status": "all"}
```
- Iterasi halaman sampai `pagination.page >= pagination.total_pages`

### 8.3 `hts/client.py` — Design

```python
# hts/client.py (design)

class HTSClient:
    """HTTP client ke HTS API. Semua request melalui persistent session."""
    
    def __init__(self, config: AppConfig, http_session: requests.Session):
        self.config = config
        self.http = http_session
        self._api_url = f"{config.hts_base_url}/get_aduan_data"
    
    def fetch_tickets_page(self, page: int = 1, limit: int = 50,
                           status: str = "all") -> dict:
        """
        Fetch satu halaman data tiket.
        Raise HTSConnectionError jika network/timeout error.
        Raise HTSSessionExpiredError jika session invalid.
        Return raw dict response JSON.
        """
        headers = {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        payload = {"page": page, "limit": limit, "status": status}
        
        try:
            resp = self.http.post(
                self._api_url, json=payload, headers=headers,
                timeout=self.config.request_timeout
            )
        except requests.Timeout:
            raise HTSConnectionError("Request timeout")
        except requests.ConnectionError as e:
            raise HTSConnectionError(str(e))
        
        if is_session_expired(resp):
            raise HTSSessionExpiredError()
        
        resp.raise_for_status()
        return resp.json()
    
    def fetch_all_tickets(self, status: str = "all") -> list[TicketData]:
        """
        Fetch semua tiket dengan pagination otomatis.
        Yield TicketData per tiket (lazy) atau return list.
        """
        page = 1
        while True:
            data = self.fetch_tickets_page(page=page, limit=50, status=status)
            items = data.get("data", [])
            pagination = data.get("pagination", {})
            
            for raw_item in items:
                yield parse_ticket(raw_item)
            
            total_pages = pagination.get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1
    
    def fetch_active_tickets(self) -> list[TicketData]:
        """
        Untuk monitoring normal: hanya fetch status='pending'.
        Lebih efisien karena hanya tiket aktif yang perlu dipantau.
        """
        return list(self.fetch_all_tickets(status="pending"))
```

### 8.4 `hts/parser.py` — JSON Field Mapping

```python
# hts/parser.py

def parse_ticket(item: dict) -> TicketData:
    """
    Map raw API JSON item → TicketData.
    
    Verified field mapping:
    - nomor_aduan  ← item["no_trouble"]
    - kategori     ← item["kategori"]
    - sub_kategori ← item["sub_kategori"]
    - instansi     ← item["opd"]
    - opd_induk    ← item["induk_opd_nama"]  (bisa '-' atau null)
    - pic_nama     ← item["pic"]
    - pic_nomor    ← item["wa"]
    - keluhan      ← item["keluhan"]
    - tanggal_aduan← item["tgltshoot"]
    - t_solve      ← item["t_solve"]         (str: '0' atau timestamp)
    - is_submitted ← item["is_submitted"]    (int: 0 atau 1)
    """
    opd_induk = item.get("induk_opd_nama") or None
    if opd_induk in ("-", "", "null"):
        opd_induk = None
    
    return TicketData(
        nomor_aduan=str(item["no_trouble"]),
        kategori=str(item.get("kategori", "")),
        sub_kategori=str(item.get("sub_kategori", "")),
        instansi=str(item.get("opd", "")),
        opd_induk=opd_induk,
        pic_nama=str(item.get("pic", "")),
        pic_nomor=str(item.get("wa", "")),
        keluhan=str(item.get("keluhan", "")),
        tanggal_aduan=str(item.get("tgltshoot", "")),
        t_solve=str(item.get("t_solve", "0")),
        is_submitted=int(item.get("is_submitted", 0)),
    )
```

---

## 9. Ticket Processor & Change Detector

### 9.1 Change Detection Algorithm

```python
# monitoring/change_detector.py

import hashlib, json

MONITORED_FIELDS = [
    "kategori", "sub_kategori", "instansi", "opd_induk",
    "pic_nama", "pic_nomor", "keluhan", "t_solve",
    "is_submitted", "status_display"
]

def compute_hash(ticket: TicketData) -> str:
    """SHA256 hash dari semua monitored fields (deterministik)."""
    data = json.dumps(ticket.to_monitored_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()

def detect_changes(current: TicketData, last_snapshot_json: str) -> dict | None:
    """
    Bandingkan current ticket dengan last snapshot.
    Return dict changed_fields jika ada perubahan, None jika sama.
    
    changed_fields format:
    {
        "keluhan": {"old": "Internet mati", "new": "Internet mati di lantai 2"},
        "t_solve": {"old": "0", "new": "1726527600"}
    }
    """
    current_hash = compute_hash(current)
    
    # Quick check: jika last_hash sama, skip (optimasi)
    # (caller bisa bandingkan last_hash dari DB sebelum memanggil ini)
    
    last_data = json.loads(last_snapshot_json)
    current_data = current.to_monitored_dict()
    
    changed = {}
    for field in MONITORED_FIELDS:
        old_val = last_data.get(field)
        new_val = current_data.get(field)
        if str(old_val) != str(new_val):
            changed[field] = {"old": old_val, "new": new_val}
    
    return changed if changed else None
```

### 9.2 `monitoring/ticket_processor.py` — Core Logic

```python
# monitoring/ticket_processor.py (design)

class TicketProcessor:
    """
    Memproses satu TicketData:
    - Tentukan apakah new / changed / completed
    - Buat event dan simpan ke DB
    - Queue notifikasi Telegram
    """
    
    def __init__(self, db: DatabaseManager, notifier: NotificationQueue,
                 is_initial_sync: bool = False):
        self.db = db
        self.notifier = notifier
        self.is_initial_sync = is_initial_sync
    
    def process(self, ticket: TicketData, sync_source: str = "monitoring"):
        """Entry point: proses satu tiket."""
        existing = self.db.models.get_ticket(ticket.nomor_aduan)
        
        if existing is None:
            self._handle_new_ticket(ticket, sync_source)
        else:
            self._handle_existing_ticket(ticket, existing)
    
    def _handle_new_ticket(self, ticket: TicketData, sync_source: str):
        """
        Simpan tiket baru ke DB.
        Jika is_initial_sync=True: TIDAK buat event, TIDAK kirim notif.
        Jika is_initial_sync=False: buat NEW_TICKET event + queue notif.
        """
        now = utcnow_iso()
        ticket_hash = compute_hash(ticket)
        snapshot_data = json.dumps(ticket.to_monitored_dict(), ensure_ascii=False)
        
        with self.db.transaction() as conn:
            ticket_id = self.db.models.insert_ticket(
                conn, ticket, sync_source=sync_source,
                first_seen=now, last_seen=now, hash=ticket_hash
            )
            snapshot_id = self.db.models.insert_snapshot(
                conn, ticket_id, ticket.nomor_aduan,
                snapshot_data, ticket_hash, snapshot_type="initial"
            )
            
            if not self.is_initial_sync:
                event_id = str(uuid.uuid4())
                self.db.models.insert_event(
                    conn, event_id=event_id, ticket_id=ticket_id,
                    nomor_aduan=ticket.nomor_aduan,
                    event_type="NEW_TICKET",
                    current_snapshot_id=snapshot_id,
                    event_timestamp=now
                )
                self.notifier.queue(
                    conn, event_id=event_id,
                    message=format_new_ticket(ticket),
                )
    
    def _handle_existing_ticket(self, ticket: TicketData, existing: dict):
        """
        Bandingkan dengan last snapshot.
        Jika ada perubahan: TICKET_CHANGED event + (jika selesai) COMPLETED event.
        Jika tidak ada perubahan: update last_seen saja.
        """
        # Quick hash check
        current_hash = compute_hash(ticket)
        if existing["last_hash"] == current_hash:
            self.db.models.update_last_seen(ticket.nomor_aduan)
            return
        
        # Field-by-field comparison
        last_snapshot = self.db.models.get_latest_snapshot(ticket.nomor_aduan)
        changed_fields = detect_changes(ticket, last_snapshot["snapshot_data"])
        
        if not changed_fields:
            # Hash berbeda tapi field sama (edge case, hash collision — log & skip)
            return
        
        now = utcnow_iso()
        snapshot_data = json.dumps(ticket.to_monitored_dict(), ensure_ascii=False)
        
        with self.db.transaction() as conn:
            self.db.models.update_ticket(conn, ticket, new_hash=current_hash, last_seen=now)
            
            prev_snapshot_id = last_snapshot["id"]
            new_snapshot_id = self.db.models.insert_snapshot(
                conn, existing["id"], ticket.nomor_aduan,
                snapshot_data, current_hash, snapshot_type="update"
            )
            
            # TICKET_CHANGED event
            event_id = str(uuid.uuid4())
            self.db.models.insert_event(
                conn, event_id=event_id, ticket_id=existing["id"],
                nomor_aduan=ticket.nomor_aduan,
                event_type="TICKET_CHANGED",
                changed_fields=json.dumps(changed_fields),
                previous_snapshot_id=prev_snapshot_id,
                current_snapshot_id=new_snapshot_id,
                event_timestamp=now
            )
            self.notifier.queue(conn, event_id=event_id,
                                message=format_changed_ticket(ticket, changed_fields))
            
            # COMPLETED event (jika baru saja selesai)
            if ticket.is_completed and not existing["is_completed"]:
                completed_event_id = str(uuid.uuid4())
                self.db.models.insert_event(
                    conn, event_id=completed_event_id, ticket_id=existing["id"],
                    nomor_aduan=ticket.nomor_aduan,
                    event_type="COMPLETED",
                    current_snapshot_id=new_snapshot_id,
                    event_timestamp=now
                )
                self.notifier.queue(conn, event_id=completed_event_id,
                                    message=format_completed_ticket(ticket))
```

---

## 10. Event System

### 10.1 Event Types

| Event Type | Pemicu | Notifikasi |
|---|---|---|
| `NEW_TICKET` | Tiket baru ditemukan (bukan initial sync) | ✅ Template "Tiket Masuk" |
| `TICKET_CHANGED` | Satu atau lebih field berubah | ✅ Template "Perubahan Aduan" |
| `STATUS_CHANGED` | Subset dari TICKET_CHANGED — field `t_solve` atau `is_submitted` berubah | *Tercakup dalam TICKET_CHANGED* |
| `COMPLETED` | `t_solve` berubah dari `'0'` ke non-zero, dan belum pernah COMPLETED | ✅ Template "Tiket Selesai" |

### 10.2 Deduplication Rules

- **Event ID** adalah UUID4 baru di setiap event baru — tidak ada recycling
- **Notification ID** juga UUID4 terpisah per notifikasi
- Sebelum menyimpan event, tidak perlu cek duplikat karena:
  - New ticket: hanya dibuat saat `nomor_aduan NOT IN tickets`
  - Changed: hanya dibuat saat hash berbeda
  - Completed: kolom `is_completed` di tickets digunakan sebagai guard

---

## 11. Initial Sync & Reconciliation

### 11.1 Initial Sync

```python
# monitoring/orchestrator.py — initial sync portion

def run_initial_sync(client: HTSClient, processor: TicketProcessor,
                     db: DatabaseManager) -> int:
    """
    Fetch semua tiket dengan status='all'.
    Simpan ke DB tanpa mengirim notifikasi.
    Return: jumlah tiket yang disimpan.
    
    Idempotent: tiket yang sudah ada (UNIQUE constraint) akan di-skip
    menggunakan INSERT OR IGNORE.
    """
    db.models.insert_system_event("INITIAL_SYNC_START")
    processor.is_initial_sync = True
    
    count = 0
    for ticket in client.fetch_all_tickets(status="all"):
        processor.process(ticket, sync_source="initial")
        count += 1
    
    db.models.insert_system_event("INITIAL_SYNC_COMPLETE",
                                   metadata={"ticket_count": count})
    processor.is_initial_sync = False
    return count
```

**Idempotency**: Jika crash di tengah initial sync, restart akan memanggil `run_initial_sync()` lagi. Karena menggunakan `INSERT OR IGNORE` pada `nomor_aduan UNIQUE`, tiket yang sudah tersimpan tidak akan duplikat.

### 11.2 Reconciliation

```python
# monitoring/reconciler.py

def run_reconciliation(client: HTSClient, db: DatabaseManager,
                       processor: TicketProcessor) -> dict:
    """
    Jalankan reconciliation antara state DB dan HTS saat ini.
    Dipanggil setelah restart, recovery HTS unavailable, atau recovery session.
    
    Return stats: {new: int, changed: int, unchanged: int, duration_ms: float}
    """
    db.models.insert_system_event("RECONCILIATION_START")
    start = time.time()
    
    stats = {"new": 0, "changed": 0, "unchanged": 0}
    
    # Fetch semua tiket dari HTS (dengan status='all' untuk completeness)
    # Note: untuk efisiensi bisa dipertimbangkan 'pending' + 'solved' terpisah
    for ticket in client.fetch_all_tickets(status="all"):
        existing = db.models.get_ticket(ticket.nomor_aduan)
        if existing is None:
            processor.process(ticket, sync_source="reconciliation")
            stats["new"] += 1
        else:
            current_hash = compute_hash(ticket)
            if existing["last_hash"] != current_hash:
                processor.process(ticket, sync_source="reconciliation")
                stats["changed"] += 1
            else:
                db.models.update_last_seen(ticket.nomor_aduan)
                stats["unchanged"] += 1
    
    stats["duration_ms"] = round((time.time() - start) * 1000, 2)
    db.models.insert_system_event("RECONCILIATION_COMPLETE", metadata=stats)
    return stats
```

---

## 12. Notification Service — Telegram

### 12.1 Message Templates

```python
# notifications/templates.py

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

Status:
{status_display}\
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

Status:
{status_display}\
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

MAX_TELEGRAM_MESSAGE_LENGTH = 4096

def _truncate(text: str, max_len: int = 1000) -> str:
    """Truncate long text untuk mencegah pesan melebihi Telegram limit."""
    if len(text) > max_len:
        return text[:max_len - 3] + "..."
    return text

def _format_pic(pic_nama: str, pic_nomor: str) -> str:
    if pic_nomor and pic_nomor.strip():
        return f"{pic_nama} ({pic_nomor})"
    return pic_nama

def _format_opd_induk(opd_induk: str | None) -> str:
    return opd_induk if opd_induk else ""

def format_new_ticket(ticket: TicketData) -> str:
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

# format_changed_ticket dan format_completed_ticket menggunakan format identik
# dengan TEMPLATE_CHANGED dan TEMPLATE_COMPLETED
```

### 12.2 Telegram Notifier

```python
# notifications/telegram.py (design)

class TelegramNotifier:
    """Wrapper tipis untuk Telegram Bot API sendMessage."""
    
    API_URL = "https://api.telegram.org/bot{token}/sendMessage"
    
    def __init__(self, config: AppConfig):
        self.config = config
        self._url = self.API_URL.format(token=config.telegram_bot_token)
    
    def send(self, text: str) -> dict:
        """
        Kirim pesan ke TELEGRAM_CHAT_ID.
        Raise TelegramError dengan status code jika gagal.
        Token tidak di-log.
        """
        # Truncate jika > MAX_TELEGRAM_MESSAGE_LENGTH
        if len(text) > MAX_TELEGRAM_MESSAGE_LENGTH:
            text = text[:MAX_TELEGRAM_MESSAGE_LENGTH - 50] + "\n...[dipotong]"
        
        payload = {
            "chat_id": self.config.telegram_chat_id,
            "text": text,
        }
        resp = requests.post(self._url, json=payload, timeout=15)
        
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            raise TelegramRateLimitError(retry_after)
        
        if not resp.ok:
            raise TelegramError(resp.status_code, resp.text)
        
        return resp.json()
```

### 12.3 Notification Queue Processor

```python
# notifications/queue.py (design)

class NotificationQueue:
    """
    AT-LEAST-ONCE delivery dengan exponential backoff retry.
    Semua notifikasi disimpan ke DB sebelum dikirim.
    """
    
    RETRY_DELAYS = [15, 60, 300, 1800]  # detik; index = attempt_count - 1
    
    def queue(self, conn, event_id: str | None, message: str):
        """
        Simpan notifikasi ke DB dengan status PENDING.
        Dipanggil DALAM transaction yang sama dengan penyimpanan event.
        """
        notif_id = str(uuid.uuid4())
        now = utcnow_iso()
        # INSERT INTO notifications (notification_id, event_id, message_text, status, ...)
    
    def process_pending(self, notifier: TelegramNotifier):
        """
        Dijalankan setiap polling cycle.
        Query PENDING dan FAILED notifications yang sudah melewati next_retry_at.
        Kirim satu per satu, update status.
        """
        pending = self.db.models.get_pending_notifications()
        for notif in pending:
            try:
                result = notifier.send(notif["message_text"])
                self.db.models.mark_notification_sent(
                    notif["notification_id"],
                    telegram_message_id=str(result.get("result", {}).get("message_id"))
                )
            except TelegramRateLimitError as e:
                # Terapkan retry-after dari Telegram
                self.db.models.mark_notification_retry(
                    notif["notification_id"],
                    delay_seconds=e.retry_after,
                    error=str(e)
                )
            except TelegramError as e:
                if e.status_code == 400:
                    # Pesan rusak — jangan retry
                    self.db.models.mark_notification_exhausted(notif["notification_id"])
                else:
                    delay = self._next_delay(notif["attempt_count"])
                    self.db.models.mark_notification_retry(
                        notif["notification_id"], delay_seconds=delay, error=str(e)
                    )
    
    def _next_delay(self, attempt_count: int) -> int:
        if attempt_count < len(self.RETRY_DELAYS):
            return self.RETRY_DELAYS[attempt_count]
        return self.RETRY_DELAYS[-1]  # cap at 30 menit
```

---

## 13. Health Endpoint

### 13.1 Implementasi

```python
# health/endpoint.py (design)

class HealthState:
    """Shared mutable state antara main loop dan health server thread."""
    app_state: str = "STARTING"
    hts_state: str = "unknown"
    last_poll: str | None = None
    last_successful_poll: str | None = None
    consecutive_failures: int = 0
    session_state: str = "UNAUTHENTICATED"
    pending_notifications: int = 0
    failed_notifications: int = 0
    last_telegram_sent: str | None = None
    total_tickets: int = 0
    start_time: datetime = field(default_factory=datetime.utcnow)

class HealthHTTPHandler(BaseHTTPRequestHandler):
    """Handle GET /health requests."""
    
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        
        state = self.server.health_state  # Injected
        
        # Tentukan status
        if state.app_state in ("HTS_UNAVAILABLE", "CAPTCHA_REQUIRED"):
            status = "unhealthy"
            http_code = 503
        elif state.failed_notifications > 0:
            status = "degraded"
            http_code = 200
        else:
            status = "healthy"
            http_code = 200
        
        body = json.dumps({
            "status": status,
            "timestamp": utcnow_iso(),
            "hts": {
                "state": state.hts_state,
                "last_poll": state.last_poll,
                "last_successful_poll": state.last_successful_poll,
                "consecutive_failures": state.consecutive_failures,
            },
            "session": {"state": state.session_state},
            "telegram": {
                "last_sent": state.last_telegram_sent,
                "pending_notifications": state.pending_notifications,
                "failed_notifications": state.failed_notifications,
            },
            "database": {
                "state": "ok",
                "total_tickets": state.total_tickets,
            },
            "app": {
                "state": state.app_state,
                "uptime_seconds": int((datetime.utcnow() - state.start_time).total_seconds()),
                "version": "1.0.0",
            }
        }, indent=2)
        
        self.send_response(http_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())
    
    def log_message(self, format, *args):
        pass  # Suppress access log spam
```

Health endpoint dijalankan di **thread daemon terpisah** (bukan blocking main loop):

```python
# Dalam main.py startup
health_server = HTTPServer((config.health_host, config.health_port), HealthHTTPHandler)
health_server.health_state = health_state
health_thread = threading.Thread(target=health_server.serve_forever, daemon=True)
health_thread.start()
```

---

## 14. Orchestrator — Main Loop & State Machine

### 14.1 Application State Machine

```
APPLICATION_STARTING
    → load config, init DB, setup logging, start health server
    → initialize session (cek CAPTCHA → kirim alert → tunggu login manual)
    → INITIAL_SYNC (jika INITIAL_SYNC=true)
    → RECONCILING
    → MONITORING

MONITORING (main loop)
    ┌─────────────────────────────────────────────────────────┐
    │  1. CHECK SESSION → jika expired: SESSION_EXPIRED       │
    │  2. FETCH TICKETS → jika error: HTS_UNAVAILABLE         │
    │  3. PROCESS TICKETS (new / changed / completed)         │
    │  4. PROCESS NOTIFICATION QUEUE (send pending)           │
    │  5. UPDATE HEALTH STATE                                 │
    │  6. INTERRUPTIBLE SLEEP (POLL_INTERVAL detik)           │
    └─────────────────────────────────────────────────────────┘

SESSION_EXPIRED / CAPTCHA_REQUIRED
    → kirim Telegram alert (sekali + reminder berkala)
    → loop: check session setiap SESSION_CHECK_INTERVAL
    → ketika session valid: RECONCILING → MONITORING

HTS_UNAVAILABLE
    → kirim Telegram alert (sekali per incident)
    → exponential backoff retry
    → ketika HTS bisa diakses: RECONCILING → MONITORING

SHUTTING_DOWN (SIGTERM dari mana saja)
    → set shutdown_requested = True
    → selesaikan cycle yang berjalan
    → commit DB, flush log, tutup connection
    → exit(0)
```

### 14.2 Interruptible Sleep

```python
def interruptible_sleep(seconds: float, shutdown_event: threading.Event):
    """
    Sleep yang dapat di-interrupt oleh SIGTERM.
    Lebih responsif dibanding time.sleep(seconds) biasa.
    """
    shutdown_event.wait(timeout=seconds)
```

### 14.3 `monitoring/orchestrator.py` — Design

```python
# monitoring/orchestrator.py (design)

class AppState(Enum):
    STARTING         = "STARTING"
    INITIAL_SYNC     = "INITIAL_SYNC"
    RECONCILING      = "RECONCILING"
    MONITORING       = "MONITORING"
    SESSION_EXPIRED  = "SESSION_EXPIRED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    HTS_UNAVAILABLE  = "HTS_UNAVAILABLE"
    SHUTTING_DOWN    = "SHUTTING_DOWN"

class Orchestrator:
    def __init__(self, config, db, hts_client, session_mgr,
                 ticket_processor, notification_queue, notifier,
                 reconciler, health_state):
        ...
        self.shutdown_event = threading.Event()
        self._hts_unavailable_notified = False
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        signal.signal(signal.SIGINT, self._handle_sigterm)
    
    def run(self):
        """Entry point utama."""
        self._transition(AppState.STARTING)
        self.db.models.insert_system_event("APP_START")
        
        # 1. Session initialization
        self.session_mgr.initialize()
        # → akan mengirim Telegram alert jika CAPTCHA diperlukan
        # → akan block di wait_for_manual_login() sampai session valid
        
        # 2. Initial sync
        if self.config.initial_sync:
            self._transition(AppState.INITIAL_SYNC)
            run_initial_sync(self.hts_client, self.ticket_processor, self.db)
        
        # 3. Reconciliation
        self._transition(AppState.RECONCILING)
        run_reconciliation(self.hts_client, self.db, self.ticket_processor)
        
        # 4. Main monitoring loop
        self._transition(AppState.MONITORING)
        self._monitoring_loop()
    
    def _monitoring_loop(self):
        while not self.shutdown_event.is_set():
            cycle_start = time.time()
            
            try:
                # Session check
                if not self.session_mgr.check_session_validity():
                    self._handle_session_expired()
                    continue
                
                # Fetch
                tickets = self.hts_client.fetch_active_tickets()
                self.health_state.last_poll = utcnow_iso()
                self.health_state.last_successful_poll = utcnow_iso()
                self.health_state.consecutive_failures = 0
                self._hts_unavailable_notified = False
                
                # Process
                for ticket in tickets:
                    self.ticket_processor.process(ticket)
                
                # Notification queue
                self.notification_queue.process_pending(self.notifier)
                
                # Update health stats
                self.health_state.total_tickets = self.db.models.count_tickets()
                self.health_state.pending_notifications = (
                    self.db.models.count_notifications_by_status("PENDING"))
                self.health_state.failed_notifications = (
                    self.db.models.count_notifications_by_status("FAILED"))
            
            except HTSSessionExpiredError:
                self._handle_session_expired()
            
            except HTSConnectionError as e:
                self._handle_hts_unavailable(e)
            
            except Exception as e:
                logger.error(f"Unexpected error in monitoring loop: {e}", exc_info=True)
                # Jangan crash — lanjutkan ke sleep
            
            # Interruptible sleep
            elapsed = time.time() - cycle_start
            sleep_time = max(0, self.config.poll_interval - elapsed)
            self.shutdown_event.wait(timeout=sleep_time)
    
    def _handle_session_expired(self):
        """Karena CAPTCHA selalu ada di HTS, langsung ke CAPTCHA_REQUIRED."""
        self._transition(AppState.CAPTCHA_REQUIRED)
        self.db.models.insert_system_event("SESSION_EXPIRED")
        self.session_mgr.wait_for_manual_login()
        # Setelah session valid:
        self._transition(AppState.RECONCILING)
        run_reconciliation(self.hts_client, self.db, self.ticket_processor)
        self._transition(AppState.MONITORING)
    
    def _handle_hts_unavailable(self, error):
        """Kirim satu alert, lalu retry dengan exponential backoff."""
        self._transition(AppState.HTS_UNAVAILABLE)
        self.health_state.consecutive_failures += 1
        
        if not self._hts_unavailable_notified:
            self.notifier_queue.queue_system_alert(
                "HTS_DOWN", format_hts_down(utcnow_iso()))
            self._hts_unavailable_notified = True
        
        # Exponential backoff
        delay = min(
            self.config.retry_initial_delay * (2 ** (self.health_state.consecutive_failures - 1)),
            self.config.max_retry_delay
        )
        logger.error(f"HTS unavailable. Retrying in {delay}s. Error: {error}")
        self.shutdown_event.wait(timeout=delay)
    
    def _handle_sigterm(self, signum, frame):
        logger.info("SIGTERM received. Initiating graceful shutdown...")
        self._transition(AppState.SHUTTING_DOWN)
        self.shutdown_event.set()
    
    def _transition(self, new_state: AppState):
        logger.info(f"State: {self.health_state.app_state} → {new_state.value}")
        self.health_state.app_state = new_state.value
```

---

## 15. Logging Design

### 15.1 Format

```
{timestamp} | {level:<8} | {event:<20} | {nomor_aduan:<15} | {message}
```

Contoh output:
```
2026-09-17T02:30:00+00:00 | INFO     | APP_START           | -               | HTS Ticket Monitor v1.0 starting
2026-09-17T02:30:01+00:00 | INFO     | STATE_CHANGE        | -               | State: STARTING → INITIAL_SYNC
2026-09-17T02:30:05+00:00 | INFO     | INITIAL_SYNC_DONE   | -               | 1894 tickets loaded, 0 notifications sent
2026-09-17T02:30:05+00:00 | INFO     | STATE_CHANGE        | -               | State: INITIAL_SYNC → MONITORING
2026-09-17T02:30:10+00:00 | INFO     | NEW_TICKET          | 1976-TShoot-... | New ticket detected
2026-09-17T02:30:10+00:00 | INFO     | TG_QUEUED           | 1976-TShoot-... | Notification queued: notif-uuid-...
2026-09-17T02:30:10+00:00 | INFO     | TG_SENT             | 1976-TShoot-... | Sent to Telegram, msg_id=12345
2026-09-17T02:30:15+00:00 | WARNING  | SESSION_EXPIRED     | -               | HTS session expired, CAPTCHA required
2026-09-17T02:30:15+00:00 | ERROR    | HTS_UNAVAILABLE     | -               | Connection failed: timeout
```

### 15.2 Setup Logger

```python
# Dalam main.py

import logging
from logging.handlers import RotatingFileHandler

class SensitiveDataFilter(logging.Filter):
    """Filter untuk memastikan credential tidak tercatat di log."""
    
    SENSITIVE_KEYS = []  # Diisi saat startup dengan nilai aktual dari config
    
    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        for key in self.SENSITIVE_KEYS:
            if key and key in msg:
                record.msg = msg.replace(key, "***REDACTED***")
        return True

def setup_logging(config: AppConfig):
    """Setup rotating file handler + console handler."""
    os.makedirs(os.path.dirname(config.log_file), exist_ok=True)
    
    logger = logging.getLogger("hts_monitor")
    logger.setLevel(getattr(logging, config.log_level))
    
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(event)-20s | %(nomor_aduan)-15s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S+00:00"
    )
    
    # File handler (rotating 10MB, keep 5 files)
    fh = RotatingFileHandler(config.log_file, maxBytes=10*1024*1024, backupCount=5)
    fh.setFormatter(fmt)
    
    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    
    # Sensitive filter
    sensitive_filter = SensitiveDataFilter()
    sensitive_filter.SENSITIVE_KEYS = [
        config.hts_password,
        config.telegram_bot_token,
    ]
    fh.addFilter(sensitive_filter)
    ch.addFilter(sensitive_filter)
    
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger
```

---

## 16. Error Handling Strategy

### 16.1 Custom Exception Hierarchy

```python
# hts/client.py

class HTSError(Exception):
    """Base class untuk semua error HTS."""

class HTSConnectionError(HTSError):
    """Network/timeout error ke HTS."""

class HTSSessionExpiredError(HTSError):
    """Session tidak valid — terdeteksi dari response content."""

class HTSParseError(HTSError):
    """Error saat parsing response dari HTS."""

# notifications/telegram.py

class TelegramError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {body}")

class TelegramRateLimitError(TelegramError):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(429, f"Rate limited, retry after {retry_after}s")
```

### 16.2 Error Handling Matrix

| Kondisi | Handler | Aksi |
|---|---|---|
| Network timeout ke HTS | `HTSConnectionError` | Log ERROR, backoff, satu Telegram alert |
| Session expired terdeteksi | `HTSSessionExpiredError` | Kirim Telegram alert, tunggu login manual |
| Response parsing gagal | `HTSParseError` | Log ERROR, skip cycle, lanjutkan |
| DB write gagal | Catch `sqlite3.Error` | Log ERROR, rollback, retry next cycle |
| DB read gagal | Catch `sqlite3.Error` | Log ERROR, skip comparison, update last_seen |
| Telegram 400 Bad Request | `TelegramError(400)` | Mark `RETRY_EXHAUSTED`, log ERROR |
| Telegram 401 Unauthorized | `TelegramError(401)` | Log CRITICAL — token tidak valid |
| Telegram 429 Rate Limit | `TelegramRateLimitError` | Backoff sesuai `Retry-After` header |
| Telegram 5xx / network | `TelegramError` / `requests.Error` | Exponential backoff retry |
| Uncaught exception di main loop | `except Exception` | Log ERROR + traceback, lanjutkan |
| Uncaught exception di startup | Program exit | Log CRITICAL + sys.exit(1) |

---

## 17. Security Implementation

### 17.1 Credential Protection

```python
# Jangan pernah log credential:
logger.info("Configuration loaded")           # ✅
logger.info(f"Password: {config.hts_password}")  # ❌ DILARANG

# Gunakan SensitiveDataFilter (lihat §15.2)
# Filter mencegah credential masuk ke log meskipun ada bug
```

### 17.2 File Permissions

```bash
# Dijalankan saat setup — dokumentasikan di README

chmod 600 .env
chmod 600 data/hts_monitor.db
chmod 600 data/backups/*.db
chmod 640 logs/*.log
```

### 17.3 `.gitignore`

```gitignore
.env
data/
logs/
__pycache__/
*.pyc
*.pyo
.venv/
```

### 17.4 CAPTCHA Policy

Tidak ada kode untuk bypass CAPTCHA. Tidak ada integrasi dengan layanan solver. Satu-satunya aksi ketika CAPTCHA terdeteksi adalah `send_telegram_alert()` dan `wait_for_manual_login()`.

---

## 18. Graceful Shutdown

```python
# Signal handler di orchestrator.py

def _handle_sigterm(self, signum, frame):
    logger.info("SIGTERM received. Initiating graceful shutdown.")
    self.shutdown_event.set()   # Interrupt interruptible_sleep()

# Main cleanup sequence (dipanggil setelah loop exit):
def shutdown(self):
    self._transition(AppState.SHUTTING_DOWN)
    
    # 1. Proses notifikasi yang masih pending (best-effort, timeout 10s)
    try:
        self.notification_queue.flush(timeout=10)
    except Exception:
        pass
    
    # 2. Commit DB yang pending
    try:
        self.db.close()
    except Exception:
        pass
    
    # 3. Flush log
    logging.shutdown()
    
    # 4. Tutup HTTP session
    try:
        self.http_session.close()
    except Exception:
        pass
    
    self.db.models.insert_system_event("APP_STOP")
    sys.exit(0)
```

PM2 `kill_timeout: 30000` (30 detik) memberikan waktu cukup untuk graceful shutdown.

---

## 19. Backup Strategy

### 19.1 Backup Script (`backup/backup.py`)

Dijalankan via **cron job terpisah** (bukan in-app scheduler) sesuai rekomendasi PRD:

```python
#!/usr/bin/env python3
# backup/backup.py
"""
Standalone backup script. Jalankan via cron, bukan via main app.
Contoh cron: 0 2 * * * /path/to/.venv/bin/python /path/to/backup/backup.py

Menggunakan SQLite Python backup API (hot backup — safe saat DB sedang berjalan).
"""

import sqlite3, os, shutil, sys
from datetime import datetime, timedelta
from pathlib import Path

def backup(db_path: str, backup_dir: str, retain_days: int = 7):
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"hts_monitor_{ts}.db")
    
    # Hot backup via Python sqlite3 backup API
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    
    os.chmod(backup_path, 0o600)
    
    # Integrity check
    conn = sqlite3.connect(backup_path)
    result = conn.execute("PRAGMA integrity_check").fetchone()
    conn.close()
    if result[0] != "ok":
        os.remove(backup_path)
        raise RuntimeError(f"Integrity check failed: {result[0]}")
    
    # Hapus backup lama
    cutoff = datetime.now() - timedelta(days=retain_days)
    for f in Path(backup_dir).glob("hts_monitor_*.db"):
        ts_str = f.stem.replace("hts_monitor_", "")
        try:
            file_dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
            if file_dt < cutoff:
                f.unlink()
        except ValueError:
            pass
    
    print(f"Backup OK: {backup_path}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    db_path    = os.getenv("DB_PATH", "data/hts_monitor.db")
    backup_dir = os.getenv("BACKUP_DIR", "data/backups")
    retain_days = int(os.getenv("BACKUP_RETAIN_DAYS", 7))
    backup(db_path, backup_dir, retain_days)
```

### 19.2 Crontab Setup

```bash
# Edit crontab: crontab -e
# Backup setiap hari pukul 02:00 WIB (UTC+7 → 19:00 UTC)
0 19 * * * cd /path/to/hts-aduan-bot && .venv/bin/python backup/backup.py >> logs/backup.log 2>&1
```

---

## 20. PM2 Deployment

### 20.1 `ecosystem.config.js`

```javascript
module.exports = {
  apps: [{
    name: 'hts-ticket-monitor',
    script: '.venv/bin/python',
    args: 'main.py',
    cwd: '/path/to/hts-aduan-bot',
    interpreter: 'none',
    watch: false,
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 5000,
    max_memory_restart: '256M',
    out_file: 'logs/pm2-out.log',
    error_file: 'logs/pm2-error.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    kill_timeout: 30000,
    env: {
      PYTHONUNBUFFERED: '1',
    }
  }]
};
```

> **Catatan**: `env_file` di PM2 tidak selalu reliable. Lebih aman menggunakan `python-dotenv` dalam kode (load `load_dotenv()` di `config.py`) sehingga `.env` dibaca langsung oleh aplikasi.

### 20.2 Setup Commands

```bash
# Install PM2 (jika belum)
npm install -g pm2

# Buat virtual environment
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Buat direktori
mkdir -p logs data/backups

# Salin dan isi .env
cp .env.example .env
chmod 600 .env
nano .env  # isi credential

# Start
pm2 start ecosystem.config.js

# Simpan agar auto-start saat reboot
pm2 save
pm2 startup  # ikuti instruksi yang ditampilkan

# Monitor
pm2 status
pm2 logs hts-ticket-monitor --lines 50
pm2 monit
```

---

## 21. Dependencies (`requirements.txt`)

```txt
# HTTP client
requests==2.31.0
urllib3==2.1.0

# HTML parsing (hanya untuk session detection & login page parsing)
beautifulsoup4==4.12.3
lxml==5.1.0

# Environment management
python-dotenv==1.0.1

# No other external dependencies needed
# sqlite3, http.server, threading, uuid, hashlib, json — semua stdlib Python
```

---

## 22. Implementation Sequence

Urutan implementasi yang direkomendasikan untuk menghindari dependency circular dan memudahkan testing incremental:

| Fase | Modul | Deliverable |
|---|---|---|
| **Fase 1** | `config.py` | AppConfig load & validate dari `.env.example` |
| **Fase 2** | `database/db.py` + `database/models.py` | Schema creation, CRUD dasar, WAL mode |
| **Fase 3** | `hts/parser.py` | TicketData dataclass + `parse_ticket()` |
| **Fase 4** | `hts/client.py` | `fetch_tickets_page()` + `fetch_all_tickets()` |
| **Fase 5** | `hts/session.py` | Session detection + `wait_for_manual_login()` |
| **Fase 6** | `monitoring/change_detector.py` | `compute_hash()` + `detect_changes()` |
| **Fase 7** | `monitoring/ticket_processor.py` | `process()` — new ticket + change + complete |
| **Fase 8** | `notifications/templates.py` | Semua format template pesan |
| **Fase 9** | `notifications/telegram.py` | `TelegramNotifier.send()` |
| **Fase 10** | `notifications/queue.py` | `queue()` + `process_pending()` + retry |
| **Fase 11** | `monitoring/reconciler.py` | `run_reconciliation()` |
| **Fase 12** | `health/endpoint.py` | HTTP server + `HealthState` |
| **Fase 13** | `monitoring/orchestrator.py` | Main loop + state machine + SIGTERM |
| **Fase 14** | `main.py` | Wiring semua komponen |
| **Fase 15** | `backup/backup.py` | Standalone backup script |
| **Fase 16** | `ecosystem.config.js` + `.gitignore` + `README.md` | Deployment files |

---

## Appendix A: Contoh Respons API HTS

### Request

```http
POST /get_aduan_data HTTP/1.1
Host: hts.diskomdigi.jatengprov.go.id
Content-Type: application/json
X-Requested-With: XMLHttpRequest
Cookie: ci_session=...; TS0128f648=...

{"page": 1, "limit": 10, "status": "pending"}
```

### Response (struktur terverifikasi)

```json
{
  "data": [
    {
      "id_trouble": 1976,
      "no_trouble": "1976-TShoot-2026-jateng-09",
      "kategori": "troubleshoot",
      "sub_kategori": "CORE NETWORK",
      "opd": "Nama Instansi OPD",
      "induk_opd_nama": "-",
      "pic": "Nama PIC",
      "wa": "08123456789",
      "keluhan": "Deskripsi keluhan detail...",
      "tgltshoot": "2026-09-17",
      "t_solve": "0",
      "is_submitted": 1,
      "created_at": "2026-09-17 09:00:00",
      "updated_at": "2026-09-17 09:00:00"
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 10,
    "total": 8,
    "total_pages": 1
  }
}
```

---

## Appendix B: Contoh Notifikasi Telegram

### Tiket Masuk

```
Menginformasikan Tiket Masuk:

Nomor Aduan:
1976-TShoot-2026-jateng-09

Kategori / Sub Kategori
troubleshoot / CORE NETWORK

Instansi:
Dinas Pemberdayaan Masyarakat Desa

OPD Induk:

PIC:
Budi Santoso (081234567890)

Keluhan:
Jaringan internet tidak dapat diakses di seluruh gedung sejak pukul 08.00 WIB.

Status:
Belum Ditangani
```

### CAPTCHA Alert

```
🔐 LOGIN MANUAL DIPERLUKAN

HTS memerlukan login manual (CAPTCHA).
Timestamp: 2026-09-17T02:30:00+00:00

Silakan login secara manual ke:
https://hts.diskomdigi.jatengprov.go.id

Monitoring akan dilanjutkan secara otomatis setelah sesi valid.
```

---

*End of Document*

*TDD Version 1.0 | HTS Ticket Monitor | Status: READY FOR IMPLEMENTATION*
