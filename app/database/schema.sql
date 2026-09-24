-- database/schema.sql
-- Complete DDL for HTS Ticket Monitor (TDD §6.2)

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
    penjelasan      TEXT    DEFAULT '',        -- penjelasan penanganan dari API
    pic_kominfo     TEXT    DEFAULT '',        -- teknisi/helpdesk HTS
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
    event_type           TEXT    NOT NULL,            -- NEW_TICKET | TICKET_CHANGED | STATUS_CHANGED | COMPLETED
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
    channel              TEXT    NOT NULL DEFAULT 'telegram',
    message_text         TEXT    NOT NULL,
    status               TEXT    NOT NULL DEFAULT 'PENDING',  -- PENDING | SENT | FAILED | RETRY_EXHAUSTED
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
    description     TEXT,
    metadata        TEXT,                      -- JSON optional context
    event_timestamp TEXT    NOT NULL,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sysevents_event_type ON system_events(event_type);
CREATE INDEX IF NOT EXISTS idx_sysevents_event_timestamp ON system_events(event_timestamp);
