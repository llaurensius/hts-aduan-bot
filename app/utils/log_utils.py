"""Logging configuration and sensitive data sanitizer filter."""

import logging
import os
from pathlib import Path
from typing import Optional, Set


class SensitiveDataFilter(logging.Filter):
    """Logging filter that scrubs sensitive strings from log messages.

    Protects secrets such as passwords, bot tokens, and session cookies
    from being emitted into stdout or log files.
    """

    def __init__(self, sensitive_patterns: Optional[Set[str]] = None) -> None:
        super().__init__()
        # Filter out empty or whitespace-only patterns to avoid replacing everything
        self._patterns = {
            p.strip() for p in (sensitive_patterns or set()) if p and p.strip()
        }

    def add_pattern(self, pattern: str) -> None:
        """Add a pattern to be scrubbed from logs."""
        if pattern and pattern.strip():
            self._patterns.add(pattern.strip())

    def filter(self, record: logging.LogRecord) -> bool:
        """Scrub sensitive patterns from record message."""
        if not self._patterns:
            return True

        if isinstance(record.msg, str):
            for pattern in self._patterns:
                if pattern in record.msg:
                    record.msg = record.msg.replace(pattern, "[REDACTED]")

        # Also inspect and sanitize arguments if msg formatting has args
        if record.args:
            if isinstance(record.args, dict):
                sanitized_args = {}
                for k, v in record.args.items():
                    if isinstance(v, str):
                        for pattern in self._patterns:
                            v = v.replace(pattern, "[REDACTED]")
                    sanitized_args[k] = v
                record.args = sanitized_args
            elif isinstance(record.args, (list, tuple)):
                sanitized_list = []
                for item in record.args:
                    if isinstance(item, str):
                        for pattern in self._patterns:
                            item = item.replace(pattern, "[REDACTED]")
                    sanitized_list.append(item)
                record.args = tuple(sanitized_list) if isinstance(record.args, tuple) else sanitized_list

        return True


def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = "logs/hts_monitor.log",
    sensitive_patterns: Optional[Set[str]] = None,
) -> logging.Logger:
    """Configure root logger with console and file handlers, applying SensitiveDataFilter.

    Args:
        log_level: Logging level string ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL').
        log_file: Path to destination log file. If None, only stream handler is configured.
        sensitive_patterns: Set of secret substrings to redact in all logs.

    Returns:
        logging.Logger: The configured root logger.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers to prevent duplicate outputs on reconfiguration
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sensitive_filter = SensitiveDataFilter(sensitive_patterns)

    # Console Handler (Stream to stdout)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(sensitive_filter)
    root_logger.addHandler(console_handler)

    # File Handler
    if log_file:
        log_path = Path(log_file)
        # Ensure log directory exists
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(sensitive_filter)
        root_logger.addHandler(file_handler)

    return root_logger
