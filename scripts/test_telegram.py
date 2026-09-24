#!/usr/bin/env python3
"""CLI utility to test Telegram Bot notification delivery."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config
from app.notifications.telegram import TelegramError, TelegramNotifier, TelegramRateLimitError
from app.utils.time_utils import utcnow_iso


def main():
    parser = argparse.ArgumentParser(description="Test Telegram notification delivery")
    parser.add_argument(
        "--message", "-m",
        default=None,
        help="Custom message to send (default: standardized test message)",
    )
    parser.add_argument(
        "--queue", "-q",
        action="store_true",
        help="Test through database notification queue instead of direct send",
    )
    args = parser.parse_args()

    print("\n=======================================================")
    print("   HTS Ticket Monitor — Telegram Delivery Test        ")
    print("=======================================================")

    try:
        config = load_config()
    except Exception as e:
        print(f"❌ Gagal memuat konfigurasi dari .env: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"• Target Chat ID : {config.telegram_chat_id}")
    # Mask bot token for display safety
    token = config.telegram_bot_token
    masked_token = token[:6] + "..." + token[-4:] if len(token) > 10 else "***"
    print(f"• Bot Token      : {masked_token}")

    test_text = args.message or (
        "🧪 *TEST NOTIFIKASI TELEGRAM*\n\n"
        f"📅 Waktu: `{utcnow_iso()}`\n"
        "🤖 Sistem: *HTS Ticket Monitor*\n"
        "✅ Status: Koneksi bot Telegram berhasil terverifikasi dan aktif!"
    )

    notifier = TelegramNotifier(config)

    if args.queue:
        from app.database.db import DatabaseManager
        from app.notifications.queue import NotificationQueue

        print("\n📬 Mode: Menguji melalui antrean database (NotificationQueue)...")
        db = DatabaseManager(config.db_path)
        db.connect()
        queue = NotificationQueue(db)

        with db.transaction() as conn:
            notif_id = queue.queue(conn, message=test_text, channel="telegram")
        print(f"• Notifikasi masuk antrean ID: {notif_id}")

        print("🚀 Memproses pengiriman antrean...")
        sent_count = queue.process_pending(notifier)
        db.close()

        if sent_count > 0:
            print(f"✅ SUKSES! Notifikasi ID {notif_id} berhasil dikirim ke Telegram melalui antrean.")
        else:
            print("⚠️ Gagal mengirim dari antrean. Periksa log atau status tabel notifications.")
    else:
        print("\n🚀 Mengirim pesan langsung ke Telegram...")
        try:
            res = notifier.send(test_text)
            msg_id = res.get("result", {}).get("message_id", "N/A")
            print(f"\n✅ SUKSES! Pesan berhasil diterima oleh Telegram.")
            print(f"• Telegram Message ID: {msg_id}")
            print("👉 Silakan periksa aplikasi Telegram Anda (chat / grup / channel tujuan).")
        except TelegramRateLimitError as e:
            print(f"\n⚠️ RATE LIMIT HIT: Telegram meminta menunggu {e.retry_after} detik.", file=sys.stderr)
            sys.exit(1)
        except TelegramError as e:
            print(f"\n❌ GAGAL MENGIRIM KE TELEGRAM (HTTP {e.status_code}):", file=sys.stderr)
            print(f"• Detail Error: {e.body}", file=sys.stderr)
            print("\nTips Pemecahan Masalah:")
            if e.status_code == 401:
                print("1. Periksa TELEGRAM_BOT_TOKEN di .env, token bot tidak valid.")
            elif e.status_code == 400:
                print("1. Pastikan TELEGRAM_CHAT_ID di .env benar.")
                print("2. Jika chat pribadi, pastikan Anda sudah menekan tombol /start di bot tersebut.")
                print("3. Jika berupa grup/channel, pastikan bot sudah dimasukkan sebagai anggota / Admin.")
            sys.exit(1)
        except Exception as e:
            print(f"\n❌ Terjadi kesalahan tak terduga: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
