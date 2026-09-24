"""HTTP client untuk fetch rekap tiket dari semua kategori HTS."""

import logging
from typing import Any, Dict, List, Optional
import requests
from app.hts.exceptions import HTSConnectionError, HTSSessionExpiredError, HTSError
from app.hts.client import is_session_expired

logger = logging.getLogger(__name__)

# Endpoint API per kategori (diperbarui jika endpoint HTS berbeda)
CATEGORY_ENDPOINTS: Dict[str, str] = {
    "aduan":               "/get_aduan_data",
    "kunjungan":           "/kunjungan/data",
    "permohonan_layanan":  "/mohon_data_layanan",
    "vps_domain_rekomtek": "/domain-pentest/data",
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

    def _fetch_all_pages(self, endpoint: str, extra_payload: Optional[Dict] = None) -> List[dict]:
        """Fetch semua halaman dari satu endpoint, return raw list item."""
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

        # 1. Fetch aduan
        try:
            result["aduan"] = self._fetch_all_pages(CATEGORY_ENDPOINTS["aduan"])
        except (HTSSessionExpiredError, HTSConnectionError):
            raise
        except Exception as e:
            logger.warning("Gagal fetch kategori aduan: %s", e)
            result["_errors"]["aduan"] = str(e)

        # 2. Fetch kunjungan
        try:
            result["kunjungan"] = self._fetch_kunjungan()
        except (HTSSessionExpiredError, HTSConnectionError):
            raise
        except Exception as e:
            logger.warning("Gagal fetch kategori kunjungan: %s", e)
            result["_errors"]["kunjungan"] = str(e)

        # 3. Fetch permohonan layanan
        try:
            result["permohonan_layanan"] = self._fetch_all_pages(CATEGORY_ENDPOINTS["permohonan_layanan"])
        except (HTSSessionExpiredError, HTSConnectionError):
            raise
        except Exception as e:
            logger.warning("Gagal fetch kategori permohonan_layanan: %s", e)
            result["_errors"]["permohonan_layanan"] = str(e)

        # Fetch VPS/Domain + Rekomtek dari endpoint yang sama, lalu pisah
        try:
            combined = self._fetch_all_pages(CATEGORY_ENDPOINTS["vps_domain_rekomtek"])
            for item in combined:
                raw_kat = str(item.get("kategori", "")).strip().lower()
                if "rekomtek" in raw_kat:
                    result["rekomtek"].append(item)
                else:
                    result["vps_domain"].append(item)
        except HTSSessionExpiredError:
            raise
        except HTSConnectionError:
            raise
        except Exception as e:
            logger.warning("Gagal fetch VPS/Domain-Rekomtek: %s", e)
            result["_errors"]["vps_domain"] = str(e)
            result["_errors"]["rekomtek"] = str(e)

        return result
