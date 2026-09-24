"""FastAPI backend server for HTS Ticket Monitor Dashboard."""

import hashlib
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.config import AppConfig, load_config
from app.database.db import DatabaseManager
from app.hts.client import HTSClient
from app.hts.parser import TicketData
from app.hts.session import SessionManager
from app.hts.rekap_client import HTSRekapClient
from app.monitoring.ticket_processor import TicketProcessor
from app.monitoring.rekap_service import RekapService, SHIFTS
from app.notifications.telegram import TelegramNotifier
from app.notifications.templates import (
    format_changed_ticket,
    format_completed_ticket,
    format_new_ticket,
)

logger = logging.getLogger("hts_dashboard")


def format_ticket_message(
    ticket: TicketData,
    petugas_nama: Optional[str] = None,
    petugas_role: Optional[str] = None,
) -> str:
    """Format notification text based on whether ticket is completed or new/pending."""
    if ticket.is_completed:
        return format_completed_ticket(ticket, petugas_nama=petugas_nama, petugas_role=petugas_role)
    return format_new_ticket(ticket)

# Paths
MODULE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = MODULE_DIR / "templates"
STATIC_DIR = MODULE_DIR / "static"

# Create App
app = FastAPI(title="HTS Ticket Monitor — Dashboard", version="1.0.0")

# Mount static and templates
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Lazy-loaded singleton services
_config: Optional[AppConfig] = None
_db: Optional[DatabaseManager] = None
_hts_client: Optional[HTSClient] = None
_notifier: Optional[TelegramNotifier] = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_db() -> DatabaseManager:
    cfg = get_config()
    db = DatabaseManager(cfg.db_path)
    db.connect()
    return db


def get_hts_client() -> HTSClient:
    global _hts_client
    if _hts_client is None:
        cfg = get_config()
        _hts_client = HTSClient(cfg)
    return _hts_client


def get_notifier() -> TelegramNotifier:
    global _notifier
    if _notifier is None:
        cfg = get_config()
        _notifier = TelegramNotifier(cfg)
    return _notifier


def get_bot_health() -> Dict[str, Any]:
    """Fetch health check from main bot process running at port 8080."""
    cfg = get_config()
    url = f"http://{cfg.health_host}:{cfg.health_port}/health"
    try:
        resp = requests.get(url, headers={"Connection": "close"}, timeout=1.0)
        if resp.status_code in (200, 503):
            return resp.json()
    except Exception:
        pass
    return {
        "status": "stopped",
        "app": {"state": "OFFLINE", "uptime_seconds": 0},
        "hts": {"state": "unknown", "last_poll": None},
        "telegram": {"last_sent": None, "pending_notifications": 0, "failed_notifications": 0},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Request Models
# ─────────────────────────────────────────────────────────────────────────────

class TicketUpdateRequest(BaseModel):
    kategori: Optional[str] = None
    sub_kategori: Optional[str] = None
    instansi: Optional[str] = None
    opd_induk: Optional[str] = None
    pic_nama: Optional[str] = None
    pic_nomor: Optional[str] = None
    keluhan: Optional[str] = None
    tanggal_aduan: Optional[str] = None
    t_solve: Optional[str] = None
    is_submitted: Optional[int] = None
    status_display: Optional[str] = None


class RekapRequest(BaseModel):
    tanggal: str
    shift: str
    nama_petugas: str


class RefetchRequest(BaseModel):
    send_notification: bool = False


class ResendRequest(BaseModel):
    petugas_nama: Optional[str] = None
    petugas_role: Optional[str] = None


class BulkResendRequest(BaseModel):
    status: Optional[str] = None  # "pending", "completed", or "all"
    nomor_aduans: Optional[List[str]] = None
    petugas_nama: Optional[str] = None
    petugas_role: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# HTML Page Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.api_route("/", methods=["GET", "HEAD"], response_class=RedirectResponse)
def root_redirect():
    return RedirectResponse(url="/dashboard", status_code=302)


@app.api_route("/dashboard", methods=["GET", "HEAD"], response_class=HTMLResponse)
def dashboard_page(request: Request):
    db = get_db()
    stats = db.models.get_dashboard_stats()
    recent_events = db.models.get_recent_events(limit=10)
    bot_health = get_bot_health()

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "active_tab": "overview",
            "stats": stats,
            "recent_events": recent_events,
            "bot_health": bot_health,
        },
    )


@app.api_route("/tickets", methods=["GET", "HEAD"], response_class=HTMLResponse)
def tickets_page(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(15, ge=5, le=100),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    sort: Optional[str] = Query("tanggal_aduan"),
    order: Optional[str] = Query("desc"),
):
    db = get_db()
    result = db.models.get_tickets_paginated(
        page=page,
        limit=limit,
        status=status,
        search=q,
        sort_by=sort,
        order=order,
    )
    stats = db.models.get_dashboard_stats()

    return templates.TemplateResponse(
        request=request,
        name="tickets.html",
        context={
            "active_tab": "tickets",
            "tickets": result["items"],
            "total": result["total"],
            "page": result["page"],
            "limit": result["limit"],
            "total_pages": result["total_pages"],
            "current_status": status or "all",
            "search_query": q or "",
            "sort_by": result["sort_by"],
            "sort_order": result["order"],
            "stats": stats,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# REST API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.api_route("/api/stats", methods=["GET", "HEAD"])
def api_stats():
    db = get_db()
    stats = db.models.get_dashboard_stats()
    bot_health = get_bot_health()
    return JSONResponse({"stats": stats, "bot_health": bot_health})


@app.get("/api/tickets")
def api_tickets(
    page: int = Query(1, ge=1),
    limit: int = Query(15, ge=5, le=100),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    sort: Optional[str] = Query("tanggal_aduan"),
    order: Optional[str] = Query("desc"),
):
    db = get_db()
    result = db.models.get_tickets_paginated(
        page=page,
        limit=limit,
        status=status,
        search=q,
        sort_by=sort,
        order=order,
    )
    return JSONResponse(result)


@app.get("/api/tickets/{nomor_aduan}")
def api_ticket_details(nomor_aduan: str):
    db = get_db()
    details = db.models.get_ticket_details(nomor_aduan)
    if not details:
        raise HTTPException(status_code=404, detail="Tiket tidak ditemukan di database.")
    return JSONResponse(details)


@app.post("/api/tickets/{nomor_aduan}/resend")
def api_resend_notification(nomor_aduan: str, payload: Optional[ResendRequest] = None):
    db = get_db()
    notifier = get_notifier()

    t_row = db.models.get_ticket(nomor_aduan)
    if not t_row:
        raise HTTPException(status_code=404, detail=f"Tiket {nomor_aduan} tidak ditemukan.")

    custom_nama = payload.petugas_nama if payload and payload.petugas_nama else None
    custom_role = payload.petugas_role if payload and payload.petugas_role else None

    row_keys = t_row.keys() if hasattr(t_row, "keys") else []
    penjelasan = (t_row["penjelasan"] if "penjelasan" in row_keys else "") or ""
    pic_kominfo = (t_row["pic_kominfo"] if "pic_kominfo" in row_keys else "") or ""

    is_completed = t_row["t_solve"] not in ("0", "", None)
    if is_completed and not penjelasan:
        try:
            client = get_hts_client()
            live_ticket = client.fetch_ticket_by_nomor(nomor_aduan)
            if live_ticket and live_ticket.penjelasan:
                penjelasan = live_ticket.penjelasan
                pic_kominfo = live_ticket.pic_kominfo or pic_kominfo
                with db.transaction() as conn:
                    conn.execute(
                        "UPDATE tickets SET penjelasan = ?, pic_kominfo = ? WHERE nomor_aduan = ?",
                        (penjelasan, pic_kominfo, nomor_aduan),
                    )
        except Exception as e:
            logger.warning("Could not fetch live ticket explanation for %s: %s", nomor_aduan, e)

    ticket = TicketData(
        nomor_aduan=t_row["nomor_aduan"],
        kategori=t_row["kategori"],
        sub_kategori=t_row["sub_kategori"],
        instansi=t_row["instansi"],
        opd_induk=t_row["opd_induk"],
        pic_nama=t_row["pic_nama"],
        pic_nomor=t_row["pic_nomor"],
        keluhan=t_row["keluhan"],
        tanggal_aduan=t_row["tanggal_aduan"],
        t_solve=t_row["t_solve"],
        is_submitted=t_row["is_submitted"],
        penjelasan=penjelasan,
        pic_kominfo=pic_kominfo,
    )

    message = format_ticket_message(ticket, petugas_nama=custom_nama, petugas_role=custom_role)
    try:
        resp = notifier.send(message)
        msg_id = resp.get("result", {}).get("message_id")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gagal kirim ke Telegram: {str(e)}")

    # Audit trail to DB (best-effort)
    audit_warning = None
    try:
        with db.transaction() as conn:
            event_id = str(uuid.uuid4())
            db.models.insert_event(
                conn=conn,
                ticket_id=t_row["id"],
                nomor_aduan=t_row["nomor_aduan"],
                event_type="MANUAL_RESEND",
                event_id=event_id,
            )
            notif_id = str(uuid.uuid4())
            db.models.insert_notification(
                conn=conn,
                message_text=message,
                event_id=event_id,
                status="SENT",
                notification_id=notif_id,
            )
            db.models.mark_notification_sent(notif_id, msg_id, conn=conn)
    except Exception as db_err:
        audit_warning = f"Terkirim ke Telegram tapi gagal catat audit ke DB: {str(db_err)}"

    return JSONResponse({
        "success": True,
        "nomor_aduan": nomor_aduan,
        "message": f"Notifikasi tiket {nomor_aduan} berhasil dikirim ke Telegram!",
        "audit_warning": audit_warning,
    })


@app.post("/api/tickets/bulk-resend")
def api_bulk_resend(payload: BulkResendRequest):
    db = get_db()
    notifier = get_notifier()

    tickets_to_send = []
    if payload.nomor_aduans:
        for num in payload.nomor_aduans:
            t = db.models.get_ticket(num)
            if t:
                tickets_to_send.append(t)
    elif payload.status:
        stat = payload.status.lower()
        if stat == "pending":
            tickets_to_send = db.models.get_tickets_by_filter(status="pending")
        elif stat == "completed":
            tickets_to_send = db.models.get_tickets_by_filter(status="completed")
        else:
            tickets_to_send = db.models.get_tickets_by_filter(status=None)
    else:
        raise HTTPException(status_code=400, detail="Harus menyertakan status atau nomor_aduans.")

    if not tickets_to_send:
        return JSONResponse({
            "success": True,
            "total": 0,
            "sent": 0,
            "failed": 0,
            "results": [],
            "message": "Tidak ada tiket yang cocok untuk dikirim.",
        })

    results = []
    sent_count = 0
    failed_count = 0

    for t_row in tickets_to_send:
        row_keys = t_row.keys() if hasattr(t_row, "keys") else []
        penjelasan = (t_row["penjelasan"] if "penjelasan" in row_keys else "") or ""
        pic_kominfo = (t_row["pic_kominfo"] if "pic_kominfo" in row_keys else "") or ""
        ticket = TicketData(
            nomor_aduan=t_row["nomor_aduan"],
            kategori=t_row["kategori"],
            sub_kategori=t_row["sub_kategori"],
            instansi=t_row["instansi"],
            opd_induk=t_row["opd_induk"],
            pic_nama=t_row["pic_nama"],
            pic_nomor=t_row["pic_nomor"],
            keluhan=t_row["keluhan"],
            tanggal_aduan=t_row["tanggal_aduan"],
            t_solve=t_row["t_solve"],
            is_submitted=t_row["is_submitted"],
            penjelasan=penjelasan,
            pic_kominfo=pic_kominfo,
        )
        msg = format_ticket_message(
            ticket,
            petugas_nama=payload.petugas_nama,
            petugas_role=payload.petugas_role,
        )
        try:
            resp = notifier.send(msg)
            msg_id = resp.get("result", {}).get("message_id")
            sent_count += 1
            results.append({"nomor_aduan": ticket.nomor_aduan, "status": "OK"})
        except Exception as e:
            failed_count += 1
            results.append({"nomor_aduan": ticket.nomor_aduan, "status": "FAIL", "error": str(e)})
            continue

        # Audit trail (best-effort)
        try:
            with db.transaction() as conn:
                event_id = str(uuid.uuid4())
                db.models.insert_event(
                    conn=conn,
                    ticket_id=t_row["id"],
                    nomor_aduan=t_row["nomor_aduan"],
                    event_type="MANUAL_RESEND",
                    event_id=event_id,
                )
                notif_id = str(uuid.uuid4())
                db.models.insert_notification(
                    conn=conn,
                    message_text=msg,
                    event_id=event_id,
                    status="SENT",
                    notification_id=notif_id,
                )
                db.models.mark_notification_sent(notif_id, msg_id, conn=conn)
        except Exception:
            pass

    return JSONResponse({
        "success": True,
        "total": len(tickets_to_send),
        "sent": sent_count,
        "failed": failed_count,
        "results": results,
        "message": f"Selesai! {sent_count}/{len(tickets_to_send)} notifikasi berhasil dikirim.",
    })


@app.post("/api/tickets/bulk-resend-stream")
def api_bulk_resend_stream(payload: BulkResendRequest):
    db = get_db()
    notifier = get_notifier()

    tickets_to_send = []
    if payload.nomor_aduans:
        for num in payload.nomor_aduans:
            t = db.models.get_ticket(num)
            if t:
                tickets_to_send.append(t)
    elif payload.status:
        stat = payload.status.lower()
        if stat == "pending":
            tickets_to_send = db.models.get_tickets_by_filter(status="pending")
        elif stat == "completed":
            tickets_to_send = db.models.get_tickets_by_filter(status="completed")
        else:
            tickets_to_send = db.models.get_tickets_by_filter(status=None)
    else:
        raise HTTPException(status_code=400, detail="Harus menyertakan status atau nomor_aduans.")

    def stream_generator():
        total = len(tickets_to_send)
        yield json.dumps({"type": "init", "total": total}) + "\n"

        if total == 0:
            yield json.dumps({"type": "done", "total": 0, "sent": 0, "failed": 0, "message": "Tidak ada tiket yang cocok."}) + "\n"
            return

        sent_count = 0
        failed_count = 0

        for i, t_row in enumerate(tickets_to_send, 1):
            row_keys = t_row.keys() if hasattr(t_row, "keys") else []
            penjelasan = (t_row["penjelasan"] if "penjelasan" in row_keys else "") or ""
            pic_kominfo = (t_row["pic_kominfo"] if "pic_kominfo" in row_keys else "") or ""
            ticket = TicketData(
                nomor_aduan=t_row["nomor_aduan"],
                kategori=t_row["kategori"],
                sub_kategori=t_row["sub_kategori"],
                instansi=t_row["instansi"],
                opd_induk=t_row["opd_induk"],
                pic_nama=t_row["pic_nama"],
                pic_nomor=t_row["pic_nomor"],
                keluhan=t_row["keluhan"],
                tanggal_aduan=t_row["tanggal_aduan"],
                t_solve=t_row["t_solve"],
                is_submitted=t_row["is_submitted"],
                penjelasan=penjelasan,
                pic_kominfo=pic_kominfo,
            )
            msg = format_ticket_message(
                ticket,
                petugas_nama=payload.petugas_nama,
                petugas_role=payload.petugas_role,
            )
            status_ok = True
            error_str = None
            try:
                resp = notifier.send(msg)
                msg_id = resp.get("result", {}).get("message_id")
                sent_count += 1
            except Exception as e:
                failed_count += 1
                status_ok = False
                error_str = str(e)

            if status_ok:
                try:
                    with db.transaction() as conn:
                        event_id = str(uuid.uuid4())
                        db.models.insert_event(
                            conn=conn,
                            ticket_id=t_row["id"],
                            nomor_aduan=t_row["nomor_aduan"],
                            event_type="MANUAL_RESEND",
                            event_id=event_id,
                        )
                        notif_id = str(uuid.uuid4())
                        db.models.insert_notification(
                            conn=conn,
                            message_text=msg,
                            event_id=event_id,
                            status="SENT",
                            notification_id=notif_id,
                        )
                        db.models.mark_notification_sent(notif_id, msg_id, conn=conn)
                except Exception:
                    pass

            yield json.dumps({
                "type": "progress",
                "current": i,
                "total": total,
                "nomor_aduan": ticket.nomor_aduan,
                "status": "OK" if status_ok else "FAIL",
                "error": error_str,
                "sent": sent_count,
                "failed": failed_count,
            }) + "\n"

        yield json.dumps({
            "type": "done",
            "total": total,
            "sent": sent_count,
            "failed": failed_count,
            "message": f"Selesai! {sent_count}/{total} notifikasi berhasil dikirim.",
        }) + "\n"

    return StreamingResponse(stream_generator(), media_type="application/x-ndjson")


@app.post("/api/tickets/{nomor_aduan}/refetch")
def api_refetch_ticket(nomor_aduan: str, payload: RefetchRequest):
    db = get_db()
    client = get_hts_client()
    notifier = get_notifier()

    # 1. Check ticket in DB
    t_row = db.models.get_ticket(nomor_aduan)
    if not t_row:
        raise HTTPException(status_code=404, detail=f"Tiket {nomor_aduan} tidak ditemukan di database lokal.")

    # 2. Delete existing cascade
    with db.transaction() as conn:
        db.models.delete_ticket_cascade(nomor_aduan, conn=conn)

    # 3. Fetch from HTS API
    try:
        ticket_data = client.fetch_ticket_by_nomor(nomor_aduan)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error saat menghubungi HTS API: {str(e)}")

    if not ticket_data:
        raise HTTPException(
            status_code=404,
            detail=f"Tiket {nomor_aduan} tidak ditemukan pada respon HTS API. Tiket mungkin sudah sangat lama.",
        )

    # 4. Save back to DB
    processor = TicketProcessor(db, is_initial_sync=True)
    processor.process(ticket_data, sync_source="manual_refetch")

    # 5. Optionally send Telegram notification
    notif_sent = False
    if payload.send_notification:
        message = format_ticket_message(ticket_data)
        try:
            resp = notifier.send(message)
            msg_id = resp.get("result", {}).get("message_id")
            notif_sent = True

            with db.transaction() as conn:
                t_new = db.models.get_ticket(nomor_aduan, conn=conn)
                event_id = str(uuid.uuid4())
                db.models.insert_event(
                    conn=conn,
                    ticket_id=t_new["id"],
                    nomor_aduan=nomor_aduan,
                    event_type="MANUAL_REFETCH",
                    event_id=event_id,
                )
                notif_id = str(uuid.uuid4())
                db.models.insert_notification(
                    conn=conn,
                    message_text=message,
                    event_id=event_id,
                    status="SENT",
                    notification_id=notif_id,
                )
                db.models.mark_notification_sent(notif_id, msg_id, conn=conn)
        except Exception as e:
            logger.warning("Gagal kirim notifikasi saat refetch %s: %s", nomor_aduan, e)

    return JSONResponse({
        "success": True,
        "nomor_aduan": nomor_aduan,
        "notification_sent": notif_sent,
        "message": f"Tiket {nomor_aduan} berhasil ditarik ulang dari HTS dan disimpan ke database!",
    })


@app.put("/api/tickets/{nomor_aduan}")
def api_update_ticket(nomor_aduan: str, payload: TicketUpdateRequest):
    db = get_db()
    t_row = db.models.get_ticket(nomor_aduan)
    if not t_row:
        raise HTTPException(status_code=404, detail=f"Tiket {nomor_aduan} tidak ditemukan.")

    # Calculate diff
    req_dict = payload.model_dump(exclude_unset=True)
    if not req_dict:
        return JSONResponse({"success": True, "message": "Tidak ada perubahan data."})

    diff = {}
    updated_dict = dict(t_row)
    for k, v in req_dict.items():
        if v is not None and str(updated_dict.get(k)) != str(v):
            diff[k] = {"old": updated_dict.get(k), "new": v}
            updated_dict[k] = v

    if not diff:
        return JSONResponse({"success": True, "message": "Nilai yang dikirim sama dengan data saat ini."})

    ticket = TicketData(
        nomor_aduan=updated_dict["nomor_aduan"],
        kategori=updated_dict["kategori"],
        sub_kategori=updated_dict["sub_kategori"],
        instansi=updated_dict["instansi"],
        opd_induk=updated_dict["opd_induk"],
        pic_nama=updated_dict["pic_nama"],
        pic_nomor=updated_dict["pic_nomor"],
        keluhan=updated_dict["keluhan"],
        tanggal_aduan=updated_dict["tanggal_aduan"],
        t_solve=updated_dict["t_solve"],
        is_submitted=int(updated_dict["is_submitted"] or 0),
    )

    monitored = ticket.to_monitored_dict()
    monitored_json = json.dumps(monitored, sort_keys=True)
    new_hash = hashlib.sha256(monitored_json.encode("utf-8")).hexdigest()

    with db.transaction() as conn:
        latest_snapshot = db.models.get_latest_snapshot(nomor_aduan, conn=conn)
        prev_snapshot_id = latest_snapshot["id"] if latest_snapshot else None

        db.models.update_ticket(conn, ticket, new_hash=new_hash)
        new_snapshot_id = db.models.insert_snapshot(
            conn,
            ticket_id=t_row["id"],
            nomor_aduan=nomor_aduan,
            data=monitored,
            hash_val=new_hash,
            snapshot_type="manual_edit",
        )
        db.models.insert_event(
            conn=conn,
            ticket_id=t_row["id"],
            nomor_aduan=nomor_aduan,
            event_type="MANUAL_EDIT",
            changed_fields=diff,
            previous_snapshot_id=prev_snapshot_id,
            current_snapshot_id=new_snapshot_id,
        )

    return JSONResponse({
        "success": True,
        "nomor_aduan": nomor_aduan,
        "changed_fields": diff,
        "message": f"Tiket {nomor_aduan} berhasil diperbarui!",
    })


@app.delete("/api/tickets/{nomor_aduan}")
def api_delete_ticket(nomor_aduan: str):
    db = get_db()
    with db.transaction() as conn:
        deleted = db.models.delete_ticket_cascade(nomor_aduan, conn=conn)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Tiket {nomor_aduan} tidak ditemukan.")

    return JSONResponse({
        "success": True,
        "nomor_aduan": nomor_aduan,
        "message": f"Tiket {nomor_aduan} beserta seluruh riwayatnya berhasil dihapus dari database lokal.",
    })


@app.post("/api/rekap/generate")
async def generate_rekap(req: RekapRequest):
    from datetime import datetime
    
    if req.shift not in SHIFTS:
        raise HTTPException(422, detail=f"Shift tidak valid: {req.shift}")
    if not req.nama_petugas.strip():
        raise HTTPException(422, detail="Nama petugas tidak boleh kosong")
    
    tanggal_formatted = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            parsed_dt = datetime.strptime(req.tanggal.strip(), fmt)
            tanggal_formatted = parsed_dt.strftime("%d/%m/%Y")
            break
        except ValueError:
            pass
    if not tanggal_formatted:
        raise HTTPException(422, detail="Format tanggal harus DD/MM/YYYY atau YYYY-MM-DD")

    cfg = get_config()
    session_manager = SessionManager(cfg)
    
    session_valid = False
    if session_manager.load_persisted_cookies():
        session_valid = session_manager.check_session_validity()
    
    if not session_valid:
        # Coba auto-login dengan kredensial .env
        session_valid = session_manager.login()

    if not session_valid:
        return {"success": False, "error": "session_expired",
                "message": "Sesi HTS belum aktif dan gagal auto-login. Pastikan kredensial di .env sudah benar."}

    from app.hts.exceptions import HTSSessionExpiredError, HTSConnectionError
    try:
        rekap_client = HTSRekapClient(
            http_session=session_manager.http,
            base_url=cfg.hts_base_url,
            timeout=15,
        )
        service = RekapService(rekap_client)
        result = service.generate(tanggal_formatted, req.shift, req.nama_petugas.strip())
        return {
            "success": True,
            "rekap_text": result.rekap_text,
            "stats": {
                "total_masuk": result.total_masuk,
                "total_selesai": result.total_selesai,
                "total_belum_selesai": result.total_belum_selesai,
                "categories": [
                    {
                        "name": c.display_name,
                        "masuk": c.masuk,
                        "selesai": c.selesai,
                        "belum_selesai": c.belum_selesai,
                    }
                    for c in result.categories
                ],
            },
            "errors": result.errors,
        }
    except HTSSessionExpiredError:
        return {"success": False, "error": "session_expired",
                "message": "Sesi HTS sudah berakhir. Silakan login manual terlebih dahulu."}
    except HTSConnectionError:
        return {"success": False, "error": "connection_error",
                "message": "HTS tidak dapat diakses. Coba lagi beberapa saat."}
    except Exception as e:
        logger.exception("Error generate rekap: %s", e)
        return {"success": False, "error": "server_error", "message": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Initial Sync (Silent / Manual Sinkronisasi)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/sync/initial")
async def api_initial_sync():
    """Jalankan sinkronisasi awal semua tiket dari HTS ke database lokal TANPA mengirim
    notifikasi Telegram sama sekali. Berguna saat deploy pertama kali agar tidak spam.

    Endpoint ini bersifat sinkron (streaming NDJSON) sehingga client bisa memantau progress.
    """
    from app.monitoring.reconciler import run_initial_sync
    from app.hts.exceptions import HTSSessionExpiredError, HTSConnectionError

    db = get_db()
    cfg = get_config()

    # Validasi session
    session_manager = SessionManager(cfg)
    session_valid = False
    if session_manager.load_persisted_cookies():
        session_valid = session_manager.check_session_validity()
    if not session_valid:
        session_valid = session_manager.login()

    if not session_valid:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": "session_expired",
                "message": "Sesi HTS belum aktif. Pastikan sudah login atau cookie valid.",
            },
        )

    # Jalankan initial sync via streaming response
    def stream_sync():
        import json as _json

        yield _json.dumps({"type": "start", "message": "Memulai sinkronisasi awal dari HTS..."}) + "\n"

        from app.hts.client import create_http_session, HTSClient
        http_session = create_http_session(cfg)

        # Salin cookies dari session_manager ke http_session
        for cookie in session_manager.http.cookies:
            http_session.cookies.set(cookie.name, cookie.value, domain=cookie.domain)

        client = HTSClient(cfg, http_session=http_session)
        processor = TicketProcessor(db=db, notif_queue=None, is_initial_sync=True)

        # Nonaktifkan notifikasi: gunakan dummy queue yang tidak mengirim apa-apa
        class _NullQueue:
            def queue(self, *a, **kw): pass
            def process_pending(self, *a, **kw): pass

        processor.notif_queue = _NullQueue()  # type: ignore[assignment]
        processor.is_initial_sync = True

        count = 0
        try:
            db.models.insert_system_event("INITIAL_SYNC_START", description="Manual sync via dashboard")
            for ticket in client.fetch_all_tickets(status="all"):
                processor.process(ticket, sync_source="initial")
                count += 1
                if count % 50 == 0:
                    yield _json.dumps({"type": "progress", "count": count}) + "\n"

            db.models.insert_system_event(
                "INITIAL_SYNC_COMPLETE",
                description="Manual sync via dashboard selesai",
                metadata={"ticket_count": count},
            )
            yield _json.dumps({
                "type": "done",
                "success": True,
                "count": count,
                "message": f"Sinkronisasi selesai! {count} tiket berhasil disimpan ke database (tanpa notifikasi Telegram).",
            }) + "\n"

        except HTSSessionExpiredError:
            yield _json.dumps({
                "type": "error",
                "error": "session_expired",
                "message": "Sesi HTS kedaluwarsa di tengah sinkronisasi.",
            }) + "\n"
        except HTSConnectionError as e:
            yield _json.dumps({
                "type": "error",
                "error": "connection_error",
                "message": f"Koneksi ke HTS gagal: {e}",
            }) + "\n"
        except Exception as e:
            logger.exception("Error saat manual initial sync: %s", e)
            yield _json.dumps({
                "type": "error",
                "error": "server_error",
                "message": str(e),
            }) + "\n"

    return StreamingResponse(stream_sync(), media_type="application/x-ndjson")


@app.get("/api/sync/status")
def api_sync_status():
    """Cek apakah database lokal masih kosong (perlu sinkronisasi awal) atau sudah ada data."""
    db = get_db()
    total = db.models.count_tickets()
    return JSONResponse({
        "total_tickets": total,
        "needs_initial_sync": total == 0,
        "message": "Database kosong, disarankan lakukan Sinkronisasi Awal." if total == 0
                   else f"Database sudah berisi {total} tiket.",
    })

