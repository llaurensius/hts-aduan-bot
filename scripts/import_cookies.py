#!/usr/bin/env python3
"""CLI utility to import authenticated HTS session cookies into HTS Ticket Monitor.

Usage:
    python scripts/import_cookies.py
    python scripts/import_cookies.py --cookie-string "ci_session=...; TS0128f648=..."
    python scripts/import_cookies.py --ci-session "..." --waf "..."
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import requests

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config
from app.hts.client import create_http_session, is_session_expired


def parse_cookie_header(header_str: str) -> dict:
    """Parse raw Cookie header string into dictionary."""
    cookies = {}
    for part in header_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, val = part.split("=", 1)
            cookies[key.strip()] = val.strip()
    return cookies


def save_cookies(target_path: str, cookies: dict) -> None:
    """Save cookies dictionary to json file with 0o600 permissions."""
    Path(target_path).parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **cookies,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    # Restrict permissions (chmod 600) on POSIX
    if os.name != "nt":
        try:
            os.chmod(target_path, 0o600)
        except OSError:
            pass


def verify_session(config, cookies: dict) -> bool:
    """Test cookies against HTS /list_aduan endpoint."""
    session = create_http_session(config)
    domain = config.hts_base_url.split("//")[-1].split("/")[0].split(":")[0]
    for k, v in cookies.items():
        if k != "updated_at" and v:
            session.cookies.set(k, str(v), domain=domain)

    url = f"{config.hts_base_url}/list_aduan"
    try:
        resp = session.get(url, timeout=config.request_timeout, allow_redirects=True)
        return not is_session_expired(resp, base_url=config.hts_base_url)
    except Exception as e:
        print(f"⚠️  Gagal menguji koneksi ke {url}: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Import HTS session cookies into bot storage")
    parser.add_argument("--cookie-string", "-s", help="Raw Cookie header string (e.g. 'ci_session=...; TS0128f648=...')")
    parser.add_argument("--ci-session", help="Value of ci_session cookie")
    parser.add_argument("--waf", help="Value of F5 WAF cookie (TS0128f648)")
    parser.add_argument("--output", "-o", help="Target cookie file (defaults to COOKIE_FILE from .env or data/cookies.json)")
    parser.add_argument("--skip-verify", action="store_true", help="Skip live verification against HTS")

    args = parser.parse_args()

    # Load configuration
    try:
        config = load_config()
    except Exception:
        # Fallback config if environment not fully populated
        from app.config import AppConfig
        config = AppConfig(
            hts_base_url=os.getenv("HTS_BASE_URL", "https://hts.diskomdigi.jatengprov.go.id"),
            hts_username="",
            hts_password="",
            telegram_bot_token="",
            telegram_chat_id="",
            cookie_file=os.getenv("COOKIE_FILE", "data/cookies.json"),
        )

    target_file = args.output or config.cookie_file
    extracted_cookies = {}

    if args.cookie_string:
        extracted_cookies = parse_cookie_header(args.cookie_string)
    elif args.ci_session:
        extracted_cookies["ci_session"] = args.ci_session.strip()
        if args.waf:
            extracted_cookies["TS0128f648"] = args.waf.strip()
    else:
        print("\n=======================================================")
        print("   HTS Ticket Monitor — Session Cookie Importer       ")
        print("=======================================================")
        print("Cara mendapatkan cookie dari browser:")
        print("1. Buka https://hts.diskomdigi.jatengprov.go.id di browser dan login.")
        print("2. Buka DevTools (F12) -> tab Network.")
        print("3. Muat ulang halaman, klik request 'list_aduan', lihat 'Request Headers' -> 'Cookie'.")
        print("4. Salin seluruh string Cookie atau nilai ci_session.\n")

        raw_input = input("Tempel (paste) Cookie header atau ci_session: ").strip()
        if not raw_input:
            print("Error: Input kosong. Pembatalan.", file=sys.stderr)
            sys.exit(1)

        if "=" in raw_input:
            extracted_cookies = parse_cookie_header(raw_input)
        else:
            extracted_cookies["ci_session"] = raw_input

    if "ci_session" not in extracted_cookies:
        print("⚠️  Peringatan: 'ci_session' tidak ditemukan dalam input. Cookies yang terdeteksi:", list(extracted_cookies.keys()))

    save_cookies(target_file, extracted_cookies)
    print(f"\n💾 Cookie berhasil disimpan ke: {target_file}")

    if not args.skip_verify:
        print("🔄 Menguji validitas sesi ke server HTS...")
        is_valid = verify_session(config, extracted_cookies)
        if is_valid:
            print("✅ SUKSES! Sesi terverifikasi VALID dan aktif.")
            print("🤖 Bot yang sedang berjalan di PM2 akan otomatis mendeteksi sesi ini dan melanjutkan pemantauan!")
        else:
            print("⚠️  PERINGATAN: Server HTS menolak sesi ini (sesi belum login atau sudah kedaluwarsa).")
            print("    Pastikan Anda menyalin cookie saat browser masih dalam status login aktif.")


if __name__ == "__main__":
    main()
