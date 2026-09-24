# Developer Summary & Technical Context

This document provides a comprehensive technical overview of the **HTS Ticket Monitor** project. It is designed to help new developers or maintainers quickly understand the system's architecture, technology stack, components, and current development state, ensuring seamless future feature development.

---

## 1. Project Overview
The **HTS Ticket Monitor** is an automated monitoring daemon built for the Helpdesk Ticketing System (HTS) of the Central Java Provincial Government. Its primary purpose is to poll the HTS web portal, detect new tickets or changes in ticket statuses, and deliver reliable, real-time alerts to a Telegram group/channel. It also features a web-based dashboard for operational oversight and automated report generation (Rekap).

**Key Capabilities:**
- Reliable, at-least-once delivery of notifications.
- Resilience against network failures, CAPTCHA challenges, and Telegram rate limits.
- A local web dashboard for monitoring statistics and generating daily shift reports across various ticket categories (Aduan, Kunjungan, Permohonan Layanan, VPS/Domain, Rekomtek).

---

## 2. Technology Stack
- **Core Language:** Python 3.12+
- **Database:** SQLite3 (running in WAL mode for concurrent read/write operations).
- **Web/Dashboard:** FastAPI, Uvicorn, Jinja2 (HTML/CSS/JS frontend).
- **HTTP & Parsing:** `requests`, `BeautifulSoup4`, `lxml` (for interacting with and scraping the legacy PHP-based HTS system).
- **Testing:** `pytest`, `pytest-cov`, `responses`, `pytest-mock` (100% test passing rate across 179 unit & integration tests).
- **Process Management:** PM2 (Node.js ecosystem) for daemonizing the application in production.

---

## 3. Core Architecture & Components

The system is highly modularized, separated into distinct domains:

### 3.1. HTS Client & Session Management (`app/hts/`)
- **`session.py` & `import_cookies.py`:** HTS is protected by dynamic image CAPTCHAs. The bot enforces a **Zero CAPTCHA Bypass** policy. When a session expires, the bot halts polling and alerts the Telegram channel. Operators manually log in via a browser, solve the CAPTCHA, and import the session cookies using `scripts/import_cookies.py`. The bot automatically detects the new cookies and resumes operations.
- **`client.py` & `rekap_client.py`:** Handles paginated fetching of tickets using HTTP POST requests. `rekap_client.py` extends this to fetch data across 5 different operational categories for reporting purposes.
- **`parser.py`:** Parses raw HTML responses, extracts CSRF tokens, and normalizes scraped HTML table data into strongly typed `TicketData` dataclasses.

### 3.2. Monitoring Engine (`app/monitoring/`)
- **`orchestrator.py`:** The main state machine loop. Handles polling intervals, error recovery, and delegates tasks to the Reconciler and Ticket Processor.
- **`change_detector.py`:** Uses SHA256 hashing to compare the state of a ticket against its last known snapshot in the database. This ensures precise field-level diffs without false positive alerts.
- **`reconciler.py`:** Ensures zero dropped tickets. Runs on startup or after recovering from downtime/session expiry to catch up on any tickets missed while the bot was offline.
- **`rekap_service.py`:** Aggregates ticket data across multiple categories based on specific time windows (Pagi/Siang/Malam shifts) to generate formatted text reports.

### 3.3. Notification Queue (`app/notifications/`)
- **`queue.py`:** A robust, local SQLite-backed queue ensuring guaranteed delivery. Features exponential backoff (`[15s, 60s, 300s, 1800s]`) to elegantly handle Telegram API rate limits (HTTP 429).
- **`templates.py`:** Contains formatting logic for Telegram messages (New Ticket, Status Changed, Completed, HTS Down, Session Expired).

### 3.4. Dashboard & API (`app/dashboard/`)
- **`server.py`:** A FastAPI application serving Jinja2 templates (`http://127.0.0.1:8888`).
- **Features:** 
  - Real-time statistics (total, pending, completed tickets).
  - Activity feed / Event history.
  - Ticket management table.
  - **Generate Rekap Form:** Queries live HTS data and local database timestamps to compile end-of-shift reports.

### 3.5. Health & Utilities
- **`app/health/endpoint.py`:** Runs an internal HTTP server (port 8080) exposing operational health metrics for monitoring tools.
- **`app/utils/log_utils.py`:** Automatically sanitizes and masks sensitive credentials (like passwords and Telegram tokens) from all application logs.

---

## 4. Current Development State

- **Core Features (100% Complete):**
  - Polling, parsing, state tracking, and reconciliation are fully operational.
  - The notification queue and Telegram integration are stable and tested.
  - SQLite database schema and hot-backup scripts (`backup/backup.py`) are fully implemented.
- **Dashboard & Reporting (Complete):**
  - The local FastAPI dashboard is functioning.
  - The `RekapService` successfully aggregates data across 5 HTS categories.
- **Test Suite (100% Passing):**
  - 179 automated tests (unit and integration) ensure stability. Mock HTTP responses are used heavily to prevent network dependencies during testing.

---

## 5. Getting Started for Developers

### Initial Setup
1. Ensure Python 3.12+ and `pm2` are installed.
2. Clone the repository and create a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt -r requirements-dev.txt
   ```
3. Copy `.env.example` to `.env` and configure your Telegram bot tokens and HTS credentials.

### Running the Application Locally
- **Run the Core Monitor:** 
  ```bash
  python3 -m app.main
  ```
- **Run the Dashboard:**
  ```bash
  python3 scripts/run_dashboard.py
  ```
- **Import New Session Cookies (when CAPTCHA is required):**
  ```bash
  python3 scripts/import_cookies.py
  ```

### Running Tests
To ensure your new changes do not break existing functionality, always run the test suite:
```bash
pytest --cov=app --cov-report=term-missing tests/
```

### Production Deployment
The application relies on PM2 to keep the processes alive.
```bash
pm2 start ecosystem.config.js
pm2 save
```

---

## 6. Future Expansion Ideas
When continuing development, consider the following potential enhancements:
1. **Interactive Telegram Bot:** Add command handling (e.g., `/status`, `/rekap`) or inline buttons directly in Telegram to control the bot without opening the web dashboard.
2. **Dashboard Analytics:** Implement graphical charts (Chart.js/ApexCharts) in the dashboard to visualize ticket trends over time.
3. **Containerization:** Create a `Dockerfile` and `docker-compose.yml` for environments that prefer Docker over PM2.
4. **Enhanced Filtering:** Add advanced search and filter capabilities to the `/tickets` page in the web dashboard.
