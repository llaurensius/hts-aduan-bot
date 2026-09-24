"""Integration tests for the Orchestrator Full State Machine and Failure Handling (Milestone M13)."""

import threading
import time
from unittest.mock import MagicMock, patch
import pytest

from app.config import AppConfig
from app.database.db import DatabaseManager
from app.hts.exceptions import HTSConnectionError, HTSSessionExpiredError
from app.monitoring.orchestrator import AppState, Orchestrator


@pytest.fixture
def test_db(tmp_path):
    """SQLite database initialized with schema."""
    db_file = tmp_path / "test_orchestrator.db"
    manager = DatabaseManager(str(db_file))
    manager.connect()
    yield manager
    manager.close()


@pytest.fixture
def mock_config():
    """Mock application configuration."""
    config = MagicMock(spec=AppConfig)
    config.poll_interval = 1
    config.session_check_interval = 1
    config.retry_initial_delay = 0.01
    config.max_retry_delay = 0.05
    config.hts_base_url = "https://hts.example.com"
    config.initial_sync = False
    return config


@pytest.fixture
def orchestrator(test_db, mock_config):
    """Orchestrator instance with mocked dependencies."""
    hts_client = MagicMock()
    session_mgr = MagicMock()
    ticket_processor = MagicMock()
    notification_queue = MagicMock()
    notifier = MagicMock()
    reconciler = MagicMock()
    health_state = {}

    session_mgr.check_session_validity.return_value = True
    session_mgr.wait_for_manual_login.return_value = True
    session_mgr.initialize.return_value = True
    hts_client.fetch_active_tickets.return_value = []
    reconciler.run_reconciliation.return_value = {"new": 0, "changed": 0, "unchanged": 0}

    return Orchestrator(
        config=mock_config,
        db=test_db,
        hts_client=hts_client,
        session_mgr=session_mgr,
        ticket_processor=ticket_processor,
        notification_queue=notification_queue,
        notifier=notifier,
        reconciler=reconciler,
        health_state=health_state,
    )


def test_hts_unavailable_sends_one_alert(orchestrator):
    """Verify that multiple consecutive HTS errors send exactly ONE Telegram alert (anti-spam)."""
    error = HTSConnectionError("Connection timed out")
    orchestrator.config.retry_initial_delay = 0.01
    orchestrator.config.max_retry_delay = 0.02

    # 3 consecutive failures
    orchestrator._handle_hts_unavailable(error)
    orchestrator._handle_hts_unavailable(error)
    orchestrator._handle_hts_unavailable(error)

    # Exactly 1 alert queued
    assert orchestrator.notification_queue.queue_system_alert.call_count == 1
    assert orchestrator.notification_queue.queue_system_alert.call_args[0][0] == "HTS_DOWN"
    assert orchestrator._hts_unavailable_notified is True
    assert orchestrator.app_state == AppState.HTS_UNAVAILABLE.value


def test_hts_recovery_sends_recovery_alert(orchestrator):
    """Verify that after HTS is unavailable, a successful cycle triggers recovery alert and reconciliation."""
    orchestrator._hts_unavailable_notified = True
    orchestrator.hts_client.fetch_active_tickets.return_value = []

    count = orchestrator._run_one_cycle()

    assert count == 0
    # Recovery alert sent
    assert orchestrator.notification_queue.queue_system_alert.call_count == 1
    assert orchestrator.notification_queue.queue_system_alert.call_args[0][0] == "HTS_RECOVERED"
    assert orchestrator._hts_unavailable_notified is False

    # Reconciliation was triggered
    orchestrator.reconciler.run_reconciliation.assert_called_once_with(
        orchestrator.hts_client, orchestrator.db, orchestrator.ticket_processor
    )
    assert orchestrator.app_state == AppState.MONITORING.value


def test_session_expired_sends_captcha_alert(orchestrator):
    """Verify session expiration transitions states, logs system event, and waits for manual login."""
    orchestrator._handle_session_expired()

    # Verify session manager waited for manual login
    orchestrator.session_mgr.wait_for_manual_login.assert_called_once()

    # Verify system event SESSION_EXPIRED was recorded
    cursor = orchestrator.db.connection.execute(
        "SELECT event_type FROM system_events WHERE event_type = 'SESSION_EXPIRED';"
    )
    assert cursor.fetchone() is not None

    # Verify post-session-recovery reconciliation ran
    orchestrator.reconciler.run_reconciliation.assert_called_once()
    assert orchestrator.app_state == AppState.MONITORING.value


def test_state_transitions(orchestrator, caplog):
    """Verify state transitions update app_state, health_state, and emit log entries."""
    caplog.set_level("INFO")

    transitions = [
        AppState.STARTING,
        AppState.INITIAL_SYNC,
        AppState.RECONCILING,
        AppState.MONITORING,
        AppState.SESSION_EXPIRED,
        AppState.CAPTCHA_REQUIRED,
        AppState.HTS_UNAVAILABLE,
        AppState.SHUTTING_DOWN,
    ]

    for state in transitions:
        orchestrator._transition(state)
        assert orchestrator.app_state == state.value
        assert orchestrator.health_state["app_state"] == state.value
        assert f"AppState transition: " in caplog.text


def test_shutdown_during_hts_unavailable(orchestrator):
    """Verify that a shutdown signal during exponential backoff interrupts sleep immediately."""
    orchestrator.config.retry_initial_delay = 30.0  # Long 30s delay
    error = HTSConnectionError("HTS down")

    start_time = time.monotonic()
    worker = threading.Thread(target=orchestrator._handle_hts_unavailable, args=(error,))
    worker.daemon = True
    worker.start()

    time.sleep(0.05)
    orchestrator.stop()
    worker.join(timeout=1.0)
    elapsed = time.monotonic() - start_time

    assert not worker.is_alive()
    assert elapsed < 1.0


def test_full_run_lifecycle(orchestrator):
    """Verify run() executes the full lifecycle sequence from STARTING to SHUTTING_DOWN."""
    orchestrator.config.initial_sync = True

    # Stop orchestrator before loop blocks
    def stop_during_monitoring():
        orchestrator.stop()

    orchestrator._monitoring_loop = MagicMock(side_effect=stop_during_monitoring)

    with patch("app.monitoring.reconciler.run_initial_sync") as mock_init_sync:
        orchestrator.run()

        # Session was initialized
        orchestrator.session_mgr.initialize.assert_called_once()

        # Initial sync was executed
        mock_init_sync.assert_called_once()

        # Reconciliation was executed
        orchestrator.reconciler.run_reconciliation.assert_called_once()

        # Notification queue was flushed on shutdown
        orchestrator.notification_queue.flush.assert_called_once_with(orchestrator.notifier)

        # APP_START and APP_STOP events recorded
        cursor = orchestrator.db.connection.execute(
            "SELECT event_type FROM system_events ORDER BY id ASC;"
        )
        event_types = [r[0] for r in cursor.fetchall()]
        assert "APP_START" in event_types
        assert "APP_STOP" in event_types
        assert orchestrator.app_state == AppState.SHUTTING_DOWN.value


def test_orchestrator_sigterm_and_is_running(orchestrator):
    """Test sigterm handler sets shutdown event and is_running reflects loop state."""
    assert orchestrator.is_running is False
    orchestrator._handle_sigterm(15, None)
    assert orchestrator.shutdown_event.is_set()
    assert orchestrator.app_state == AppState.SHUTTING_DOWN.value


def test_orchestrator_health_stats_update(orchestrator):
    """Test health state update for dataclass-like or dict objects."""
    from app.health.endpoint import HealthState

    health = HealthState()
    orchestrator.health_state = health

    orchestrator._update_health_on_poll_success()
    assert health.hts_state == "ok"
    assert health.consecutive_failures == 0
    assert health.last_successful_poll is not None

    orchestrator._update_health_db_stats()
    assert health.total_tickets == 0
    assert health.pending_notifications == 0


def test_monitoring_loop_handles_cycle_exceptions(orchestrator):
    """Test _monitoring_loop handles HTSSessionExpired, HTSConnectionError, and generic Exception."""
    call_count = 0

    def mock_cycle():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise HTSSessionExpiredError("Session expired")
        elif call_count == 2:
            raise HTSConnectionError("HTS connection down")
        elif call_count == 3:
            raise RuntimeError("Unexpected boom")
        else:
            orchestrator.shutdown_event.set()
            return 0

    orchestrator._run_one_cycle = mock_cycle
    orchestrator._handle_session_expired = MagicMock()
    orchestrator._handle_hts_unavailable = MagicMock()

    orchestrator._monitoring_loop()

    assert orchestrator._handle_session_expired.call_count == 1
    assert orchestrator._handle_hts_unavailable.call_count == 1
    assert call_count >= 3


def test_orchestrator_run_shutdown_prior_to_loop(orchestrator):
    """Test run() halts immediately if shutdown_event is set at startup or after session init."""
    orchestrator.shutdown_event.set()
    orchestrator.run()
    assert orchestrator.app_state == AppState.SHUTTING_DOWN.value

