"""Orchestrator and polling engine for HTS Ticket Monitor."""

from datetime import datetime
from enum import Enum
import logging
import signal
import threading
import time
from typing import Any, Optional, Union

from app.config import AppConfig
from app.database.db import DatabaseManager
from app.hts.exceptions import HTSConnectionError, HTSSessionExpiredError
from app.utils.time_utils import utcnow_iso
import requests

logger = logging.getLogger(__name__)


class AppState(str, Enum):
    """Lifecycle and operational states of the application."""
    STARTING = "STARTING"
    INITIAL_SYNC = "INITIAL_SYNC"
    RECONCILING = "RECONCILING"
    MONITORING = "MONITORING"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    HTS_UNAVAILABLE = "HTS_UNAVAILABLE"
    SHUTTING_DOWN = "SHUTTING_DOWN"


class Orchestrator:
    """Coordinates polling loop, session validation, ticket processing, and notifications."""

    def __init__(
        self,
        config: AppConfig,
        db: DatabaseManager,
        hts_client: Any,
        session_mgr: Any,
        ticket_processor: Any,
        notification_queue: Any,
        notifier: Any,
        reconciler: Optional[Any] = None,
        health_state: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.db = db
        self.hts_client = hts_client
        self.session_mgr = session_mgr
        self.ticket_processor = ticket_processor
        self.notification_queue = notification_queue
        self.notifier = notifier
        self.reconciler = reconciler
        self.health_state = health_state

        self.shutdown_event = threading.Event()
        self.app_state: str = AppState.STARTING.value
        self._hts_unavailable_notified: bool = False
        self._is_running: bool = False

        self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        """Register signal handlers for graceful shutdown."""
        try:
            signal.signal(signal.SIGTERM, self._handle_sigterm)
            signal.signal(signal.SIGINT, self._handle_sigterm)
        except (ValueError, AttributeError):
            # In non-main thread or specific environments, signal registration is not allowed
            pass

    def _handle_sigterm(self, signum: int, frame: Any) -> None:
        """Handle termination signal."""
        logger.info("Signal %s received. Initiating graceful shutdown...", signum)
        self.stop()

    def stop(self) -> None:
        """Signal the orchestrator to shutdown gracefully."""
        logger.info("Stopping orchestrator...")
        self._transition(AppState.SHUTTING_DOWN)
        self.shutdown_event.set()

    @property
    def is_running(self) -> bool:
        """Return whether monitoring loop is actively executing."""
        return self._is_running

    def _transition(self, new_state: Union[AppState, str]) -> None:
        """Record application state transition and update health_state."""
        state_str = new_state.value if isinstance(new_state, AppState) else str(new_state)
        logger.info("AppState transition: %s -> %s", self.app_state, state_str)
        self.app_state = state_str
        if self.health_state is not None:
            if hasattr(self.health_state, "app_state"):
                self.health_state.app_state = state_str
            elif isinstance(self.health_state, dict):
                self.health_state["app_state"] = state_str

    def _update_health_on_poll_success(self) -> None:
        """Update health indicators when an HTS poll succeeds."""
        now_iso = utcnow_iso()
        session_st = getattr(self.session_mgr, "state", "AUTHENTICATED")
        session_st_str = session_st.value if hasattr(session_st, "value") else str(session_st)

        if self.health_state is not None:
            if hasattr(self.health_state, "last_poll"):
                self.health_state.last_poll = now_iso
                self.health_state.last_successful_poll = now_iso
                self.health_state.consecutive_failures = 0
                self.health_state.hts_state = "ok"
                self.health_state.session_state = session_st_str
            elif isinstance(self.health_state, dict):
                self.health_state["last_poll"] = now_iso
                self.health_state["last_successful_poll"] = now_iso
                self.health_state["consecutive_failures"] = 0
                self.health_state["hts_state"] = "ok"
                self.health_state["session_state"] = session_st_str
        self._hts_unavailable_notified = False


    def _update_health_db_stats(self) -> None:
        """Update ticket and notification metrics in health_state."""
        if self.health_state is None or self.db is None:
            return
        try:
            total_tickets = self.db.models.count_tickets()
            pending_notifs = self.db.models.count_notifications_by_status("PENDING")
            failed_notifs = self.db.models.count_notifications_by_status("FAILED")

            if hasattr(self.health_state, "total_tickets"):
                self.health_state.total_tickets = total_tickets
                self.health_state.pending_notifications = pending_notifs
                self.health_state.failed_notifications = failed_notifs
            elif isinstance(self.health_state, dict):
                self.health_state["total_tickets"] = total_tickets
                self.health_state["pending_notifications"] = pending_notifs
                self.health_state["failed_notifications"] = failed_notifs
        except Exception as e:
            logger.debug("Failed to update health DB stats: %s", e)

    def _handle_hts_recovery(self) -> None:
        """Handle recovery after HTS was unavailable: send alert and reconcile."""
        if not self._hts_unavailable_notified:
            return
        now_iso = utcnow_iso()
        logger.info("HTS connectivity recovered. Sending recovery notification...")
        try:
            from app.notifications.templates import format_hts_recovered
            msg = format_hts_recovered(now_iso)
            self.notification_queue.queue_system_alert("HTS_RECOVERED", msg)
        except Exception as e:
            logger.warning("Could not queue HTS_RECOVERED alert: %s", e)

        self._hts_unavailable_notified = False

        # Transition to RECONCILING and run post-recovery reconciliation
        if self.reconciler and hasattr(self.reconciler, "run_reconciliation"):
            self._transition(AppState.RECONCILING)
            try:
                self.reconciler.run_reconciliation(self.hts_client, self.db, self.ticket_processor)
            except Exception as e:
                logger.error("Error during post-recovery reconciliation: %s", e)
        else:
            try:
                from app.monitoring.reconciler import run_reconciliation
                self._transition(AppState.RECONCILING)
                run_reconciliation(self.hts_client, self.db, self.ticket_processor)
            except Exception as e:
                logger.error("Error during post-recovery reconciliation: %s", e)

        self._transition(AppState.MONITORING)

    def _handle_session_expired(self) -> None:
        """Handle session expiration by requesting manual login and resuming."""
        logger.warning("HTS session expired or invalid. Waiting for manual login...")
        self._transition(AppState.SESSION_EXPIRED)
        self._transition(AppState.CAPTCHA_REQUIRED)
        if self.health_state is not None:
            if hasattr(self.health_state, "session_state"):
                self.health_state.session_state = "CAPTCHA_REQUIRED"
            elif isinstance(self.health_state, dict):
                self.health_state["session_state"] = "CAPTCHA_REQUIRED"

        if self.db and hasattr(self.db, "models"):

            try:
                self.db.models.insert_system_event(
                    "SESSION_EXPIRED",
                    description="HTS session expired during monitoring",
                    metadata={"timestamp": utcnow_iso()},
                )
            except Exception as e:
                logger.warning("Failed to record SESSION_EXPIRED event: %s", e)

        try:
            recovered = self.session_mgr.wait_for_manual_login(self.shutdown_event)
        except TypeError:
            recovered = self.session_mgr.wait_for_manual_login()

        if not recovered:
            logger.info("Manual login wait aborted or shutdown requested.")
            return

        self._transition(AppState.RECONCILING)
        try:
            if self.reconciler and hasattr(self.reconciler, "run_reconciliation"):
                self.reconciler.run_reconciliation(self.hts_client, self.db, self.ticket_processor)
            else:
                from app.monitoring.reconciler import run_reconciliation
                run_reconciliation(self.hts_client, self.db, self.ticket_processor)
        except Exception as e:
            logger.error("Error during post-session-recovery reconciliation: %s", e)

        self._transition(AppState.MONITORING)

    def _handle_hts_unavailable(self, error: Exception) -> None:
        """Handle HTS connectivity failures with system alert and exponential backoff."""
        self._transition(AppState.HTS_UNAVAILABLE)
        failures = 1
        if self.health_state is not None:
            if hasattr(self.health_state, "consecutive_failures"):
                self.health_state.consecutive_failures += 1
                failures = self.health_state.consecutive_failures
            elif isinstance(self.health_state, dict):
                self.health_state["consecutive_failures"] = (
                    self.health_state.get("consecutive_failures", 0) + 1
                )
                failures = self.health_state["consecutive_failures"]

        if not self._hts_unavailable_notified:
            try:
                from app.notifications.templates import format_hts_down
                msg = format_hts_down(utcnow_iso())
                self.notification_queue.queue_system_alert("HTS_DOWN", msg)
                self._hts_unavailable_notified = True
            except Exception as alert_err:
                logger.warning("Could not queue HTS_DOWN alert: %s", alert_err)

        initial_delay = getattr(self.config, "retry_initial_delay", 5) if self.config else 5
        max_delay = getattr(self.config, "max_retry_delay", 300) if self.config else 300

        delay = min(initial_delay * (2 ** max(0, failures - 1)), max_delay)
        logger.error("HTS unavailable (failure count %d). Retrying in %.2fs. Error: %s", failures, delay, error)
        self.shutdown_event.wait(timeout=delay)

    def _run_one_cycle(self) -> int:
        """Execute one complete polling cycle synchronously.

        Returns:
            int: Total number of tickets fetched in this cycle.
        """
        # 1. Session check
        try:
            is_valid = self.session_mgr.check_session_validity()
        except (HTSConnectionError, requests.RequestException) as net_err:
            logger.warning("Network connection error during session check: %s", net_err)
            self._handle_hts_unavailable(net_err)
            return 0

        if not is_valid:
            if getattr(self.session_mgr, "last_check_was_network_error", None) is True:
                logger.warning("Session validity check failed due to connection error.")
                self._handle_hts_unavailable(Exception("Session check connection failure"))
            else:
                logger.warning("Session validity check failed before fetch (session expired).")
                self._handle_session_expired()
            return 0

        # 2. Fetch active tickets
        try:
            tickets = list(self.hts_client.fetch_active_tickets())
        except HTSSessionExpiredError:
            logger.warning("Session expired while fetching tickets.")
            self._handle_session_expired()
            return 0
        except (HTSConnectionError, requests.RequestException) as net_err:
            logger.warning("Network connection error while fetching tickets: %s", net_err)
            self._handle_hts_unavailable(net_err)
            return 0

        # Check if recovering from previous HTS outage
        if self._hts_unavailable_notified:
            self._handle_hts_recovery()

        self._update_health_on_poll_success()

        # 3. Process every ticket
        for ticket in tickets:
            self.ticket_processor.process(ticket)

        # 4. Process pending notifications in queue
        self.notification_queue.process_pending(self.notifier)

        # 5. Update health DB stats
        self._update_health_db_stats()

        return len(tickets)

    def _monitoring_loop(self) -> None:
        """Main synchronous polling loop with interruptible sleep."""
        poll_interval = self.config.poll_interval if self.config else 60
        logger.info("Starting monitoring loop with poll_interval=%ss", poll_interval)
        self._is_running = True

        try:
            while not self.shutdown_event.is_set():
                cycle_start = time.monotonic()
                ticket_count = 0

                try:
                    ticket_count = self._run_one_cycle()
                except HTSSessionExpiredError:
                    self._handle_session_expired()
                except HTSConnectionError as e:
                    self._handle_hts_unavailable(e)
                except Exception as e:
                    logger.error("Unexpected error in monitoring loop cycle: %s", e, exc_info=True)

                elapsed = time.monotonic() - cycle_start
                logger.info("Cycle completed in %.2fs (fetched %s tickets)", elapsed, ticket_count)

                sleep_time = max(0.0, poll_interval - elapsed)
                if self.shutdown_event.wait(timeout=sleep_time):
                    logger.info("Shutdown signaled during sleep. Exiting loop.")
                    break
        finally:
            self._is_running = False
            logger.info("Monitoring loop stopped.")

    def run(self) -> None:
        """Execute full orchestrator lifecycle:
        STARTING -> INITIAL_SYNC -> RECONCILING -> MONITORING -> SHUTTING_DOWN
        """
        self._transition(AppState.STARTING)
        if self.db and hasattr(self.db, "models"):
            try:
                self.db.models.insert_system_event("APP_START")
            except Exception as e:
                logger.warning("Could not record APP_START event: %s", e)

        if self.shutdown_event.is_set():
            self._transition(AppState.SHUTTING_DOWN)
            return

        # 1. Session initialization
        if hasattr(self.session_mgr, "initialize"):
            logger.info("Initializing HTS session...")
            self.session_mgr.initialize()

        if self.shutdown_event.is_set():
            self._transition(AppState.SHUTTING_DOWN)
            return

        # 2. Initial sync (if configured)
        if getattr(self.config, "initial_sync", False):
            self._transition(AppState.INITIAL_SYNC)
            try:
                from app.monitoring.reconciler import run_initial_sync
                run_initial_sync(self.hts_client, processor=self.ticket_processor, db=self.db)
            except Exception as e:
                logger.error("Error during initial sync: %s", e)

        if self.shutdown_event.is_set():
            self._transition(AppState.SHUTTING_DOWN)
            return

        # 3. Auto-guard: jika DB kosong saat startup DAN initial_sync tidak di-set di config,
        #    lakukan silent initial sync agar tiket lama tidak memicu spam notifikasi.
        #    Jika initial_sync=True di config, biarkan step #2 yang menanganinya.
        already_syncing = getattr(self.config, "initial_sync", False)
        db_is_empty = False
        if not already_syncing and self.db and hasattr(self.db, "models"):
            try:
                db_is_empty = self.db.models.count_tickets() == 0
            except Exception:
                pass

        if db_is_empty:
            logger.info(
                "Database kosong terdeteksi saat startup. "
                "Menjalankan Silent Initial Sync untuk mencegah spam notifikasi..."
            )
            self._transition(AppState.INITIAL_SYNC)
            try:
                from app.monitoring.reconciler import run_initial_sync
                from app.monitoring.ticket_processor import TicketProcessor

                class _NullQueue:
                    def queue(self, *a, **kw): pass
                    def process_pending(self, *a, **kw): pass

                silent_processor = TicketProcessor(
                    db=self.db,
                    notif_queue=_NullQueue(),  # type: ignore[arg-type]
                    is_initial_sync=True,
                )
                count = run_initial_sync(
                    self.hts_client,
                    processor=silent_processor,
                    db=self.db,
                )
                logger.info(
                    "Silent Initial Sync selesai: %d tiket disimpan (tanpa notifikasi Telegram).",
                    count,
                )
            except Exception as e:
                logger.error("Error saat auto silent initial sync: %s", e)

            if self.shutdown_event.is_set():
                self._transition(AppState.SHUTTING_DOWN)
                return

        # 4. Reconciliation (hanya untuk mendeteksi perubahan sejak terakhir aktif)
        self._transition(AppState.RECONCILING)
        try:
            if self.reconciler and hasattr(self.reconciler, "run_reconciliation"):
                self.reconciler.run_reconciliation(self.hts_client, self.db, self.ticket_processor)
            else:
                from app.monitoring.reconciler import run_reconciliation
                run_reconciliation(self.hts_client, self.db, self.ticket_processor)
        except Exception as e:
            logger.error("Error during startup reconciliation: %s", e)

        if self.shutdown_event.is_set():
            self._transition(AppState.SHUTTING_DOWN)
            return


        # 4. Main monitoring loop
        self._transition(AppState.MONITORING)
        try:
            self._monitoring_loop()
        finally:
            self._transition(AppState.SHUTTING_DOWN)
            if hasattr(self.notification_queue, "flush"):
                try:
                    self.notification_queue.flush(self.notifier)
                except Exception as e:
                    logger.warning("Error during final notification flush: %s", e)
            if self.db and hasattr(self.db, "models"):
                try:
                    self.db.models.insert_system_event("APP_STOP")
                except Exception as e:
                    logger.warning("Could not record APP_STOP event: %s", e)
