# HTS Ticket Monitor

Aplikasi monitoring otomatis untuk sistem **Helpdesk Ticketing System (HTS)** Dinas Komunikasi dan Informatika Pemerintah Provinsi Jawa Tengah ([https://hts.diskomdigi.jatengprov.go.id](https://hts.diskomdigi.jatengprov.go.id)). Aplikasi ini mendeteksi aduan baru serta perubahan status aduan, lalu mengirimkan notifikasi siap-forward ke Telegram channel/grup operator secara real-time dan andal (*at-least-once delivery*).

---

## Daftar Isi
- [Arsitektur & Fitur Utama](#arsitektur--fitur-utama)
- [Persyaratan Sistem](#persyaratan-sistem)
- [Instalasi](#instalasi)
- [Konfigurasi Environment](#konfigurasi-environment)
- [Menjalankan Pengujian (Testing)](#menjalankan-pengujian-testing)
- [Deployment Produksi dengan PM2](#deployment-produksi-dengan-pm2)
- [Health Check & Monitoring](#health-check--monitoring)
- [Backup Otomatis (Hot Backup)](#backup-otomatis-hot-backup)
- [Prosedur Login Manual (CAPTCHA Handling)](#prosedur-login-manual-captcha-handling)
- [Struktur Proyek](#struktur-proyek)

---

## Arsitektur & Fitur Utama

1. **Change Detection Presisi**: Menggunakan SHA256 hashing per-tiket dan snapshot history untuk mendeteksi perubahan field tiket secara tepat tanpa false alert.
2. **Reconciliation Engine**: Sinkronisasi otomatis saat startup, setelah recovery HTS, dan setelah login ulang sesi untuk menjamin zero dropped tickets saat offline.
3. **Notification Queue**: Antrean SQLite lokal dengan exponential backoff retry `[15s, 60s, 300s, 1800s]`, mitigasi Telegram rate limit (HTTP 429 Retry-After), dan isolasi kegagalan permanen (HTTP 400).
4. **Resilience & Anti-Spam**: Rate limiting notifikasi status HTS down dan pengingat CAPTCHA, sleep yang dapat diinterupsi sinyal SIGTERM/SIGINT, dan graceful shutdown 30 detik.
5. **Zero Credential Leakage**: Filter otomatis sensor kata sandi dan token bot dari seluruh output log dan endpoint status kesehatan.
6. **Built-in Health Check**: Daemon HTTP server di port `8080` (`/health`) menyajikan metrik operasional aplikasi secara transparan.

---

## Persyaratan Sistem

- **Sistem Operasi**: Linux / Ubuntu 22.04+ (atau Windows WSL2)
- **Python**: Versi 3.11 atau lebih baru
- **Node.js & PM2**: Node.js 18+ dan `pm2` global (`npm install -g pm2`)
- **Database**: SQLite 3.35+ (bawaan modul Python standard)
- **Akses Jaringan**: Akses keluar (outbound) HTTPS ke `hts.diskomdigi.jatengprov.go.id` dan `api.telegram.org`

---

## Instalasi

1. **Clone repositori**:
   ```bash
   git clone <repository-url>
   cd hts-aduan-bot
   ```

2. **Buat dan aktifkan Virtual Environment**:
   ```bash
   python3 -m venv .venv

   # Linux / WSL / macOS:
   source .venv/bin/activate

   # Windows PowerShell:
   .venv\Scripts\Activate.ps1
   ```

3. **Pasang dependensi**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   # Untuk kebutuhan testing dan development:
   pip install -r requirements-dev.txt
   ```

---

## Konfigurasi Environment

Salin template konfigurasi `.env.example` ke `.env`:
```bash
cp .env.example .env
chmod 600 .env  # Pastikan hanya user pemilik yang dapat membaca
```

Sesuaikan variabel konfigurasi di dalam `.env`:
```ini
# HTS Credentials
HTS_BASE_URL=https://hts.diskomdigi.jatengprov.go.id
HTS_USERNAME=your_username
HTS_PASSWORD=your_password

# Telegram Bot
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=-1001234567890

# Engine Intervals (detik)
POLL_INTERVAL=5
SESSION_CHECK_INTERVAL=30
RETRY_INITIAL_DELAY=15
MAX_RETRY_DELAY=300
CAPTCHA_REMINDER_INTERVAL=1800
REQUEST_TIMEOUT=15

# Health Server
HEALTH_HOST=127.0.0.1
HEALTH_PORT=8080

# Logging & Storage
LOG_LEVEL=INFO
DB_PATH=data/hts_monitor.db
INITIAL_SYNC=true
```

---

## Menjalankan Pengujian (Testing)

Jalankan seluruh unit test dan integration test beserta laporan coverage:
```bash
pytest --cov=app --cov-report=term-missing tests/
```

*Catatan: Seluruh pengujian menggunakan mock HTTP internal. Tidak ada request nyata yang dikirim ke HTS maupun Telegram selama pengujian.*

---

## Deployment Produksi dengan PM2

Aplikasi ini dikelola menggunakan **PM2 Process Manager** untuk memastikan daemon berjalan terus-menerus, me-restart otomatis jika crash, serta membatasi alokasi memori maksimum (256M).

1. **Menjalankan Daemon**:
   ```bash
   pm2 start ecosystem.config.js
   ```

2. **Mengecek Status**:
   ```bash
   pm2 status
   ```

3. **Melihat Log Aplikasi**:
   ```bash
   pm2 logs hts-ticket-monitor
   # Atau langsung melihat file log rotasi aplikasi:
   tail -f logs/hts_monitor.log
   ```

4. **Restart & Reload**:
   ```bash
   pm2 restart hts-ticket-monitor
   ```

5. **Stop Gracefully**:
   ```bash
   pm2 stop hts-ticket-monitor
   ```

6. **Konfigurasi Auto-Start saat Server Booting (Linux Native)**:
   ```bash
   pm2 save
   pm2 startup
   # Jalankan perintah sudo env PATH... yang dimunculkan oleh pm2 startup
   ```

---

## Health Check & Monitoring

Aplikasi menjalankan endpoint HTTP lokal di `http://127.0.0.1:8080/health`.

Gunakan script helper:
```bash
chmod +x scripts/check_health.sh
bash scripts/check_health.sh
```

Contoh respons JSON:
```json
{
  "status": "healthy",
  "app_state": "MONITORING",
  "uptime_seconds": 3600,
  "last_successful_poll": "2026-09-17T04:00:00.000000Z",
  "consecutive_failures": 0,
  "hts_state": "ok",
  "db_connected": true,
  "total_tickets": 150,
  "pending_notifications": 0,
  "failed_notifications": 0
}
```

---

## Backup Otomatis (Hot Backup)

Aplikasi menyediakan script hot-backup mandiri berbasis SQLite `Connection.backup()` di `backup/backup.py`. Script ini aman dijalankan kapan pun tanpa mengganggu proses monitoring yang sedang aktif.

1. **Uji coba backup manual**:
   ```bash
   python backup/backup.py
   ```

2. **Jadwalkan via Cron (Setiap hari pukul 02:00 WIB / 19:00 UTC)**:
   Buka crontab:
   ```bash
   crontab -e
   ```
   Tambahkan baris berikut:
   ```bash
   0 19 * * * /opt/hts-aduan-bot/.venv/bin/python /opt/hts-aduan-bot/backup/backup.py >> /opt/hts-aduan-bot/logs/backup.log 2>&1
   ```

---

## Prosedur Login Manual (CAPTCHA Handling & Cookie Import)

Karena sistem HTS dilindungi oleh CAPTCHA gambar dinamis, bot mematuhi kebijakan *Zero CAPTCHA Bypass*:

1. **Notifikasi Login Masuk ke Telegram**:
   Saat bot mendeteksi sesi kedaluwarsa atau belum login, peringatan dikirimkan ke Telegram operator:
   ```text
   🔐 LOGIN MANUAL DIPERLUKAN
   HTS memerlukan login manual (CAPTCHA).
   Silakan login secara manual ke: https://hts.diskomdigi.jatengprov.go.id
   ```
2. **Login di Browser**:
   Buka `https://hts.diskomdigi.jatengprov.go.id` di browser Anda (Chrome/Edge/Firefox), masukkan username, password, dan selesaikan kode CAPTCHA.
3. **Salin Cookie Sesi**:
   - Tekan **F12** (Buka Developer Tools) -> Pilih tab **Network**.
   - Muat ulang (Refresh) halaman atau klik menu Aduan.
   - Klik request `list_aduan` atau request pertama yang muncul.
   - Pada panel kanan tab **Headers** -> cari bagian **Request Headers** -> **Cookie**.
   - Salin seluruh nilai string header `Cookie:` (atau cukup ambil nilai `ci_session=...; TS0128f648=...`).
4. **Impor Cookie ke Bot**:
   Jalankan script helper di terminal WSL / server:
   ```bash
   python scripts/import_cookies.py
   # Lalu tempel (paste) nilai cookie yang disalin tadi saat diminta prompt.
   ```
   *Atau langsung via argumen perintah:*
   ```bash
   python scripts/import_cookies.py -s "ci_session=YOUR_SESSION_VALUE; TS0128f648=YOUR_WAF_VALUE"
   ```
5. **Otomatis Melanjutkan Monitoring**:
   Script akan memvalidasi sesi ke HTS dan menyimpannya secara aman ke `data/cookies.json` (`chmod 600`).
   Bot yang sedang berjalan di PM2 akan mendeteksi file cookie tersebut pada siklus probe berikutnya secara otomatis tanpa perlu restart PM2!


---

## Struktur Proyek

```text
hts-aduan-bot/
├── app/
│   ├── config.py                 # Konfigurasi aplikasi & validasi environment
│   ├── main.py                   # Main entry point & lifecycle wiring
│   ├── database/
│   │   ├── db.py                 # SQLite DatabaseManager (WAL mode, busy timeout)
│   │   ├── models.py             # ModelLayer CRUD, queries, transactions
│   │   └── schema.sql            # Schema DDL (tickets, events, notifications, etc.)
│   ├── health/
│   │   └── endpoint.py           # HTTP /health server daemon thread
│   ├── hts/
│   │   ├── client.py             # HTS HTTP client & paginated fetcher
│   │   ├── exceptions.py         # Hierarki error domain HTS
│   │   ├── parser.py             # Parser HTML CSRF, parsing JSON & normalisasi
│   │   └── session.py            # SessionManager & CAPTCHA detection
│   ├── monitoring/
│   │   ├── change_detector.py    # Perbandingan SHA256 & field-level diff
│   │   ├── orchestrator.py       # State machine loop & error recovery
│   │   ├── reconciler.py         # Initial sync & delta reconciliation
│   │   └── ticket_processor.py   # Pemrosesan event & pembuatan notifikasi
│   ├── notifications/
│   │   ├── queue.py              # NotificationQueue & exponential backoff retry
│   │   ├── telegram.py           # TelegramNotifier client
│   │   └── templates.py          # Formatter pesan Telegram
│   └── utils/
│       ├── log_utils.py          # Log filter (masking token & password)
│       └── time_utils.py         # ISO8601 UTC timestamp helper
├── backup/
│   └── backup.py                 # Hot-backup SQLite script (retention 7 days)
├── scripts/
│   └── check_health.sh           # Utility script inspeksi /health
├── tests/                        # Comprehensive test suite (>90% coverage)
├── ecosystem.config.js           # PM2 configuration
├── requirements.txt              # Production dependencies
├── requirements-dev.txt          # Development & test dependencies
└── .env.example                  # Environment template
```
