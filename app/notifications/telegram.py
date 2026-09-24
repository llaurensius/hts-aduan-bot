"""Telegram Bot API notification client."""

import logging
from typing import Any, Dict, Optional
import requests
from app.config import AppConfig

logger = logging.getLogger(__name__)

MAX_TELEGRAM_MESSAGE_LENGTH = 4096


class TelegramError(Exception):
    """Exception raised when Telegram Bot API returns an error status."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Telegram API error (status={status_code}): {body}")
        self.status_code = status_code
        self.body = body


class TelegramRateLimitError(TelegramError):
    """Exception raised when Telegram responds with HTTP 429 Too Many Requests."""

    def __init__(self, retry_after: int = 60, body: str = "Too Many Requests") -> None:
        super().__init__(status_code=429, body=body)
        self.retry_after = retry_after


class TelegramNotifier:
    """Dispatches outgoing messages to the Telegram Bot API."""

    API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, config: AppConfig, session: Optional[requests.Session] = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self._url = self.API_URL.format(token=config.telegram_bot_token)

    def send(self, text: str) -> Dict[str, Any]:
        """Send a plain text message to the configured TELEGRAM_CHAT_ID.

        Args:
            text: Text content of the message.

        Returns:
            dict: Telegram API response JSON (e.g. {'ok': True, 'result': {'message_id': 123}}).

        Raises:
            TelegramRateLimitError: If HTTP status is 429.
            TelegramError: If HTTP status is non-200.
        """
        # Truncate message if it exceeds Telegram maximum limit
        if len(text) > MAX_TELEGRAM_MESSAGE_LENGTH:
            text = text[: MAX_TELEGRAM_MESSAGE_LENGTH - 50] + "\n...[dipotong]"

        payload = {
            "chat_id": self.config.telegram_chat_id,
            "text": text,
        }

        logger.debug("Sending Telegram message (length=%d, chat_id=%s)", len(text), self.config.telegram_chat_id)

        try:
            resp = self.session.post(
                self._url,
                json=payload,
                timeout=self.config.request_timeout,
            )
        except requests.RequestException as e:
            logger.error("Failed to connect to Telegram Bot API: %s", e)
            raise TelegramError(0, f"Connection error: {e}") from e

        if resp.status_code == 429:
            retry_after = 60
            try:
                # Try header first, then json parameters.retry_after
                header_val = resp.headers.get("Retry-After")
                if header_val:
                    retry_after = int(header_val)
                else:
                    json_data = resp.json()
                    retry_after = int(json_data.get("parameters", {}).get("retry_after", 60))
            except Exception:
                pass
            logger.warning("Telegram rate limit hit. Retry-After: %ds", retry_after)
            raise TelegramRateLimitError(retry_after=retry_after, body=resp.text)

        if not resp.ok:
            logger.error("Telegram Bot API returned HTTP %d: %s", resp.status_code, resp.text)
            raise TelegramError(status_code=resp.status_code, body=resp.text)

        try:
            return resp.json()
        except ValueError:
            return {"ok": True, "result": {}}
