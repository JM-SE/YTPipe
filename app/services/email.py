from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.settings import Settings


FAKE_EMAIL_MODE = "fake"
RESEND_EMAIL_MODE = "resend"
DISABLED_EMAIL_MODE = "disabled"
PRODUCTION_ENVS = {"production", "prod"}


@dataclass
class EmailNotificationPayload:
    recipient_email: str
    channel_title: str | None
    video_title: str | None
    youtube_video_id: str


class EmailDeliveryAttemptError(Exception):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class EmailDeliveryService:
    def __init__(self, settings: Settings):
        self.mode = settings.email_delivery_mode.strip().lower()
        self.app_env = settings.app_env.strip().lower()
        self.resend_api_key = settings.resend_api_key.strip()
        self.resend_from_email = settings.resend_from_email.strip()
        self._validate_configuration()

    def send_video_notification(self, payload: EmailNotificationPayload) -> None:
        if self.mode in {FAKE_EMAIL_MODE, DISABLED_EMAIL_MODE}:
            return

        subject = f"Nuevo video: {payload.video_title or payload.youtube_video_id}"
        body = (
            f"{payload.channel_title or 'Canal desconocido'} subió un nuevo video:\n\n"
            f"{payload.video_title or payload.youtube_video_id}\n"
            f"https://www.youtube.com/watch?v={payload.youtube_video_id}"
        )

        try:
            response = httpx.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {self.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": self.resend_from_email,
                    "to": [payload.recipient_email],
                    "subject": subject,
                    "text": body,
                },
                timeout=10.0,
            )
        except httpx.TimeoutException as exc:
            raise EmailDeliveryAttemptError("Email delivery timeout.", retryable=True) from exc
        except httpx.TransportError as exc:
            raise EmailDeliveryAttemptError("Email delivery network error.", retryable=True) from exc

        if 200 <= response.status_code < 300:
            return

        error_message = self._extract_error_message(response)
        retryable = self._is_retryable_status(response.status_code)
        raise EmailDeliveryAttemptError(error_message, retryable=retryable)

    def _validate_configuration(self) -> None:
        if self.mode not in {RESEND_EMAIL_MODE, FAKE_EMAIL_MODE, DISABLED_EMAIL_MODE}:
            raise ValueError("EMAIL_DELIVERY_MODE must be 'resend', 'fake', or 'disabled'.")

        if self.mode in {FAKE_EMAIL_MODE, DISABLED_EMAIL_MODE} and self.app_env in PRODUCTION_ENVS:
            raise ValueError("EMAIL_DELIVERY_MODE=fake is not allowed in production environments.")

        if self.mode == RESEND_EMAIL_MODE:
            if not self.resend_api_key:
                raise ValueError("RESEND_API_KEY is required when EMAIL_DELIVERY_MODE=resend.")
            if not self.resend_from_email:
                raise ValueError("RESEND_FROM_EMAIL is required when EMAIL_DELIVERY_MODE=resend.")

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code in {408, 429} or status_code >= 500

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        detail: str | None = None
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if isinstance(payload, dict):
            for key in ("message", "error"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    detail = value
                    break

        if detail is None:
            text = response.text.strip()
            detail = text or "Provider rejected email send request."

        return f"Resend API request failed ({response.status_code}): {detail}"
