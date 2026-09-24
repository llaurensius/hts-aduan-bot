# Fitur Rekap Laporan Aduan

Menambahkan fitur **Generate Rekap Laporan** di dashboard yang memungkinkan operator membuat rekap harian per shift (Pagi/Siang/Malam) secara on-demand. Rekap diformat sesuai contoh yang diberikan, ditampilkan di dashboard, dan bisa langsung disalin ke clipboard untuk dikirim ke Telegram secara manual.

---

## User Review Required

> [!IMPORTANT]
> **Endpoint API tiap kategori belum diketahui pasti.** Karena HTS membutuhkan login + CAPTCHA untuk diakses, endpoint untuk Kunjungan, Permohonan Layanan, dan VPS/Domain-Rekomtek harus ditemukan secara empiris (trial-and-error saat runtime). Implementation plan ini menggunakan **konvensi naming yang paling masuk akal** berdasarkan pola `/get_aduan_data`, dengan fallback error handling yang jelas.
>
> Jika endpoint ternyata berbeda, cukup update konstanta di `app/hts/rekap_client.py`.

> [!WARNING]
> **Sinkronisasi data real-time.** Rekap mengambil data langsung dari HTS saat tombol diklik (bukan dari DB lokal). Artinya, jika bot sedang dalam state `CAPTCHA_REQUIRED` atau HTS sedang down, rekap tidak bisa diambil dan akan menampilkan pesan error.

---

## Open Questions

> [!NOTE]
> Semua pertanyaan sudah dijawab. Tidak ada open question tersisa.

**Keputusan desain yang sudah dikonfirmasi:**
- Trigger: Tombol di dashboard (bukan cron/Telegram command)
- Shift: Pagi 07:00–15:00, Siang 15:00–23:00, Malam 23:00–07:00 (WIB)
- "Tiket Masuk" = tiket yang `first_seen` jatuh dalam window shift tersebut di database lokal
- Kategori: 5 kategori tetap — Aduan, Kunjungan, Permohonan Layanan, VPS/Domain, Rekomtek
- Output: Hanya di dashboard (text area yang bisa disalin), tidak kirim Telegram otomatis
- Nama petugas: Input manual di form sebelum generate

---

## Proposed Changes

### Arsitektur Rekap

```
Dashboard UI (form) 
   → POST /api/rekap/generate 
   → RekapService
       ├── Fetch dari HTS (semua 5 kategori via HTSRekapClient)
       ├── Hitung statistik per kategori
       └── Format teks rekap
   → Return teks rekap ke dashboard
```

**Penting:** Rekap tidak menyimpan data ke DB. Ini adalah operasi read-only murni.

---

### Kategori & Asumsi Endpoint

| Kategori | Page URL | Asumsi API Endpoint | Payload |
|---|---|---|---|
| Aduan | `/list_aduan` | `/get_aduan_data` ✅ (sudah terbukti) | `{page, limit, status}` |
| Kunjungan | `/kunjungan/list` | `/kunjungan/get_data` | `{page, limit, status}` |
| Permohonan Layanan | `/udp` | `/udp/get_data` | `{page, limit, status}` |
| VPS/Domain | `/domain-pentest/list` | `/domain-pentest/get_data` | `{page, limit, status, kategori: "VPS/Domain"}` |
| Rekomtek | `/domain-pentest/list` | `/domain-pentest/get_data` | `{page, limit, status, kategori: "Rekomtek"}` |

> Endpoint dengan `?` adalah asumsi — akan dicoba saat runtime, error ditampilkan jelas ke user.

---

### Definisi Shift

| Shift | Jam WIB | Logika filter |
|---|---|---|
| Pagi | 07:00 – 15:00 | `07:00 <= first_seen_wib < 15:00` |
| Siang | 15:00 – 23:00 | `15:00 <= first_seen_wib < 23:00` |
| Malam | 23:00 – 07:00 | `23:00 <= first_seen_wib` atau `< 07:00` (cross-midnight) |

"Tiket Masuk" = tiket pertama kali terlihat pada shift tersebut (`first_seen` dari DB lokal).
"Tiket Selesai" = tiket yang sudah `is_completed=1` dan `first_seen` dalam range tanggal tersebut.
"Belum Selesai" = Tiket Masuk - Tiket Selesai.

---

### Component: HTSRekapClient

#### [NEW] [`rekap_client.py`](file:///d:/Kuliah/Repository/hts-aduan-bot/app/hts/rekap_client.py)

Kelas baru `HTSRekapClient` yang melakukan fetch data dari semua 5 kategori. Menggunakan session yang sama dengan `HTSClient` yang sudah terautentikasi.

```python
# Metode utama:
fetch_category_tickets(category_key: str, status: str = "all") -> List[dict]
```

---

### Component: RekapService

#### [NEW] [`rekap_service.py`](file:///d:/Kuliah/Repository/hts-aduan-bot/app/monitoring/rekap_service.py)

Service layer yang:
1. Menerima parameter: `tanggal`, `shift`, `nama_petugas`
2. Mengambil data dari HTS via `HTSRekapClient`
3. Query DB lokal untuk mendapat `first_seen` tiket (untuk filter "Tiket Masuk" per shift)
4. Menghitung statistik per kategori
5. Menghasilkan teks rekap sesuai format

---

### Component: Notification Templates

#### [MODIFY] [`templates.py`](file:///d:/Kuliah/Repository/hts-aduan-bot/app/notifications/templates.py)

Tambah template `TEMPLATE_REKAP` dan fungsi `format_rekap()` untuk menghasilkan teks rekap sesuai format contoh:

```
Rekap Laporan Aduan dan Permohonan Layanan {tanggal} ({shift})

Jumlah Tiket Masuk = {total_masuk}
Jumlah Tiket Selesai = {total_selesai}
Jumlah Tiket Belum Selesai = {total_belum}

Dengan Rincian Sebagai Berikut :

{emoji} {kategori} (Tiket Masuk {masuk} / Selesai {selesai} / Belum Selesai {belum})
1. {nomor} - {keluhan}
...

Terima kasih atas perhatian dan kerjasamanya.
{nama_petugas}
```

**Logika emoji:**
- 🔴 jika ada tiket yang belum selesai
- 🟢 jika semua selesai (atau tidak ada tiket)

---

### Component: Dashboard API

#### [MODIFY] [`server.py`](file:///d:/Kuliah/Repository/hts-aduan-bot/app/dashboard/server.py)

Tambah 2 endpoint baru:

1. **`GET /api/rekap/config`** — Return daftar shift yang tersedia dan tanggal hari ini (untuk pre-fill form)
2. **`POST /api/rekap/generate`** — Terima `{tanggal, shift, nama_petugas}`, jalankan `RekapService`, return teks rekap

```json
// Request body:
{
  "tanggal": "18/09/2026",
  "shift": "pagi",
  "nama_petugas": "Radit"
}

// Response:
{
  "success": true,
  "rekap_text": "Rekap Laporan Aduan...",
  "stats": { ... }
}
```

---

### Component: Dashboard UI

#### [MODIFY] [`templates/` atau inline JS di `server.py`](file:///d:/Kuliah/Repository/hts-aduan-bot/app/dashboard)

Tambah halaman/section baru **"Generate Rekap"** di dashboard dengan:

1. **Form input:**
   - Tanggal (date picker, default hari ini)
   - Shift (dropdown: Pagi / Siang / Malam)
   - Nama Petugas (text input)
   - Tombol **"Generate Rekap"**

2. **Output area:**
   - Text area read-only berisi teks rekap hasil generate
   - Tombol **"Salin ke Clipboard"** (copy-paste ke Telegram)
   - Status/error message jika fetch gagal

---

## Verification Plan

### Automated Tests
- Tidak ada test baru yang direncanakan (mengikuti pola existing — unit test tidak wajib untuk fitur baru ini).

### Manual Verification
1. Jalankan dashboard: `python3 scripts/run_dashboard.py`
2. Buka `http://127.0.0.1:8888/dashboard`
3. Navigasi ke section "Generate Rekap"
4. Isi form: tanggal hari ini, pilih shift, isi nama petugas
5. Klik "Generate Rekap" — verifikasi teks rekap muncul dengan format yang benar
6. Verifikasi statistik (Tiket Masuk/Selesai/Belum) sesuai data di DB
7. Klik "Salin ke Clipboard" — verifikasi teks tersalin
8. **Test error handling:** Jalankan dengan bot offline / HTS tidak bisa diakses — verifikasi pesan error yang jelas muncul di UI
