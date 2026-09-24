"""HTTP client untuk fetch rekap tiket dari semua kategori HTS."""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
import requests
from app.hts.exceptions import HTSConnectionError, HTSSessionExpiredError, HTSError
from app.hts.client import is_session_expired

logger = logging.getLogger(__name__)

# Endpoint API per kategori (diperbarui jika endpoint HTS berbeda)
CATEGORY_ENDPOINTS: Dict[str, str] = {
    "aduan":               "/get_aduan_data",
    "kunjungan":           "/kunjungan/data",
    "permohonan_layanan":  "/mohon_data_layanan",   # POST DataTables, perlu CSRF
    "vps_domain_rekomtek": "/domain-pentest/data",  # GET DataTables, tanpa CSRF
}

# Nama tampilan per kategori
DISPLAY_NAMES: Dict[str, str] = {
    "aduan":              "Aduan",
    "kunjungan":          "Kunjungan",
    "permohonan_layanan": "Permohonan Layanan",
    "vps_domain":         "VPS/Domain",
    "rekomtek":           "Rekomtek",
}


class HTSRekapCategoryError(HTSError):
    """Error saat fetch satu kategori tiket untuk rekap."""


class HTSRekapClient:
    """Fetch semua kategori tiket HTS untuk keperluan rekap."""

    def __init__(
        self,
        http_session: requests.Session,
        base_url: str,
        timeout: int = 15,
    ) -> None:
        self.session = http_session
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ─── Fetchers ─────────────────────────────────────────────────────────────

    def _fetch_all_pages(self, endpoint: str, extra_payload: Optional[Dict] = None) -> List[dict]:
        """Fetch semua halaman dari satu endpoint JSON (POST), return raw list item."""
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        all_items: List[dict] = []
        page = 1

        while True:
            payload: Dict[str, Any] = {"page": page, "limit": 100, "status": "all"}
            if extra_payload:
                payload.update(extra_payload)

            try:
                resp = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
            except requests.Timeout as e:
                raise HTSRekapCategoryError(f"Timeout pada {endpoint}") from e
            except requests.ConnectionError as e:
                raise HTSConnectionError(f"Gagal koneksi ke HTS: {e}") from e

            if is_session_expired(resp, base_url=self.base_url):
                raise HTSSessionExpiredError("Sesi HTS expired saat fetch rekap")

            if resp.status_code != 200:
                raise HTSRekapCategoryError(f"HTTP {resp.status_code} dari {endpoint}")

            try:
                data = resp.json()
            except ValueError as e:
                raise HTSRekapCategoryError(f"JSON tidak valid dari {endpoint}: {e}") from e

            items = data.get("data", [])
            if isinstance(items, list):
                all_items.extend(items)

            pagination = data.get("pagination", {})
            total_pages = pagination.get("total_pages", 1)
            if page >= total_pages or not items:
                break
            page += 1

        return all_items

    def _fetch_kunjungan(self) -> List[dict]:
        """Fetch data kunjungan dari /kunjungan/data untuk semua request_status."""
        url = f"{self.base_url}{CATEGORY_ENDPOINTS['kunjungan']}"
        headers = {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        all_items: List[dict] = []
        seen_ids = set()

        # request_status:
        # '1' = approved / belum checkout (visit_status = 0)
        # '2' = visited / sudah checkout (visit_status = 1)
        # '0' = pending approval
        for req_status in ["1", "2", "0"]:
            page = 1
            while True:
                payload = {"page": page, "limit": 100, "request_status": req_status}
                try:
                    resp = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
                except requests.Timeout as e:
                    raise HTSRekapCategoryError("Timeout pada /kunjungan/data") from e
                except requests.ConnectionError as e:
                    raise HTSConnectionError(f"Gagal koneksi ke HTS: {e}") from e

                if is_session_expired(resp, base_url=self.base_url):
                    raise HTSSessionExpiredError("Sesi HTS expired saat fetch rekap kunjungan")

                if resp.status_code != 200:
                    raise HTSRekapCategoryError(f"HTTP {resp.status_code} dari /kunjungan/data")

                try:
                    data = resp.json()
                except ValueError as e:
                    raise HTSRekapCategoryError(f"JSON tidak valid dari /kunjungan/data: {e}") from e

                items = data.get("data", [])
                if isinstance(items, list):
                    for it in items:
                        item_id = it.get("id_kunjung") or it.get("no_kunjung")
                        if item_id and item_id not in seen_ids:
                            seen_ids.add(item_id)
                            all_items.append(it)

                pagination = data.get("pagination", {})
                total_pages = pagination.get("total_pages", 1)
                if page >= total_pages or not items:
                    break
                page += 1

        return all_items

    def _fetch_udp_datatables(self) -> List[dict]:
        """Fetch semua data Permohonan Layanan dari /mohon_data_layanan via POST DataTables.

        Endpoint ini membutuhkan CSRF token yang diambil dari halaman /udp terlebih dahulu,
        dan menggunakan format x-www-form-urlencoded (bukan JSON).
        """
        # Ambil CSRF token dari halaman /udp
        try:
            resp = self.session.get(f"{self.base_url}/udp", timeout=self.timeout)
        except Exception as e:
            raise HTSConnectionError(f"Gagal koneksi ke /udp: {e}") from e

        if is_session_expired(resp, base_url=self.base_url):
            raise HTSSessionExpiredError("Sesi HTS expired saat fetch rekap UDP")

        csrf_match = re.search(r'csrf_test_name"\s+value="([^"]+)"', resp.text)
        csrf_token = csrf_match.group(1) if csrf_match else ""
        if not csrf_token:
            logger.warning("CSRF token tidak ditemukan di halaman /udp")

        url = f"{self.base_url}{CATEGORY_ENDPOINTS['permohonan_layanan']}"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        }

        all_items: List[dict] = []
        start = 0
        length = 100

        while True:
            payload = {
                "draw": "1",
                "start": str(start),
                "length": str(length),
                "status_filter": "all",
                "service_filter": "",
                "csrf_test_name": csrf_token,
                "search[value]": "",
                "search[regex]": "false",
                "columns[0][data]": "0",
                "columns[0][name]": "",
                "columns[0][searchable]": "true",
                "columns[0][orderable]": "true",
                "columns[0][search][value]": "",
                "columns[0][search][regex]": "false",
                "order[0][column]": "0",
                "order[0][dir]": "asc",
            }

            try:
                resp = self.session.post(
                    url, data=urlencode(payload), headers=headers, timeout=self.timeout
                )
            except requests.Timeout as e:
                raise HTSRekapCategoryError(f"Timeout pada {url}") from e
            except requests.ConnectionError as e:
                raise HTSConnectionError(f"Gagal koneksi ke HTS: {e}") from e

            if is_session_expired(resp, base_url=self.base_url):
                raise HTSSessionExpiredError("Sesi HTS expired saat fetch permohonan_layanan")

            if resp.status_code != 200:
                raise HTSRekapCategoryError(f"HTTP {resp.status_code} dari {url}")

            try:
                j = resp.json()
            except Exception as e:
                raise HTSRekapCategoryError(f"JSON tidak valid dari {url}: {e}") from e

            items = j.get("data", [])
            if not items:
                break

            all_items.extend(items)

            records_total = j.get("recordsFiltered", j.get("recordsTotal", 0))
            start += length
            if start >= records_total:
                break

        return all_items

    def _fetch_domain_pentest_datatables(self) -> List[dict]:
        """Fetch semua data VPS/Domain & Rekomtek dari /domain-pentest/data via GET DataTables.

        Endpoint ini menggunakan method GET dengan parameter langsung di URL (query string).
        Tidak memerlukan CSRF token. Key data yang dikembalikan berbeda dari endpoint lain:
          - code_ticketting_aplication      => nomor tiket
          - perihal_surat                   => keterangan/keluhan
          - tanggal_surat_masuk             => tanggal masuk
          - status_text                     => teks status ("selesai" atau lainnya)
          - type_aplication_domain_pentest  => jenis pengajuan (untuk klasifikasi rekomtek)
        """
        url = f"{self.base_url}{CATEGORY_ENDPOINTS['vps_domain_rekomtek']}"
        headers = {
            "X-Requested-With": "XMLHttpRequest",
        }

        all_items: List[dict] = []
        start = 0
        length = 100

        while True:
            params = {
                "draw": "1",
                "start": str(start),
                "length": str(length),
                "status_filter": "all",
                "type": "",
                "search[value]": "",
                "search[regex]": "false",
                "order[0][column]": "0",
                "order[0][dir]": "desc",
            }

            try:
                resp = self.session.get(
                    url, params=params, headers=headers, timeout=self.timeout
                )
            except requests.Timeout as e:
                raise HTSRekapCategoryError("Timeout pada /domain-pentest/data") from e
            except requests.ConnectionError as e:
                raise HTSConnectionError(f"Gagal koneksi ke HTS: {e}") from e

            if is_session_expired(resp, base_url=self.base_url):
                raise HTSSessionExpiredError("Sesi HTS expired saat fetch domain-pentest")

            if resp.status_code != 200:
                raise HTSRekapCategoryError(
                    f"HTTP {resp.status_code} dari /domain-pentest/data"
                )

            try:
                j = resp.json()
            except Exception as e:
                raise HTSRekapCategoryError(
                    f"JSON tidak valid dari /domain-pentest/data: {e}"
                ) from e

            items = j.get("data", [])
            if not items:
                break

            all_items.extend(items)

            records_total = j.get("recordsFiltered", j.get("recordsTotal", 0))
            start += length
            if start >= records_total:
                break

        return all_items

    # ─── Normalizers ──────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_udp_item(item: dict) -> dict:
        """Normalisasi key item dari /mohon_data_layanan ke key standar rekap_service.

        Mapping:
          - nomor_aduan : teks dari <a>...</a> di field no_mohon
          - keluhan     : teks dari <span>...</span> di field layanan
          - created_at  : dari field tanggal
          - t_solve     : tanggal jika status badge mengandung 'selesai', else None
        """
        out = dict(item)

        # Nomor tiket: ekstrak teks dari <a>...text...</a>
        no_mohon_raw = str(item.get("no_mohon", ""))
        match_no = re.search(r">([^<]+)</a>", no_mohon_raw)
        out["nomor_aduan"] = match_no.group(1).strip() if match_no else no_mohon_raw

        # Keluhan/layanan: ekstrak teks dari <span>...text...</span>
        layanan_raw = str(item.get("layanan", ""))
        match_kel = re.search(r"<span>([^<]+)<", layanan_raw)
        out["keluhan"] = match_kel.group(1).strip() if match_kel else layanan_raw

        # Tanggal masuk
        out["created_at"] = item.get("tanggal")

        # t_solve proxy: jika status badge mengandung 'selesai'
        status_raw = str(item.get("status", "")).lower()
        out["t_solve"] = item.get("tanggal") if "selesai" in status_raw else None

        return out

    @staticmethod
    def _normalize_domain_pentest_item(item: dict) -> dict:
        """Normalisasi key item dari /domain-pentest/data ke key standar rekap_service.

        Mapping:
          - nomor_aduan : dari code_ticketting_aplication
          - keluhan     : dari perihal_surat
          - created_at  : dari tanggal_surat_masuk
          - t_solve     : tanggal_surat_masuk jika status_text == 'selesai', else None
        """
        out = dict(item)

        out["nomor_aduan"] = item.get("code_ticketting_aplication", "")
        out["keluhan"] = item.get("perihal_surat", "")
        out["created_at"] = item.get("tanggal_surat_masuk")

        status_text = str(item.get("status_text", "")).strip().lower()
        out["t_solve"] = item.get("tanggal_surat_masuk") if status_text == "selesai" else None

        return out

    # ─── Orchestrator ─────────────────────────────────────────────────────────

    def fetch_all_categories(self) -> Dict[str, Any]:
        """
        Fetch semua 5 kategori. Jika satu kategori gagal (bukan session expired),
        lanjutkan kategori lain dan catat error.

        Returns dict dengan keys: aduan, kunjungan, permohonan_layanan,
        vps_domain, rekomtek, _errors.
        """
        result: Dict[str, Any] = {
            "aduan": [],
            "kunjungan": [],
            "permohonan_layanan": [],
            "vps_domain": [],
            "rekomtek": [],
            "_errors": {},
        }

        # 1. Fetch aduan (JSON POST, paginasi internal)
        try:
            result["aduan"] = self._fetch_all_pages(CATEGORY_ENDPOINTS["aduan"])
        except (HTSSessionExpiredError, HTSConnectionError):
            raise
        except Exception as e:
            logger.warning("Gagal fetch kategori aduan: %s", e)
            result["_errors"]["aduan"] = str(e)

        # 2. Fetch kunjungan (JSON POST, multi request_status)
        try:
            result["kunjungan"] = self._fetch_kunjungan()
        except (HTSSessionExpiredError, HTSConnectionError):
            raise
        except Exception as e:
            logger.warning("Gagal fetch kategori kunjungan: %s", e)
            result["_errors"]["kunjungan"] = str(e)

        # 3. Fetch Permohonan Layanan dari /udp via POST DataTables + CSRF
        try:
            udp_items = self._fetch_udp_datatables()
            result["permohonan_layanan"] = [
                self._normalize_udp_item(item) for item in udp_items
            ]
            logger.info("Fetched %d item permohonan_layanan", len(result["permohonan_layanan"]))
        except (HTSSessionExpiredError, HTSConnectionError):
            raise
        except Exception as e:
            logger.warning("Gagal fetch kategori permohonan_layanan: %s", e)
            result["_errors"]["permohonan_layanan"] = str(e)

        # 4. Fetch VPS/Domain + Rekomtek dari /domain-pentest/data via GET DataTables
        try:
            dp_items = self._fetch_domain_pentest_datatables()
            for item in dp_items:
                normalized = self._normalize_domain_pentest_item(item)
                # Klasifikasi berdasarkan type_aplication_domain_pentest atau perihal_surat
                raw_type = str(item.get("type_aplication_domain_pentest", "")).strip().lower()
                perihal = str(item.get("perihal_surat", "")).strip().lower()
                if "rekomtek" in raw_type or "rekomtek" in perihal:
                    result["rekomtek"].append(normalized)
                else:
                    result["vps_domain"].append(normalized)
            logger.info(
                "Fetched %d vps_domain, %d rekomtek",
                len(result["vps_domain"]),
                len(result["rekomtek"]),
            )
        except (HTSSessionExpiredError, HTSConnectionError):
            raise
        except Exception as e:
            logger.warning("Gagal fetch VPS/Domain-Rekomtek: %s", e)
            result["_errors"]["vps_domain"] = str(e)
            result["_errors"]["rekomtek"] = str(e)

        return result
