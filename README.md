# HTS Ticket Monitor & Web Dashboard

Aplikasi monitoring otomatis untuk sistem **Helpdesk Ticketing System (HTS)** Dinas Komunikasi dan Informatika Pemerintah Provinsi Jawa Tengah ([https://hts.diskomdigi.jatengprov.go.id](https://hts.diskomdigi.jatengprov.go.id)).

Aplikasi ini mendeteksi tiket baru serta perubahan status tiket secara real-time, mengirim notifikasi siap-forward ke Telegram channel/grup operator (*at-least-once delivery*), menyediakan **Web Dashboard interaktif**, serta fitur **Generate Rekap Laporan Shift** otomatis.

Aplikasi ini mendukung **2 metode deployment**:
1. 🐳 **Docker & Docker Compose** (Sangat Direkomendasikan - praktis, terisolasi, dan tanpa setup dependensi manual).
2. ⚡ **Native Python & PM2 Process Manager** (Metode tradisional langsung di host/server).

---

## Daftar Isi
- [Fitur Utama](#fitur-utama)
- [Pilihan 1: Deployment dengan Docker (Direkomendasikan)](#pilihan-1-deployment-dengan-docker-direkomendasikan)
- [Pilihan 2: Deployment dengan PM2 / Python Native](#pilihan-2-deployment-dengan-pm2--python-native)
- [Konfigurasi Environment (.env)](#konfigurasi-environment-env)
- [Web Dashboard & Sinkronisasi Awal](#web-dashboard--sinkronisasi-awal)
- [Fitur Generate Rekap Shift](#fitur-generate-rekap-shift)
- [Prosedur Login Manual (CAPTCHA Handling)](#prosedur-login-manual-captcha-handling)
- [Testing](#testing)
- [Struktur Proyek](#struktur-proyek)

---

## Fitur Utama

1. **Change Detection Presisi**: Menggunakan SHA256 hashing per-tiket dan snapshot history untuk mendeteksi perubahan field tiket tanpa false alert.
2. **Multi-Kategori Lengkap**:
   - **Aduan** (`/get_aduan_data`)
   - **Kunjungan Data Center** (`/kunjungan/data`)
   - **Permohonan Layanan** (`/udp` via `/mohon_data_layanan`)
   - **VPS/Domain & Rekomtek** (`/domain-pentest/data`)
3. **Web Dashboard Interaktif (FastAPI di Port 8888)**:
   - Akses via browser untuk memantau tiket, riwayat status, dan audit event.
   - **Sinkronisasi Awal (Anti-Spam)**: Mencegah spam ratusan notifikasi saat deploy pertama kali pada database baru.
   - **Kirim Ulang Notifikasi (Resend / Bulk Resend)** ke Telegram dengan kustomisasi nama & jabatan petugas.
   - **Generate Rekap Shift Otomatis**: Menghitung tiket Pagi/Siang/Malam secara instan.
4. **Resilience & Antrean Notifikasi**:
   - SQLite queue dengan exponential backoff retry `[15s, 60s, 300s, 1800s]`.
   - Menangani Telegram rate limit (HTTP 429 Retry-After).
   - Auto-guard saat database kosong untuk mencegah banjir pesan.
5. **Zero Credential Leakage**: Sensor kata sandi dan token bot otomatis dari seluruh output log dan endpoint status kesehatan.

---

## Pilihan 1: Deployment dengan Docker (Direkomendasikan)

Metode ini paling mudah dan bersih karena seluruh environment (Python 3.12, timezone Asia/Jakarta, dan dependensi) sudah dibundle ke dalam kontainer.

### 1. Salin Konfigurasi Environment
```bash
cp .env.example .env
nano .env   # Sesuaikan HTS_USERNAME, HTS_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

### 2. Jalankan dengan Docker Compose
```bash
docker compose up -d --build
```
Dua kontainer akan otomatis berjalan di background:
- **`hts-monitor`**: Worker daemon pemantau tiket HTS dan pengirim notifikasi Telegram.
- **`hts-dashboard`**: Web dashboard FastAPI pada port `8888`.

### 3. Perintah Operasional Docker
- **Cek status kontainer:**
  ```bash
  docker compose ps
  ```
- **Lihat log monitor Telegram real-time:**
  ```bash
  docker compose logs -f hts-monitor
  ```
- **Lihat log web dashboard:**
  ```bash
  docker compose logs -f hts-dashboard
  ```
- **Matikan / Stop kontainer:**
  ```bash
  docker compose down
  ```

> 💾 **Penyimpanan Data Persisten**:
> Folder `./data` (database SQLite & cookie sesi) serta folder `./logs` dimounting ke server host Anda, sehingga data tiket tidak akan hilang meskipun kontainer di-restart atau di-update.

---

## Pilihan 2: Deployment dengan PM2 / Python Native

Jika Anda tidak menggunakan Docker dan ingin menjalankannya langsung di host OS (Ubuntu/Debian):

### 1. Prasyarat
- Python 3.11+
- Node.js & PM2 (`npm install -g pm2`)

### 2. Setup Lingkungan
```bash
# Setup virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Setup file .env
cp .env.example .env
nano .env
```

### 3. Jalankan Aplikasi via PM2
```bash
# Start monitor daemon dan dashboard
pm2 start ecosystem.config.js

# Cek status
pm2 status

# Lihat log
pm2 logs hts-ticket-monitor
pm2 logs hts-dashboard

# Setup auto-start saat reboot OS
pm2 save
pm2 startup
```

---

## Konfigurasi Environment (.env)

| Variabel | Deskripsi | Default / Contoh |
|---|---|---|
| `HTS_BASE_URL` | URL basis Helpdesk Ticketing System | `https://hts.diskomdigi.jatengprov.go.id` |
| `HTS_USERNAME` | Username akun HTS Helpdesk | `your_username` |
| `HTS_PASSWORD` | Password akun HTS | `your_password` |
| `TELEGRAM_BOT_TOKEN` | Token API bot Telegram | `123456789:ABC...` |
| `TELEGRAM_CHAT_ID` | ID grup / channel tujuan notifikasi | `-1001234567890` |
| `POLL_INTERVAL` | Interval polling HTS (detik) | `5` |
| `LOG_LEVEL` | Level log aplikasi | `INFO` |
| `DB_PATH` | Lokasi file database SQLite | `data/hts_monitor.db` |
| `INITIAL_SYNC` | Jalankan initial sync saat start | `true` |
| `PETUGAS_NAMA` | Nama default petugas pada signature | `"Nama Petugas"` |
| `PETUGAS_ROLE` | Jabatan default petugas | `"Helpdesk DC"` |

---

## Web Dashboard & Sinkronisasi Awal

Buka browser dan kunjungi:
👉 **`http://localhost:8888/dashboard`** *(atau ganti `localhost` dengan IP server Anda)*

### 🛡️ Fitur Sinkronisasi Awal (Anti-Spam)
Ketika aplikasi dideploy pertama kali dengan database baru, sistem tidak akan mengirim spam ratusan tiket lama ke Telegram.
- **Auto-Guard**: Jika database terdeteksi kosong, sistem otomatis melakukan *Silent Sync*.
- **Tombol Manual**: Anda juga dapat mengklik tombol **"Sinkronisasi Awal"** di kanan atas Web Dashboard kapan saja untuk menarik tiket dari HTS secara aman **tanpa notifikasi Telegram**.

---

## Fitur Generate Rekap Shift

Pada Web Dashboard (menu Overview):
1. Tentukan **Tanggal** laporan.
2. Pilih **Shift**:
   - **Pagi**: 07:00 - 15:00 WIB
   - **Siang**: 15:00 - 23:00 WIB
   - **Malam**: 23:00 - 07:00 WIB (menghitung tiket lintas hari)
3. Masukkan **Nama Petugas**.
4. Klik **Generate Rekap**. Format laporan rekap lengkap 5 kategori (Aduan, Kunjungan, Permohonan Layanan, VPS/Domain, Rekomtek) akan langsung dihasilkan dan siap disalin.

---

## Prosedur Login Manual (CAPTCHA Handling)

Jika sistem HTS memerlukan login ulang karena CAPTCHA dinamis:

1. **Notifikasi Masuk ke Telegram**: Bot akan mengirim alert bahwa sesi login kedaluwarsa.
2. **Login di Browser**: Buka `https://hts.diskomdigi.jatengprov.go.id` di browser, selesaikan CAPTCHA dan login.
3. **Salin Cookie Sesi**:
   - Buka Developer Tools (**F12**) -> tab **Network**.
   - Refresh halaman atau klik salah satu menu.
   - Salin isi header `Cookie:` (khususnya nilai `ci_session=...; TS0128f648=...`).
4. **Impor Cookie ke Bot**:
   - **Jika Menggunakan Docker:**
     ```bash
     docker compose exec hts-monitor python scripts/import_cookies.py -s "COOKIE_STRING_ANDA"
     ```
   - **Jika Menggunakan PM2 / Python Native:**
     ```bash
     python scripts/import_cookies.py -s "COOKIE_STRING_ANDA"
     ```
5. Bot akan langsung memvalidasi dan melanjutkan proses monitoring secara otomatis tanpa perlu restart container/PM2.

---

## Testing

Proyek ini memiliki unit & integration test suite komprehensif (170+ test cases):

```bash
pytest tests/ -q
```
*Seluruh pengujian berjalan menggunakan mock internal dan tidak mengirimkan request nyata ke server HTS/Telegram.*

---

## Struktur Proyek

```text
hts-aduan-bot/
├── app/
│   ├── config.py                 # Konfigurasi aplikasi & validasi environment
│   ├── main.py                   # Main entry point & worker daemon
│   ├── dashboard/                # FastAPI Web Dashboard & server
│   │   ├── server.py             # Route API dashboard & generator rekap
│   │   ├── templates/            # Template HTML (Jinja2)
│   │   └── static/               # File CSS & JavaScript
│   ├── database/                 # SQLite DatabaseManager (WAL mode)
│   ├── health/                   # Endpoint health check (port 8080)
│   ├── hts/                      # Client API HTS, Session Manager, & DataTables fetcher
│   ├── monitoring/               # Change detector, orchestrator, reconciler & rekap service
│   └── notifications/            # Telegram notifier, queue & message templates
├── backup/                       # Script hot-backup SQLite
├── scripts/                      # Script utilitas (import cookie, runner dashboard)
├── tests/                        # Test suite pytest
├── Dockerfile                    # Image build container Python 3.12
├── docker-compose.yml            # Orkestrasi container monitor & dashboard
├── ecosystem.config.js           # Konfigurasi PM2 (opsi native)
├── requirements.txt              # Dependensi Python produksi
└── .env.example                  # Template variabel lingkungan
```
