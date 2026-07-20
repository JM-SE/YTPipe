from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.settings import Settings


@dataclass
class TelegramNotificationPayload:
    channel_title: str | None
    video_title: str | None
    youtube_video_id: str
    summary: str | None = None
    is_short: bool = False


class TelegramDeliveryAttemptError(Exception):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class TelegramDeliveryService:
    def __init__(self, settings: Settings):
        self.enabled = settings.telegram_notifications_enabled
        self.bot_token = settings.telegram_bot_token.strip()
        self.chat_id = settings.telegram_chat_id.strip()
        self._validate_configuration()

    def send_video_notification(self, payload: TelegramNotificationPayload) -> None:
        if not self.enabled:
            return

        channel = payload.channel_title or "Canal desconocido"
        title = payload.video_title or payload.youtube_video_id
        video_url = f"https://www.youtube.com/watch?v={payload.youtube_video_id}"

        short_prefix = "SHORT: " if payload.is_short else ""
        message = f"🎬 {short_prefix}{title}\n{channel}\n{video_url}"

        if payload.summary:
            summary_text = payload.summary
            max_summary_len = 3800 - len(message) - 5
            if len(summary_text) > max_summary_len:
                summary_text = summary_text[:max_summary_len].rsplit(" ", 1)[0]
            message = f"{message}\n\n📝 {summary_text}"

        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                },
                timeout=10.0,
            )
        except httpx.TimeoutException as exc:
            raise TelegramDeliveryAttemptError("Telegram delivery timeout.", retryable=True) from exc
        except httpx.TransportError as exc:
            raise TelegramDeliveryAttemptError("Telegram delivery network error.", retryable=True) from exc

        if 200 <= response.status_code < 300:
            return

        error_message = self._extract_error_message(response)
        retryable = self._is_retryable_status(response.status_code)
        raise TelegramDeliveryAttemptError(error_message, retryable=retryable)

    def send_message(self, text: str) -> None:
        if not self.enabled:
            return

        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                },
                timeout=10.0,
            )
        except httpx.TimeoutException as exc:
            raise TelegramDeliveryAttemptError("Telegram delivery timeout.", retryable=True) from exc
        except httpx.TransportError as exc:
            raise TelegramDeliveryAttemptError("Telegram delivery network error.", retryable=True) from exc

        if 200 <= response.status_code < 300:
            return

        error_message = self._extract_error_message(response)
        retryable = self._is_retryable_status(response.status_code)
        raise TelegramDeliveryAttemptError(error_message, retryable=retryable)

    def _validate_configuration(self) -> None:
        if not self.enabled:
            return

        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required when TELEGRAM_NOTIFICATIONS_ENABLED=true.")
        if not self.chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is required when TELEGRAM_NOTIFICATIONS_ENABLED=true.")

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        detail: str | None = None
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if isinstance(payload, dict):
            value = payload.get("description")
            if isinstance(value, str) and value:
                detail = value

        if detail is None:
            text = response.text.strip()
            detail = text or "Telegram API rejected send request."

        return f"Telegram API request failed ({response.status_code}): {detail}"
