# HTS Ticket Monitor - System Design & Architecture (As-Built)

Dokumen ini menjelaskan arsitektur sistem, desain database, dan alur kerja aplikasi HTS Ticket Monitor berdasarkan implementasi akhir yang ada pada repositori ini.

---

## 1. Gambaran Umum Sistem (System Overview)

Aplikasi ini bertindak sebagai **middleware pintar** antara Helpdesk Ticketing System (HTS) berbasis web (CodeIgniter) milik Diskominfo Jateng dan Telegram. Karena HTS tidak menyediakan webhook atau API resmi untuk notifikasi, sistem ini melakukan *intelligent polling* (scraping berkala) dengan deteksi perubahan state (diffing) untuk men-trigger notifikasi Telegram.

### 1.1. Komponen Utama
1. **Scraper / HTS Client**: Berinteraksi dengan web HTS, menangani sesi (cookie), dan membaca data tabel (Aduan, Kunjungan, Permohonan Layanan, VPS/Domain).
2. **Reconciliation & Diff Engine**: Membandingkan snapshot tiket baru dengan data di database lokal (menggunakan *SHA-256 hash*) untuk mendeteksi tiket baru atau field yang berubah (misal: status dari "Pending" menjadi "Solved").
3. **Orchestrator**: State-machine utama yang mengatur siklus hidup aplikasi (Init -> Sync -> Reconcile -> Monitor -> Error Recovery).
4. **Notification Queue**: Antrean pengiriman pesan Telegram berbasis SQLite. Mencegah pesan hilang saat Telegram error/rate-limit menggunakan mekanisme *Exponential Backoff*.
5. **Web Dashboard (FastAPI)**: Antarmuka UI (port 8888) untuk memonitor tiket, mengelola sistem (Silent Sync), mengirim ulang notifikasi, dan meng-generate rekap shift harian.

---

## 2. Arsitektur Data & Scraping

Karena endpoint HTS memiliki format yang berbeda-beda, sistem ini mengimplementasikan adaptasi spesifik:
*   **Aduan & Kunjungan DC**: Menggunakan HTTP POST standar ke `/get_aduan_data` dan `/kunjungan/data`.
*   **Permohonan Layanan (UDP)**: Menggunakan HTTP POST URL-encoded (DataTables format) ke `/mohon_data_layanan` dengan ekstraksi *CSRF Token* dinamis dari halaman `/udp`. Data HTML di-parse menggunakan BeautifulSoup4.
*   **VPS/Domain & Rekomtek**: Menggunakan HTTP GET (DataTables format) ke `/domain-pentest/data` yang mengembalikan format JSON bersih.

---

## 3. Desain Database (SQLite - WAL Mode)

Database menggunakan SQLite3 dengan mode **WAL (Write-Ahead Logging)** untuk mendukung konkurensi (banyak baca, satu tulis) agar web dashboard tidak memblokir daemon monitoring.

### 3.1. Skema Tabel Utama
*   `tickets`: Menyimpan state terakhir dari setiap tiket (Nomor Aduan, Keluhan, Status, dll).
*   `ticket_snapshots`: Menyimpan riwayat perubahan tiket beserta hash-nya (Versioning).
*   `system_events`: Audit trail untuk semua aktivitas (sinkronisasi, HTS down, perubahan status).
*   `notifications`: Antrean pesan Telegram. Memiliki status `PENDING`, `SENT`, atau `FAILED`.
*   `bot_state`: Menyimpan cursor konfigurasi runtime internal (seperti notifikasi HTS Down terakhir).

---

## 4. Alur Kerja (Workflows)

### 4.1. Startup & Silent Initial Sync (Auto-Guard)
Untuk mencegah spam ratusan notifikasi saat pertama kali di-deploy di server baru:
1. Orchestrator mengecek apakah tabel `tickets` kosong.
2. Jika kosong, mode **Silent Initial Sync** otomatis berjalan.
3. Semua tiket lama dari HTS ditarik dan disimpan ke database lokal, **tanpa** memasukkannya ke antrean Telegram.
4. (Fitur ini juga bisa dipicu manual melalui tombol di Web Dashboard).

### 4.2. Monitoring Loop
1. Orchestrator memanggil `HTSClient` setiap `POLL_INTERVAL` detik.
2. `ChangeDetector` membandingkan tiket dari HTS dengan `tickets` di DB.
3. Jika ada perubahan (tiket baru atau status berubah), `TicketProcessor` membuat draft pesan dan menyimpannya ke `notifications` dengan status `PENDING`.
4. `NotificationQueue` worker membaca tiket `PENDING` dan mengirimkannya via Telegram API.
5. Jika Telegram mengembalikan error 429 (Too Many Requests), worker akan melakukan *sleep* dan menerapkan *Exponential Backoff Retry* (15s, 60s, 300s, 1800s).

### 4.3. Penanganan Sesi (Session Recovery)
HTS menggunakan CAPTCHA dinamis. Jika cookie sesi kedaluwarsa:
1. `SessionManager` mendeteksi HTML respons berisi form login/CAPTCHA.
2. Bot menghentikan polling dan masuk ke mode *WAITING_FOR_SESSION*.
3. Bot mengirimkan peringatan (Alert) ke Telegram bahwa login manual diperlukan.
4. Setelah admin mengupdate file `cookies.json` (via script CLI atau container), bot otomatis mendeteksi perubahan file dan melanjutkan polling tanpa perlu *restart*.

---

## 5. Deployment Architecture

Aplikasi dapat dijalankan melalui dua metode:
1.  **Native / PM2 (ecosystem.config.js)**: Cocok untuk VPS tradisional. Menjalankan Python daemon di latar belakang dengan kontrol memori dan auto-restart.
2.  **Docker (docker-compose.yml)**: Arsitektur modern yang memisahkan aplikasi ke dalam container (Image Python 3.12-slim). Folder `data/` (SQLite) dan `logs/` di-mount sebagai volume persisten ke host OS agar data aman.

