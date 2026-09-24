# Implementation Plan
# HTS Ticket Monitor — v1.0

| Metadata | Value |
|---|---|
| Document Version | 1.0 |
| Status | READY FOR IMPLEMENTATION |
| Source of Truth | PRD (`prd-hts-aduan.md`) + TDD (`tdd-hts-aduan.md`) |
| Created | 2026-09-17 |
| Target Environment | Ubuntu 22.04+ / WSL2 · Python 3.11+ · PM2 |

---

## Table of Contents

1. [PRD vs TDD — Conflict Analysis](#1-prd-vs-tdd--conflict-analysis)
2. [Final Technology Stack](#2-final-technology-stack)
3. [Final Repository Structure](#3-final-repository-structure)
4. [Domain Model](#4-domain-model)
5. [Module Interfaces / Contracts](#5-module-interfaces--contracts)
6. [Concurrency Model](#6-concurrency-model)
7. [Idempotency Keys](#7-idempotency-keys)
8. [Crash Recovery Analysis](#8-crash-recovery-analysis)
9. [Milestone Plan (M0–M18)](#9-milestone-plan-m0m18)
10. [Dependency Graph](#10-dependency-graph)
11. [Testing Strategy & Matrix](#11-testing-strategy--matrix)
12. [Mocking Strategy](#12-mocking-strategy)
13. [Test Fixtures](#13-test-fixtures)
14. [Deployment Plan](#14-deployment-plan)
15. [PM2 Configuration](#15-pm2-configuration)
16. [Backup Plan](#16-backup-plan)
17. [Security Checklist](#17-security-checklist)
18. [Coding Conventions](#18-coding-conventions)
19. [AI Coding Agent Rules](#19-ai-coding-agent-rules)
20. [AI Coding Agent Prompts (per Milestone)](#20-ai-coding-agent-prompts-per-milestone)
21. [Code Review Checklist Template](#21-code-review-checklist-template)
22. [Production Checklist (Final Go-Live)](#22-production-checklist-final-go-live)

---

## 1. PRD vs TDD — Conflict Analysis

Tidak ada konflik substantif antara PRD dan TDD. Seluruh keputusan teknis di TDD merupakan implementasi langsung dari requirement PRD. Berikut catatan penyempurnaan:

| # | Aspek | PRD | TDD | Status |
|---|---|---|---|---|
| C1 | Monitoring Normal Scope | Semua status tiket | Hanya `status=pending` untuk efisiensi | **Potensi Gap** — PRD tidak secara eksplisit membatasi, TDD memilih `pending` saja. **Rekomendasi**: gunakan `pending` untuk polling normal (efisien), gunakan `all` untuk initial sync & reconciliation. Ini tidak melanggar PRD karena tiket `solved` tidak perlu di-poll ulang setelah selesai. |
| C2 | Auto-relogin | PRD FR-AUTH-03: "jika CAPTCHA TIDAK diperlukan, HARUS re-login otomatis" | TDD menyatakan CAPTCHA selalu muncul → tidak ada auto-relogin | **Resolved** — TDD didasari verifikasi empiris. CAPTCHA selalu ada di HTS. Keputusan TDD correct. |
| C3 | Health endpoint library | PRD tidak menyebutkan library | TDD memilih `http.server` stdlib | **No conflict** — PRD hanya requirement fungsional. Pilihan stdlib tepat (zero dependency). |
| C4 | Backup method | PRD FR merekomendasikan cron | TDD: cron job terpisah | **Aligned** — tidak ada konflik. |
| C5 | `INITIAL_SYNC` flag | PRD: env var `INITIAL_SYNC=true` | TDD: sama | **Aligned** |

**Kesimpulan**: Tidak ada konflik yang memerlukan keputusan eksplisit. TDD adalah turunan yang valid dari PRD.

---

## 2. Final Technology Stack

### 2.1 Stack Keputusan Final

| Komponen | Keputusan | Versi | Alasan | Alternatif Ditolak |
|---|---|---|---|---|
| **Python** | Python 3.11+ | 3.11.x | Union type hints `X \| Y`, `tomllib`, performa asyncio lebih baik | 3.9, 3.10 (masih aman tapi kurang fitur) |
| **HTTP Client** | `requests` | 2.31.0 | Mature, session reuse built-in, cookie jar otomatis, tidak butuh async | `httpx` (overkill untuk sync daemon), `urllib3` (terlalu low-level) |
| **HTML Parser** | `beautifulsoup4` + `lxml` | 4.12.3 + 5.1.0 | Hanya untuk parsing CSRF token dari login page. `lxml` jauh lebih cepat dari `html.parser` | `html.parser` stdlib (cukup tapi lambat) |
| **Browser Automation** | **TIDAK ADA** | — | API JSON sudah tersedia. CAPTCHA tidak boleh diautomasi. Tidak diperlukan. | `playwright`, `selenium` (ditolak — overkill, tidak diperlukan) |
| **Database** | SQLite 3 (stdlib) | built-in | No server, zero ops, WAL mode cukup untuk daemon single-writer, data lokal | PostgreSQL (overkill), Redis (tidak persistent) |
| **ORM** | **Raw SQL** | — | SQLite + raw SQL lebih predictable, tidak ada migration complexity, performa optimal | `SQLAlchemy` (overkill), `peewee` (tidak perlu) |
| **Web Framework** | **TIDAK ADA** | — | Health endpoint cukup `http.server` stdlib (satu class, zero dependency) | `Flask`, `FastAPI` (overkill untuk satu endpoint) |
| **Telegram API** | `requests` (same) | — | Telegram Bot API adalah REST HTTP. Cukup `requests.post()`. | `python-telegram-bot` (overkill, dependency besar) |
| **Testing** | `pytest` | 7.4.x | Standard de-facto, fixture system, parametrize, excellent mock support | `unittest` (verbose), `nose2` (dead) |
| **Mock** | `pytest-mock` + `responses` | latest | `pytest-mock` untuk object mock, `responses` untuk mock HTTP requests | `unittest.mock` (lebih verbose tapi bisa digunakan) |
| **Process Manager** | PM2 | latest | Node.js based, matang, cross-platform, log management, auto-restart | `supervisord` (ok), `systemd` (tidak reliable di WSL2) |
| **Config** | `python-dotenv` | 1.0.1 | De facto untuk `.env` loading. Ringan. | `environs` (feature lebih tapi tidak diperlukan) |
| **Logging** | `logging` (stdlib) + `RotatingFileHandler` | built-in | Zero dependency. `RotatingFileHandler` sudah mencukupi. | `loguru` (nice tapi dependency tambahan) |
| **Type Hints** | Stdlib `typing`, `dataclasses` | built-in | Zero dependency, Python 3.11 sudah lengkap | `attrs`, `pydantic` (overkill untuk use case ini) |

### 2.2 Dependency Final (`requirements.txt`)

```txt
# HTTP & HTML
requests==2.31.0
urllib3==2.1.0
beautifulsoup4==4.12.3
lxml==5.1.0

# Configuration
python-dotenv==1.0.1

# Testing
pytest==7.4.4
pytest-mock==3.12.0
responses==0.25.0

# No ORM, no web framework, no async — by design
```

**Total production runtime dependencies: 4** (`requests`, `urllib3`, `beautifulsoup4`, `lxml`, `python-dotenv`)

---

## 3. Final Repository Structure

```
hts-aduan-bot/
│
├── app/
│   ├── __init__.py
│   ├── main.py                    # Entry point: wiring semua komponen, SIGTERM handler
│   │
│   ├── config.py                  # AppConfig dataclass, load_config(), validasi env
│   │
│   ├── database/
│   │   ├── __init__.py
│   │   ├── db.py                  # DatabaseManager: connect, transaction(), WAL, integrity_check()
│   │   ├── models.py              # Semua CRUD: insert/get/update ticket, snapshot, event, notif, sysevent
│   │   └── schema.sql             # DDL lengkap (CREATE TABLE IF NOT EXISTS, indexes)
│   │
│   ├── hts/
│   │   ├── __init__.py
│   │   ├── client.py              # HTSClient: fetch_tickets_page(), fetch_all_tickets(), fetch_active_tickets()
│   │   ├── session.py             # SessionManager: initialize(), check_session_validity(), wait_for_manual_login()
│   │   ├── parser.py              # TicketData dataclass, parse_ticket(), normalize_*()
│   │   └── exceptions.py         # HTSError, HTSConnectionError, HTSSessionExpiredError, HTSParseError
│   │
│   ├── monitoring/
│   │   ├── __init__.py
│   │   ├── orchestrator.py        # Orchestrator: main loop, AppState FSM, run()
│   │   ├── ticket_processor.py    # TicketProcessor: process(), _handle_new(), _handle_existing()
│   │   ├── change_detector.py     # compute_hash(), detect_changes(), ChangeResult dataclass
│   │   └── reconciler.py         # run_reconciliation(), run_initial_sync()
│   │
│   ├── notifications/
│   │   ├── __init__.py
│   │   ├── telegram.py            # TelegramNotifier: send(), TelegramError, TelegramRateLimitError
│   │   ├── templates.py           # format_new_ticket(), format_changed(), format_completed(), format_*_system()
│   │   └── queue.py               # NotificationQueue: queue(), process_pending(), flush()
│   │
│   ├── health/
│   │   ├── __init__.py
│   │   └── endpoint.py            # HealthState dataclass, HealthHTTPHandler, start_health_server()
│   │
│   └── utils/
│       ├── __init__.py
│       ├── time_utils.py          # utcnow_iso(), parse_iso(), format_wib()
│       └── log_utils.py           # setup_logging(), SensitiveDataFilter
│
├── backup/
│   └── backup.py                  # Standalone backup script; jalankan via cron
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                # Fixtures: config, db, mock_hts, mock_telegram
│   │
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_parser.py
│   │   ├── test_change_detector.py
│   │   ├── test_templates.py
│   │   ├── test_models.py
│   │   └── test_notification_queue.py
│   │
│   ├── integration/
│   │   ├── test_database.py
│   │   ├── test_ticket_processor.py
│   │   ├── test_reconciler.py
│   │   └── test_session_manager.py
│   │
│   └── fixtures/
│       ├── ticket_new.json
│       ├── ticket_changed.json
│       ├── ticket_completed.json
│       ├── ticket_pending.json
│       ├── ticket_empty_opd.json
│       ├── ticket_invalid.json
│       ├── api_response_page1.json
│       └── api_response_empty.json
│
├── scripts/
│   ├── init_db.py                 # Utility: inisialisasi database secara manual
│   └── check_health.sh            # Utility: curl /health dan tampilkan hasil
│
├── data/                          # Gitignored — runtime SQLite DB
│   └── backups/                   # Gitignored — backup files
│
├── logs/                          # Gitignored — log files
│
├── .env                           # Gitignored — credential aktual
├── .env.example                   # Template (committed ke git)
├── .gitignore
├── requirements.txt
├── requirements-dev.txt           # pytest, pytest-mock, responses
├── ecosystem.config.js            # PM2 config
└── README.md
```

### 3.1 Catatan Perubahan vs TDD

TDD menggunakan struktur flat (`hts/`, `monitoring/`, `notifications/`). Implementation Plan ini memindahkan semua ke dalam `app/` untuk membuat root directory lebih bersih dan memudahkan pytest discovery. `backup/` tetap di root karena dijalankan sebagai standalone script via cron (bukan diimport oleh `app/`).

---

## 4. Domain Model

### 4.1 `TicketData` (hts/parser.py)

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class TicketData:
    """
    Representasi internal satu tiket HTS.
    Sumber: response JSON dari POST /get_aduan_data.
    Semua string field sudah di-strip whitespace.
    """
    nomor_aduan:    str            # no_trouble — business key, UNIQUE
    kategori:       str            # kategori
    sub_kategori:   str            # sub_kategori
    instansi:       str            # opd
    opd_induk:      Optional[str]  # induk_opd_nama (None jika '-' / kosong)
    pic_nama:       str            # pic
    pic_nomor:      str            # wa
    keluhan:        str            # keluhan
    tanggal_aduan:  str            # tgltshoot
    t_solve:        str            # raw: '0' atau UNIX timestamp string
    is_submitted:   int            # 0 atau 1

    @property
    def is_completed(self) -> bool:
        """True jika t_solve != '0' (sudah ditangani)."""
        return self.t_solve not in ('0', '', None)

    @property
    def status_display(self) -> str:
        """Human-readable status untuk template notifikasi."""
        if self.is_completed:
            return "Sudah Ditangani"
        if self.is_submitted == 1:
            return "Belum Ditangani"
        return "Belum Disubmit"

    def to_monitored_dict(self) -> dict:
        """
        Dict dari field yang dimonitor untuk change detection & snapshot.
        Urutan key harus deterministik (sort_keys=True di json.dumps).
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
```

### 4.2 `ChangeResult` (monitoring/change_detector.py)

```python
@dataclass
class ChangeResult:
    """Output dari detect_changes(). Tidak bergantung pada DB atau Telegram."""
    is_new:         bool
    is_changed:     bool
    is_completed:   bool   # True jika t_solve baru saja berubah ke non-zero
    changed_fields: dict   # {"field": {"old": ..., "new": ...}}
    previous_data:  Optional[dict]  # snapshot data sebelumnya (parsed)
    current:        TicketData      # data terkini
```

### 4.3 Database Row Representations

Row dari DB dikembalikan sebagai `sqlite3.Row` (dict-like). Tidak ada ORM class — akses via `row["column_name"]`.

### 4.4 Notification Status Enum

```python
from enum import Enum

class NotificationStatus(str, Enum):
    PENDING         = "PENDING"
    SENT            = "SENT"
    FAILED          = "FAILED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
```

### 4.5 AppState Enum

```python
class AppState(str, Enum):
    STARTING         = "STARTING"
    INITIAL_SYNC     = "INITIAL_SYNC"
    RECONCILING      = "RECONCILING"
    MONITORING       = "MONITORING"
    SESSION_EXPIRED  = "SESSION_EXPIRED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    HTS_UNAVAILABLE  = "HTS_UNAVAILABLE"
    SHUTTING_DOWN    = "SHUTTING_DOWN"
```

---

## 5. Module Interfaces / Contracts

### 5.1 HTSClient

```python
class HTSClient:
    def fetch_tickets_page(self, page: int, limit: int, status: str) -> dict:
        """
        Raises: HTSConnectionError, HTSSessionExpiredError
        Returns: raw dict response JSON
        """

    def fetch_all_tickets(self, status: str = "all") -> Generator[TicketData, None, None]:
        """Generator — lazy fetch semua halaman."""

    def fetch_active_tickets(self) -> list[TicketData]:
        """Shortcut: fetch status='pending' saja."""
```

### 5.2 SessionManager

```python
class SessionManager:
    def initialize(self) -> None:
        """Kirim CAPTCHA alert + masuk wait_for_manual_login() jika session tidak valid."""

    def check_session_validity(self) -> bool:
        """True = session valid, False = expired (dan state sudah di-update)."""

    def wait_for_manual_login(self) -> None:
        """Block sampai session valid. Kirim reminder berkala."""
```

### 5.3 ChangeDetector (functional, no class)

```python
def compute_hash(ticket: TicketData) -> str: ...

def detect_changes(
    current: TicketData,
    last_snapshot_json: str,
    was_completed: bool
) -> ChangeResult: ...
```

### 5.4 TicketProcessor

```python
class TicketProcessor:
    is_initial_sync: bool

    def process(self, ticket: TicketData, sync_source: str = "monitoring") -> None:
        """
        Semua logika: new / changed / completed.
        Semua DB write dalam satu transaction.
        Queue notifikasi di DB (belum send ke Telegram).
        """
```

### 5.5 NotificationQueue

```python
class NotificationQueue:
    def queue(self, conn, event_id: Optional[str], message: str) -> str:
        """
        Simpan notifikasi ke DB dalam transaction yang sama.
        Return: notification_id (UUID)
        """

    def process_pending(self, notifier: "TelegramNotifier") -> tuple[int, int]:
        """
        Send semua PENDING/FAILED notifications.
        Return: (sent_count, failed_count)
        """

    def queue_system_alert(self, alert_type: str, message: str) -> str:
        """Queue notifikasi system (CAPTCHA, HTS down) tanpa event_id."""
```

### 5.6 TelegramNotifier

```python
class TelegramNotifier:
    def send(self, text: str) -> str:
        """
        Send pesan ke chat.
        Return: telegram_message_id
        Raises: TelegramError, TelegramRateLimitError
        """
```

### 5.7 DatabaseManager

```python
class DatabaseManager:
    def connect(self) -> None: ...
    def close(self) -> None: ...
    
    @contextmanager
    def transaction(self) -> Generator: ...
    
    def integrity_check(self) -> bool: ...
    
    # models property: akses ke semua CRUD
    @property
    def models(self) -> "ModelLayer": ...
```

---

## 6. Concurrency Model

### 6.1 Keputusan: Single-Threaded Synchronous Polling

**Alasan**: Use case ini tidak memerlukan concurrency kompleks. Satu polling cycle berjalan selesai sebelum cycle berikutnya dimulai. Tidak ada I/O yang perlu diparalelkan dalam satu cycle.

**Model**:
```
Main Thread:
  └── Orchestrator main loop (synchronous, blocking per cycle)
      ├── fetch_active_tickets()    → blocking HTTP request
      ├── process_ticket() x N      → blocking DB writes
      └── process_pending() x M     → blocking HTTP request (Telegram)

Daemon Thread (non-blocking):
  └── Health HTTP Server (threading.Thread, daemon=True)
      └── HealthState (shared read-only dari main thread; no lock needed
          karena hanya main thread yang write, health thread hanya read)

Shutdown Coordination:
  └── threading.Event shutdown_event
      ├── Main thread: shutdown_event.wait(timeout=POLL_INTERVAL)
      │   → interruptible sleep; set oleh SIGTERM handler
      └── SessionManager.wait_for_manual_login():
          → shutdown_event.wait(timeout=SESSION_CHECK_INTERVAL)
```

### 6.2 Polling Interval Guarantee

```python
# Jika satu cycle memakan waktu > POLL_INTERVAL:
# → tidak ada overlapping poll, cycle berikutnya langsung dimulai
# (sleep_time = max(0, poll_interval - elapsed) → bisa 0)

cycle_start = time.monotonic()
# ... run full cycle ...
elapsed = time.monotonic() - cycle_start
sleep_time = max(0.0, config.poll_interval - elapsed)
shutdown_event.wait(timeout=sleep_time)  # interruptible sleep
```

### 6.3 Thread Safety

- `HealthState` fields: hanya ditulis oleh main thread, dibaca oleh health thread → `volatile` pattern, tidak butuh `Lock` untuk correctness (Python GIL + simple assignment)
- Database connection: digunakan hanya oleh main thread → `check_same_thread=False` untuk `sqlite3.connect()` tetapi akses selalu dari main thread
- `shutdown_event: threading.Event`: thread-safe by design

---

## 7. Idempotency Keys

| Entitas | Idempotency Key | Strategi |
|---|---|---|
| `tickets` | `nomor_aduan` (UNIQUE constraint) | `INSERT OR IGNORE` → duplikat diabaikan tanpa error |
| `ticket_snapshots` | `(ticket_id, snapshot_hash)` | Cek hash sebelum insert — jangan simpan snapshot identik |
| `ticket_events` | `event_id` (UUID4, UNIQUE) | UUID baru per event — deduplication di level "jangan buat event kalau tidak ada perubahan" |
| `notifications` | `notification_id` (UUID4, UNIQUE) + `event_id` | Satu event → satu notification row. Jika retry, update row yang ada, bukan insert baru |
| `system_events` | Tidak ada unique key — semua system event dicatat | Tidak perlu idempotency; system event adalah audit log |

### 7.1 Notification Idempotency Detail

```
Event E1 dibuat → notification N1 dibuat (PENDING) dalam satu transaction
→ send ke Telegram → SENT → update N1.status = SENT, N1.sent_at = now

Jika crash setelah send berhasil tapi sebelum update status:
→ Pada restart, N1 masih PENDING → retry send
→ Telegram menerima duplikat (AT-LEAST-ONCE)
→ Operator menerima 2 notifikasi untuk 1 event (acceptable per PRD)

Jika crash sebelum send:
→ N1 masih PENDING → retry → kirim satu kali → OK
```

---

## 8. Crash Recovery Analysis

| # | Titik Crash | State Tersimpan | Recovery |
|---|---|---|---|
| CR1 | Sebelum ticket disimpan | Tidak ada | Cycle berikutnya: ticket terdeteksi sebagai "new" → simpan + notif → OK |
| CR2 | Setelah ticket disimpan, sebelum snapshot | `tickets` ada, `ticket_snapshots` kosong | Reconciliation: get_latest_snapshot() → None → perlakukan sebagai snapshot-less → buat snapshot pada perubahan berikutnya |
| CR3 | Setelah snapshot, sebelum event | `tickets` + `snapshot` ada | Reconciliation: bandingkan snapshot terbaru dengan HTS → deteksi perubahan jika ada |
| CR4 | Setelah event, sebelum notification | `ticket_events` ada, `notifications` tidak ada | Reconciliation: event ada tapi notification belum → pada arsitektur ini, notification dibuat dalam transaction yang sama dengan event → tidak mungkin CR4 |
| CR5 | Setelah notification dibuat (PENDING), Telegram belum dikirim | `notifications.status = PENDING` | Restart → `process_pending()` menemukan PENDING → kirim → OK |
| CR6 | Telegram berhasil, belum update status ke SENT | `notifications.status = PENDING`, Telegram sudah terima | Restart → `process_pending()` → retry → duplikat notifikasi (AT-LEAST-ONCE acceptable) |

**Mitigasi CR4**: `ticket + snapshot + event + notification` selalu dalam **satu transaction**. Jika transaksi commit → semua ada. Jika rollback → tidak ada yang tersimpan. Tidak ada state CR4.

**Mitigasi CR2/CR3**: `_handle_existing_ticket()` selalu memanggil `get_latest_snapshot()`. Jika tidak ada snapshot (edge case crash), perlakukan sebagai "tidak ada last_hash" → hash check gagal → field comparison gagal (tidak ada baseline) → log WARNING, skip perubahan sampai snapshot terbentuk pada change berikutnya.

---

## 9. Milestone Plan (M0–M18)

---

### M0 — Project Foundation

#### Objective
Setup repository, Python environment, structure direktori, config loader, logging dasar. Aplikasi dapat start dan validasi config tanpa error.

#### Dependencies
Tidak ada (milestone pertama).

#### Files Created
```
app/__init__.py
app/main.py              (skeleton: load config, setup logging, exit)
app/config.py            (AppConfig dataclass + load_config())
app/utils/__init__.py
app/utils/time_utils.py  (utcnow_iso())
app/utils/log_utils.py   (setup_logging(), SensitiveDataFilter)
.env.example
.gitignore
requirements.txt
requirements-dev.txt
README.md                (draft)
```

#### Implementation Tasks
1. Buat struktur direktori sesuai Section 3
2. Implement `AppConfig` dataclass dengan semua field dan defaults
3. Implement `load_config()` dengan validasi REQUIRED vars dan SystemExit(1) jika missing
4. Implement `setup_logging()` dengan RotatingFileHandler + Console + SensitiveDataFilter
5. Implement `utcnow_iso()` helper
6. `main.py`: load config → setup logging → log "Application starting" → exit(0)
7. Buat `.env.example` lengkap
8. Buat `.gitignore` (`.env`, `data/`, `logs/`, `__pycache__/`, `*.pyc`, `.venv/`)

#### Tests
```
tests/unit/test_config.py:
  - test_load_config_success          : semua var ada → AppConfig valid
  - test_load_config_missing_required : var wajib hilang → SystemExit(1)
  - test_load_config_defaults         : var optional tidak ada → defaults correct
  - test_config_types                 : POLL_INTERVAL="5" → int(5), bukan "5"
  - test_sensitive_filter             : password tidak muncul di log output
```

#### Acceptance Criteria
- `python app/main.py` dengan `.env` valid → exit 0, log "Application starting" tampil
- `python app/main.py` tanpa `.env` → exit 1, pesan error yang jelas
- `pytest tests/unit/test_config.py` → PASS

#### Definition of Done
- [ ] Semua files dibuat
- [ ] `python -m pytest tests/unit/test_config.py` → semua green
- [ ] `python app/main.py` berjalan tanpa error
- [ ] `.env.example` lengkap dan tepat
- [ ] `.gitignore` mencakup `.env` dan `data/`

---

### M1 — Database Foundation

#### Objective
Database SQLite dengan schema lengkap, WAL mode, foreign keys, dan CRUD layer untuk semua tabel.

#### Dependencies
M0 (AppConfig.db_path tersedia)

#### Files Created/Modified
```
app/database/__init__.py
app/database/db.py        (DatabaseManager)
app/database/models.py    (ModelLayer — semua CRUD)
app/database/schema.sql   (DDL lengkap)
tests/unit/test_models.py
tests/integration/test_database.py
```

#### Implementation Tasks
1. Buat `schema.sql` dengan semua CREATE TABLE IF NOT EXISTS dan index
2. Implement `DatabaseManager.connect()`: buka koneksi, PRAGMA WAL + FK + busy_timeout, run schema
3. Implement `DatabaseManager.transaction()` context manager (try/commit, except/rollback/raise)
4. Implement `DatabaseManager.integrity_check()`: `PRAGMA integrity_check` → bool
5. Implement `DatabaseManager.close()`: commit + close
6. Implement `ModelLayer` dengan methods:
   - `insert_ticket(conn, ticket, ...)` → `int` (rowid)
   - `get_ticket(nomor_aduan)` → `sqlite3.Row | None`
   - `update_ticket(conn, ticket, new_hash, last_seen)`
   - `update_last_seen(nomor_aduan)`
   - `count_tickets()` → int
   - `insert_snapshot(conn, ticket_id, nomor_aduan, data, hash, type)` → int
   - `get_latest_snapshot(nomor_aduan)` → `sqlite3.Row | None`
   - `insert_event(conn, **kwargs)` → int
   - `insert_notification(conn, **kwargs)` → int (notification_id)
   - `get_pending_notifications()` → list[sqlite3.Row]
   - `mark_notification_sent(notif_id, telegram_msg_id)`
   - `mark_notification_failed(notif_id, error, next_retry_at)`
   - `mark_notification_exhausted(notif_id)`
   - `count_notifications_by_status(status)` → int
   - `insert_system_event(event_type, description=None, metadata=None)`
7. Pastikan `insert_ticket` menggunakan `INSERT OR IGNORE` untuk idempotency

#### Database Changes
Schema lengkap dibuat: 5 tabel + indexes.

#### Tests
```
tests/unit/test_models.py:
  - test_insert_ticket_new           : insert → row ada di DB
  - test_insert_ticket_duplicate     : INSERT OR IGNORE → tidak error, tidak duplikat
  - test_get_ticket_not_found        : nomor tidak ada → None
  - test_update_last_seen
  - test_insert_snapshot_and_get_latest
  - test_insert_event
  - test_notification_lifecycle      : PENDING → SENT → count check
  - test_transaction_rollback        : exception dalam transaction → rollback

tests/integration/test_database.py:
  - test_db_wal_mode                 : PRAGMA journal_mode → wal
  - test_db_foreign_keys             : PRAGMA foreign_keys → 1
  - test_integrity_check             : DB baru → True
  - test_full_ticket_lifecycle       : insert → snapshot → event → notification (dalam satu transaction)
```

#### Acceptance Criteria
- Database file dibuat otomatis jika tidak ada
- WAL mode aktif
- FK aktif
- Duplicate `nomor_aduan` diabaikan tanpa error
- Semua test pass

#### Definition of Done
- [ ] `pytest tests/unit/test_models.py tests/integration/test_database.py` → green
- [ ] `scripts/init_db.py` dapat menginisialisasi DB kosong

---

### M2 — HTS Connectivity

#### Objective
HTTP client yang dapat berkomunikasi dengan HTS (HTTPS, session reuse, timeout, custom headers). **Belum ada authentication**.

#### Dependencies
M0 (config), M1 (tidak langsung, tapi harus ada)

#### Files Created/Modified
```
app/hts/__init__.py
app/hts/exceptions.py     (HTSError, HTSConnectionError, HTSSessionExpiredError, HTSParseError)
app/hts/client.py         (HTSClient — hanya connectivity, belum auth)
tests/unit/test_client.py
```

#### Implementation Tasks
1. Implement `HTSError` hierarchy di `exceptions.py`
2. Implement `create_http_session(config)` → `requests.Session` dengan headers standard
3. Implement `HTSClient.__init__(config, http_session)`
4. Implement `HTSClient._make_api_request(page, limit, status)` → raw response
5. Implement `is_session_expired(response)` sebagai standalone function
6. Uji konektivitas dengan `requests.head()` ke base URL (tidak perlu login)

#### Tests (via mock — bukan live HTS)
```
tests/unit/test_client.py (menggunakan `responses` library untuk mock HTTP):
  - test_http_session_headers          : User-Agent, X-Requested-With ada
  - test_connection_error_raises       : requests.ConnectionError → HTSConnectionError
  - test_timeout_raises               : requests.Timeout → HTSConnectionError
  - test_session_expired_url_redirect  : response.url endswith '/login' → is_session_expired True
  - test_session_expired_html_content  : body mengandung 'captchaimg' → True
  - test_session_not_expired          : response normal → False
```

#### Acceptance Criteria
- HTTP session dibuat dengan headers yang benar
- Timeout dan connection error dikonversi ke `HTSConnectionError`
- Session expiry detection berfungsi
- Semua test pass via mock (tanpa hit live HTS)

#### Definition of Done
- [ ] `pytest tests/unit/test_client.py` → green
- [ ] Tidak ada live HTTP request dalam test

---

### M3 — HTS Authentication & Session Management

#### Objective
SessionManager yang mengelola login (CAPTCHA alert + manual wait) dan session validity check.

#### Dependencies
M0, M1, M2

#### Files Created/Modified
```
app/hts/session.py
app/hts/parser.py          (minimal: hanya untuk parse CSRF token — TicketData belum)
tests/integration/test_session_manager.py
```

#### Implementation Tasks
1. Implement `SessionState` enum
2. Implement `SessionManager.__init__(config, http, db, notifier_stub)`
   - `notifier_stub`: interface minimal yang bisa di-mock (method `send()`)
3. Implement `_get_csrf_token()`: GET `/`, parse `input[name=csrf_test_name]` via BeautifulSoup
4. Implement `initialize()`:
   - Cek session validity (GET `/list_aduan`)
   - Jika expired/unauthenticated: kirim Telegram CAPTCHA alert, panggil `wait_for_manual_login()`
5. Implement `check_session_validity()`: GET `/list_aduan` → `is_session_expired(resp)` → bool
6. Implement `wait_for_manual_login()`:
   - Loop: `shutdown_event.wait(SESSION_CHECK_INTERVAL)`
   - Setiap `CAPTCHA_REMINDER_INTERVAL` kirim reminder
   - Break ketika `check_session_validity()` → True
7. `_send_captcha_alert()`: rate-limited (cek `_last_captcha_alert`)

#### Tests
```
tests/integration/test_session_manager.py (dengan mock HTTP):
  - test_check_validity_authenticated    : response normal → True
  - test_check_validity_expired          : response redirect ke login → False
  - test_initialize_already_valid        : skip wait, return immediately
  - test_send_captcha_alert_rate_limited : dua panggilan cepat → hanya satu alert terkirim
```

> **CATATAN**: Test `wait_for_manual_login()` memerlukan threading atau timeout kecil. Gunakan mock session yang langsung return valid pada check pertama.

#### Acceptance Criteria
- `initialize()` tidak crash jika session valid
- CAPTCHA alert dikirim saat session tidak valid
- Reminder tidak dikirim terlalu sering

#### Definition of Done
- [ ] `pytest tests/integration/test_session_manager.py` → green
- [ ] Tidak ada real HTTP request dalam test

---

### M4 — HTS Data Parser & Field Mapping

#### Objective
`parse_ticket()` dan `TicketData` lengkap berdasarkan verified JSON field mapping dari TDD.

#### Dependencies
M2

#### Files Modified
```
app/hts/parser.py          (TicketData, parse_ticket(), normalization functions)
tests/unit/test_parser.py
tests/fixtures/            (semua JSON fixtures)
```

#### Implementation Tasks
1. Implement `TicketData` dataclass lengkap (Section 4.1)
2. Implement normalization helpers:
   - `normalize_str(val)` → strip whitespace, konversi None/kosong ke `""`
   - `normalize_opd_induk(val)` → jika `"-"` atau kosong → `None`
   - `normalize_t_solve(val)` → pastikan string
3. Implement `parse_ticket(item: dict) -> TicketData` dengan verified field mapping
4. Implement `parse_api_response(response_json: dict) -> tuple[list[TicketData], dict]`
   → return `(tickets_list, pagination_dict)`
5. Buat semua fixture JSON di `tests/fixtures/`

#### Field Mapping Final (dari TDD §8.4)

| TicketData field | JSON key | Normalisasi |
|---|---|---|
| `nomor_aduan` | `no_trouble` | `str()` |
| `kategori` | `kategori` | `normalize_str()` |
| `sub_kategori` | `sub_kategori` | `normalize_str()` |
| `instansi` | `opd` | `normalize_str()` |
| `opd_induk` | `induk_opd_nama` | `normalize_opd_induk()` |
| `pic_nama` | `pic` | `normalize_str()` |
| `pic_nomor` | `wa` | `normalize_str()` |
| `keluhan` | `keluhan` | `normalize_str()` |
| `tanggal_aduan` | `tgltshoot` | `normalize_str()` |
| `t_solve` | `t_solve` | `normalize_t_solve()` |
| `is_submitted` | `is_submitted` | `int()` |

#### Tests
```
tests/unit/test_parser.py:
  - test_parse_ticket_full            : semua field valid → TicketData correct
  - test_parse_ticket_opd_induk_dash  : induk_opd_nama="-" → opd_induk=None
  - test_parse_ticket_opd_induk_empty : induk_opd_nama="" → opd_induk=None
  - test_parse_ticket_t_solve_zero    : t_solve="0" → is_completed=False
  - test_parse_ticket_t_solve_nonzero : t_solve="1726527600" → is_completed=True
  - test_status_display_pending       : t_solve="0", is_submitted=1 → "Belum Ditangani"
  - test_status_display_completed     : t_solve!=0 → "Sudah Ditangani"
  - test_status_display_unsubmitted   : t_solve="0", is_submitted=0 → "Belum Disubmit"
  - test_to_monitored_dict_keys       : dict has exact expected keys
  - test_parse_missing_optional_field : field tidak ada di JSON → default kosong
```

#### Tests Fixtures
```json
// tests/fixtures/ticket_new.json — satu tiket baru, pending
{
  "no_trouble": "1977-TShoot-2026-jateng-09",
  "kategori": "troubleshoot",
  "sub_kategori": "CORE NETWORK",
  "opd": "Dinas Kesehatan",
  "induk_opd_nama": "-",
  "pic": "Siti Rahayu",
  "wa": "081234567891",
  "keluhan": "Jaringan tidak bisa diakses.",
  "tgltshoot": "2026-09-17",
  "t_solve": "0",
  "is_submitted": 1,
  "created_at": "2026-09-17 09:00:00",
  "updated_at": "2026-09-17 09:00:00"
}
```

#### Acceptance Criteria
- `parse_ticket(fixture_data)` menghasilkan `TicketData` yang tepat
- Normalisasi konsisten untuk semua edge case
- Semua test pass

#### Definition of Done
- [ ] `pytest tests/unit/test_parser.py` → green
- [ ] Semua fixture files dibuat dan valid JSON

---

### M5 — Ticket Fetcher (full fetch pipeline)

#### Objective
`HTSClient.fetch_tickets_page()`, `fetch_all_tickets()`, `fetch_active_tickets()` bekerja penuh dengan pagination dan parsing.

#### Dependencies
M2 (HTTPClient), M4 (parser)

#### Files Modified
```
app/hts/client.py          (fetch_tickets_page, fetch_all_tickets, fetch_active_tickets)
tests/unit/test_client.py  (tambah test untuk fetch methods)
tests/fixtures/api_response_page1.json
tests/fixtures/api_response_empty.json
```

#### Implementation Tasks
1. Implement `fetch_tickets_page(page, limit, status)`:
   - POST ke `/get_aduan_data` dengan JSON payload
   - Header: `Content-Type: application/json`, `X-Requested-With: XMLHttpRequest`
   - Cek `is_session_expired(resp)` → raise `HTSSessionExpiredError`
   - `resp.raise_for_status()`
   - Return raw dict
2. Implement `fetch_all_tickets(status="all")` sebagai generator:
   - Loop halaman 1 → `total_pages`
   - Yield `TicketData` per item
3. Implement `fetch_active_tickets()`:
   - Shortcut: `list(fetch_all_tickets(status="pending"))`

#### Tests
```
tests/unit/test_client.py (mock HTTP):
  - test_fetch_page_success           : response JSON valid → dict
  - test_fetch_page_session_expired   : body='captchaimg' → HTSSessionExpiredError
  - test_fetch_page_http_error        : status 500 → HTTPError
  - test_fetch_all_tickets_pagination : 2 halaman → semua items di-yield
  - test_fetch_all_tickets_empty      : total=0 → empty list
  - test_fetch_active_tickets         : status='pending' dikirim ke API
```

#### Acceptance Criteria
- Pagination berjalan otomatis sampai `total_pages` terpenuhi
- HTSSessionExpiredError raised dengan benar
- Semua test pass via mock

---

### M6 — Initial Sync

#### Objective
Saat pertama kali dijalankan, fetch semua tiket dan simpan ke DB tanpa mengirim notifikasi Telegram.

#### Dependencies
M1 (DB), M4 (parser), M5 (fetcher)

#### Files Created/Modified
```
app/monitoring/__init__.py
app/monitoring/reconciler.py   (run_initial_sync())
tests/integration/test_reconciler.py
```

#### Implementation Tasks
1. Implement `run_initial_sync(client, processor, db)`:
   - `db.models.insert_system_event("INITIAL_SYNC_START")`
   - Set `processor.is_initial_sync = True`
   - Fetch semua tiket `status="all"` (pagination)
   - Untuk setiap tiket: `processor.process(ticket, sync_source="initial")`
   - `db.models.insert_system_event("INITIAL_SYNC_COMPLETE", metadata={count})`
   - Set `processor.is_initial_sync = False`
   - Return count
2. `TicketProcessor` stub: hanya `is_initial_sync` flag, `process()` stub untuk test
3. Pastikan idempotent: jika restart di tengah sync, tiket yang sudah ada tidak duplikat

#### Tests
```
tests/integration/test_reconciler.py:
  - test_initial_sync_stores_tickets     : 10 mock tickets → 10 rows di DB
  - test_initial_sync_no_notifications   : 10 tickets → 0 rows di notifications
  - test_initial_sync_idempotent         : jalankan 2x → tetap 10 rows (tidak duplikat)
  - test_initial_sync_system_events      : INITIAL_SYNC_START + COMPLETE di system_events
```

#### Acceptance Criteria
- 0 notifikasi dikirim/dibuat selama initial sync
- Tiket tersimpan dengan `sync_source='initial'`
- Idempotent (restart aman)

---

### M7 — Change Detector

#### Objective
`detect_changes()` yang menghasilkan `ChangeResult` dengan `changed_fields` dict.

#### Dependencies
M4 (TicketData)

#### Files Created/Modified
```
app/monitoring/change_detector.py
tests/unit/test_change_detector.py
```

#### Implementation Tasks
1. Implement `compute_hash(ticket: TicketData) -> str`
   - `json.dumps(ticket.to_monitored_dict(), sort_keys=True, ensure_ascii=False)`
   - `hashlib.sha256(data.encode()).hexdigest()`
2. Implement `ChangeResult` dataclass
3. Implement `detect_changes(current, last_snapshot_json, was_completed) -> ChangeResult`:
   - Parse `last_snapshot_json`
   - Bandingkan setiap field dari `MONITORED_FIELDS`
   - Isi `changed_fields`
   - Set `is_completed` jika `t_solve` baru berubah ke non-zero DAN `was_completed=False`
4. Define `MONITORED_FIELDS` list (deterministik — alphabet order)

#### Tests
```
tests/unit/test_change_detector.py:
  - test_compute_hash_deterministic    : panggil 2x → hash sama
  - test_compute_hash_different        : field berbeda → hash berbeda
  - test_detect_no_change              : same data → is_changed=False, changed_fields={}
  - test_detect_keluhan_changed        : keluhan berubah → is_changed=True, changed_fields correct
  - test_detect_multiple_fields        : kategori + pic_nama → 2 entries di changed_fields
  - test_detect_completed              : t_solve berubah dari '0' → is_completed=True
  - test_detect_already_completed      : was_completed=True → is_completed=False (tidak duplikat)
  - test_detect_pic_changed            : pic_nomor berubah → terdeteksi
  - test_detect_all_fields_unchanged   : identical dict → is_changed=False
```

#### Acceptance Criteria
- `compute_hash()` deterministik
- `changed_fields` format: `{"field": {"old": ..., "new": ...}}`
- `is_completed` hanya True sekali (was_completed guard)
- Semua test pass

---

### M8 — Event Processing (TicketProcessor)

#### Objective
`TicketProcessor` yang mengintegrasikan change detection, DB persistence, dan notification queueing dalam satu transaction atomik.

#### Dependencies
M1 (DB), M6 (initial sync flag), M7 (change detector)

#### Files Created/Modified
```
app/monitoring/ticket_processor.py
tests/integration/test_ticket_processor.py
```

#### Implementation Tasks
1. Implement `TicketProcessor.__init__(db, notif_queue, is_initial_sync=False)`
2. Implement `process(ticket, sync_source)`:
   - Query `get_ticket(nomor_aduan)`
   - Dispatch ke `_handle_new()` atau `_handle_existing()`
3. Implement `_handle_new_ticket(ticket, sync_source)`:
   - transaction: insert ticket → insert snapshot → (jika tidak initial_sync) insert event + queue notification
4. Implement `_handle_existing_ticket(ticket, existing)`:
   - Quick hash check → update last_seen dan return jika sama
   - Detect changes
   - transaction: update ticket + insert snapshot + insert TICKET_CHANGED event + queue notif
   - Jika baru completed: insert COMPLETED event + queue notif
5. Implement `NotificationQueue.queue()` (versi minimal, DB-only, belum kirim Telegram)

#### Tests
```
tests/integration/test_ticket_processor.py:
  - test_process_new_ticket_monitoring   : new ticket → ticket row + snapshot + NEW_TICKET event + notification
  - test_process_new_ticket_initial_sync : new ticket + is_initial_sync → NO event, NO notification
  - test_process_unchanged_ticket        : same hash → only last_seen updated
  - test_process_changed_keluhan         : keluhan berubah → TICKET_CHANGED event, changed_fields correct
  - test_process_completed               : t_solve berubah → TICKET_CHANGED + COMPLETED event
  - test_process_no_duplicate_completed  : ticket sudah is_completed=1 → tidak buat COMPLETED lagi
  - test_process_atomicity               : DB error di tengah transaction → rollback, tidak ada partial state
```

#### Acceptance Criteria
- Semua DB writes dalam satu transaction
- Notification dibuat di DB (belum kirim ke Telegram)
- Tidak ada duplikat COMPLETED event

---

### M9 — Telegram Notifier & Templates

#### Objective
`TelegramNotifier.send()` dan semua template formatter untuk ticket events dan system alerts.

#### Dependencies
M0 (config)

#### Files Created/Modified
```
app/notifications/__init__.py
app/notifications/telegram.py    (TelegramNotifier, exceptions)
app/notifications/templates.py   (semua format_*() functions)
tests/unit/test_templates.py
tests/unit/test_telegram.py
```

#### Implementation Tasks
1. Implement `TelegramError(status_code, body)` dan `TelegramRateLimitError(retry_after)`
2. Implement `TelegramNotifier.send(text) -> str`:
   - POST ke `api.telegram.org/bot{token}/sendMessage`
   - Handle 429 → `TelegramRateLimitError`
   - Handle 400 → `TelegramError(400, ...)`
   - Handle other errors → `TelegramError`
   - **Token tidak pernah di-log**
3. Implement semua template functions (lihat TDD §12.1):
   - `format_new_ticket(ticket) -> str`
   - `format_changed_ticket(ticket, changed_fields) -> str`
   - `format_completed_ticket(ticket) -> str`
   - `format_hts_down(timestamp) -> str`
   - `format_hts_recovered(timestamp) -> str`
   - `format_captcha_alert(timestamp, base_url) -> str`
   - `format_session_expired(timestamp, base_url) -> str`
4. Implement `_truncate(text, max_len)` dan `_format_pic(nama, nomor)`
5. Pastikan semua template menghasilkan plain text (tanpa Markdown/HTML)

#### Tests
```
tests/unit/test_templates.py:
  - test_format_new_ticket_full        : semua field ada → string sesuai template
  - test_format_new_ticket_no_opd      : opd_induk=None → baris OPD kosong
  - test_format_new_ticket_no_pic_nomor: pic_nomor="" → "Nama Saja" tanpa kurung
  - test_format_changed_ticket
  - test_format_completed_ticket
  - test_keluhan_truncation            : keluhan >1000 char → dipotong + "..."
  - test_format_captcha_alert          : timestamp dan URL ada di output
  - test_message_within_telegram_limit : len(output) <= 4096

tests/unit/test_telegram.py (mock HTTP):
  - test_send_success                  : 200 → return message_id
  - test_send_rate_limit               : 429 → TelegramRateLimitError(retry_after=60)
  - test_send_bad_request              : 400 → TelegramError(400)
  - test_send_server_error             : 500 → TelegramError(500)
  - test_token_not_in_log              : mock logger → token tidak muncul
```

#### Acceptance Criteria
- Template output sesuai PRD §17.1–17.3 secara literal
- Telegram token tidak pernah di-log
- Semua test pass

---

### M10 — Notification Reliability (Queue + Retry)

#### Objective
`NotificationQueue` dengan AT-LEAST-ONCE delivery, exponential backoff retry, dan deduplication.

#### Dependencies
M1 (DB), M9 (TelegramNotifier)

#### Files Created/Modified
```
app/notifications/queue.py
tests/unit/test_notification_queue.py
```

#### Implementation Tasks
1. Implement `NotificationQueue.__init__(db, config)`
2. Implement `queue(conn, event_id, message) -> str`:
   - Insert row ke `notifications` dengan status=PENDING dalam transaction caller
3. Implement `queue_system_alert(alert_type, message) -> str`:
   - Insert dengan `event_id=None` (system notification)
   - Dalam transaction sendiri
4. Implement `process_pending(notifier)`:
   - Query: `SELECT ... WHERE status IN ('PENDING', 'FAILED') AND (next_retry_at IS NULL OR next_retry_at <= now) ORDER BY created_at ASC`
   - Untuk setiap notif:
     - `notifier.send(message_text)`
     - Success: `mark_notification_sent()`
     - `TelegramRateLimitError`: `mark_notification_failed(delay=e.retry_after)`
     - `TelegramError(400)`: `mark_notification_exhausted()`
     - Other: `mark_notification_failed(delay=_next_delay(attempt_count))`
5. Implement `_next_delay(attempt: int) -> int`: `[15, 60, 300, 1800][min(attempt, 3)]`
6. Implement `flush(timeout=10)`: process pending dengan timeout

#### Tests
```
tests/unit/test_notification_queue.py:
  - test_queue_creates_pending_row       : queue() → status=PENDING di DB
  - test_process_pending_success         : mock send ok → status=SENT
  - test_process_pending_rate_limit      : TelegramRateLimitError → status=FAILED, next_retry_at set
  - test_process_pending_bad_request     : TelegramError(400) → status=RETRY_EXHAUSTED
  - test_process_pending_retry_delay     : attempt=0 → 15s, attempt=1 → 60s, attempt=3+ → 1800s
  - test_process_pending_respects_retry_at: notif dengan next_retry_at di masa depan → di-skip
  - test_at_least_once                  : crash setelah send → notif masih PENDING → retry → duplikat (expected)
```

---

### M11 — Polling Engine

#### Objective
Main polling loop dengan configurable interval, no overlapping, graceful cancellation, dan session check.

#### Dependencies
M0, M3 (session), M5 (fetcher), M8 (processor), M10 (queue), M9 (notifier)

#### Files Created/Modified
```
app/monitoring/orchestrator.py    (polling loop logic — belum state machine penuh)
tests/integration/              (polling integration test dengan mock)
```

#### Implementation Tasks
1. Implement satu polling cycle sebagai method `_run_one_cycle()`:
   - check_session_validity
   - fetch_active_tickets
   - process setiap ticket
   - process_pending
   - update health state
2. Implement `_monitoring_loop()` dengan:
   - `while not shutdown_event.is_set()`
   - Record `cycle_start = time.monotonic()`
   - Call `_run_one_cycle()`
   - `elapsed = time.monotonic() - cycle_start`
   - `shutdown_event.wait(timeout=max(0, poll_interval - elapsed))`
3. Pastikan: tidak ada overlapping (synchronous — by design)
4. Logging: setiap cycle log duration dan jumlah tiket

#### Tests
```
tests/integration/test_polling.py:
  - test_single_cycle_completes      : mock semua dependency → cycle selesai tanpa error
  - test_no_overlapping_poll         : slow fetch (mock) → next cycle mulai setelah yang pertama selesai
  - test_shutdown_during_sleep       : set shutdown_event → loop exit
  - test_session_expired_in_cycle    : HTSSessionExpiredError raised → loop memanggil handler
```

---

### M12 — Reconciliation

#### Objective
`run_reconciliation()` yang membandingkan state DB dengan HTS dan menghasilkan events untuk perbedaan yang ditemukan.

#### Dependencies
M5 (fetcher), M7 (change detector), M8 (processor), M1 (DB)

#### Files Modified
```
app/monitoring/reconciler.py    (run_reconciliation() — full implementation)
tests/integration/test_reconciler.py  (tambah reconciliation tests)
```

#### Implementation Tasks
1. Implement `run_reconciliation(client, db, processor) -> dict`:
   - Insert RECONCILIATION_START system event
   - Fetch `status="all"` dengan pagination
   - Untuk setiap ticket: bandingkan dengan DB (new / changed / unchanged)
   - Track stats
   - Insert RECONCILIATION_COMPLETE + metadata
2. Gunakan `processor.process()` untuk new/changed (bukan is_initial_sync)

#### Tests
```
tests/integration/test_reconciler.py:
  - test_reconcile_new_tickets          : 2 tiket baru di HTS → NEW_TICKET events + notifications
  - test_reconcile_changed_tickets      : 1 tiket berubah → TICKET_CHANGED event
  - test_reconcile_unchanged_tickets    : semua sama → hanya last_seen diupdate
  - test_reconcile_mixed               : new + changed + unchanged dalam satu run
  - test_reconcile_system_events       : RECONCILIATION_START + COMPLETE tersimpan
  - test_reconcile_stats               : return stats dict correct
```

---

### M13 — Full State Machine & Failure Handling

#### Objective
Orchestrator dengan state machine penuh: MONITORING, SESSION_EXPIRED, CAPTCHA_REQUIRED, HTS_UNAVAILABLE, SHUTTING_DOWN.

#### Dependencies
M11 (polling loop), M12 (reconciliation), M3 (session), M10 (notification queue)

#### Files Modified
```
app/monitoring/orchestrator.py    (full Orchestrator class)
app/main.py                       (wiring semua komponen)
```

#### Implementation Tasks
1. Implement `AppState` enum
2. Implement `Orchestrator` class dengan:
   - `shutdown_event: threading.Event`
   - `_handle_sigterm(signum, frame)`: set shutdown_event
   - `_transition(new_state)`: log + update health_state
   - `_handle_session_expired()`: state = CAPTCHA_REQUIRED, wait_for_manual_login, reconcile, MONITORING
   - `_handle_hts_unavailable(error)`: satu alert per incident, exponential backoff, recovery, reconcile
   - `run()`: full lifecycle: init → initial_sync → reconcile → monitoring_loop
3. `_hts_unavailable_notified: bool` flag untuk anti-spam alert
4. Exponential backoff: `min(initial * 2^(failures-1), max_delay)`
5. Signal handlers: `SIGTERM` dan `SIGINT`

#### Tests
```
tests/integration/test_orchestrator.py:
  - test_hts_unavailable_sends_one_alert     : 3 consecutive errors → 1 Telegram alert
  - test_hts_recovery_sends_recovery_alert   : setelah unavailable, HTS OK → recovery notification
  - test_session_expired_sends_captcha_alert
  - test_state_transitions                   : log setiap transition
  - test_shutdown_during_hts_unavailable     : shutdown_event → loop exit
```

---

### M14 — Health Endpoint

#### Objective
`GET /health` HTTP endpoint di thread daemon, dengan HealthState yang di-update oleh main loop.

#### Dependencies
M0 (config), M13 (state machine untuk state updates)

#### Files Created/Modified
```
app/health/__init__.py
app/health/endpoint.py    (HealthState, HealthHTTPHandler, start_health_server())
tests/unit/test_health.py
```

#### Implementation Tasks
1. Implement `HealthState` dataclass (semua fields — lihat TDD §13.1)
2. Implement `HealthHTTPHandler(BaseHTTPRequestHandler)`:
   - GET `/health` → JSON response
   - GET selain `/health` → 404
   - Suppress access log (`log_message` override)
   - Determine status: healthy / degraded / unhealthy
   - **Tidak mengekspos credentials atau session cookies**
3. Implement `start_health_server(config, health_state) -> threading.Thread`:
   - `HTTPServer` bind ke `config.health_host:config.health_port`
   - Inject `health_state` ke server
   - Thread daemon, start, return thread

#### Tests
```
tests/unit/test_health.py:
  - test_health_healthy               : semua normal → 200 "healthy"
  - test_health_degraded              : failed_notifications > 0 → 200 "degraded"
  - test_health_unhealthy_hts_down    : app_state=HTS_UNAVAILABLE → 503 "unhealthy"
  - test_health_no_credentials        : response tidak mengandung token/password
  - test_health_404_other_path        : GET /other → 404
  - test_health_response_json_valid   : response parseable JSON
```

---

### M15 — Backup

#### Objective
Standalone backup script dengan hot backup, retention, dan integrity check.

#### Dependencies
M1 (tahu struktur DB)

#### Files Created/Modified
```
backup/backup.py
tests/unit/test_backup.py
```

#### Implementation Tasks
1. Implement `backup(db_path, backup_dir, retain_days)`:
   - `Path(backup_dir).mkdir(parents=True, exist_ok=True)`
   - SQLite Python backup API (hot backup, aman saat DB berjalan)
   - `os.chmod(backup_path, 0o600)`
   - `PRAGMA integrity_check` pada backup
   - Hapus backup lebih tua dari `retain_days`
2. `__main__`: load dotenv, baca config, call backup()
3. Tulis ke `system_events` setelah backup berhasil (opsional: perlu DB connection)

#### Tests
```
tests/unit/test_backup.py:
  - test_backup_creates_file          : backup() → file ada
  - test_backup_integrity_check       : backup valid → tidak dihapus
  - test_backup_retention             : file lama > retain_days → dihapus
  - test_backup_permission            : file permission 0o600
```

---

### M16 — Testing Completion

#### Objective
Lengkapi semua test yang belum dibuat di milestone sebelumnya. Target: >90% coverage.

#### Dependencies
Semua milestone sebelumnya

#### Implementation Tasks
1. Tulis test yang terlewat dari tiap milestone
2. Jalankan `pytest --cov=app tests/` → lihat coverage report
3. Tambah test untuk edge cases dari PRD §29:
   - EC-01 tiket hilang dari HTS
   - EC-04 keluhan >10.000 karakter
   - EC-05 polling lambat
   - EC-09 Telegram rate limit
4. Jalankan seluruh test suite dan pastikan PASS

---

### M17 — PM2 Deployment

#### Objective
`ecosystem.config.js` lengkap, setup PM2, verifikasi start/stop/restart dan crash recovery.

#### Dependencies
Semua milestone sebelumnya

#### Files Created/Modified
```
ecosystem.config.js
scripts/check_health.sh
README.md                    (update dengan deployment instructions)
```

#### Implementation Tasks
1. Buat `ecosystem.config.js` (lihat §15)
2. Test manual: `pm2 start ecosystem.config.js`
3. Test crash recovery: kill -9 process → PM2 restart → reconciliation terjadi
4. Test graceful shutdown: `pm2 stop` → SIGTERM → exit 0
5. Test memory restart: jalankan dengan `max_memory_restart: 256M`
6. Buat `scripts/check_health.sh`:
   ```bash
   #!/bin/bash
   curl -s http://127.0.0.1:8080/health | python3 -m json.tool
   ```

---

### M18 — Production Hardening

#### Objective
Review keamanan, performa, reliability sebelum go-live. Tidak ada fitur baru — hanya verifikasi dan perbaikan.

#### Checklist
- [ ] File permissions: `.env` = 600, `data/*.db` = 600, `logs/*.log` = 640
- [ ] Tidak ada credential hardcoded di kode
- [ ] Log test: jalankan aplikasi dengan LOG_LEVEL=DEBUG → verifikasi tidak ada password/token
- [ ] Semua timeout sudah dikonfigurasi (request_timeout, session_check_interval)
- [ ] Backup script terdaftar di cron
- [ ] `pm2 save` dan `pm2 startup` sudah dijalankan
- [ ] Health endpoint hanya bisa diakses dari localhost
- [ ] `PRAGMA integrity_check` berjalan pada startup
- [ ] Semua edge case dari PRD §29 ter-handle
- [ ] `pytest tests/` → semua PASS
- [ ] README lengkap dan akurat

---

## 10. Dependency Graph

```
M0 (Foundation)
│
├── M1 (Database)
│   ├── M6 (Initial Sync)
│   ├── M8 (Event Processing) ──────────────────┐
│   │   └── M11 (Polling Engine)                │
│   │       └── M12 (Reconciliation)            │
│   │           └── M13 (State Machine) ────────┤
│   │               ├── M14 (Health)            │
│   │               ├── M17 (PM2)               │
│   │               └── M18 (Hardening)         │
│   └── M10 (Notification Queue)                │
│       └── connects to M9 ─────────────────────┤
│                                               │
├── M2 (HTS Connectivity)                       │
│   ├── M3 (Authentication)                     │
│   │   └── feeds into M13 ────────────────────►┤
│   ├── M4 (Parser)                             │
│   │   └── M5 (Fetcher)                        │
│   │       ├── M6 ──────────────────────────►──┤
│   │       └── M12 ────────────────────────────┤
│   └── M7 (Change Detector)                    │
│       └── M8 ──────────────────────────────►──┘
│
├── M9 (Telegram + Templates)
│   └── M10
│
└── M15 (Backup)
    └── M16 (Testing)
        └── M17
            └── M18
```

**Critical Path**: M0 → M1 → M2 → M4 → M7 → M8 → M13 → M17 → M18

---

## 11. Testing Strategy & Matrix

### 11.1 Test Levels

| Level | Scope | Tools | Live HTS? | Live Telegram? |
|---|---|---|---|---|
| Unit | Satu function/method | `pytest`, `pytest-mock` | ❌ | ❌ |
| Integration | Beberapa modules + SQLite real | `pytest`, `responses` | ❌ | ❌ |
| E2E | Full application | Manual + `pm2` | ✅ (manual) | ✅ (test bot) |

### 11.2 Testing Matrix

| Skenario | Module Under Test | Expected Result |
|---|---|---|
| First startup, DB kosong, INITIAL_SYNC=true | reconciler, ticket_processor | Semua tiket tersimpan, 0 notifikasi |
| First startup, existing tickets | reconciler | Tiket lama di-load, no notification |
| New ticket muncul saat polling | ticket_processor | NEW_TICKET event + 1 notifikasi PENDING |
| Ticket tidak berubah | change_detector | last_seen diupdate, no event |
| `keluhan` berubah | change_detector, ticket_processor | TICKET_CHANGED event, changed_fields berisi keluhan |
| `pic_nama` berubah | change_detector | TICKET_CHANGED, changed_fields berisi pic_nama |
| `kategori` berubah | change_detector | TICKET_CHANGED |
| `opd_induk` berubah | change_detector | TICKET_CHANGED |
| `t_solve` 0 → non-zero | ticket_processor | TICKET_CHANGED + COMPLETED event |
| Multiple fields berubah sekaligus | change_detector | Satu TICKET_CHANGED dengan semua changed_fields |
| Application crash (kill -9) | orchestrator + reconciler | PM2 restart → reconciliation |
| PM2 restart | orchestrator | Reconciliation, monitoring lanjut |
| WSL restart | orchestrator + pm2 | `pm2 resurrect` → boot otomatis |
| HTS timeout | orchestrator | HTSConnectionError → HTS_UNAVAILABLE state, 1 alert |
| HTS 500 | HTSClient | HTTPError → HTSConnectionError |
| Session expired mid-polling | session, orchestrator | CAPTCHA alert, wait_for_manual_login() |
| CAPTCHA required on startup | session | CAPTCHA alert, wait |
| Telegram timeout | telegram | requests.Timeout → TelegramError → retry |
| Telegram 429 | telegram | TelegramRateLimitError → wait retry_after |
| Database write failure | models | Log error, rollback, skip cycle |
| Malformed HTS JSON response | client, parser | HTSParseError → log, skip cycle |
| Duplicate `nomor_aduan` di response | ticket_processor | INSERT OR IGNORE, log warning |
| `keluhan` > 10.000 karakter | templates | Truncated di notif, full di DB |

---

## 12. Mocking Strategy

### 12.1 Mock HTS (`responses` library)

```python
# tests/conftest.py

import responses as resp_mock
import pytest

@pytest.fixture
def mock_hts_api():
    """Mock untuk API endpoint /get_aduan_data."""
    with resp_mock.RequestsMock() as rsps:
        rsps.add(
            resp_mock.POST,
            "https://hts.diskomdigi.jatengprov.go.id/get_aduan_data",
            json={"data": [...], "pagination": {"page": 1, "limit": 50, "total": 1, "total_pages": 1}},
            status=200
        )
        yield rsps

@pytest.fixture
def mock_hts_session_expired():
    """Mock session expired response."""
    with resp_mock.RequestsMock() as rsps:
        rsps.add(
            resp_mock.POST,
            "https://hts.diskomdigi.jatengprov.go.id/get_aduan_data",
            body='<input id="userEmail">',
            status=200
        )
        yield rsps

@pytest.fixture
def mock_hts_timeout():
    with resp_mock.RequestsMock() as rsps:
        rsps.add(
            resp_mock.POST,
            "https://hts.diskomdigi.jatengprov.go.id/get_aduan_data",
            body=requests.exceptions.Timeout()
        )
        yield rsps
```

### 12.2 Mock Telegram

```python
@pytest.fixture
def mock_telegram_success():
    with resp_mock.RequestsMock() as rsps:
        rsps.add(
            resp_mock.POST,
            "https://api.telegram.org/botTOKEN/sendMessage",
            json={"ok": True, "result": {"message_id": 42}},
            status=200
        )
        yield rsps

@pytest.fixture
def mock_telegram_rate_limit():
    with resp_mock.RequestsMock() as rsps:
        rsps.add(
            resp_mock.POST,
            "https://api.telegram.org/botTOKEN/sendMessage",
            status=429,
            headers={"Retry-After": "60"}
        )
        yield rsps
```

### 12.3 Test Database

```python
@pytest.fixture
def test_db(tmp_path):
    """In-memory atau tmp file SQLite untuk setiap test."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    db.connect()
    yield db
    db.close()
```

---

## 13. Test Fixtures

### `tests/fixtures/api_response_page1.json`
```json
{
  "data": [
    {
      "id_trouble": 1977,
      "no_trouble": "1977-TShoot-2026-jateng-09",
      "kategori": "troubleshoot",
      "sub_kategori": "CORE NETWORK",
      "opd": "Dinas Kesehatan",
      "induk_opd_nama": "-",
      "pic": "Siti Rahayu",
      "wa": "081234567891",
      "keluhan": "Jaringan tidak bisa diakses.",
      "tgltshoot": "2026-09-17",
      "t_solve": "0",
      "is_submitted": 1,
      "created_at": "2026-09-17 09:00:00",
      "updated_at": "2026-09-17 09:00:00"
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 50,
    "total": 1,
    "total_pages": 1
  }
}
```

### `tests/fixtures/ticket_completed.json`
```json
{
  "no_trouble": "1977-TShoot-2026-jateng-09",
  "kategori": "troubleshoot",
  "sub_kategori": "CORE NETWORK",
  "opd": "Dinas Kesehatan",
  "induk_opd_nama": "-",
  "pic": "Siti Rahayu",
  "wa": "081234567891",
  "keluhan": "Jaringan tidak bisa diakses.",
  "tgltshoot": "2026-09-17",
  "t_solve": "1726527600",
  "is_submitted": 1,
  "created_at": "2026-09-17 09:00:00",
  "updated_at": "2026-09-17 12:00:00"
}
```

### `tests/fixtures/ticket_invalid.json`
```json
{
  "no_trouble": null,
  "kategori": null
}
```

---

## 14. Deployment Plan

```bash
# 1. Clone / copy project ke server
git clone <repo> /opt/hts-aduan-bot
cd /opt/hts-aduan-bot

# 2. Python virtual environment
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 3. Configure .env
cp .env.example .env
chmod 600 .env
nano .env   # isi HTS_USERNAME, HTS_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# 4. Initialize database
mkdir -p data/backups logs
.venv/bin/python scripts/init_db.py

# 5. Test HTS connectivity (bukan login — hanya network)
curl -I https://hts.diskomdigi.jatengprov.go.id

# 6. Test Telegram
.venv/bin/python -c "
from app.config import load_config
from app.notifications.telegram import TelegramNotifier
cfg = load_config()
n = TelegramNotifier(cfg)
print(n.send('Test dari HTS Monitor — setup berhasil'))
"

# 7. Start via PM2
npm install -g pm2
pm2 start ecosystem.config.js

# 8. Verify health
bash scripts/check_health.sh

# 9. PM2 startup (auto-start saat reboot)
pm2 save
pm2 startup   # ikuti instruksi yang ditampilkan

# 10. Setup backup cron
crontab -e
# Tambahkan:
# 0 19 * * * cd /opt/hts-aduan-bot && .venv/bin/python backup/backup.py >> logs/backup.log 2>&1

# 11. Verify logs
pm2 logs hts-ticket-monitor --lines 50
```

**Catatan WSL**: `pm2 startup` tidak menggunakan systemd di WSL2. Gunakan Windows Task Scheduler atau Windows Service untuk auto-start WSL + PM2 saat boot, atau jalankan manual setelah WSL start.

---

## 15. PM2 Configuration

### `ecosystem.config.js`
```javascript
module.exports = {
  apps: [{
    name: 'hts-ticket-monitor',

    // Gunakan Python dari venv langsung sebagai interpreter
    script: '.venv/bin/python',
    args: 'app/main.py',
    cwd: '/opt/hts-aduan-bot',
    interpreter: 'none',

    // Jangan watch file — ini daemon
    watch: false,

    // Restart policy
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',      // Restart tidak dihitung jika crash dalam 10s
    restart_delay: 5000,    // 5s delay sebelum restart

    // Memory guard
    max_memory_restart: '256M',

    // Logs
    out_file: 'logs/pm2-out.log',
    error_file: 'logs/pm2-error.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: false,

    // Graceful shutdown
    kill_timeout: 30000,    // 30s untuk SIGTERM → selesaikan cycle

    // Environment
    env: {
      PYTHONUNBUFFERED: '1',
      // JANGAN masukkan credential di sini
      // Credential dibaca dari .env oleh python-dotenv
    }
  }]
};
```

---

## 16. Backup Plan

### Crontab Setup
```bash
# Edit crontab:
crontab -e

# Backup setiap hari pukul 02:00 WIB (UTC+7 = 19:00 UTC)
0 19 * * * cd /opt/hts-aduan-bot && .venv/bin/python backup/backup.py >> logs/backup.log 2>&1
```

### Manual Backup
```bash
.venv/bin/python backup/backup.py
```

### Restore dari Backup
```bash
# Stop app
pm2 stop hts-ticket-monitor

# Cek integrity backup
sqlite3 data/backups/hts_monitor_YYYYMMDD_HHMMSS.db "PRAGMA integrity_check;"

# Restore
cp data/backups/hts_monitor_YYYYMMDD_HHMMSS.db data/hts_monitor.db

# Restart
pm2 start hts-ticket-monitor
```

---

## 17. Security Checklist

| Item | Requirement | Cara Verifikasi |
|---|---|---|
| SC-01 | `.env` tidak di git | `git ls-files .env` → kosong |
| SC-02 | `.env` permission 600 | `ls -la .env` → `-rw-------` |
| SC-03 | DB permission 600 | `ls -la data/hts_monitor.db` → `-rw-------` |
| SC-04 | Backup permission 600 | `ls -la data/backups/*.db` → `-rw-------` |
| SC-05 | Password tidak di log | Grep log files untuk credential |
| SC-06 | Telegram token tidak di log | `grep "TOKEN_VALUE" logs/` → tidak ada |
| SC-07 | Health endpoint tidak ekspos credential | `curl /health` → tidak ada password/token |
| SC-08 | Health endpoint hanya localhost | `HEALTH_HOST=127.0.0.1` di .env |
| SC-09 | Tidak ada CAPTCHA bypass | Tidak ada kode yang mengirim ke CAPTCHA solver |
| SC-10 | Semua credential dari .env | `grep -r "password\|token\|secret" app/` → tidak ada hardcode |
| SC-11 | requirements.txt pinned versions | Semua versi eksplisit |
| SC-12 | Session cookie tidak di log | SensitiveDataFilter aktif |

---

## 18. Coding Conventions

### 18.1 General
- Python 3.11+ type hints di semua function signature
- Docstring untuk semua public method (satu baris untuk simple, multiline untuk complex)
- `black` formatting (opsional, tapi konsisten)
- Max line length: 100 characters
- Semua konstanta di uppercase: `MONITORED_FIELDS`, `MAX_TELEGRAM_MESSAGE_LENGTH`

### 18.2 Error Handling
```python
# ❌ DILARANG
try:
    do_something()
except:
    pass

# ✅ BENAR
try:
    do_something()
except HTSConnectionError as e:
    logger.error(f"HTS connection failed: {e}")
    raise

# ✅ BENAR — jika benar-benar ingin continue
try:
    do_something()
except SomeSpecificError as e:
    logger.warning(f"Non-critical error, continuing: {e}")
    # explicitly continue
```

### 18.3 Logging
```python
# ❌ DILARANG
logger.info(f"Password: {config.hts_password}")
logger.debug(f"Cookie: {response.cookies}")

# ✅ BENAR
logger.info("HTS login attempt")
logger.debug(f"Fetch page {page} of {total_pages}")
logger.error(f"Telegram send failed: HTTP {status_code}")  # tanpa token
```

### 18.4 Database
```python
# Selalu gunakan transaction untuk multi-step writes
with db.transaction() as conn:
    ticket_id = db.models.insert_ticket(conn, ticket, ...)
    snapshot_id = db.models.insert_snapshot(conn, ticket_id, ...)
    db.models.insert_event(conn, ..., current_snapshot_id=snapshot_id)
    db.models.insert_notification(conn, ...)
# Commit otomatis di akhir with block
```

### 18.5 Import Organization
```python
# stdlib
import os, sys, json, hashlib, uuid, time, signal, threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from http.server import BaseHTTPRequestHandler, HTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Generator

# third-party
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# local
from app.config import AppConfig
from app.database.db import DatabaseManager
```

---

## 19. AI Coding Agent Rules

1. **Read PRD first** (`prd-hts-aduan.md`)
2. **Read TDD second** (`tdd-hts-aduan.md`)
3. **Read Implementation Plan** (dokumen ini)
4. **Read current repository state** sebelum mulai coding
5. **Never overwrite working functionality** tanpa alasan eksplisit
6. **Do not invent HTS API** — gunakan hanya endpoint dan field yang terverifikasi di TDD §1.1
7. **Do not bypass CAPTCHA** — tidak ada kode yang mengirim ke solver eksternal
8. **Do not hardcode secrets** — semua dari `.env` melalui `AppConfig`
9. **Write tests** — setiap milestone memiliki test requirement
10. **Run tests after changes** — `pytest tests/` harus green sebelum milestone dianggap selesai
11. **Report failures honestly** — jangan mark incomplete sebagai complete
12. **Keep changes scoped to milestone** — jangan refactor M1 saat mengerjakan M5
13. **Do not refactor unrelated code** — single responsibility
14. **Update documentation when architecture changes** — README dan docstring
15. **Never mark incomplete functionality as complete** dalam laporan
16. **If blocked by TBD** — stop dan report: `"TBD — HTS VERIFICATION REQUIRED: [describe what is needed]"`
17. **No fake endpoints** — jangan buat mock yang kemudian dianggap production-ready
18. **All exceptions must be caught specifically** — tidak ada bare `except:`
19. **Sensitive data never in logs** — filter aktif + coding discipline
20. **Each transaction is atomic** — ticket + snapshot + event + notification dalam satu `with db.transaction()`

---

## 20. AI Coding Agent Prompts (per Milestone)

---

### Prompt M0 — Project Foundation

```
You are implementing Milestone M0 (Project Foundation) for the HTS Ticket Monitor project.

SOURCE OF TRUTH:
- PRD: prd-hts-aduan.md
- TDD: tdd-hts-aduan.md
- Implementation Plan: implementation-plan.md (this document's parent)

SCOPE:
Create the foundational project structure. No database, no HTS client, no Telegram.

FILES TO CREATE:
- app/__init__.py (empty)
- app/main.py (skeleton: load config, setup logging, log "Application starting", exit 0)
- app/config.py (AppConfig dataclass + load_config())
- app/utils/__init__.py (empty)
- app/utils/time_utils.py (utcnow_iso() only)
- app/utils/log_utils.py (setup_logging() + SensitiveDataFilter)
- .env.example (complete, all variables)
- .gitignore
- requirements.txt (pinned versions)
- requirements-dev.txt (pytest, pytest-mock, responses)
- README.md (skeleton)
- tests/__init__.py
- tests/unit/__init__.py
- tests/unit/test_config.py

DO NOT CREATE: database, hts, monitoring, notifications, health modules.

TESTS REQUIRED (tests/unit/test_config.py):
- test_load_config_success
- test_load_config_missing_required → SystemExit(1)
- test_load_config_defaults
- test_config_types (string env var → int type)
- test_sensitive_filter (password not in log output)

ACCEPTANCE CRITERIA:
- python app/main.py (with valid .env) → exit 0
- python app/main.py (without .env) → exit 1, clear error message
- pytest tests/unit/test_config.py → ALL PASS
- .gitignore includes: .env, data/, logs/, __pycache__, *.pyc, .venv/

DEFINITION OF DONE:
Report: Changed Files | What Changed | Tests Run | Test Result | Next Milestone
```

---

### Prompt M1 — Database Foundation

```
You are implementing Milestone M1 (Database Foundation) for the HTS Ticket Monitor.

SOURCE OF TRUTH: prd-hts-aduan.md, tdd-hts-aduan.md, implementation-plan.md

PREREQUISITE: M0 must be complete and all M0 tests passing.

SCOPE:
SQLite database with complete schema, WAL mode, foreign keys, and full CRUD layer.

FILES TO CREATE:
- app/database/__init__.py
- app/database/schema.sql (complete DDL from TDD §6.2)
- app/database/db.py (DatabaseManager class)
- app/database/models.py (ModelLayer with all CRUD methods)
- tests/unit/test_models.py
- tests/integration/__init__.py
- tests/integration/test_database.py
- scripts/init_db.py

DO NOT MODIFY: app/main.py beyond importing DatabaseManager.

CRITICAL RULES:
- tickets.nomor_aduan → INSERT OR IGNORE (idempotency)
- All related writes → single transaction
- WAL mode: PRAGMA journal_mode=WAL
- Foreign keys: PRAGMA foreign_keys=ON
- busy_timeout: PRAGMA busy_timeout=5000

TESTS REQUIRED: See Implementation Plan §M1 Tests section.

ACCEPTANCE CRITERIA:
- pytest tests/unit/test_models.py tests/integration/test_database.py → ALL PASS
- python scripts/init_db.py → DB created with all 5 tables
- sqlite3 data/hts_monitor.db ".tables" → tickets ticket_snapshots ticket_events notifications system_events
```

---

### Prompt M2 — HTS Connectivity

```
You are implementing Milestone M2 (HTS Connectivity) for the HTS Ticket Monitor.

PREREQUISITE: M0, M1 complete and passing.

SCOPE:
HTTP client that can reach HTS with correct headers. NO authentication yet.
Tests use mock HTTP — do NOT make real requests to live HTS.

FILES TO CREATE:
- app/hts/__init__.py
- app/hts/exceptions.py
- app/hts/client.py (only: create_http_session(), is_session_expired(), HTSClient shell)
- tests/unit/test_client.py

CRITICAL: Do not implement fetch_tickets_page() yet — that's M5.
DO NOT make live HTTP requests to HTS in any test.

EXCEPTIONS TO IMPLEMENT:
- HTSError(Exception)
- HTSConnectionError(HTSError)
- HTSSessionExpiredError(HTSError)
- HTSParseError(HTSError)

is_session_expired(response) must check:
1. response.url ends with '/login' or equals base_url
2. 'id="userEmail"' or 'captchaimg' in response.text
3. status_code in (401, 403)
```

---

### Prompt M3 — Authentication & Session

```
You are implementing Milestone M3 (HTS Authentication & Session Management).

PREREQUISITE: M0, M1, M2 complete and passing.

SCOPE:
SessionManager that handles CAPTCHA alert and wait_for_manual_login().
CAPTCHA is ALWAYS present on HTS login page — NO auto-relogin.

CRITICAL RULES:
- DO NOT bypass CAPTCHA in any way
- DO NOT call external CAPTCHA solving services
- The only action when CAPTCHA is detected: send Telegram alert, wait for manual login
- TelegramNotifier can be a stub/interface for testing

FILES TO CREATE:
- app/hts/session.py
- app/hts/parser.py (minimal: CSRF token parsing only — TicketData in M4)
- tests/integration/test_session_manager.py

TelegramNotifier interface needed (can be stub):
  send(text: str) -> str

SESSION CHECK: GET /list_aduan → is_session_expired(response) → bool
```

---

### Prompt M4 — HTS Data Parser

```
You are implementing Milestone M4 (HTS Data Parser & Field Mapping).

PREREQUISITE: M2 complete.

SCOPE:
Complete TicketData dataclass and parse_ticket() with verified field mapping.
See TDD §8.4 for exact field mapping. Do NOT invent any field names.

FILES TO CREATE/MODIFY:
- app/hts/parser.py (complete TicketData + parse_ticket() + normalization)
- tests/unit/test_parser.py
- tests/fixtures/ (all JSON fixture files)
- tests/fixtures/__init__.py

VERIFIED FIELD MAPPING (from TDD empirical verification):
no_trouble → nomor_aduan
kategori → kategori
sub_kategori → sub_kategori
opd → instansi
induk_opd_nama → opd_induk (None if '-' or empty)
pic → pic_nama
wa → pic_nomor
keluhan → keluhan
tgltshoot → tanggal_aduan
t_solve → t_solve (string; '0' = pending; non-zero = completed)
is_submitted → is_submitted (int 0 or 1)

CRITICAL: to_monitored_dict() must have deterministic key order (sort_keys=True compatible).
```

---

### Prompt M5 — Ticket Fetcher

```
You are implementing Milestone M5 (Ticket Fetcher).

PREREQUISITE: M2, M4 complete and passing.

SCOPE:
Complete HTSClient with fetch_tickets_page(), fetch_all_tickets() (generator with pagination),
and fetch_active_tickets().

VERIFIED API:
POST https://hts.diskomdigi.jatengprov.go.id/get_aduan_data
Headers: Content-Type: application/json, X-Requested-With: XMLHttpRequest
Body: {page, limit, status}  where status: 'pending' | 'solved' | 'all' | 'unsubmitted'
Response: {data: [...], pagination: {page, limit, total, total_pages}}

ALL TESTS MUST USE MOCK HTTP (no live HTS requests).

MONITORING STRATEGY:
- fetch_active_tickets() → status='pending' (only active tickets, efficient)
- fetch_all_tickets(status='all') → used only for initial_sync and reconciliation
```

---

### Prompt M6 — Initial Sync

```
You are implementing Milestone M6 (Initial Sync).

PREREQUISITE: M1, M4, M5 complete.

SCOPE:
run_initial_sync(client, processor, db) — fetch all tickets, save to DB, NO notifications.

IDEMPOTENCY REQUIREMENT:
If the process crashes mid-sync and restarts, re-running initial_sync must be safe.
Use INSERT OR IGNORE on tickets table (already implemented in M1).

FILES TO CREATE:
- app/monitoring/__init__.py
- app/monitoring/reconciler.py (run_initial_sync() only — reconciliation in M12)
- Minimal TicketProcessor stub for testing (is_initial_sync flag only)

ACCEPTANCE: 0 rows in notifications table after initial sync of N tickets.
```

---

### Prompt M7 — Change Detector

```
You are implementing Milestone M7 (Change Detector).

PREREQUISITE: M4 (TicketData)

SCOPE:
compute_hash() and detect_changes() — pure functions, no DB, no Telegram dependency.

FILES TO CREATE:
- app/monitoring/change_detector.py
- tests/unit/test_change_detector.py

MONITORED_FIELDS (alphabetical for determinism):
is_submitted, instansi, kategori, keluhan, opd_induk, pic_nama, pic_nomor,
status_display, sub_kategori, t_solve

changed_fields FORMAT:
{"field_name": {"old": <value>, "new": <value>}}

COMPLETED DETECTION:
is_completed=True only when: current.t_solve != '0' AND was_completed=False
This prevents duplicate COMPLETED events.
```

---

### Prompt M8 — Event Processing

```
You are implementing Milestone M8 (Event Processing / TicketProcessor).

PREREQUISITE: M1, M6, M7 complete.

SCOPE:
TicketProcessor.process() — the core business logic.
Integrates change detection, DB persistence, and notification queueing.
ALL DB writes for one ticket cycle in ONE transaction.

FILES TO CREATE:
- app/monitoring/ticket_processor.py
- app/notifications/__init__.py
- app/notifications/queue.py (NotificationQueue.queue() only — send in M10)
- tests/integration/test_ticket_processor.py

TRANSACTION REQUIREMENT:
ticket insert/update + snapshot + event + notification — ALL in one db.transaction() block.
If any step fails, rollback ALL.

DO NOT send to Telegram in this milestone — only queue to DB.
```

---

### Prompt M9 — Telegram Notifier & Templates

```
You are implementing Milestone M9 (Telegram Notifier & Templates).

PREREQUISITE: M0 (AppConfig)

SCOPE:
TelegramNotifier.send() and all message format functions.

CRITICAL SECURITY:
- NEVER log TELEGRAM_BOT_TOKEN
- NEVER log session cookies
- SensitiveDataFilter must be active (from M0)

TEMPLATE REQUIREMENT:
Output must match PRD §17 EXACTLY (plain text, no Markdown formatting).
Templates must be suitable for direct WhatsApp copy-paste.

TRUNCATION:
- keluhan: max 1000 chars in notification (full stored in DB)
- Total message: max 4096 chars (Telegram limit)

ALL TESTS VIA MOCK HTTP — do not send to real Telegram.
```

---

### Prompt M10 — Notification Queue & Retry

```
You are implementing Milestone M10 (Notification Reliability).

PREREQUISITE: M1, M9 complete.

SCOPE:
NotificationQueue.process_pending() with exponential backoff retry and AT-LEAST-ONCE delivery.

RETRY DELAYS: [15, 60, 300, 1800] seconds (index = attempt_count, capped at index 3)

TELEGRAM 400 Bad Request → mark RETRY_EXHAUSTED (don't retry broken messages)
TELEGRAM 429 Rate Limit → use Retry-After header value
TELEGRAM 5xx / network → exponential backoff

IDEMPOTENCY CONCERN:
If crash after successful Telegram send but before DB update:
→ notification remains PENDING → retry → duplicate message
This is acceptable (AT-LEAST-ONCE). Document this in code comments.

DO NOT change existing queue() method from M8.
```

---

### Prompt M11 — Polling Engine

```
You are implementing Milestone M11 (Polling Engine).

PREREQUISITE: M0, M3, M5, M8, M10 complete.

SCOPE:
Main polling loop. Single-threaded. No overlapping cycles. Interruptible sleep.

CONCURRENCY MODEL:
- Main thread: synchronous polling loop
- Daemon thread: health server (started in M14, stub here)
- Coordination: threading.Event shutdown_event

INTERRUPTIBLE SLEEP:
shutdown_event.wait(timeout=max(0, poll_interval - elapsed))
This allows SIGTERM to interrupt sleep immediately.

DO NOT implement full state machine yet — that's M13.
Implement _run_one_cycle() and _monitoring_loop() only.
```

---

### Prompt M12 — Reconciliation

```
You are implementing Milestone M12 (Reconciliation).

PREREQUISITE: M5, M7, M8, M1 complete.

SCOPE:
run_reconciliation(client, db, processor) — full implementation.
Called after: startup, restart, session recovery, HTS recovery.

STRATEGY:
- status='all' (not just pending) to catch tickets that moved to 'solved' while offline
- Tickets in HTS but not in DB → process as new (notify)
- Tickets in DB and changed in HTS → process as changed (notify)
- Tickets in DB and unchanged → update last_seen only
- Tickets in DB but not in HTS → log WARNING only (not deleted, not notified — V1)
```

---

### Prompt M13 — Full State Machine

```
You are implementing Milestone M13 (Full State Machine & Failure Handling).

PREREQUISITE: M11, M12, M3, M10 complete.

SCOPE:
Complete Orchestrator with all AppState transitions and failure handlers.

STATE TRANSITIONS:
STARTING → INITIAL_SYNC → RECONCILING → MONITORING
MONITORING → SESSION_EXPIRED → CAPTCHA_REQUIRED → RECONCILING → MONITORING
MONITORING → HTS_UNAVAILABLE → RECONCILING → MONITORING
Any → SHUTTING_DOWN (SIGTERM)

ANTI-SPAM RULES:
- HTS_UNAVAILABLE: send ONE alert per incident (not per retry)
- CAPTCHA: rate-limited by CAPTCHA_REMINDER_INTERVAL

EXPONENTIAL BACKOFF:
delay = min(retry_initial_delay * 2^(failures-1), max_retry_delay)

GRACEFUL SHUTDOWN:
signal.signal(SIGTERM, ...) + signal.signal(SIGINT, ...)
shutdown_event.set() → loop exits after current cycle completes → cleanup → sys.exit(0)
```

---

### Prompt M14 — Health Endpoint

```
You are implementing Milestone M14 (Health Endpoint).

PREREQUISITE: M0, M13 (for AppState)

SCOPE:
GET /health HTTP endpoint in daemon thread.
HealthState shared between main loop (writer) and health thread (reader).

SECURITY:
- NEVER include in response: passwords, tokens, cookies, stack traces
- Bind to config.health_host (default 127.0.0.1) — never 0.0.0.0 unless explicit

STATUS LOGIC:
healthy: all components normal
degraded: failed_notifications > 0 OR consecutive_failures > 0
unhealthy: app_state in (HTS_UNAVAILABLE, CAPTCHA_REQUIRED) OR db unreachable
```

---

### Prompt M15 — Backup

```
You are implementing Milestone M15 (Backup).

SCOPE:
Standalone backup script using Python sqlite3 backup API (hot backup).
Runs as standalone cron job — NOT imported by main app.

FILE: backup/backup.py

MUST:
- Use sqlite3.Connection.backup() (not file copy — hot backup)
- Set file permissions to 0o600 after backup
- Run PRAGMA integrity_check on backup file
- Delete backups older than BACKUP_RETAIN_DAYS
- Work without PM2 / app running

CRON:
Document setup: 0 19 * * * (02:00 WIB daily)
```

---

### Prompt M16 — Testing Completion

```
You are implementing Milestone M16 (Testing Completion).

SCOPE:
Complete test coverage. No new features.

TASK:
1. Run: pytest --cov=app --cov-report=term-missing tests/
2. Identify modules below 80% coverage
3. Add missing tests (focus: edge cases from PRD §29)
4. Re-run until pytest returns ALL PASS

EDGE CASES TO TEST:
- EC-01: ticket removed from HTS (in DB but not in HTS response)
- EC-04: keluhan > 10,000 chars → truncated in notification, full in DB
- EC-07: initial sync crash → restart → idempotent
- EC-09: Telegram 429 rate limit → correct retry delay

DO NOT add new features. Only tests.
```

---

### Prompt M17 — PM2 Deployment

```
You are implementing Milestone M17 (PM2 Deployment).

SCOPE:
ecosystem.config.js, setup scripts, and manual verification.

FILES TO CREATE/MODIFY:
- ecosystem.config.js (see §15 in implementation plan)
- scripts/check_health.sh
- README.md (complete deployment section)

CRITICAL:
- Do NOT put secrets in ecosystem.config.js
- Use python-dotenv in app (reads .env automatically)
- kill_timeout: 30000 (allow 30s for graceful shutdown)

VERIFY:
1. pm2 start ecosystem.config.js → status: online
2. pm2 stop → SIGTERM received → exit code 0
3. pm2 restart → reconciliation runs
4. kill -9 → PM2 detects crash → restart → reconciliation
5. bash scripts/check_health.sh → JSON response
```

---

### Prompt M18 — Production Hardening

```
You are implementing Milestone M18 (Production Hardening).

SCOPE:
Security review and production checklist verification. No new features.

CHECKLIST (verify each item):
- File permissions: .env=600, data/*.db=600, logs/*.log=640
- No hardcoded credentials in code: grep -r "password\|token" app/ (must be empty for literals)
- Log security: grep for actual credential values in logs/ (must be empty)
- Health endpoint: curl http://127.0.0.1:8080/health → no credentials in response
- Telegram token not logged: search SensitiveDataFilter is active
- Backup cron: crontab -l → backup line exists
- PM2 startup: pm2 list → status online
- Full test suite: pytest tests/ → ALL PASS

REPORT:
For each checklist item: PASS / FAIL / N/A + evidence.
```

---

## 21. Code Review Checklist Template

Gunakan template ini setelah setiap milestone:

```markdown
## Code Review — Milestone M[N]

### Changed Files
- `app/xxx/yyy.py` — [brief description]
- ...

### What Changed
[Summary of implementation decisions]

### Tests Added
- `tests/unit/test_xxx.py`: N tests
- `tests/integration/test_yyy.py`: M tests

### Tests Run
```
pytest tests/unit/test_xxx.py tests/integration/test_yyy.py -v
```

### Test Result
✅ PASS — N passed, 0 failed, 0 errors
OR
❌ FAIL — [list failing tests and reason]

### Known Issues
[None / or list issues]

### Remaining TBD
[None / or list TBD items]

### Security Notes
[Sensitive data handling, permissions, etc.]

### Next Milestone
M[N+1] — [name]
```

---

## 22. Production Checklist (Final Go-Live)

### Pre-Launch

- [ ] Semua M0–M18 complete dan Definition of Done terpenuhi
- [ ] `pytest tests/` → ALL PASS
- [ ] `.env` berisi credential aktual yang valid
- [ ] File permissions dikonfigurasi (600/640)
- [ ] Health endpoint merespons: `curl http://127.0.0.1:8080/health`
- [ ] Telegram test berhasil (pesan test diterima)
- [ ] HTS connectivity verified (bukan login — hanya HTTPS reach)
- [ ] Backup script berjalan: `python backup/backup.py`
- [ ] Backup cron terdaftar: `crontab -l`
- [ ] PM2 berjalan: `pm2 status`
- [ ] PM2 startup dikonfigurasi: `pm2 save && pm2 startup`
- [ ] Log rotation dikonfigurasi (PM2 atau RotatingFileHandler)
- [ ] README.md akurat dan lengkap

### Post-Launch (24 Jam Pertama)

- [ ] Monitor `pm2 logs hts-ticket-monitor` selama 30 menit
- [ ] Verifikasi bahwa initial sync selesai: `sqlite3 data/hts_monitor.db "SELECT COUNT(*) FROM tickets"`
- [ ] Verifikasi bahwa notifikasi Telegram diterima saat ada tiket baru/berubah
- [ ] Verifikasi bahwa health endpoint responsive
- [ ] Verifikasi bahwa log tidak mengandung ERROR yang tidak diharapkan
- [ ] Verifikasi bahwa backup terjadi pada jadwal

---

*End of Document*

*Implementation Plan v1.0 | HTS Ticket Monitor | Status: READY FOR MILESTONE-BY-MILESTONE CODING*
