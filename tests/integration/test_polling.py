"""Integration tests for the Polling Engine and Orchestrator."""

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
    db_file = tmp_path / "test_polling.db"
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
    return config


@pytest.fixture
def orchestrator(test_db, mock_config):
    """Orchestrator instance with mocked dependencies."""
    hts_client = MagicMock()
    session_mgr = MagicMock()
    ticket_processor = MagicMock()
    notification_queue = MagicMock()
    notifier = MagicMock()
    health_state = {}

    session_mgr.check_session_validity.return_value = True
    session_mgr.wait_for_manual_login.return_value = True
    hts_client.fetch_active_tickets.return_value = []

    return Orchestrator(
        config=mock_config,
        db=test_db,
        hts_client=hts_client,
        session_mgr=session_mgr,
        ticket_processor=ticket_processor,
        notification_queue=notification_queue,
        notifier=notifier,
        reconciler=None,
        health_state=health_state,
    )


def test_single_cycle_completes(orchestrator):
    """Mock dependencies and verify that a single polling cycle completes cleanly."""
    ticket_data = {"nomor_aduan": "HTS-001", "keluhan": "Gangguan jaringan"}
    orchestrator.hts_client.fetch_active_tickets.return_value = [ticket_data]

    count = orchestrator._run_one_cycle()

    assert count == 1
    orchestrator.session_mgr.check_session_validity.assert_called_once()
    orchestrator.hts_client.fetch_active_tickets.assert_called_once()
    orchestrator.ticket_processor.process.assert_called_once_with(ticket_data)
    orchestrator.notification_queue.process_pending.assert_called_once_with(orchestrator.notifier)
    assert orchestrator.health_state.get("hts_state") == "ok"


def test_run_one_cycle_session_expired(orchestrator):
    """Verify that when session check fails, manual login is requested and fetch is bypassed."""
    orchestrator.session_mgr.check_session_validity.return_value = False

    count = orchestrator._run_one_cycle()

    assert count == 0
    orchestrator.session_mgr.wait_for_manual_login.assert_called_once()
    orchestrator.hts_client.fetch_active_tickets.assert_not_called()
    orchestrator.ticket_processor.process.assert_not_called()


def test_session_expired_in_cycle(orchestrator):
    """Verify that HTSSessionExpiredError during fetch triggers wait_for_manual_login."""
    orchestrator.hts_client.fetch_active_tickets.side_effect = HTSSessionExpiredError("Session timed out")

    count = orchestrator._run_one_cycle()

    assert count == 0
    orchestrator.session_mgr.wait_for_manual_login.assert_called_once()
    orchestrator.ticket_processor.process.assert_not_called()


def test_no_overlapping_poll(orchestrator):
    """Verify that polling cycles run strictly sequentially without overlapping."""
    orchestrator.config.poll_interval = 0.02
    active_cycles = 0
    max_concurrent = 0
    cycle_count = 0
    cycle_barrier = threading.Event()

    def slow_run_cycle():
        nonlocal active_cycles, max_concurrent, cycle_count
        active_cycles += 1
        max_concurrent = max(max_concurrent, active_cycles)
        # Sleep longer than poll_interval to attempt overlap
        time.sleep(0.05)
        cycle_count += 1
        active_cycles -= 1
        if cycle_count >= 3:
            cycle_barrier.set()
        return 0

    orchestrator._run_one_cycle = slow_run_cycle

    worker = threading.Thread(target=orchestrator._monitoring_loop)
    worker.daemon = True
    worker.start()

    # Wait until at least 3 cycles have run
    cycle_barrier.wait(timeout=2.0)
    orchestrator.stop()
    worker.join(timeout=1.0)

    assert max_concurrent == 1
    assert cycle_count >= 3
    assert not worker.is_alive()


def test_shutdown_during_sleep(orchestrator):
    """Verify that shutdown_event interrupts sleep immediately without waiting for full interval."""
    orchestrator.config.poll_interval = 30.0  # 30 second sleep
    first_cycle_done = threading.Event()

    original_run_cycle = orchestrator._run_one_cycle

    def run_cycle_wrapper():
        res = original_run_cycle()
        first_cycle_done.set()
        return res

    orchestrator._run_one_cycle = run_cycle_wrapper

    start_time = time.monotonic()
    worker = threading.Thread(target=orchestrator._monitoring_loop)
    worker.daemon = True
    worker.start()

    # Wait for the first cycle to finish and enter sleep
    assert first_cycle_done.wait(timeout=2.0)

    # Stop during the 30-second sleep
    orchestrator.stop()
    worker.join(timeout=1.0)
    elapsed = time.monotonic() - start_time

    assert not worker.is_alive()
    # Should exit almost immediately, well below 30 seconds
    assert elapsed < 2.0


def test_hts_unavailable_handling(orchestrator):
    """Verify that HTSConnectionError triggers HTS_UNAVAILABLE state and system alert."""
    error = HTSConnectionError("HTS server unreachable")
    orchestrator.config.retry_initial_delay = 0.01
    orchestrator.config.max_retry_delay = 0.02

    orchestrator._handle_hts_unavailable(error)

    assert orchestrator.app_state == AppState.HTS_UNAVAILABLE.value
    orchestrator.notification_queue.queue_system_alert.assert_called_once()
    assert orchestrator._hts_unavailable_notified is True

    # Second call should not queue duplicate alert
    orchestrator._handle_hts_unavailable(error)
    assert orchestrator.notification_queue.queue_system_alert.call_count == 1
