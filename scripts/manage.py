import os
import sys
from pathlib import Path

# Add project root to sys.path so we can import 'app'
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.config import load_config
from app.database.db import DatabaseManager
from app.hts.client import HTSClient
from app.notifications.telegram import TelegramNotifier
from app.notifications.templates import format_new_ticket
from app.monitoring.ticket_processor import TicketProcessor
from app.utils.time_utils import utcnow_iso
import uuid


def prompt_ticket_number(prompt_text: str = "Masukkan nomor tiket: ") -> str:
    """Prompt user for a ticket number."""
    return input(prompt_text).strip()


def resend_notification(db: DatabaseManager, notifier: TelegramNotifier):
    print("\n--- Fitur 1: Kirim Ulang Notifikasi ---")
    print("[1] Berdasarkan nomor tiket tertentu (satuan)")
    print("[2] Berdasarkan rentang nomor tiket")
    print("[3] Berdasarkan status tiket saat ini di DB")
    
    choice = input("Pilih target tiket (1-3): ").strip()
    
    tickets_to_process = []
    
    with db.connection:
        models = db.models
        if choice == "1":
            nomor = prompt_ticket_number()
            t = models.get_ticket(nomor)
            if t:
                tickets_to_process.append(t)
        elif choice == "2":
            start_num = prompt_ticket_number("Masukkan nomor tiket awal: ")
            end_num = prompt_ticket_number("Masukkan nomor tiket akhir: ")
            # Range logic by the numeric prefix:
            try:
                start_int = int(start_num)
                end_int = int(end_num)
            except ValueError:
                print("Format rentang harus berupa angka (prefix tiket)!")
                return
                
            all_tickets = models.get_tickets_by_filter(status=None)
            for t in all_tickets:
                prefix = t['nomor_aduan'].split('-')[0]
                if prefix.isdigit() and start_int <= int(prefix) <= end_int:
                    tickets_to_process.append(t)
        elif choice == "3":
            print("Pilih status:\n [1] Belum Ditangani\n [2] Sudah Ditangani\n [3] Semua")
            stat_choice = input("Pilih (1-3): ").strip()
            if stat_choice == "1":
                tickets_to_process = models.get_tickets_by_filter(status="pending")
            elif stat_choice == "2":
                tickets_to_process = models.get_tickets_by_filter(status="completed")
            elif stat_choice == "3":
                tickets_to_process = models.get_tickets_by_filter(status=None)
            else:
                print("Pilihan tidak valid.")
                return
        else:
            print("Pilihan tidak valid.")
            return

    if not tickets_to_process:
        print("Tidak ada tiket yang cocok dengan kriteria.")
        return
        
    print(f"\nDitemukan {len(tickets_to_process)} tiket.")
    if input("Lanjutkan mengirim notifikasi? [y/N]: ").strip().lower() != 'y':
        return
        
    from app.hts.parser import TicketData
    
    success_count = 0
    models = db.models
    for t_row in tickets_to_process:
        ticket = TicketData(
                nomor_aduan=t_row['nomor_aduan'],
                kategori=t_row['kategori'],
                sub_kategori=t_row['sub_kategori'],
                instansi=t_row['instansi'],
                opd_induk=t_row['opd_induk'],
                pic_nama=t_row['pic_nama'],
                pic_nomor=t_row['pic_nomor'],
                keluhan=t_row['keluhan'],
                tanggal_aduan=t_row['tanggal_aduan'],
                t_solve=t_row['t_solve'],
                is_submitted=t_row['is_submitted'],
            )
            
        message = format_new_ticket(ticket)
        try:
            resp = notifier.send(message)
            msg_id = resp.get("result", {}).get("message_id")
            print(f"[OK] {ticket.nomor_aduan}")
            success_count += 1
        except Exception as e:
            print(f"[FAIL] {ticket.nomor_aduan} - Gagal kirim Telegram: {e}")
            continue

        # Catat ke DB sebagai audit trail (best-effort, tidak pengaruhi status sukses)
        try:
            with db.transaction() as conn:
                event_id = str(uuid.uuid4())
                models.insert_event(
                    conn=conn,
                    ticket_id=t_row['id'],
                    nomor_aduan=t_row['nomor_aduan'],
                    event_type="MANUAL_RESEND",
                    event_id=event_id
                )
                notif_id = str(uuid.uuid4())
                models.insert_notification(
                    conn=conn,
                    message_text=message,
                    event_id=event_id,
                    status="SENT",
                    notification_id=notif_id
                )
                models.mark_notification_sent(notif_id, msg_id, conn=conn)
        except Exception as db_err:
            print(f"  [WARN] {ticket.nomor_aduan} - Terkirim tapi gagal catat ke DB: {db_err}")
                
    print(f"Selesai! {success_count}/{len(tickets_to_process)} berhasil dikirim.")


def delete_and_refetch(db: DatabaseManager, client: HTSClient, notifier: TelegramNotifier):
    print("\n--- Fitur 2: Hapus & Tarik Ulang (Re-fetch) ---")
    nomor = prompt_ticket_number()
    
    with db.connection:
        t_row = db.models.get_ticket(nomor)
        
    if not t_row:
        print(f"Tiket {nomor} tidak ditemukan di database lokal.")
        return
        
    print(f"Tiket {nomor} ditemukan di DB.")
    if input(f"Yakin ingin menghapus {nomor} dari DB? [y/N]: ").strip().lower() != 'y':
        return
        
    with db.transaction() as conn:
        db.models.delete_ticket_cascade(nomor, conn=conn)
    print(f"Tiket {nomor} berhasil dihapus dari DB lokal.")
    
    print(f"Sedang menarik ulang {nomor} dari HTS API... (bisa memakan waktu beberapa detik)")
    try:
        ticket_data = client.fetch_ticket_by_nomor(nomor)
    except Exception as e:
        print(f"Error fetching from API: {e}")
        return
        
    if not ticket_data:
        print(f"Peringatan: Tiket {nomor} tidak ditemukan di HTS API.")
        return
        
    print(f"Tiket {nomor} berhasil diambil dari API.")
    
    processor = TicketProcessor(db, is_initial_sync=True)
    processor.process(ticket_data, sync_source="manual_refetch")
        
    print(f"Tiket {nomor} berhasil disimpan kembali ke DB.")
    
    if input("Kirim notifikasi tiket ini ke Telegram? [y/N]: ").strip().lower() == 'y':
        message = format_new_ticket(ticket_data)
        try:
            resp = notifier.send(message)
            msg_id = resp.get("result", {}).get("message_id")
            
            with db.transaction() as conn:
                t_row_new = db.models.get_ticket(nomor, conn=conn)
                event_id = str(uuid.uuid4())
                db.models.insert_event(
                    conn=conn,
                    ticket_id=t_row_new['id'],
                    nomor_aduan=nomor,
                    event_type="MANUAL_REFETCH",
                    event_id=event_id
                )
                notif_id = str(uuid.uuid4())
                db.models.insert_notification(
                    conn=conn,
                    message_text=message,
                    event_id=event_id,
                    status="SENT",
                    notification_id=notif_id
                )
                db.models.mark_notification_sent(notif_id, msg_id, conn=conn)
            print("[OK] Notifikasi terkirim.")
        except Exception as e:
            print(f"[FAIL] Gagal mengirim notifikasi: {e}")


def edit_ticket(db: DatabaseManager):
    print("\n--- Fitur 3: Edit Data Tiket ---")
    nomor = prompt_ticket_number()
    
    with db.connection:
        t_row = db.models.get_ticket(nomor)
        
    if not t_row:
        print(f"Tiket {nomor} tidak ditemukan di database lokal.")
        return
        
    fields = [
        ("kategori", t_row["kategori"]),
        ("sub_kategori", t_row["sub_kategori"]),
        ("instansi", t_row["instansi"]),
        ("opd_induk", t_row["opd_induk"]),
        ("pic_nama", t_row["pic_nama"]),
        ("pic_nomor", t_row["pic_nomor"]),
        ("keluhan", t_row["keluhan"]),
        ("tanggal_aduan", t_row["tanggal_aduan"]),
        ("t_solve", t_row["t_solve"]),
        ("is_submitted", t_row["is_submitted"]),
    ]
    
    print("\nField yang bisa diedit:")
    for i, (f_name, f_val) in enumerate(fields, 1):
        print(f"[{i}] {f_name:<15}: {f_val}")
        
    choice = input("\nPilih nomor field yang ingin diubah (atau 0 untuk batal): ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(fields)):
        print("Batal edit.")
        return
        
    idx = int(choice) - 1
    selected_field = fields[idx][0]
    old_val = fields[idx][1]
    
    print(f"\nMengubah '{selected_field}'. Nilai saat ini: {old_val}")
    new_val = input("Masukkan nilai baru: ").strip()
    
    if selected_field == "is_submitted":
        try:
            new_val = int(new_val)
        except ValueError:
            print("is_submitted harus berupa angka (0/1).")
            return
            
    if input(f"Yakin mengubah {selected_field} dari '{old_val}' menjadi '{new_val}'? [y/N]: ").strip().lower() != 'y':
        print("Batal menyimpan.")
        return
        
    from app.hts.parser import TicketData
    import hashlib
    import json
    
    updated_dict = dict(t_row)
    updated_dict[selected_field] = new_val
    
    ticket = TicketData(
        nomor_aduan=updated_dict['nomor_aduan'],
        kategori=updated_dict['kategori'],
        sub_kategori=updated_dict['sub_kategori'],
        instansi=updated_dict['instansi'],
        opd_induk=updated_dict['opd_induk'],
        pic_nama=updated_dict['pic_nama'],
        pic_nomor=updated_dict['pic_nomor'],
        keluhan=updated_dict['keluhan'],
        tanggal_aduan=updated_dict['tanggal_aduan'],
        t_solve=updated_dict['t_solve'],
        is_submitted=int(updated_dict['is_submitted']),
    )
    
    monitored = ticket.to_monitored_dict()
    monitored_json = json.dumps(monitored, sort_keys=True)
    new_hash = hashlib.sha256(monitored_json.encode('utf-8')).hexdigest()
    
    with db.transaction() as conn:
        latest_snapshot = db.models.get_latest_snapshot(nomor, conn=conn)
        prev_snapshot_id = latest_snapshot["id"] if latest_snapshot else None
        
        db.models.update_ticket(conn, ticket, new_hash=new_hash)
        new_snapshot_id = db.models.insert_snapshot(
            conn,
            ticket_id=t_row['id'],
            nomor_aduan=nomor,
            data=monitored,
            hash_val=new_hash,
            snapshot_type="manual_edit"
        )
        
        db.models.insert_event(
            conn=conn,
            ticket_id=t_row['id'],
            nomor_aduan=nomor,
            event_type="MANUAL_EDIT",
            changed_fields={selected_field: {"old": old_val, "new": new_val}},
            previous_snapshot_id=prev_snapshot_id,
            current_snapshot_id=new_snapshot_id
        )
        
    print(f"Berhasil mengupdate tiket {nomor}!")


def main():
    config = load_config()
    db = DatabaseManager(config.db_path)
    client = HTSClient(config)
    
    # We may need to pass session to notifier if using custom session
    notifier = TelegramNotifier(config)
    
    while True:
        print("\n==================================================")
        print("        HTS TICKET MONITOR — ADMIN TOOLS")
        print("==================================================")
        print("[1] Kirim Ulang Notifikasi Tiket ke Telegram")
        print("[2] Hapus Tiket & Tarik Ulang dari HTS (Re-fetch)")
        print("[3] Edit Data Tiket di Database Lokal")
        print("[0] Keluar")
        
        choice = input("Pilihan Anda: ").strip()
        
        if choice == "0":
            break
        elif choice == "1":
            resend_notification(db, notifier)
        elif choice == "2":
            delete_and_refetch(db, client, notifier)
        elif choice == "3":
            edit_ticket(db)
        else:
            print("Pilihan tidak valid.")

if __name__ == "__main__":
    main()
