# Product Requirements Document
# HTS Ticket Monitor — v1.0

| Metadata | Value |
|---|---|
| Document Version | 1.0 |
| Status | VERIFIED (READY FOR TECHNICAL DESIGN) |
| Author | — |
| Created | 2026-09-17 |
| Last Updated | 2026-09-17 |
| Target Release | V1 |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Background](#2-background)
3. [Problem Statement](#3-problem-statement)
4. [Goals](#4-goals)
5. [Non-Goals](#5-non-goals)
6. [Users / Actors](#6-users--actors)
7. [User Stories](#7-user-stories)
8. [Functional Requirements](#8-functional-requirements)
9. [Non-Functional Requirements](#9-non-functional-requirements)
10. [System Workflow](#10-system-workflow)
11. [Authentication & Session Management](#11-authentication--session-management)
12. [Monitoring & Polling](#12-monitoring--polling)
13. [Ticket Detection](#13-ticket-detection)
14. [Change Detection](#14-change-detection)
15. [Initial Sync](#15-initial-sync)
16. [Reconciliation](#16-reconciliation)
17. [Telegram Notification](#17-telegram-notification)
18. [Error Handling](#18-error-handling)
19. [Database Requirements](#19-database-requirements)
20. [Security](#20-security)
21. [Configuration](#21-configuration)
22. [Logging](#22-logging)
23. [Health Monitoring](#23-health-monitoring)
24. [PM2 / Deployment](#24-pm2--deployment)
25. [Backup](#25-backup)
26. [Architecture](#26-architecture)
27. [State Machine](#27-state-machine)
28. [Acceptance Criteria](#28-acceptance-criteria)
29. [Edge Cases](#29-edge-cases)
30. [Risks](#30-risks)
31. [Technical Unknowns / TBD](#31-technical-unknowns--tbd)
32. [V2 / Future Enhancements](#32-v2--future-enhancements)

---

## 1. Executive Summary

**HTS Ticket Monitor** adalah aplikasi daemon berbasis Python yang secara otomatis memantau sistem pengaduan HTS (Help Desk Ticketing System) milik Dinas Komunikasi dan Informatika Jawa Tengah pada URL:

```
https://hts.diskomdigi.jatengprov.go.id/list_aduan
```

Aplikasi bertugas mendeteksi aduan baru, perubahan pada aduan yang sudah ada, dan penyelesaian aduan — kemudian mengirimkan notifikasi terstruktur melalui **Telegram Bot**. Notifikasi dirancang agar dapat langsung di-*copy-paste* secara manual oleh operator ke WhatsApp, tanpa automasi WhatsApp sama sekali.

Aplikasi akan berjalan sebagai background process yang dikelola oleh **PM2** pada lingkungan **Ubuntu/WSL**, menggunakan **SQLite** sebagai penyimpanan data, dan mengikuti prinsip keamanan *least privilege* dengan seluruh konfigurasi sensitif disimpan melalui environment variables / file `.env`.

---

## 2. Background

Dinas terkait menggunakan sistem HTS untuk mengelola aduan dari instansi-instansi di Jawa Tengah. Operator secara manual memantau halaman daftar aduan dan meneruskan informasi ke grup WhatsApp sebagai saluran komunikasi operasional.

Proses manual ini memiliki kelemahan:
- Potensi keterlambatan deteksi aduan baru
- Risiko aduan terlewat, terutama di luar jam kerja
- Beban manual yang tinggi pada operator
- Tidak ada catatan historis perubahan aduan secara terstruktur

**HTS Ticket Monitor** hadir untuk mengotomatisasi proses pemantauan dan pendeteksian perubahan, sehingga operator hanya perlu melakukan tindakan *copy-paste* dari Telegram ke WhatsApp — bukan memantau halaman web secara manual.

---

## 3. Problem Statement

| # | Masalah | Dampak |
|---|---|---|
| P1 | Operator harus membuka halaman HTS secara manual untuk memantau aduan baru | Aduan bisa terlewat, terutama di luar jam kerja |
| P2 | Tidak ada notifikasi otomatis ketika aduan baru masuk | Response time lambat |
| P3 | Perubahan isi aduan (bukan hanya status) tidak mudah dipantau secara manual | Perubahan penting bisa tidak terdeteksi |
| P4 | Tidak ada riwayat perubahan aduan yang terstruktur | Sulit melakukan audit |
| P5 | Penerusan informasi ke WhatsApp dilakukan manual tanpa format konsisten | Format tidak seragam, informasi bisa tidak lengkap |

---

## 4. Goals

| ID | Goal | Priority |
|---|---|---|
| G1 | Memonitor halaman aduan HTS secara otomatis dengan polling interval yang dapat dikonfigurasi | P0 |
| G2 | Mendeteksi aduan baru dan mengirim notifikasi Telegram | P0 |
| G3 | Mendeteksi perubahan pada seluruh field aduan dan mengirim notifikasi Telegram | P0 |
| G4 | Mendeteksi tiket selesai dan mengirim notifikasi Telegram | P0 |
| G5 | Menyimpan seluruh data, snapshot, event, dan notifikasi ke SQLite | P0 |
| G6 | Menangani session expired dengan notifikasi manual ke operator | P0 |
| G7 | Tidak membanjiri Telegram pada startup (Initial Sync Mode) | P0 |
| G8 | Melakukan reconciliation ketika aplikasi restart setelah offline | P0 |
| G9 | Notifikasi Telegram dapat langsung di-copy-paste ke WhatsApp | P0 |
| G10 | Berjalan sebagai daemon yang reliabel menggunakan PM2 | P1 |
| G11 | Menyediakan health endpoint sederhana | P1 |
| G12 | Memiliki backup strategy untuk database SQLite | P1 |

---

## 5. Non-Goals

Fitur-fitur berikut **tidak termasuk** dalam V1 dan tidak boleh diimplementasikan kecuali ada keputusan eksplisit untuk merevisi scope:

| ID | Non-Goal |
|---|---|
| NG1 | Pengiriman WhatsApp secara otomatis (WhatsApp API, WhatsApp Web automation) |
| NG2 | Web dashboard untuk monitoring |
| NG3 | User management / multi-user |
| NG4 | Multi-tenant / multiple HTS accounts |
| NG5 | Analytics dashboard / reporting |
| NG6 | Integrasi Grafana, Prometheus, atau external monitoring platform |
| NG7 | Export ke Excel, PDF, atau format laporan lain |
| NG8 | Mobile application |
| NG9 | Multiple Telegram destinations / routing rules |
| NG10 | Bypass CAPTCHA dengan cara apapun |
| NG11 | Advanced workflow engine |
| NG12 | HTS menjadi source of truth — aplikasi hanya membaca, tidak menulis ke HTS |

---

## 6. Users / Actors

| Actor | Deskripsi | Interaksi dengan Sistem |
|---|---|---|
| **Operator** | Staf yang bertanggung jawab memantau dan menindaklanjuti aduan | Menerima notifikasi Telegram, melakukan copy-paste ke WhatsApp, melakukan login manual jika CAPTCHA diperlukan |
| **System Administrator** | Teknisi yang mengkonfigurasi dan mengelola aplikasi | Setup environment, konfigurasi `.env`, monitoring via PM2 dan health endpoint |
| **Telegram Bot** | Bot Telegram yang digunakan sebagai channel notifikasi | Aktor non-human; menerima pesan dari aplikasi dan mengirimkannya ke grup/chat |
| **HTS System** | Sistem pengaduan eksternal yang dipantau | Sumber data; tidak dimodifikasi oleh aplikasi |
| **PM2** | Process manager | Mengelola lifecycle aplikasi (start, restart, stop) |

---

## 7. User Stories

### Operator

| ID | User Story | Priority |
|---|---|---|
| US-01 | Sebagai operator, saya ingin menerima notifikasi Telegram ketika ada aduan baru masuk ke HTS, sehingga saya dapat segera menindaklanjutinya | P0 |
| US-02 | Sebagai operator, saya ingin notifikasi berisi teks yang siap di-*copy-paste* ke WhatsApp tanpa perlu mengedit apapun | P0 |
| US-03 | Sebagai operator, saya ingin menerima notifikasi ketika isi aduan berubah (bukan hanya status), sehingga saya selalu mendapatkan informasi terbaru | P0 |
| US-04 | Sebagai operator, saya ingin menerima notifikasi ketika aduan selesai | P0 |
| US-05 | Sebagai operator, saya ingin diberitahu melalui Telegram jika sistem membutuhkan login manual / CAPTCHA, sehingga saya tahu kapan harus bertindak | P0 |
| US-06 | Sebagai operator, saya tidak ingin dibanjiri notifikasi ratusan/ribuan tiket lama ketika aplikasi pertama kali dijalankan | P0 |
| US-07 | Sebagai operator, saya ingin diberitahu jika HTS tidak bisa diakses, tanpa notifikasi yang berulang-ulang setiap beberapa detik | P1 |

### System Administrator

| ID | User Story | Priority |
|---|---|---|
| US-08 | Sebagai sysadmin, saya ingin mengatur polling interval melalui `.env` tanpa mengubah source code | P0 |
| US-09 | Sebagai sysadmin, saya ingin seluruh credential dan token tersimpan di `.env` dan tidak hardcoded | P0 |
| US-10 | Sebagai sysadmin, saya ingin aplikasi dapat restart otomatis jika crash menggunakan PM2 | P1 |
| US-11 | Sebagai sysadmin, saya ingin dapat mengecek status aplikasi melalui health endpoint | P1 |
| US-12 | Sebagai sysadmin, saya ingin database SQLite memiliki backup otomatis | P1 |
| US-13 | Sebagai sysadmin, saya ingin log aplikasi memiliki level dan format yang jelas | P1 |

---

## 8. Functional Requirements

### FR-AUTH: Authentication & Session

| ID | Requirement | Priority |
|---|---|---|
| FR-AUTH-01 | Aplikasi HARUS dapat melakukan login ke HTS menggunakan credential dari `.env` | P0 |
| FR-AUTH-02 | Aplikasi HARUS mendeteksi kondisi session expired (redirect ke login, unauthorized response, dsb.) | P0 |
| FR-AUTH-03 | Ketika session expired dan CAPTCHA TIDAK diperlukan, aplikasi HARUS mencoba re-login otomatis | P0 |
| FR-AUTH-04 | Ketika session expired dan CAPTCHA diperlukan, aplikasi HARUS mengirim Telegram notification dan TIDAK boleh mencoba bypass CAPTCHA | P0 |
| FR-AUTH-05 | Setelah session valid kembali, aplikasi HARUS melanjutkan monitoring secara otomatis | P0 |
| FR-AUTH-06 | Credential TIDAK BOLEH dicatat di log dalam bentuk plaintext | P0 |

### FR-POLL: Polling & Data Fetching

| ID | Requirement | Priority |
|---|---|---|
| FR-POLL-01 | Aplikasi HARUS melakukan polling terhadap HTS secara periodik dengan interval yang dikonfigurasi | P0 |
| FR-POLL-02 | Polling interval HARUS dapat dikonfigurasi melalui `.env` (POLL_INTERVAL) | P0 |
| FR-POLL-03 | Sebelum implementasi HTML scraping, aplikasi HARUS memeriksa ketersediaan API/AJAX/JSON endpoint pada HTS | P0 |
| FR-POLL-04 | Jika API/AJAX endpoint tersedia dan dapat digunakan secara sah, HARUS diprioritaskan di atas HTML scraping | P0 |
| FR-POLL-05 | Jika hanya HTML yang tersedia, HARUS dilakukan scraping dengan pendekatan stabil dan maintainable | P0 |
| FR-POLL-06 | Setiap request HARUS memiliki timeout yang dikonfigurasi | P0 |
| FR-POLL-07 | Aplikasi HARUS menggunakan session/connection reuse untuk efisiensi | P1 |

### FR-SYNC: Initial Sync

| ID | Requirement | Priority |
|---|---|---|
| FR-SYNC-01 | Ketika pertama kali dijalankan (atau INITIAL_SYNC=true), aplikasi HARUS mengambil seluruh tiket yang ada | P0 |
| FR-SYNC-02 | Tiket yang ditemukan saat initial sync HARUS disimpan ke database tanpa mengirim notifikasi Telegram | P0 |
| FR-SYNC-03 | Setelah initial sync selesai, monitoring normal HARUS diaktifkan | P0 |
| FR-SYNC-04 | Tiket yang muncul setelah initial sync selesai HARUS dianggap sebagai tiket baru | P0 |

### FR-DETECT: Ticket Detection

| ID | Requirement | Priority |
|---|---|---|
| FR-DETECT-01 | Aplikasi HARUS mendeteksi tiket baru berdasarkan nomor aduan yang belum ada di database | P0 |
| FR-DETECT-02 | Ketika tiket baru ditemukan, HARUS dibuat event NEW_TICKET | P0 |
| FR-DETECT-03 | Tiket baru HARUS disimpan lengkap ke database beserta timestamp `first_seen` | P0 |
| FR-DETECT-04 | Notifikasi Telegram untuk tiket baru HARUS dikirim tepat satu kali per event | P0 |

### FR-CHANGE: Change Detection

| ID | Requirement | Priority |
|---|---|---|
| FR-CHANGE-01 | Aplikasi HARUS membandingkan data tiket saat ini dengan snapshot terakhir | P0 |
| FR-CHANGE-02 | Perubahan pada SELURUH field yang dipantau HARUS dideteksi, tidak hanya status | P0 |
| FR-CHANGE-03 | Setiap perubahan HARUS menghasilkan event TICKET_CHANGED | P0 |
| FR-CHANGE-04 | Event perubahan HARUS mencatat field apa yang berubah, nilai lama, dan nilai baru | P0 |
| FR-CHANGE-05 | Snapshot terbaru HARUS disimpan setelah setiap perubahan terdeteksi | P0 |
| FR-CHANGE-06 | Notifikasi Telegram perubahan HARUS berisi data tiket terbaru | P0 |

### FR-COMPLETE: Completion Detection

| ID | Requirement | Priority |
|---|---|---|
| FR-COMPLETE-01 | Aplikasi HARUS mendeteksi ketika status tiket berubah menjadi `TBD_STATUS_COMPLETED` | P0 |
| FR-COMPLETE-02 | Ketika tiket selesai, HARUS dibuat event COMPLETED | P0 |
| FR-COMPLETE-03 | Notifikasi COMPLETED HARUS dikirim tepat satu kali untuk setiap completion event | P0 |
| FR-COMPLETE-04 | Jika tiket sudah pernah dibuat event COMPLETED, jangan buat duplikat | P0 |

### FR-RECON: Reconciliation

| ID | Requirement | Priority |
|---|---|---|
| FR-RECON-01 | Ketika aplikasi restart, HARUS dilakukan reconciliation untuk menemukan tiket baru yang muncul saat aplikasi offline | P0 |
| FR-RECON-02 | Tiket yang ditemukan saat reconciliation dan belum ada di database HARUS diproses sebagai tiket baru | P0 |
| FR-RECON-03 | Tiket yang sudah ada di database dan mengalami perubahan saat aplikasi offline HARUS menghasilkan event TICKET_CHANGED | P0 |
| FR-RECON-04 | Reconciliation HARUS didahulukan sebelum monitoring normal dimulai kembali | P0 |

### FR-TG: Telegram Notification

| ID | Requirement | Priority |
|---|---|---|
| FR-TG-01 | Aplikasi HARUS menggunakan Telegram Bot API untuk mengirim notifikasi | P0 |
| FR-TG-02 | Setiap notifikasi HARUS menggunakan template yang telah ditentukan | P0 |
| FR-TG-03 | Teks notifikasi HARUS siap di-*copy-paste* ke WhatsApp tanpa modifikasi | P0 |
| FR-TG-04 | Jika pengiriman gagal, HARUS dicoba ulang dengan strategi retry yang dikontrol | P0 |
| FR-TG-05 | Event yang gagal dikirim TIDAK BOLEH hilang dari database | P0 |
| FR-TG-06 | Sistem HARUS menerapkan prinsip AT-LEAST-ONCE delivery | P0 |
| FR-TG-07 | Sistem HARUS memiliki deduplication berdasarkan notification ID / event ID | P0 |

### FR-HEALTH: Health Endpoint

| ID | Requirement | Priority |
|---|---|---|
| FR-HEALTH-01 | Aplikasi HARUS menyediakan HTTP endpoint `GET /health` | P1 |
| FR-HEALTH-02 | Health endpoint HARUS mengembalikan status: `healthy`, `degraded`, atau `unhealthy` | P1 |
| FR-HEALTH-03 | Health endpoint TIDAK BOLEH membocorkan credential atau session data | P1 |

---

## 9. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-01 | **Reliability** | Aplikasi harus berjalan 24/7 tanpa intervensi manual kecuali untuk CAPTCHA |
| NFR-02 | **Reliability** | Tidak boleh ada data loss untuk tiket atau event, bahkan ketika Telegram atau HTS mengalami gangguan |
| NFR-03 | **Reliability** | Aplikasi harus dapat restart otomatis melalui PM2 tanpa kehilangan state |
| NFR-04 | **Performance** | Satu siklus polling (fetch + parse + compare + store) harus selesai dalam waktu yang wajar sebelum polling berikutnya (target: < POLL_INTERVAL) |
| NFR-05 | **Performance** | Polling tidak boleh membebani HTS secara berlebihan |
| NFR-06 | **Security** | Credential, token, dan session cookie tidak boleh tercatat di log |
| NFR-07 | **Security** | File `.env` harus terdaftar di `.gitignore` |
| NFR-08 | **Maintainability** | Kode harus menggunakan structured logging |
| NFR-09 | **Maintainability** | Konfigurasi harus dapat diubah tanpa mengubah source code |
| NFR-10 | **Operability** | Graceful shutdown harus dapat menyelesaikan pekerjaan yang sedang berjalan sebelum exit |
| NFR-11 | **Operability** | Health endpoint harus tersedia untuk monitoring eksternal |
| NFR-12 | **Data Integrity** | Database harus menggunakan transaction untuk operasi yang bersifat atomic |
| NFR-13 | **Compatibility** | Aplikasi harus berjalan di Ubuntu / WSL dengan Python |
| NFR-14 | **Observability** | Seluruh event penting harus tercatat di log dengan level, timestamp, dan konteks yang tepat |

---

## 10. System Workflow

### High-Level Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│                     APPLICATION START                            │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│              LOAD CONFIGURATION (.env)                           │
│  - Validate required env vars                                    │
│  - Set up logging                                                │
│  - Initialize database                                           │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     HTS LOGIN                                    │
│  - POST credentials to login endpoint                            │
│  - Handle CAPTCHA if present → notify operator, wait            │
│  - Store session                                                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                  INITIAL SYNC (if enabled)                       │
│  - Fetch all current tickets                                     │
│  - Store all to DB                                               │
│  - DO NOT send Telegram notifications                            │
│  - Mark sync as complete                                         │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                  RECONCILIATION                                  │
│  - Compare DB state vs. current HTS state                        │
│  - Detect missed tickets / changes during offline period         │
│  - Process as new events                                         │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                   MONITORING LOOP                                │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Wait POLL_INTERVAL seconds                               │  │
│  │  Fetch ticket list from HTS                               │  │
│  │  For each ticket:                                         │  │
│  │    - New ticket? → NEW_TICKET event → Telegram            │  │
│  │    - Changed? → TICKET_CHANGED event → Telegram           │  │
│  │    - Completed? → COMPLETED event → Telegram              │  │
│  │  Update DB & snapshots                                    │  │
│  └───────────────────────────────────────────────────────────┘  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                    (Interrupted / SIGTERM)
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                   GRACEFUL SHUTDOWN                              │
│  - Stop accepting new polls                                      │
│  - Finish in-progress work                                       │
│  - Flush logs                                                    │
│  - Close DB connection                                           │
│  - Close HTTP session                                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 11. Authentication & Session Management

### 11.1 Login Flow

Proses login akan disesuaikan dengan mekanisme aktual HTS setelah verifikasi teknis. Secara konseptual, flow-nya adalah:

```
                    ┌──────────────────┐
                    │   APPLICATION    │
                    │     START        │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │  GET Login Page  │◄──────────────────────┐
                    │  (check CAPTCHA) │                       │
                    └────────┬─────────┘                       │
                             │                                 │
              ┌──────────────┴──────────────┐                  │
              │                             │                  │
         No CAPTCHA                    CAPTCHA Present          │
              │                             │                  │
    ┌─────────▼──────────┐     ┌────────────▼────────────┐    │
    │  POST Login with   │     │  Send Telegram Alert:   │    │
    │  username/password │     │  "Manual login needed"  │    │
    └─────────┬──────────┘     └────────────┬────────────┘    │
              │                             │                  │
    ┌─────────▼──────────┐     ┌────────────▼────────────┐    │
    │   Login Success?   │     │  WAIT for operator to   │    │
    └───┬────────────┬───┘     │  complete login manually│    │
        │            │         └────────────┬────────────┘    │
      YES            NO                     │                  │
        │            │         Periodically check session ────►┘
        │       Error/Retry
        │
┌───────▼──────────┐
│  Store Session   │
│  Authenticated   │
└──────────────────┘
```

### 11.2 Session Detection

Aplikasi harus mendeteksi session expired melalui:

| Indikasi | Deskripsi |
|---|---|
| HTTP 3xx redirect ke URL login | Response dialihkan ke halaman login |
| HTTP 401 / 403 | Unauthorized / Forbidden response |
| Konten HTML mengandung form login | Response body mengandung elemen login form |
| Cookie session tidak valid | Session cookie ditolak oleh server |
| **[TBD]** Indikasi spesifik HTS | Perlu diverifikasi dari respons aktual HTS |

### 11.3 Session State Machine

```
         ┌─────────────┐
    ┌────►│ UNAUTHENTICATED │◄────────────────────┐
    │    └──────┬──────┘                          │
    │           │ (login attempt)                 │
    │           ▼                                 │
    │    ┌─────────────────────┐                  │
    │    │  AUTHENTICATING     │                  │
    │    └──────┬──────────────┘                  │
    │           │                                 │
    │    ┌──────┴──────────────────────────┐      │
    │    │                                 │      │
    │  No CAPTCHA                    CAPTCHA       │
    │    │                                 │      │
    │    ▼                                 ▼      │
    │ ┌──────────────┐         ┌─────────────────┐│
    │ │ AUTHENTICATED│         │ CAPTCHA_REQUIRED ││
    │ └──────┬───────┘         └────────┬────────┘│
    │        │                         │          │
    │        │ (session                │ (manual  │
    │        │  expires)               │  login)  │
    │        ▼                         │          │
    │ ┌──────────────────┐             │          │
    │ │ SESSION_EXPIRED  │             │          │
    │ └──────┬──────┬────┘             │          │
    │        │      │                  │          │
    │  (No   │      │ (CAPTCHA         │          │
    │  CAPTCHA)    │  needed)          │          │
    │        │      ▼                  │          │
    │        │  CAPTCHA_REQUIRED◄──────┘          │
    │        │      │                             │
    │        │      │ (operator completes)        │
    │        ▼      └────────────────────────────►┘
    │ REAUTHENTICATING
    └────────┘ (fail)
```

### 11.4 Session Recovery Rules

| Kondisi | Aksi |
|---|---|
| Session expired, tanpa CAPTCHA | Re-login otomatis |
| Session expired, CAPTCHA diperlukan | Kirim Telegram alert, masuk ke state CAPTCHA_REQUIRED, tunggu |
| Re-login berhasil | Lakukan reconciliation, lanjutkan monitoring |
| Re-login gagal berulang kali | Kirim Telegram alert, terapkan exponential backoff |

---

## 12. Monitoring & Polling

### 12.1 Polling Strategy

Prioritas implementasi polling (dari tertinggi ke terendah):

```
┌─────────────────────────────────────────────────┐
│  PRIORITAS 1: API / JSON / AJAX Endpoint        │
│  - Jika HTS memiliki endpoint JSON yang dapat   │
│    digunakan secara sah                         │
│  - Lebih efisien, lebih stabil                  │
├─────────────────────────────────────────────────┤
│  PRIORITAS 2: Incremental / Filtered Query      │
│  - Jika endpoint mendukung filter tanggal/      │
│    paginasi                                     │
│  - Minimalkan data yang diambil per polling     │
├─────────────────────────────────────────────────┤
│  PRIORITAS 3: Full HTML Scraping                │
│  - Fallback jika tidak ada endpoint             │
│  - Menggunakan selector yang stabil             │
│  - Minimal parsing overhead                     │
└─────────────────────────────────────────────────┘
```

> **[TBD]** Strategi polling aktual akan ditentukan setelah inspeksi teknis HTS. Lihat Bagian 31.

### 12.2 Polling Cycle

```
┌──────────────────────────────────────────────────────────────────┐
│                       POLLING CYCLE                              │
│                                                                  │
│  1. CHECK SESSION STATUS                                         │
│     - Authenticated? → Continue                                  │
│     - Expired? → Trigger recovery flow                           │
│                                                                  │
│  2. FETCH TICKET DATA                                            │
│     - Send request with timeout (REQUEST_TIMEOUT seconds)        │
│     - HTS unreachable? → Trigger HTS failure flow               │
│     - Parse response                                             │
│                                                                  │
│  3. PROCESS TICKETS                                              │
│     - Compare with DB                                            │
│     - Create events                                              │
│     - Store updates                                              │
│                                                                  │
│  4. SEND NOTIFICATIONS                                           │
│     - Process pending notification queue                         │
│     - Retry failed notifications                                 │
│                                                                  │
│  5. UPDATE HEALTH STATE                                          │
│     - last_poll timestamp                                        │
│     - last_successful_poll timestamp                             │
│                                                                  │
│  6. WAIT POLL_INTERVAL                                           │
│     - Interruptible sleep (respond to SIGTERM)                   │
└──────────────────────────────────────────────────────────────────┘
```

### 12.3 Polling Efficiency Rules

- Gunakan HTTP session reuse (persistent connection)
- Set timeout pada setiap request (`REQUEST_TIMEOUT` dari `.env`)
- Jangan melakukan request paralel yang tidak perlu ke HTS
- Jika HTS mendukung `Last-Modified` atau `ETag`, manfaatkan conditional requests
- **[TBD]** Periksa apakah HTS memiliki rate limiting

---

## 13. Ticket Detection

### 13.1 New Ticket Detection

```
For each ticket in current_poll:
    if ticket.nomor_aduan NOT IN database:
        is_initial_sync = check_initial_sync_flag()
        
        if is_initial_sync:
            save_ticket(ticket)
            # DO NOT send Telegram
        else:
            save_ticket(ticket)
            create_event(NEW_TICKET)
            send_telegram(NEW_TICKET)
```

### 13.2 Data Snapshot Pada Deteksi Tiket Baru

Ketika tiket baru ditemukan, sistem harus menyimpan:
- Seluruh field tiket ke tabel `tickets`
- Snapshot awal ke tabel `ticket_snapshots`
- Timestamp `first_seen` dan `last_seen`
- Event ke tabel `ticket_events`

### 13.3 Field yang Dipantau

| Field | Deskripsi | Notes |
|---|---|---|
| nomor_aduan | Primary business identifier | Unik, tidak berubah |
| kategori | Kategori aduan | **[TBD]** Nama field aktual |
| sub_kategori | Sub-kategori aduan | **[TBD]** Nama field aktual |
| instansi | Instansi pelapor | **[TBD]** Nama field aktual |
| opd_induk | OPD Induk | **[TBD]** Nama field aktual, nullable |
| pic_nama | Nama PIC | **[TBD]** Nama field aktual |
| pic_nomor | Nomor kontak PIC | **[TBD]** Nama field aktual |
| keluhan | Detail keluhan/aduan | **[TBD]** Nama field aktual |
| status | Status tiket | **[TBD]** Nilai valid dan value "selesai" |

---

## 14. Change Detection

### 14.1 Perubahan yang Dideteksi

Perubahan dideteksi pada **semua field** yang dipantau (lihat 13.3), bukan hanya field status.

### 14.2 Strategi Change Detection

```
┌──────────────────────────────────────────────────────────────────┐
│                   CHANGE DETECTION STRATEGY                      │
│                                                                  │
│  1. HASH-BASED QUICK CHECK (Optimasi)                            │
│     Hitung hash dari concatenated field values tiket.            │
│     Jika hash sama dengan snapshot terakhir → skip (no change).  │
│     Jika hash berbeda → lakukan field-by-field comparison.       │
│                                                                  │
│  2. FIELD-BY-FIELD COMPARISON (Akurasi)                          │
│     Compare setiap field antara current_data vs last_snapshot.   │
│     Catat hanya field yang benar-benar berbeda.                  │
│                                                                  │
│  3. GENERATE CHANGE RECORD                                       │
│     {                                                            │
│       "keluhan": {                                               │
│         "old": "Internet tidak bisa digunakan",                  │
│         "new": "Internet tidak bisa digunakan di lantai 2"       │
│       }                                                          │
│     }                                                            │
│                                                                  │
│  4. STORE SNAPSHOT + EVENT                                       │
│     - Save new snapshot                                          │
│     - Create TICKET_CHANGED event with changed_fields            │
│     - Queue notification                                         │
└──────────────────────────────────────────────────────────────────┘
```

### 14.3 Multiple Changes (Rapid Change Handling)

Skenario: Satu tiket mengalami perubahan berulang dalam jangka waktu pendek.

```
07:00:00 - Keluhan: "A"   ← Snapshot disimpan
07:00:05 - Keluhan: "B"   ← Polling mendeteksi: A→B, event dibuat
07:00:10 - Keluhan: "C"   ← Polling mendeteksi: B→C, event dibuat
```

**Default Behavior**: Setiap perubahan yang terdeteksi polling menghasilkan event terpisah. Tidak ada perubahan yang dihilangkan.

**Debounce (Optional, Configurable)**:

| Parameter | Default | Deskripsi |
|---|---|---|
| `CHANGE_DEBOUNCE_SECONDS` | 0 (disabled) | Jika > 0, tunda pembuatan event selama N detik setelah perubahan pertama terdeteksi. Perubahan-perubahan dalam window tersebut digabung menjadi satu event. |

> **Konsekuensi Debounce**: Jika debounce diaktifkan, perubahan intermediate tidak akan menghasilkan notifikasi terpisah. History tetap tersimpan di snapshot, tetapi hanya perubahan final yang dikirim ke Telegram. Default **TIDAK** menggunakan debounce agar tidak ada perubahan yang hilang.

### 14.4 Deduplication

- Setiap event memiliki event ID yang unik (UUID atau kombinasi `nomor_aduan + timestamp + hash`)
- Sebelum membuat event baru, periksa apakah event dengan karakteristik yang sama sudah ada dalam window waktu tertentu
- Notification table menyimpan notification ID untuk mencegah pengiriman duplikat

---

## 15. Initial Sync

### 15.1 Tujuan

Mencegah banjir notifikasi ketika aplikasi pertama kali dijalankan dengan tiket-tiket yang sudah ada sebelum deployment.

### 15.2 Flow

```
                ┌──────────────────────────────────┐
                │       INITIAL_SYNC = true         │
                └─────────────────┬────────────────┘
                                  │
                                  ▼
                ┌──────────────────────────────────┐
                │  Fetch ALL tickets from HTS       │
                │  (dengan pagination jika ada)     │
                └─────────────────┬────────────────┘
                                  │
                                  ▼
                ┌──────────────────────────────────┐
                │  For each ticket:                 │
                │  - Save to database               │
                │  - Save initial snapshot          │
                │  - Mark as sync_source='initial'  │
                │  - DO NOT create events           │
                │  - DO NOT send Telegram           │
                └─────────────────┬────────────────┘
                                  │
                                  ▼
                ┌──────────────────────────────────┐
                │  Mark initial sync complete       │
                │  (flag in system_events or DB)    │
                └─────────────────┬────────────────┘
                                  │
                                  ▼
                ┌──────────────────────────────────┐
                │     START MONITORING LOOP         │
                └──────────────────────────────────┘
```

### 15.3 Idempotency

- Jika aplikasi crash di tengah initial sync dan di-restart, sync harus dapat dilanjutkan atau diulang dari awal dengan aman
- Tiket yang sudah tersimpan selama partial sync tidak boleh diproses sebagai tiket baru

### 15.4 Konfigurasi

| Variabel | Default | Deskripsi |
|---|---|---|
| `INITIAL_SYNC` | `true` | `true`: jalankan initial sync. `false`: asumsikan DB sudah fresh dan monitoring langsung dimulai |

---

## 16. Reconciliation

### 16.1 Tujuan

Memastikan tiket dan perubahan yang terjadi saat aplikasi offline tidak hilang.

### 16.2 Kapan Reconciliation Dijalankan

- Setelah aplikasi restart (non-initial-sync restart)
- Setelah recovery dari session expired
- Setelah recovery dari HTS unavailable

### 16.3 Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     RECONCILIATION                               │
│                                                                  │
│  1. Fetch current ticket list from HTS                           │
│                                                                  │
│  2. For each ticket in current HTS data:                         │
│     A. NOT in DB → process as NEW_TICKET (send notification)     │
│     B. IN DB, data changed → process as TICKET_CHANGED          │
│        (send notification)                                       │
│     C. IN DB, no change → update last_seen timestamp only       │
│                                                                  │
│  3. Tickets in DB but NOT in current HTS data:                   │
│     - Log warning                                                │
│     - [TBD] Strategi: apakah tiket bisa dihapus dari HTS?       │
│       Jika ya, buat event TICKET_REMOVED (out of scope V1        │
│       untuk pengiriman notifikasi, cukup dicatat)               │
│                                                                  │
│  4. Log reconciliation results:                                  │
│     - Total tickets compared                                     │
│     - New tickets found                                          │
│     - Changed tickets found                                      │
│     - Reconciliation duration                                    │
│                                                                  │
│  5. Proceed to MONITORING state                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 16.4 Priority: No Ticket Left Behind

Reconciliation harus memprioritaskan **tidak ada tiket yang terlewat**, bahkan jika ini berarti notifikasi yang sudah lama tapi belum pernah dikirim akan dikirim ketika aplikasi kembali online.

---

## 17. Telegram Notification

### 17.1 Template Tiket Masuk (NEW_TICKET)

```
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
{pic_nama} ({pic_nomor})

Keluhan:
{keluhan}

Status:
{status}
```

### 17.2 Template Perubahan Aduan (TICKET_CHANGED)

```
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
{pic_nama} ({pic_nomor})

Keluhan:
{keluhan}

Status:
{status}
```

### 17.3 Template Tiket Selesai (COMPLETED)

```
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
{pic_nama} ({pic_nomor})

Keluhan:
{keluhan}

Status:
{status}
```

### 17.4 Fallback Rules untuk Field Kosong / Null

| Field | Fallback |
|---|---|
| `opd_induk` | Tampilkan `OPD Induk:` diikuti baris kosong (tetap ada label, tidak ada nilai) |
| `pic_nomor` | Tampilkan `{pic_nama}` tanpa tanda kurung jika nomor kosong |
| `sub_kategori` | Tampilkan `{kategori} /` jika sub kategori kosong |
| Field lainnya (selain OPD Induk) | Tampilkan `-` sebagai nilai placeholder |

### 17.5 System Alert Templates

**HTS Connection Error:**
```
⚠️ HTS CONNECTION ERROR

HTS tidak dapat diakses sejak:
{timestamp}

Monitoring sementara dihentikan.
Retry otomatis akan dilakukan.
```

**HTS Connection Recovered:**
```
✅ HTS CONNECTION RECOVERED

Koneksi HTS kembali normal.
{timestamp}
```

**CAPTCHA Required:**
```
🔐 LOGIN MANUAL DIPERLUKAN

HTS memerlukan CAPTCHA untuk login.
Timestamp: {timestamp}

Silakan login secara manual ke:
{HTS_BASE_URL}

Monitoring akan dilanjutkan secara otomatis setelah sesi valid.
```

**Session Expired (auto-recovery gagal):**
```
⚠️ SESSION EXPIRED

Sesi HTS telah berakhir dan tidak dapat dipulihkan otomatis.
Timestamp: {timestamp}

Diperlukan login manual.
```

### 17.6 Notification Suppression (Anti-Spam)

| Skenario | Behavior |
|---|---|
| HTS down berulang dalam satu incident | Kirim SATU alert saat awal incident terdeteksi |
| HTS recovered | Kirim SATU recovery notification |
| CAPTCHA required berulang tanpa perubahan state | Kirim reminder dengan interval minimal (konfigurasikan `CAPTCHA_REMINDER_INTERVAL`, default 30 menit) |

### 17.7 Retry Strategy

```
Attempt 1: Immediate
Attempt 2: 15 seconds delay
Attempt 3: 60 seconds delay
Attempt 4: 5 minutes delay
Attempt 5+: Configurable max delay (default: 30 minutes)

Max attempts: Configurable (MAX_RETRY, default: tidak terbatas dengan cap delay)
```

> Setelah batas retry tercapai tanpa berhasil, event tetap tersimpan di database dengan status `RETRY_EXHAUSTED` dan dapat diproses ulang secara manual atau saat startup berikutnya.

---

## 18. Error Handling

### 18.1 HTS Unreachable

```
┌────────────────────────────────────────────────────────────────┐
│                 HTS FAILURE HANDLING                            │
│                                                                 │
│  Deteksi: Connection timeout / HTTP 5xx / DNS failure          │
│                                                                 │
│  Aksi:                                                          │
│  1. Log error dengan timestamp                                  │
│  2. Set health state → HTS_UNAVAILABLE                         │
│  3. Kirim Telegram alert (HANYA SATU per incident)             │
│  4. Enter retry loop dengan exponential backoff:               │
│     - Retry 1: 15 detik                                         │
│     - Retry 2: 30 detik                                         │
│     - Retry 3: 60 detik                                         │
│     - Retry N: min(base * 2^n, MAX_BACKOFF_SECONDS)            │
│  5. Ketika HTS dapat diakses kembali:                           │
│     - Set health state → MONITORING                             │
│     - Kirim Telegram recovery notification                      │
│     - Lakukan reconciliation                                    │
│     - Lanjutkan monitoring                                      │
└────────────────────────────────────────────────────────────────┘
```

### 18.2 Database Error

| Jenis Error | Aksi |
|---|---|
| Koneksi DB gagal saat startup | Log, exit dengan error |
| Write gagal saat monitoring | Log error, retry operation, kirim Telegram alert jika kritis |
| Read gagal saat perbandingan | Log error, skip siklus, lanjutkan polling |
| Corruption detected | Log critical, kirim Telegram alert, jangan crash loop |

### 18.3 Telegram API Error

| HTTP Code | Aksi |
|---|---|
| 400 Bad Request | Log error, simpan sebagai FAILED, jangan retry (pesan rusak) |
| 401 Unauthorized | Log error kritis — token tidak valid |
| 429 Too Many Requests | Terapkan Telegram-specified retry-after |
| 5xx Server Error | Retry dengan exponential backoff |
| Network error | Retry dengan exponential backoff |

### 18.4 Notification Queue

Semua notifikasi yang gagal disimpan di tabel `notifications` dengan status `FAILED`. Setiap siklus polling, aplikasi memproses antrian notifikasi yang belum berhasil dikirim.

```
┌─────────────────────────────────────────────────────────┐
│            NOTIFICATION QUEUE PROCESSOR                  │
│                                                          │
│  1. Query: SELECT * FROM notifications                   │
│            WHERE status IN ('PENDING', 'FAILED')         │
│            AND attempt_count < max_retry                 │
│            ORDER BY created_at ASC                       │
│                                                          │
│  2. For each notification:                               │
│     - Check if already sent (deduplication)             │
│     - Attempt send                                       │
│     - Update status (SENT / FAILED)                      │
│     - Update attempt_count                               │
│     - Update next_retry_at                               │
└─────────────────────────────────────────────────────────┘
```

---

## 19. Database Requirements

### 19.1 Technology

- **SQLite** untuk V1
- Gunakan **WAL mode** (Write-Ahead Logging) untuk performa dan integritas yang lebih baik
- Gunakan **foreign keys** dan **transactions** untuk integritas data

### 19.2 Tabel: `tickets`

| Column | Type | Constraint | Deskripsi |
|---|---|---|---|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Internal ID |
| `nomor_aduan` | TEXT | UNIQUE NOT NULL | Nomor aduan — business identifier |
| `kategori` | TEXT | | Kategori aduan |
| `sub_kategori` | TEXT | | Sub-kategori aduan |
| `instansi` | TEXT | | Instansi pelapor |
| `opd_induk` | TEXT | NULLABLE | OPD Induk (bisa kosong) |
| `pic_nama` | TEXT | | Nama PIC |
| `pic_nomor` | TEXT | | Nomor kontak PIC |
| `keluhan` | TEXT | | Detail keluhan |
| `status` | TEXT | | Status tiket |
| `is_completed` | INTEGER | DEFAULT 0 | Flag 1 jika tiket sudah selesai |
| `sync_source` | TEXT | | 'initial' / 'monitoring' / 'reconciliation' |
| `first_seen` | TEXT | NOT NULL | Timestamp pertama kali ditemukan (ISO 8601) |
| `last_seen` | TEXT | NOT NULL | Timestamp terakhir kali ditemukan di polling |
| `last_hash` | TEXT | | Hash dari seluruh field untuk optimasi change detection |
| `created_at` | TEXT | NOT NULL | Timestamp record dibuat |
| `updated_at` | TEXT | NOT NULL | Timestamp record terakhir diperbarui |

**Indexes**: `nomor_aduan` (UNIQUE), `status`, `is_completed`

### 19.3 Tabel: `ticket_snapshots`

| Column | Type | Constraint | Deskripsi |
|---|---|---|---|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `ticket_id` | INTEGER | FOREIGN KEY → tickets.id | |
| `nomor_aduan` | TEXT | NOT NULL | Redundant untuk query efisiensi |
| `snapshot_data` | TEXT | NOT NULL | JSON string dari seluruh field tiket saat snapshot |
| `snapshot_hash` | TEXT | | Hash dari snapshot_data |
| `snapshot_type` | TEXT | | 'initial' / 'update' |
| `created_at` | TEXT | NOT NULL | Timestamp snapshot dibuat |

**Indexes**: `ticket_id`, `nomor_aduan`, `created_at`

### 19.4 Tabel: `ticket_events`

| Column | Type | Constraint | Deskripsi |
|---|---|---|---|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `event_id` | TEXT | UNIQUE NOT NULL | UUID event |
| `ticket_id` | INTEGER | FOREIGN KEY → tickets.id | |
| `nomor_aduan` | TEXT | NOT NULL | |
| `event_type` | TEXT | NOT NULL | NEW_TICKET / TICKET_CHANGED / STATUS_CHANGED / COMPLETED |
| `changed_fields` | TEXT | NULLABLE | JSON: `{"field": {"old": "...", "new": "..."}}` |
| `previous_snapshot_id` | INTEGER | NULLABLE | FK → ticket_snapshots.id |
| `current_snapshot_id` | INTEGER | NULLABLE | FK → ticket_snapshots.id |
| `event_timestamp` | TEXT | NOT NULL | Waktu event terdeteksi |
| `created_at` | TEXT | NOT NULL | Waktu record dibuat |

**Indexes**: `event_id` (UNIQUE), `ticket_id`, `nomor_aduan`, `event_type`, `event_timestamp`

**Event Types**:
| Event Type | Kapan Dibuat |
|---|---|
| `NEW_TICKET` | Tiket baru ditemukan (bukan saat initial sync) |
| `TICKET_CHANGED` | Satu atau lebih field berubah |
| `STATUS_CHANGED` | Field status berubah (subset dari TICKET_CHANGED) |
| `COMPLETED` | Status berubah menjadi `TBD_STATUS_COMPLETED` |

### 19.5 Tabel: `notifications`

| Column | Type | Constraint | Deskripsi |
|---|---|---|---|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `notification_id` | TEXT | UNIQUE NOT NULL | UUID notifikasi |
| `event_id` | TEXT | FOREIGN KEY → ticket_events.event_id | Bisa NULL untuk system notifications |
| `channel` | TEXT | NOT NULL | 'telegram' |
| `message_text` | TEXT | NOT NULL | Teks pesan yang dikirim |
| `status` | TEXT | NOT NULL | PENDING / SENT / FAILED / RETRY_EXHAUSTED |
| `attempt_count` | INTEGER | DEFAULT 0 | |
| `max_attempts` | INTEGER | NOT NULL | |
| `next_retry_at` | TEXT | NULLABLE | Timestamp retry berikutnya |
| `sent_at` | TEXT | NULLABLE | Timestamp berhasil terkirim |
| `telegram_message_id` | TEXT | NULLABLE | Message ID dari Telegram |
| `last_error` | TEXT | NULLABLE | Pesan error terakhir |
| `created_at` | TEXT | NOT NULL | |
| `updated_at` | TEXT | NOT NULL | |

**Indexes**: `notification_id` (UNIQUE), `event_id`, `status`, `next_retry_at`

### 19.6 Tabel: `system_events`

| Column | Type | Constraint | Deskripsi |
|---|---|---|---|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `event_type` | TEXT | NOT NULL | Lihat daftar di bawah |
| `description` | TEXT | | Deskripsi tambahan |
| `metadata` | TEXT | NULLABLE | JSON data tambahan |
| `event_timestamp` | TEXT | NOT NULL | |
| `created_at` | TEXT | NOT NULL | |

**System Event Types**:
| Event Type | Deskripsi |
|---|---|
| `APP_START` | Aplikasi mulai |
| `APP_STOP` | Aplikasi berhenti |
| `HTS_LOGIN` | Login HTS berhasil |
| `HTS_LOGOUT` | Logout / session expired |
| `CAPTCHA_REQUIRED` | CAPTCHA diperlukan |
| `SESSION_EXPIRED` | Session expired terdeteksi |
| `SESSION_RECOVERED` | Session berhasil dipulihkan |
| `HTS_UNAVAILABLE` | HTS tidak dapat diakses |
| `HTS_RECOVERED` | HTS dapat diakses kembali |
| `INITIAL_SYNC_START` | Initial sync dimulai |
| `INITIAL_SYNC_COMPLETE` | Initial sync selesai |
| `RECONCILIATION_START` | Reconciliation dimulai |
| `RECONCILIATION_COMPLETE` | Reconciliation selesai |
| `BACKUP_START` | Backup database dimulai |
| `BACKUP_COMPLETE` | Backup database selesai |
| `BACKUP_FAILED` | Backup database gagal |
| `DB_ERROR` | Error database |

### 19.7 Database Constraints & Integrity Rules

- `nomor_aduan` pada tabel `tickets` harus UNIQUE
- `event_id` pada tabel `ticket_events` harus UNIQUE
- `notification_id` pada tabel `notifications` harus UNIQUE
- Seluruh operasi write yang berkaitan (ticket + snapshot + event + notification) harus dalam satu database transaction
- Gunakan SQLite WAL mode untuk meningkatkan concurrent read performance
- Aktifkan `PRAGMA foreign_keys = ON` saat koneksi dibuka

---

## 20. Security

### 20.1 Credential Management

| Requirement | Detail |
|---|---|
| Tidak ada hardcoded credential | Seluruh credential, token, dan URL berasal dari `.env` |
| `.env` wajib ada di `.gitignore` | File `.env` tidak boleh masuk ke version control |
| Contoh konfigurasi | Sediakan file `.env.example` dengan nilai placeholder |
| Log filtering | Logger harus memiliki filter untuk mencegah pencetakan password, token, atau cookie |

### 20.2 Session Cookie Security

- Session cookie disimpan dalam memory, bukan di file system
- Jika disimpan ke disk (untuk persistence), gunakan file permission yang ketat (chmod 600)
- Session cookie tidak boleh tercatat di log

### 20.3 CAPTCHA Policy

- Aplikasi **TIDAK BOLEH** menggunakan layanan CAPTCHA solver eksternal
- Aplikasi **TIDAK BOLEH** mencoba bypass CAPTCHA dengan cara apapun
- Jika CAPTCHA diperlukan, satu-satunya aksi yang diperbolehkan adalah mengirim notifikasi ke operator

### 20.4 Telegram Security

- `TELEGRAM_BOT_TOKEN` harus disimpan di `.env`
- Token tidak boleh tercatat di log
- Gunakan HTTPS untuk seluruh komunikasi dengan Telegram Bot API

### 20.5 File Permission

| File | Permission |
|---|---|
| `.env` | `600` (owner read/write only) |
| Database SQLite | `600` |
| Backup files | `600` |
| Log files | `640` |

### 20.6 Dependency Management

- Gunakan `requirements.txt` dengan versi yang dipinning
- Lakukan audit dependency secara berkala terhadap CVE yang diketahui
- Hindari dependency yang tidak diperlukan (prinsip minimal footprint)

### 20.7 Secret Rotation

- Saat TELEGRAM_BOT_TOKEN atau password HTS dirotasi, cukup update `.env` dan restart aplikasi
- Dokumen prosedur rotasi harus disertakan dalam README

---

## 21. Configuration

### 21.1 File `.env` Lengkap

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
POLL_INTERVAL=5              # Detik antara polling (default: 5)
REQUEST_TIMEOUT=10           # Timeout HTTP request dalam detik

# ─────────────────────────────────────────────
# Retry Configuration
# ─────────────────────────────────────────────
RETRY_INITIAL_DELAY=15       # Detik delay retry pertama
MAX_RETRY_DELAY=1800         # Detik maksimum delay retry (30 menit)
MAX_TELEGRAM_RETRY=0         # 0 = unlimited retries (dengan cap delay)

# ─────────────────────────────────────────────
# Application Configuration
# ─────────────────────────────────────────────
INITIAL_SYNC=true            # true: jalankan initial sync saat startup
LOG_LEVEL=INFO               # DEBUG / INFO / WARNING / ERROR / CRITICAL
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
HEALTH_HOST=127.0.0.1        # Jangan expose ke public tanpa authentication

# ─────────────────────────────────────────────
# Optional: Change Debounce
# ─────────────────────────────────────────────
CHANGE_DEBOUNCE_SECONDS=0    # 0 = disabled

# ─────────────────────────────────────────────
# Optional: Session Recovery
# ─────────────────────────────────────────────
CAPTCHA_REMINDER_INTERVAL=1800  # Detik antara pengiriman reminder CAPTCHA
SESSION_CHECK_INTERVAL=60       # Detik antara pengecekan session saat CAPTCHA_REQUIRED
```

### 21.2 Klasifikasi Variabel

| Variabel | Status | Deskripsi |
|---|---|---|
| `HTS_BASE_URL` | **WAJIB** | URL dasar HTS |
| `HTS_USERNAME` | **WAJIB** | Username HTS |
| `HTS_PASSWORD` | **WAJIB** | Password HTS |
| `TELEGRAM_BOT_TOKEN` | **WAJIB** | Token Telegram Bot |
| `TELEGRAM_CHAT_ID` | **WAJIB** | Destination chat/group ID |
| `POLL_INTERVAL` | Optional | Default: 5 |
| `REQUEST_TIMEOUT` | Optional | Default: 10 |
| `INITIAL_SYNC` | Optional | Default: true |
| `LOG_LEVEL` | Optional | Default: INFO |
| `DB_PATH` | Optional | Default: data/hts_monitor.db |
| `BACKUP_ENABLED` | Optional | Default: true |
| Semua lainnya | Optional | Ada default yang aman |

---

## 22. Logging

### 22.1 Format Log

```
{timestamp} | {level} | {event} | {nomor_aduan} | {message}
```

Contoh:
```
2026-09-17T08:30:00+07:00 | INFO  | POLL_START      | -          | Polling cycle #1234 started
2026-09-17T08:30:01+07:00 | INFO  | NEW_TICKET      | ADU-00123  | New ticket detected
2026-09-17T08:30:01+07:00 | INFO  | TG_SENT         | ADU-00123  | Telegram notification sent
2026-09-17T08:30:05+07:00 | WARN  | HTS_SLOW        | -          | Response time 8.2s (threshold: 5s)
2026-09-17T08:31:00+07:00 | ERROR | HTS_UNAVAILABLE | -          | Connection failed: Connection timeout
```

### 22.2 Event yang Wajib Dicatat

| Event | Level | Catatan |
|---|---|---|
| Application start | INFO | |
| Application stop | INFO | |
| Configuration loaded | INFO | Jangan cetak nilai credential |
| HTS login attempt | INFO | |
| HTS login success | INFO | |
| HTS login failed | ERROR | Jangan cetak password |
| Session expired detected | WARNING | |
| CAPTCHA required | WARNING | |
| Session recovered | INFO | |
| Poll cycle start | DEBUG | |
| Poll cycle end | DEBUG | Sertakan durasi |
| Fetch success | DEBUG | Sertakan jumlah tiket yang diambil |
| New ticket detected | INFO | Sertakan nomor_aduan |
| Ticket changed | INFO | Sertakan nomor_aduan dan field yang berubah |
| Ticket completed | INFO | Sertakan nomor_aduan |
| Telegram sent | INFO | Sertakan notification_id |
| Telegram failed | ERROR | Sertakan notification_id dan error |
| Telegram retry | WARNING | Sertakan attempt_count |
| HTS unavailable | ERROR | |
| HTS recovered | INFO | |
| Reconciliation start | INFO | |
| Reconciliation complete | INFO | Sertakan statistics |
| Initial sync start | INFO | |
| Initial sync complete | INFO | Sertakan jumlah tiket |
| Backup complete | INFO | |
| Backup failed | ERROR | |
| Database error | ERROR | Jangan cetak data sensitif |

### 22.3 Log Rotation

- Gunakan `RotatingFileHandler` atau PM2 log management
- Konfigurasi maksimum ukuran file dan jumlah file yang dipertahankan
- Sertakan konfigurasi logrotate atau PM2 log-rotate

---

## 23. Health Monitoring

### 23.1 Health Endpoint

**Request**: `GET /health`

**Response sukses (200 OK)**:
```json
{
  "status": "healthy",
  "timestamp": "2026-09-17T08:30:00+07:00",
  "hts": {
    "state": "connected",
    "last_poll": "2026-09-17T08:30:00+07:00",
    "last_successful_poll": "2026-09-17T08:30:00+07:00",
    "consecutive_failures": 0
  },
  "session": {
    "state": "authenticated"
  },
  "telegram": {
    "last_sent": "2026-09-17T08:15:00+07:00",
    "pending_notifications": 0,
    "failed_notifications": 0
  },
  "database": {
    "state": "ok",
    "total_tickets": 423,
    "last_ticket_seen": "ADU-00123"
  },
  "app": {
    "state": "MONITORING",
    "uptime_seconds": 86400,
    "version": "1.0.0"
  }
}
```

**Response degraded (200 OK)**:
```json
{
  "status": "degraded",
  "reason": "telegram_failures",
  ...
}
```

**Response unhealthy (503 Service Unavailable)**:
```json
{
  "status": "unhealthy",
  "reason": "hts_unavailable",
  ...
}
```

### 23.2 Status Definitions

| Status | Kondisi |
|---|---|
| `healthy` | Semua komponen berfungsi normal |
| `degraded` | Aplikasi berjalan tetapi ada komponen yang mengalami masalah (misalnya Telegram gagal beberapa kali) |
| `unhealthy` | Komponen kritis tidak berfungsi (HTS tidak bisa diakses, database error, session CAPTCHA_REQUIRED) |

### 23.3 Keamanan Health Endpoint

- Bind hanya ke `127.0.0.1` secara default (tidak boleh diakses dari internet)
- Tidak mengembalikan credential, password, session token, atau cookie dalam response
- Tidak mengembalikan stack trace atau internal error detail

---

## 24. PM2 / Deployment

### 24.1 Struktur File PM2 Ecosystem

File konfigurasi PM2 (`ecosystem.config.js`):

```javascript
module.exports = {
  apps: [{
    name: 'hts-ticket-monitor',
    script: 'python3',
    args: 'main.py',
    cwd: '/path/to/hts-ticket-monitor',
    interpreter: 'none',
    env_file: '.env',
    watch: false,
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 5000,
    max_memory_restart: '256M',
    log_file: 'logs/pm2-combined.log',
    out_file: 'logs/pm2-out.log',
    error_file: 'logs/pm2-error.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    kill_timeout: 30000
  }]
};
```

### 24.2 Setup Commands

```bash
# Install PM2
npm install -g pm2

# Start aplikasi
pm2 start ecosystem.config.js

# Simpan konfigurasi agar auto-start saat boot
pm2 save
pm2 startup

# Monitor
pm2 status
pm2 logs hts-ticket-monitor
pm2 monit
```

### 24.3 Graceful Shutdown

- PM2 akan mengirim `SIGTERM` ketika melakukan stop/restart
- Aplikasi harus menangkap `SIGTERM` dan melakukan graceful shutdown:
  1. Set flag `shutdown_requested = True`
  2. Selesaikan polling cycle yang sedang berjalan
  3. Commit database transaction yang pending
  4. Flush dan tutup log handler
  5. Tutup HTTP session
  6. Tutup database connection
  7. `sys.exit(0)`
- PM2 `kill_timeout` harus diset cukup besar (30 detik) untuk memberi waktu graceful shutdown

### 24.4 Restart Policy

| Kondisi | Behavior |
|---|---|
| Crash / uncaught exception | PM2 restart otomatis |
| Memory melebihi `max_memory_restart` | PM2 restart otomatis |
| Startup error (config tidak valid) | Exit dengan error code 1, PM2 akan retry |
| Graceful shutdown (SIGTERM) | Exit code 0, PM2 tidak restart |

---

## 25. Backup

### 25.1 Strategi Backup

Untuk V1, gunakan strategi backup berbasis SQLite backup API:

```
┌─────────────────────────────────────────────┐
│           BACKUP SCHEDULE                    │
│                                              │
│  Method: SQLite online backup                │
│  (menggunakan sqlite3 .backup command        │
│   atau Python backup API untuk hot backup)   │
│                                              │
│  Schedule: Daily (misal pukul 02:00 WIB)    │
│  Retention: 7 hari (configurable)           │
│  Location: BACKUP_DIR dari .env             │
│                                              │
│  Nama file:                                  │
│  hts_monitor_YYYYMMDD_HHMMSS.db             │
└─────────────────────────────────────────────┘
```

### 25.2 Backup Implementation Options

| Opsi | Deskripsi |
|---|---|
| **In-app scheduler** | Aplikasi menjalankan backup job di thread terpisah sesuai jadwal |
| **Cron job terpisah** | Script backup Python/Bash yang dijalankan melalui crontab (lebih sederhana) |

**Rekomendasi V1**: Cron job terpisah — lebih sederhana dan tidak menambah kompleksitas pada aplikasi utama.

### 25.3 Backup Failure Handling

- Log error ke sistem log dan tabel `system_events`
- Kirim Telegram alert jika backup gagal (opsional, dapat dikonfigurasi)

### 25.4 Integrity Check

Sebelum menggunakan backup, jalankan:
```bash
sqlite3 backup_file.db "PRAGMA integrity_check;"
```

---

## 26. Architecture

### 26.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          HTS TICKET MONITOR                              │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                     CONFIGURATION LAYER                          │   │
│  │  .env → ConfigLoader → AppConfig                                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌────────────────────┐   ┌─────────────────────────────────────────┐   │
│  │   HEALTH ENDPOINT  │   │            ORCHESTRATOR                  │   │
│  │   (HTTP Server)    │   │  (Main loop, state machine, lifecycle)   │   │
│  │   GET /health      │   └──────────────────┬──────────────────────┘   │
│  └────────────────────┘                      │                          │
│                                              │                          │
│  ┌───────────────────────────────────────────▼───────────────────────┐  │
│  │                      SESSION MANAGER                               │  │
│  │  - Login / Re-login                                                │  │
│  │  - Session detection (expired / CAPTCHA)                          │  │
│  │  - State machine: UNAUTHENTICATED → AUTHENTICATED → EXPIRED       │  │
│  └───────────────────────────────────────────┬───────────────────────┘  │
│                                              │                          │
│  ┌───────────────────────────────────────────▼───────────────────────┐  │
│  │                      HTS CLIENT                                    │  │
│  │  - HTTP requests (with session reuse, timeout, retry)             │  │
│  │  - Fetch ticket list                                               │  │
│  │  - [TBD] API endpoint or HTML scraping                            │  │
│  └───────────────────────────────────────────┬───────────────────────┘  │
│                                              │                          │
│  ┌───────────────────────────────────────────▼───────────────────────┐  │
│  │                    TICKET PROCESSOR                                │  │
│  │  ┌────────────────────┐   ┌────────────────────────────────────┐  │  │
│  │  │   CHANGE DETECTOR  │   │         EVENT CREATOR              │  │  │
│  │  │  - Compare current │   │  - NEW_TICKET                      │  │  │
│  │  │    vs. snapshot    │   │  - TICKET_CHANGED                  │  │  │
│  │  │  - Field diff      │   │  - COMPLETED                       │  │  │
│  │  │  - Hash check      │   │  - Deduplication                   │  │  │
│  │  └────────────────────┘   └────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────┬───────────────────────┘  │
│                                              │                          │
│  ┌───────────────────────────────────────────▼───────────────────────┐  │
│  │                       SQLITE DATABASE                              │  │
│  │  tickets | ticket_snapshots | ticket_events | notifications |      │  │
│  │  system_events                                                     │  │
│  └───────────────────────────────────────────┬───────────────────────┘  │
│                                              │                          │
│  ┌───────────────────────────────────────────▼───────────────────────┐  │
│  │                   NOTIFICATION SERVICE                             │  │
│  │  ┌──────────────────────────────┐  ┌───────────────────────────┐  │  │
│  │  │    TELEGRAM NOTIFIER         │  │   NOTIFICATION QUEUE      │  │  │
│  │  │  - Send message              │  │  - Retry FAILED           │  │  │
│  │  │  - Format template           │  │  - Exponential backoff    │  │  │
│  │  │  - Handle errors             │  │  - Deduplication          │  │  │
│  │  └──────────────────────────────┘  └───────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    LOGGING SERVICE                                │   │
│  │  Structured logging → File + Console                             │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
        │                                              │
        ▼                                              ▼
┌───────────────┐                           ┌──────────────────┐
│  HTS SYSTEM   │                           │  TELEGRAM BOT API│
│  (External)   │                           │  (External)      │
└───────────────┘                           └──────────────────┘
```

### 26.2 Component Responsibilities

| Komponen | Tanggung Jawab |
|---|---|
| **ConfigLoader** | Membaca dan memvalidasi `.env`, menyediakan typed config |
| **Orchestrator** | Main loop, state machine, lifecycle management |
| **Session Manager** | Login, session detection, re-auth, state CAPTCHA |
| **HTS Client** | HTTP requests ke HTS, parsing response, error handling |
| **Ticket Processor** | Koordinasi antara change detector dan event creator |
| **Change Detector** | Membandingkan snapshot, menghasilkan changed_fields |
| **Event Creator** | Membuat event dengan deduplication |
| **Database** | CRUD, transactions, WAL mode |
| **Notification Service** | Queue, retry, kirim ke Telegram |
| **Telegram Notifier** | Formatting pesan, Telegram Bot API call |
| **Health Endpoint** | HTTP server sederhana untuk `/health` |
| **Logging Service** | Structured logging dengan filtering sensitif data |

---

## 27. State Machine

### 27.1 Application States

```
         ┌────────────────────────┐
         │    APPLICATION_STARTING│
         └───────────┬────────────┘
                     │ (config loaded, DB initialized)
                     ▼
         ┌────────────────────────┐
         │      INITIAL_SYNC      │◄── (if INITIAL_SYNC=true)
         └───────────┬────────────┘
                     │ (sync complete)
                     ▼
         ┌────────────────────────┐
         │      RECONCILING       │◄── (after restart/recovery)
         └───────────┬────────────┘
                     │ (reconciliation complete)
                     ▼
         ┌────────────────────────┐
    ┌───►│       MONITORING       │◄──────────────┐
    │    └─────┬──────────┬───────┘               │
    │          │          │                        │
    │   (HTS   │          │ (session              │
    │  unavail)│          │  expired)              │
    │          ▼          ▼                        │
    │  ┌──────────────┐  ┌──────────────────────┐  │
    │  │HTS_UNAVAILABLE│  │   SESSION_EXPIRED    │  │
    │  └──────┬───────┘  └──────────┬───────────┘  │
    │         │                     │               │
    │  (HTS   │              ┌──────┴──────────┐    │
    │  recov) │              │                 │    │
    │         │         No CAPTCHA         CAPTCHA  │
    │         │              │                 │    │
    │         │    ┌─────────▼─────────┐       │    │
    │         │    │ REAUTHENTICATING  │       │    │
    │         │    └─────────┬─────────┘       │    │
    │         │              │ (success)       │    │
    │         │              │              ┌──▼──┐ │
    │         │              │              │CAPTCHA│ │
    │         │              │              │REQUIRED│
    │         │              │              └──┬──┘ │
    │         │              │                 │    │
    │         │              │    (operator    │    │
    │         │              │     completes)  │    │
    │         ▼              ▼                 ▼    │
    │    (reconcile) → RECONCILING ────────────────►┘
    │
    └── (after reconcile complete)

         ┌────────────────────────┐
         │     SHUTTING_DOWN      │ ← SIGTERM received (any state)
         └───────────┬────────────┘
                     │ (cleanup done)
                     ▼
                  [EXIT]
```

### 27.2 State Transition Table

| From State | Trigger | To State | Action |
|---|---|---|---|
| `APPLICATION_STARTING` | Config valid, DB OK, login OK | `INITIAL_SYNC` or `RECONCILING` | Mulai sync/reconcile |
| `APPLICATION_STARTING` | Config invalid | `SHUTTING_DOWN` | Log error, exit |
| `INITIAL_SYNC` | Sync selesai | `RECONCILING` | Start reconciliation |
| `RECONCILING` | Reconciliation selesai | `MONITORING` | Start polling loop |
| `MONITORING` | Session expired | `SESSION_EXPIRED` | Stop polling |
| `MONITORING` | HTS unavailable | `HTS_UNAVAILABLE` | Send alert, retry loop |
| `MONITORING` | SIGTERM | `SHUTTING_DOWN` | Graceful shutdown |
| `SESSION_EXPIRED` | No CAPTCHA needed | `REAUTHENTICATING` | Re-login |
| `SESSION_EXPIRED` | CAPTCHA needed | `CAPTCHA_REQUIRED` | Send Telegram alert |
| `REAUTHENTICATING` | Login success | `RECONCILING` | Reconcile then monitor |
| `REAUTHENTICATING` | Login failed + CAPTCHA | `CAPTCHA_REQUIRED` | Send alert |
| `CAPTCHA_REQUIRED` | Session valid | `RECONCILING` | Reconcile then monitor |
| `HTS_UNAVAILABLE` | HTS accessible again | `RECONCILING` | Send recovery, reconcile |
| Any | SIGTERM | `SHUTTING_DOWN` | Graceful shutdown |

---

## 28. Acceptance Criteria

### AC-01: Login

```
GIVEN valid credential tersimpan di .env
WHEN aplikasi dimulai
THEN aplikasi berhasil membuat authenticated HTS session
AND system_event HTS_LOGIN dicatat
AND tidak ada credential yang tercatat di log
```

### AC-02: CAPTCHA Handling

```
GIVEN HTS memerlukan CAPTCHA saat login
WHEN aplikasi mendeteksi kehadiran CAPTCHA
THEN aplikasi mengirim Telegram notification kepada operator
AND aplikasi TIDAK mencoba bypass CAPTCHA
AND aplikasi masuk ke state CAPTCHA_REQUIRED
AND polling dihentikan sementara
```

### AC-03: New Ticket

```
GIVEN monitoring aktif
WHEN tiket baru dengan nomor_aduan yang belum ada di DB muncul di HTS
THEN tiket disimpan ke database
AND tepat satu event NEW_TICKET dibuat
AND tepat satu notifikasi Telegram dikirim
AND notifikasi menggunakan template "Menginformasikan Tiket Masuk"
```

### AC-04: Duplicate Prevention (New Ticket)

```
GIVEN tiket X sudah ada di database
WHEN polling berikutnya menemukan tiket X lagi
THEN tidak ada event NEW_TICKET tambahan yang dibuat untuk tiket X
AND tidak ada notifikasi Telegram duplikat dikirim
```

### AC-05: Initial Sync

```
GIVEN aplikasi dijalankan pertama kali dengan INITIAL_SYNC=true
AND HTS sudah memiliki 100 tiket yang ada
WHEN initial sync berjalan
THEN 100 tiket disimpan ke database
AND NOLL notifikasi Telegram dikirim untuk 100 tiket tersebut
AND setelah sync selesai, monitoring normal dimulai
```

### AC-06: Change Detection

```
GIVEN tiket X ada di database
WHEN field keluhan tiket X berubah dari "A" ke "B"
THEN event TICKET_CHANGED dibuat dengan changed_fields yang mencatat perubahan
AND snapshot baru disimpan
AND notifikasi Telegram dikirim dengan data tiket terbaru
AND template "Menginformasikan Perubahan Aduan" digunakan
```

### AC-07: Multi-Field Change Detection

```
GIVEN tiket X ada di database
WHEN kategori DAN keluhan tiket X berubah sekaligus
THEN event TICKET_CHANGED dibuat dengan changed_fields yang mencatat KEDUA field
```

### AC-08: Completed Ticket

```
GIVEN tiket X ada di database dengan status aktif
WHEN status tiket X berubah menjadi TBD_STATUS_COMPLETED
THEN event COMPLETED dibuat
AND notifikasi Telegram dikirim dengan template "Menginformasikan Tiket Selesai"
AND event COMPLETED tidak dibuat lagi untuk tiket yang sama
```

### AC-09: End-to-End Notification Sequence

```
GIVEN tiket X baru masuk
THEN dikirim notifikasi "Menginformasikan Tiket Masuk"
WHEN tiket X mengalami perubahan
THEN dikirim notifikasi "Menginformasikan Perubahan Aduan"
WHEN tiket X diselesaikan
THEN dikirim notifikasi "Menginformasikan Tiket Selesai"
```

### AC-10: Reconciliation

```
GIVEN aplikasi offline selama T menit
AND selama offline, tiket A (baru) dan tiket B (baru) dibuat di HTS
AND tiket C (existing) mengalami perubahan di HTS
WHEN aplikasi restart
THEN reconciliation menemukan tiket A dan B sebagai tiket baru
AND event NEW_TICKET dibuat untuk tiket A dan B
AND notifikasi Telegram dikirim untuk A dan B
AND event TICKET_CHANGED dibuat untuk tiket C
AND notifikasi Telegram dikirim untuk C
```

### AC-11: Telegram Failure & Retry

```
GIVEN Telegram API mengalami gangguan sementara
WHEN aplikasi mencoba mengirim notifikasi
THEN notifikasi disimpan dengan status FAILED
AND sistem melakukan retry dengan exponential backoff
AND event tidak hilang dari database
WHEN Telegram API kembali normal
THEN notifikasi yang pending berhasil dikirim
AND status diubah menjadi SENT
```

### AC-12: HTS Unavailable

```
GIVEN HTS tidak dapat diakses
WHEN polling cycle berikutnya berjalan
THEN aplikasi TIDAK crash
AND HTS_UNAVAILABLE tercatat di log dan system_events
AND SATU Telegram alert "HTS CONNECTION ERROR" dikirim
AND retry dilakukan dengan exponential backoff
AND TIDAK ada duplikat alert selama satu incident
WHEN HTS dapat diakses kembali
THEN SATU Telegram notification "HTS CONNECTION RECOVERED" dikirim
AND reconciliation dilakukan
AND monitoring dilanjutkan
```

### AC-13: Session Expiration

```
GIVEN session HTS expired saat monitoring berjalan
WHEN polling cycle mendeteksi session expired
THEN polling dihentikan sementara
AND state berubah ke SESSION_EXPIRED
IF re-login berhasil tanpa CAPTCHA:
    THEN reconciliation dilakukan
    AND monitoring dilanjutkan
IF CAPTCHA diperlukan:
    THEN Telegram notification dikirim ke operator
    AND state menjadi CAPTCHA_REQUIRED
    AND aplikasi menunggu
```

### AC-14: PM2 Restart

```
GIVEN PM2 mengelola proses aplikasi
WHEN proses crash karena uncaught exception
THEN PM2 me-restart proses otomatis
AND setelah restart, aplikasi melakukan reconciliation
AND monitoring dilanjutkan dari kondisi terakhir
```

### AC-15: Health Endpoint

```
GIVEN aplikasi berjalan normal
WHEN GET /health dipanggil
THEN response 200 OK dikembalikan
AND body JSON berisi status, hts, session, telegram, database, app
AND tidak ada credential atau session token di response
```

### AC-16: Graceful Shutdown

```
GIVEN aplikasi sedang dalam polling cycle
WHEN SIGTERM diterima
THEN polling cycle yang berjalan diselesaikan terlebih dahulu
AND database transaction di-commit
AND log di-flush
AND HTTP session ditutup
AND database connection ditutup
AND proses exit dengan code 0
```

### AC-17: No Password in Logs

```
GIVEN apapun yang terjadi
THEN tidak ada password, token Telegram, atau session cookie yang tercatat di log
```

---

## 29. Edge Cases

| ID | Edge Case | Expected Behavior |
|---|---|---|
| EC-01 | Tiket dihapus dari HTS setelah tersimpan di DB | Log warning, jangan hapus dari DB, tidak kirim notifikasi (V1) |
| EC-02 | Nomor aduan sama muncul dua kali di respons HTS dalam satu polling | Proses hanya satu, log warning duplikat |
| EC-03 | Field yang dipantau mengembalikan `null` / kosong dari HTS | Simpan sebagai null/empty string, gunakan fallback di template |
| EC-04 | Nilai field sangat panjang (keluhan >10.000 karakter) | Truncate untuk notifikasi Telegram (Telegram max 4096 karakter), simpan full di DB |
| EC-05 | Polling cycle memakan waktu lebih lama dari POLL_INTERVAL | Jangan tumpuk polling, tunggu cycle selesai lalu mulai berikutnya |
| EC-06 | Database disk penuh | Log error kritis, kirim Telegram alert, jangan crash terus-menerus |
| EC-07 | Initial sync gagal di tengah jalan (crash) | Saat restart, ulangi initial sync; pastikan idempoten |
| EC-08 | Tiket yang di-initial sync kemudian mengalami perubahan di polling pertama | Deteksi sebagai TICKET_CHANGED dan kirim notifikasi |
| EC-09 | Telegram rate limit terpenuhi | Terapkan retry-after dari Telegram response header |
| EC-10 | Semua field tiket berubah sekaligus | Buat satu event TICKET_CHANGED dengan semua changed_fields |
| EC-11 | Tiket kembali dari selesai ke status aktif | Deteksi sebagai TICKET_CHANGED (perubahan status), bukan COMPLETED |
| EC-12 | HTS mengembalikan HTML login page di tengah polling (session expired mendadak) | Deteksi segera, masuk ke SESSION_EXPIRED state |
| EC-13 | Health endpoint dikunjungi saat aplikasi dalam state HTS_UNAVAILABLE | Kembalikan status `degraded` atau `unhealthy` dengan reason |
| EC-14 | Backup gagal beberapa kali berturut-turut | Log error, kirim Telegram alert, jangan stop monitoring |
| EC-15 | `.env` tidak ditemukan atau tidak lengkap | Exit dengan error message yang jelas sebelum memulai apapun |

---

## 30. Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-01 | Struktur HTML HTS berubah tanpa pemberitahuan | Medium | High | Monitor 404/unexpected response, unit test selector, alert ketika parsing gagal |
| R-02 | HTS menambahkan anti-bot/rate limiting yang memblokir aplikasi | Low | High | Implementasi delay yang sopan, jangan aggressive polling, gunakan API jika tersedia |
| R-03 | Session timeout HTS sangat pendek, CAPTCHA sering muncul | Medium | Medium | Desain agar operator dapat dengan cepat menyelesaikan CAPTCHA; monitoring interval CAPTCHA_REQUIRED |
| R-04 | Volume tiket sangat besar sehingga reconciliation lambat | Low | Medium | Implementasi pagination, limit query, progress logging |
| R-05 | Telegram Bot diblokir atau token expired | Low | High | Monitoring Telegram failure, rotasi token, alert melalui channel lain jika memungkinkan |
| R-06 | SQLite corruption akibat force kill atau power loss | Low | High | Gunakan WAL mode, backup rutin, integrity check pada startup |
| R-07 | Operator tidak merespons CAPTCHA notification | Medium | Medium | CAPTCHA reminder dengan interval teratur |
| R-08 | Perubahan pada API/endpoint HTS yang tidak diketahui | Medium | High | Implementasi version check atau signature detection |
| R-09 | Server Ubuntu/WSL kehabisan disk space (database + log) | Low | Medium | Log rotation, backup retention policy, disk space monitoring |
| R-10 | PM2 tidak dikonfigurasi auto-start saat server reboot | Low | High | Dokumentasi jelas untuk `pm2 save` dan `pm2 startup` |

---

## 31. Technical Unknowns / TBD

> **PENTING**: Bagian ini mendokumentasikan semua aspek teknis HTS yang **belum diverifikasi**. Nilai-nilai di bawah ini adalah **placeholder** dan **HARUS diverifikasi** melalui inspeksi langsung terhadap sistem HTS sebelum implementasi dimulai.

### 31.1 Authentication

| Item | Status | Deskripsi / Nilai Aktual |
|---|---|---|
| Login endpoint URL | **VERIFIED** | `https://hts.diskomdigi.jatengprov.go.id/login` |
| Login method | **VERIFIED** | `POST` |
| Login form fields | **VERIFIED** | `csrf_test_name` (CSRF token), `email` (input id `userEmail`), `password` (input id `userPassword`), `captcha_code` (input id `captcha_code`), `authCheck` (checkbox) |
| CSRF token | **VERIFIED** | Wajib. Framework CodeIgniter. Cookie: `csrf_cookie_name`, Form Field: `csrf_test_name` |
| CAPTCHA jenis | **VERIFIED** | Native Image CAPTCHA via `<img id="captchaimg" src="https://hts.diskomdigi.jatengprov.go.id/captcha?rand=...">` |
| CAPTCHA trigger | **VERIFIED** | Selalu muncul pada halaman form login utama (`/`) |
| Session mechanism | **VERIFIED** | Cookie-based session (`ci_session`) + F5 BIG-IP WAF Cookie (`TS0128f648`) |
| Session cookie name | **VERIFIED** | `ci_session` |
| Session timeout duration | **VERIFIED** | 7200 detik (2 jam / 120 menit) (`Max-Age=7200; SameSite=Lax`) |
| Session detection | **VERIFIED** | Mengakses `/list_aduan` tanpa session valid mengembalikan HTTP 200 (redirect ke `/` dengan form login & captcha) |
| Logout endpoint | **VERIFIED** | `https://hts.diskomdigi.jatengprov.go.id/logout` |

### 31.2 Ticket Data

| Item | Status | Deskripsi / Nilai Aktual |
|---|---|---|
| List aduan endpoint | **VERIFIED** | `https://hts.diskomdigi.jatengprov.go.id/list_aduan` (Halaman web) |
| API / JSON endpoint | **VERIFIED** | `POST https://hts.diskomdigi.jatengprov.go.id/get_aduan_data` (Tersedia API AJAX JSON resmi!) |
| Mekanisme data retrieval | **VERIFIED** | **AJAX / JSON Endpoint** (Prioritas 1 PRD terpenuhi tanpa perlu HTML scraping). Header: `Content-Type: application/json`, `X-Requested-With: XMLHttpRequest`. |
| Pagination mechanism | **VERIFIED** | Request JSON: `{"page": 1, "limit": 20, "status": "pending"}`. Response JSON: `pagination: {"page": 1, "limit": 20, "total": 1894, "total_pages": 95}` |
| Sorting default | **VERIFIED** | Descending by `id_trouble` / `created_at` (Tiket terbaru otomatis di urutan pertama / page 1) |
| Filter / query parameter | **VERIFIED** | Payload JSON: `page`, `limit`, `status` (`pending`, `solved`, `unsubmitted`, `input-pic`, `all`), `kategori`, `sub_kategori`, `pic_filter`, `tgl_awal`, `tgl_akhir`, `searchTerm` |
| Field mapping JSON | **VERIFIED** | - `nomor_aduan` -> `item.no_trouble` (e.g. `"1976-TShoot-2026-jateng-09"`)<br>- `kategori` -> `item.kategori` (`"troubleshoot"`, `"request"`, `"monitoring"`)<br>- `sub_kategori` -> `item.sub_kategori` (`"CORE NETWORK"`)<br>- `instansi` -> `item.opd`<br>- `opd_induk` -> `item.induk_opd_nama` (nullable / `"-"`)<br>- `pic_nama` -> `item.pic`<br>- `pic_nomor` -> `item.wa`<br>- `keluhan` -> `item.keluhan`<br>- `status` -> dihitung dari `item.t_solve` (lihat 31.3)<br>- `tanggal_aduan` -> `item.tgltshoot`<br>- `created_at` -> `item.created_at`<br>- `updated_at` -> `item.updated_at` |
| HTML selector fallback | **VERIFIED** | `#pending-aduan-data`, `#solved-aduan-data`, `#unsubmitted-aduan-data` (hanya jika fallback HTML scraping diperlukan) |
| Ticket detail URL | **VERIFIED** | `https://hts.diskomdigi.jatengprov.go.id/view_aduan/{id_trouble}` |
| Total tiket | **VERIFIED** | ~1.894 tiket (Pending: ~8, Solved: ~1.886) |

### 31.3 Status Values

| Item | Status | Deskripsi / Nilai Aktual |
|---|---|---|
| Daftar nilai status valid | **VERIFIED** | Status dikontrol oleh 2 atribut utama pada JSON:<br>1. `t_solve`: `'0'` (Belum Ditangani) vs Non-zero/UNIX timestamp (Sudah Ditangani)<br>2. `is_submitted`: `1` (Sudah Disubmit) vs `0` (Belum Disubmit)<br>3. Kategori tab filter: `'pending'`, `'solved'`, `'unsubmitted'`, `'input-pic'` |
| Nilai status "selesai" | **VERIFIED** | `item.t_solve != '0'` / `item.t_solve != 0` (Teks tampilan HTS: **`"Sudah Ditangani"`**) |
| Nilai status "open/baru" | **VERIFIED** | `item.t_solve == '0'` dan `item.is_submitted == 1` (Teks tampilan HTS: **`"Belum Ditangani"`**, masuk tab `'pending'`) |
| Nilai status lainnya | **VERIFIED** | Belum disubmit (`is_submitted == 0`), Perlu Input PIC (`status == 'input-pic'`) |

### 31.4 Network & Performance

| Item | Status | Deskripsi / Nilai Aktual |
|---|---|---|
| Rate limiting | **VERIFIED** | Tidak ada blokir agresif untuk polling interval 5 detik, namun tetap gunakan session reuse |
| Anti-bot detection | **VERIFIED** | F5 BIG-IP ASM WAF Cookie (`TS0128f648`) aktif. Gunakan persistent `requests.Session` dan Browser User-Agent standar. |
| Average response time | **VERIFIED** | Response API JSON sangat cepat: **~150ms - 350ms** (jauh lebih cepat daripada HTML scraping) |
| Typical data size | **VERIFIED** | Payload JSON sangat ringan: **~2 KB - 8 KB** per polling 10 tiket (sangat hemat bandwidth) |
| HTTPS/TLS | **VERIFIED** | Enforced HTTPS (TLSv1.2 / TLSv1.3) |

### 31.5 Catatan Hasil Verifikasi Empiris

Seluruh aspek teknis HTS pada Bagian 31 telah selesai diverifikasi secara langsung (*live empirical verification*):
1. **API Resmi Ditemukan**: HTS memiliki AJAX JSON endpoint resmi `POST /get_aduan_data` yang mengembalikan data terstruktur, pagination, dan filter status lengkap. **Scraping HTML penuh tidak diperlukan untuk polling normal**, yang secara signifikan meningkatkan kecepatan response (~200ms) dan menghemat bandwidth.
2. **Autentikasi & Session**: Menggunakan CodeIgniter CSRF protection (`csrf_test_name` / `csrf_cookie_name`) dan session cookie `ci_session` berdurasi 2 jam (7200s). Terdapat WAF F5 BIG-IP (`TS0128f648`).
3. **Status Tiket**: Terpetakan jelas melalui field `t_solve` (`0` = Belum Ditangani / Pending; non-zero = Sudah Ditangani / Selesai).

### 31.6 Status Dokumen

| Label | Deskripsi |
|---|---|
| **KNOWN REQUIREMENT** | Kebutuhan fungsional dan operasional sistem |
| **VERIFIED** | Telah diverifikasi secara langsung ke sistem HTS aktual (100% item pada Bagian 31 terverifikasi) |
| **TBD** | Tidak ada item TBD kritis yang tersisa; siap lanjut ke Technical Design |

---

## 32. V2 / Future Enhancements

Item-item berikut **tidak termasuk dalam V1** namun dapat dipertimbangkan untuk iterasi berikutnya:

| ID | Feature | Deskripsi |
|---|---|---|
| V2-01 | **Multiple Telegram destinations** | Routing notifikasi ke grup berbeda berdasarkan instansi, kategori, atau OPD |
| V2-02 | **Web Dashboard** | UI monitoring sederhana untuk melihat status tiket, event, dan notifikasi |
| V2-03 | **Advanced Analytics** | Laporan statistik tiket (volume per hari, rata-rata waktu selesai, dll.) |
| V2-04 | **Export** | Export data ke Excel/CSV untuk pelaporan |
| V2-05 | **WhatsApp Integration** | Integrasi dengan WhatsApp Business API (bukan automation) |
| V2-06 | **Grafana/Prometheus** | Metrics endpoint untuk monitoring eksternal |
| V2-07 | **Multiple HTS accounts** | Mendukung lebih dari satu akun HTS dengan konfigurasi terpisah |
| V2-08 | **Ticket detail enrichment** | Mengambil detail lengkap tiket dari halaman detail jika ada |
| V2-09 | **Keyword filtering** | Filter notifikasi berdasarkan kata kunci di keluhan |
| V2-10 | **Scheduled reports** | Kirim laporan harian/mingguan via Telegram |
| V2-11 | **Operator commands via Telegram** | Operator dapat query status/tiket melalui perintah Telegram Bot |
| V2-12 | **Database migration tooling** | CLI tool untuk migrasi schema database |
| V2-13 | **External monitoring integration** | Integrasi dengan UptimeRobot, Betterstack, atau sejenisnya via health endpoint |

---

## Appendix A: Glossary

| Term | Deskripsi |
|---|---|
| **HTS** | Help Desk Ticketing System — sistem pengaduan yang dipantau |
| **Aduan / Tiket** | Satu unit pengaduan dalam sistem HTS |
| **Nomor Aduan** | Identifikasi unik tiket, tidak berubah |
| **Initial Sync** | Proses loading tiket existing saat pertama kali aplikasi dijalankan |
| **Reconciliation** | Proses sinkronisasi antara state DB dan HTS setelah aplikasi offline |
| **Polling** | Proses periodik mengambil data dari HTS |
| **Snapshot** | Gambaran kondisi tiket pada satu titik waktu |
| **Changed Fields** | Daftar field yang nilainya berubah antara dua snapshot |
| **AT-LEAST-ONCE** | Prinsip delivery: lebih baik duplikat daripada hilang |
| **Deduplication** | Mekanisme mencegah event/notifikasi yang sama dikirim lebih dari sekali |
| **Exponential Backoff** | Strategi retry dengan delay yang meningkat secara eksponensial |
| **WAL Mode** | Write-Ahead Logging — mode SQLite untuk performa dan integritas yang lebih baik |
| **Graceful Shutdown** | Penghentian aplikasi yang bersih dengan menyelesaikan pekerjaan yang sedang berjalan |
| **TBD_STATUS_COMPLETED** | Placeholder untuk nilai status "selesai" di HTS yang belum diverifikasi |

---

## Appendix B: File Structure (Proyeksi V1)

```
hts-ticket-monitor/
├── main.py                      # Entry point
├── config.py                    # Configuration loader
├── database/
│   ├── __init__.py
│   ├── db.py                    # Database connection & setup
│   └── models.py                # Table definitions & queries
├── hts/
│   ├── __init__.py
│   ├── client.py                # HTS HTTP client
│   ├── session.py               # Session management
│   └── parser.py                # Response parser (HTML/JSON - TBD)
├── monitoring/
│   ├── __init__.py
│   ├── orchestrator.py          # Main monitoring loop & state machine
│   ├── ticket_processor.py      # Ticket processing coordination
│   ├── change_detector.py       # Change detection logic
│   └── reconciler.py            # Reconciliation logic
├── notifications/
│   ├── __init__.py
│   ├── telegram.py              # Telegram Bot API client
│   ├── templates.py             # Message templates
│   └── queue.py                 # Notification queue processor
├── health/
│   ├── __init__.py
│   └── endpoint.py              # Health HTTP endpoint
├── backup/
│   └── backup.py                # Backup script (dapat dijalankan terpisah)
├── logs/                        # Log files (gitignored)
├── data/                        # Database & backups (gitignored)
│   └── backups/
├── .env                         # Credentials & config (gitignored)
├── .env.example                 # Template konfigurasi
├── .gitignore
├── requirements.txt
├── ecosystem.config.js          # PM2 configuration
└── README.md
```

---

*End of Document*

*PRD Version 1.0 | HTS Ticket Monitor | Status: DRAFT*
*Document harus diupdate ketika TBD items diverifikasi dari sistem HTS aktual.*
