from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    def __init__(self, message: str, retryable: bool, *, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class TelegramDeliveryResult:
    provider_message_id: int | None = None


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
            self._extract_success_message_id(response)
            return

        error_message = self._extract_error_message(response)
        retryable = self._is_retryable_status(response.status_code)
        raise TelegramDeliveryAttemptError(error_message, retryable=retryable)

    def send_message(self, text: str) -> None:
        if not self.enabled:
            return

        self.send_message_to_chat(text, chat_id=self.chat_id)

    def send_message_to_chat(
        self,
        text: str,
        *,
        chat_id: int | str,
        reply_to_message_id: int | None = None,
    ) -> TelegramDeliveryResult:
        if not self.enabled:
            return TelegramDeliveryResult()
        if str(chat_id) != self.chat_id:
            raise TelegramDeliveryAttemptError(
                "Telegram reply destination is not authorized.",
                retryable=False,
            )

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {
                "message_id": reply_to_message_id,
                "allow_sending_without_reply": True,
            }

        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json=payload,
                timeout=10.0,
            )
        except httpx.TimeoutException as exc:
            raise TelegramDeliveryAttemptError("Telegram delivery timeout.", retryable=True) from exc
        except httpx.TransportError as exc:
            raise TelegramDeliveryAttemptError("Telegram delivery network error.", retryable=True) from exc

        if 200 <= response.status_code < 300:
            return TelegramDeliveryResult(provider_message_id=self._extract_success_message_id(response))

        error_message = self._extract_error_message(response)
        retryable = self._is_retryable_status(response.status_code)
        raise TelegramDeliveryAttemptError(
            error_message,
            retryable=retryable,
            retry_after_seconds=self._extract_retry_after(response),
        )

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
        return f"Telegram API request failed ({response.status_code})."

    @staticmethod
    def _extract_retry_after(response: httpx.Response) -> int | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        parameters = payload.get("parameters") if isinstance(payload, dict) else None
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if not isinstance(retry_after, int):
            return None
        return min(max(retry_after, 1), 300)

    @staticmethod
    def _extract_success_message_id(response: httpx.Response) -> int:
        try:
            body = response.json()
        except ValueError as exc:
            raise TelegramDeliveryAttemptError(
                "Telegram returned an invalid success response.",
                retryable=True,
            ) from exc
        result = body.get("result") if isinstance(body, dict) and body.get("ok") is True else None
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if not isinstance(message_id, int):
            raise TelegramDeliveryAttemptError(
                "Telegram returned an invalid success response.",
                retryable=True,
            )
        return message_id
