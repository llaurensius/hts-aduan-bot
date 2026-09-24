"""Tests for FastAPI dashboard routes and endpoints."""

import pytest
from fastapi.testclient import TestClient
from app.dashboard.server import app, get_db
from app.database.db import DatabaseManager
from app.hts.parser import TicketData


@pytest.fixture
def test_db(tmp_path):
    """Create isolated SQLite database for dashboard tests."""
    db_path = str(tmp_path / "test_dashboard.db")
    db = DatabaseManager(db_path)
    db.connect()
    return db


@pytest.fixture
def client(test_db, monkeypatch):
    """Create TestClient with overridden database."""
    from app.dashboard import server
    monkeypatch.setattr(server, "get_db", lambda: test_db)
    monkeypatch.setattr(server, "_db", test_db)

    # Insert sample ticket for testing
    ticket = TicketData(
        nomor_aduan="1001-TShoot-2026-test-01",
        kategori="jaringan",
        sub_kategori="koneksi",
        instansi="DINAS KOMINFO",
        opd_induk="",
        pic_nama="Budi",
        pic_nomor="081234567890",
        keluhan="Internet lambat",
        tanggal_aduan="2026-09-18 10:00:00",
        t_solve="0",
        is_submitted=1,
    )
    with test_db.transaction() as conn:
        test_db.models.insert_ticket(conn, ticket, sync_source="test")

    return TestClient(app)


def test_root_redirect(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"


def test_dashboard_page(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Dashboard Overview" in response.text
    assert "Total Tiket" in response.text


def test_tickets_page(client):
    response = client.get("/tickets")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Daftar Tiket Aduan" in response.text
    assert "1001-TShoot-2026-test-01" in response.text


def test_api_stats(client):
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert "stats" in data
    assert "bot_health" in data
    assert data["stats"]["total_tickets"] >= 1
    assert data["stats"]["pending_tickets"] >= 1


def test_api_tickets_paginated(client):
    response = client.get("/api/tickets?page=1&limit=10")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert data["total"] >= 1
    assert data["items"][0]["nomor_aduan"] == "1001-TShoot-2026-test-01"


def test_api_ticket_details(client):
    response = client.get("/api/tickets/1001-TShoot-2026-test-01")
    assert response.status_code == 200
    data = response.json()
    assert data["nomor_aduan"] == "1001-TShoot-2026-test-01"
    assert data["pic_nama"] == "Budi"
    assert "snapshots" in data
    assert "events" in data
    assert "notifications" in data


def test_api_ticket_details_not_found(client):
    response = client.get("/api/tickets/non-existent-ticket")
    assert response.status_code == 404


def test_api_update_ticket(client):
    update_payload = {
        "pic_nama": "Budi Santoso",
        "keluhan": "Internet sudah lancar kembali",
    }
    response = client.put("/api/tickets/1001-TShoot-2026-test-01", json=update_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True

    # Verify changes
    detail_res = client.get("/api/tickets/1001-TShoot-2026-test-01")
    detail_data = detail_res.json()
    assert detail_data["pic_nama"] == "Budi Santoso"
    assert detail_data["keluhan"] == "Internet sudah lancar kembali"
    # Verify event was recorded
    assert len(detail_data["events"]) >= 1
    assert detail_data["events"][0]["event_type"] == "MANUAL_EDIT"


def test_api_delete_ticket(client):
    response = client.delete("/api/tickets/1001-TShoot-2026-test-01")
    assert response.status_code == 200
    assert response.json()["success"] is True

    # Verify ticket is gone
    get_res = client.get("/api/tickets/1001-TShoot-2026-test-01")
    assert get_res.status_code == 404


def test_api_resend_completed_ticket(client, test_db, monkeypatch):
    """Test that resending a completed ticket uses TEMPLATE_COMPLETED with explanation and footer."""
    from app.dashboard import server

    completed_ticket = TicketData(
        nomor_aduan="2000-TShoot-2026-test-02",
        kategori="troubleshoot",
        sub_kategori="SERVER",
        instansi="Dinas Kesehatan",
        opd_induk="Setda",
        pic_nama="Dr. Agus",
        pic_nomor="08111222333",
        keluhan="Aplikasi error 500",
        tanggal_aduan="2026-09-22 10:00:00",
        t_solve="1726527600",
        is_submitted=1,
        penjelasan="Service Apache di-restart dan database pool dibersihkan.",
    )
    with test_db.transaction() as conn:
        test_db.models.insert_ticket(conn, completed_ticket, sync_source="test")

    captured_messages = []

    class MockNotifier:
        def send(self, msg):
            captured_messages.append(msg)
            return {"result": {"message_id": 9999}}

    monkeypatch.setattr(server, "get_notifier", lambda: MockNotifier())

    response = client.post("/api/tickets/2000-TShoot-2026-test-02/resend")
    assert response.status_code == 200
    assert response.json()["success"] is True

    assert len(captured_messages) == 1
    sent_msg = captured_messages[0]
    assert "Menginformasikan Tiket Selesai:" in sent_msg
    assert "Nomor Aduan:\n2000-TShoot-2026-test-02" in sent_msg
    assert "Penjelasan Penanganan:\nService Apache di-restart dan database pool dibersihkan." in sent_msg
    assert "Status:\nSudah Ditangani" in sent_msg
    assert "Terima kasih atas perhatian dan kerjasamanya." in sent_msg
    assert "Laurensius Liquori" in sent_msg
    assert "Helpdesk DC" in sent_msg

    # Resend with custom signature payload
    captured_messages.clear()
    res_custom = client.post(
        "/api/tickets/2000-TShoot-2026-test-02/resend",
        json={"petugas_nama": "Rizki Pratama", "petugas_role": "Network Engineer"},
    )
    assert res_custom.status_code == 200
    assert len(captured_messages) == 1
    assert "Rizki Pratama" in captured_messages[0]
    assert "Network Engineer" in captured_messages[0]


def test_api_tickets_sorting(client, test_db):
    """Test API ticket sorting across multiple columns and directions."""
    t1 = TicketData(
        nomor_aduan="1002-TShoot-2026-alpha",
        kategori="aplikasi",
        sub_kategori="-",
        instansi="A Badan Arsip",
        opd_induk="",
        pic_nama="Ahmad",
        pic_nomor="0811111111",
        keluhan="Error login",
        tanggal_aduan="2026-09-10 08:00:00",
        t_solve="1",
        is_submitted=1,
    )
    t2 = TicketData(
        nomor_aduan="1003-TShoot-2026-zeta",
        kategori="server",
        sub_kategori="-",
        instansi="Z Dinas Sosial",
        opd_induk="",
        pic_nama="Zul",
        pic_nomor="0899999999",
        keluhan="Server down",
        tanggal_aduan="2026-09-25 15:00:00",
        t_solve="0",
        is_submitted=1,
    )
    with test_db.transaction() as conn:
        test_db.models.insert_ticket(conn, t1, sync_source="test")
        test_db.models.insert_ticket(conn, t2, sync_source="test")

    # Sort by instansi ASC
    res_asc = client.get("/api/tickets?sort=instansi&order=asc")
    assert res_asc.status_code == 200
    items = res_asc.json()["items"]
    instansis = [item["instansi"] for item in items]
    assert instansis[0] == "A Badan Arsip"

    # Sort by instansi DESC
    res_desc = client.get("/api/tickets?sort=instansi&order=desc")
    assert res_desc.status_code == 200
    items_desc = res_desc.json()["items"]
    instansis_desc = [item["instansi"] for item in items_desc]
    assert instansis_desc[0] == "Z Dinas Sosial"

    # Sort by status ASC (pending first, 0 before 1)
    res_stat_asc = client.get("/api/tickets?sort=status&order=asc")
    assert res_stat_asc.status_code == 200
    items_stat = res_stat_asc.json()["items"]
    assert items_stat[0]["is_completed"] == 0

    # Sort by tanggal_aduan DESC (newest first)
    res_date_desc = client.get("/api/tickets?sort=tanggal_aduan&order=desc")
    assert res_date_desc.status_code == 200
    assert res_date_desc.json()["items"][0]["nomor_aduan"] == "1003-TShoot-2026-zeta"


def test_tickets_page_with_sorting(client):
    """Test HTML tickets page includes sort query params and header macro."""
    response = client.get("/tickets?sort=instansi&order=asc")
    assert response.status_code == 200
    html = response.text
    assert 'sort=instansi&order=desc' in html
    assert 'Urutkan Instansi / OPD' in html
    assert 'class="th-sortable active"' in html

