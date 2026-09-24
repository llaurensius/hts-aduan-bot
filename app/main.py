"""Application entrypoint wiring all services and running the Orchestrator."""

import logging
import sys
from pathlib import Path

# Ensure project root is in sys.path when executed directly as `python app/main.py`
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.config import load_config
from app.database.db import DatabaseManager
from app.health import HealthState, start_health_server
from app.hts.client import HTSClient, create_http_session
from app.hts.session import SessionManager
from app.monitoring import reconciler
from app.monitoring.orchestrator import Orchestrator
from app.monitoring.ticket_processor import TicketProcessor
from app.notifications.queue import NotificationQueue
from app.notifications.telegram import TelegramNotifier
from app.utils.log_utils import setup_logging


def main() -> int:
    """Initialize configuration, set up logging, and run the Orchestrator."""
    config = load_config()

    # Configure logging with sensitive data masking
    sensitive_patterns = {
        config.hts_password,
        config.telegram_bot_token,
    }
    setup_logging(
        log_level=config.log_level,
        log_file=config.log_file,
        sensitive_patterns=sensitive_patterns,
    )

    logger = logging.getLogger("hts_monitor")
    logger.info("Application starting - HTS Ticket Monitor")
    logger.info(
        "Configuration loaded: HTS URL=%s, poll interval=%ds",
        config.hts_base_url,
        config.poll_interval,
    )

    # 1. Database
    db = DatabaseManager(config.db_path)
    db.connect()

    # 2. HTTP Session & HTS Client
    http_session = create_http_session(config)
    hts_client = HTSClient(config, http_session=http_session)

    # 3. Telegram Notifier & Queue
    notifier = TelegramNotifier(config)
    notification_queue = NotificationQueue(db, config=config)

    # 4. Session Manager
    session_mgr = SessionManager(
        config=config,
        http_session=http_session,
        db=db,
        notifier=notifier,
    )

    # 5. Ticket Processor
    ticket_processor = TicketProcessor(
        db=db,
        notif_queue=notification_queue,
        is_initial_sync=False,
    )

    # 6. Health State & Server Daemon
    health_state = HealthState()
    health_thread = start_health_server(config, health_state)

    # 7. Orchestrator
    orchestrator = Orchestrator(
        config=config,
        db=db,
        hts_client=hts_client,
        session_mgr=session_mgr,
        ticket_processor=ticket_processor,
        notification_queue=notification_queue,
        notifier=notifier,
        reconciler=reconciler,
        health_state=health_state,
    )

    try:
        orchestrator.run()
    except KeyboardInterrupt:
        logger.info("Application interrupted by user.")
        orchestrator.stop()
    finally:
        if hasattr(health_thread, "server"):
            health_thread.server.shutdown()
            health_thread.server.server_close()
        db.close()
        logger.info("Application shutdown complete.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
