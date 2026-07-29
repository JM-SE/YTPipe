from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
from typing import Any, Callable

import httpx


logger = logging.getLogger(__name__)

POLL_TIMEOUT_SECONDS = 50
POLL_LIMIT = 100
POLL_READ_TIMEOUT_SECONDS = 65.0
WORKER_INTERVAL_SECONDS = 30.0
WORKER_REQUEST_TIMEOUT_SECONDS = 420.0
BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0)


class TelegramBotError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class LocalAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class ListenerConfig:
    commands_enabled: bool
    bot_token: str
    chat_id: int
    allowed_user_id: int
    bot_username: str
    internal_api_bearer_token: str
    api_base_url: str

    @classmethod
    def from_settings(cls, settings: Any, *, require_command_config: bool = True) -> ListenerConfig:
        try:
            chat_id = int(settings.telegram_chat_id)
        except (TypeError, ValueError) as exc:
            if require_command_config:
                raise ValueError("Telegram listener numeric configuration is invalid.") from exc
            chat_id = 0
        try:
            allowed_user_id = int(settings.telegram_allowed_user_id)
        except (TypeError, ValueError) as exc:
            if require_command_config:
                raise ValueError("Telegram listener numeric configuration is invalid.") from exc
            allowed_user_id = 0

        bot_token = str(settings.telegram_bot_token).strip()
        internal_token = str(settings.internal_api_bearer_token).strip()
        bot_username = str(settings.normalized_telegram_bot_username).strip()
        if require_command_config and not bool(settings.telegram_notifications_enabled):
            raise ValueError("TELEGRAM_NOTIFICATIONS_ENABLED must be true for the Telegram listener.")
        if not bot_token or not bot_username:
            raise ValueError("Telegram listener credentials and bot username are required.")
        if require_command_config and (not internal_token or internal_token == "replace-me-internal"):
            raise ValueError("Telegram listener credentials and bot username are required.")

        app_port = int(settings.app_port)
        if not 1 <= app_port <= 65535:
            raise ValueError("APP_PORT must be a valid TCP port.")

        return cls(
            commands_enabled=bool(settings.telegram_commands_enabled),
            bot_token=bot_token,
            chat_id=chat_id,
            allowed_user_id=allowed_user_id,
            bot_username=bot_username,
            internal_api_bearer_token=internal_token,
            api_base_url=f"http://127.0.0.1:{app_port}",
        )


class TelegramBotClient:
    def __init__(
        self,
        bot_token: str,
        *,
        http_client: Any | None = None,
    ):
        self._bot_token = bot_token
        self._http = http_client or httpx.Client()

    def close(self) -> None:
        close = getattr(self._http, "close", None)
        if close is not None:
            close()

    def get_me(self) -> dict[str, Any]:
        result = self._call("getMe", {}, timeout=10.0)
        if not isinstance(result, dict):
            raise TelegramBotError("Telegram returned an invalid getMe response.", retryable=True)
        return result

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        self._call(
            "deleteWebhook",
            {"drop_pending_updates": drop_pending_updates},
            timeout=10.0,
        )

    def set_my_commands(self) -> None:
        self._call(
            "setMyCommands",
            {"commands": [{"command": "summary", "description": "Resume un video de YouTube"}]},
            timeout=10.0,
        )

    def get_updates(self, *, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "limit": POLL_LIMIT,
            "timeout": POLL_TIMEOUT_SECONDS,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=POLL_READ_TIMEOUT_SECONDS)
        if not isinstance(result, list) or any(not isinstance(update, dict) for update in result):
            raise TelegramBotError("Telegram returned an invalid updates response.", retryable=True)
        return result

    def send_message(self, *, chat_id: int, text: str, reply_to_message_id: int | None = None) -> int:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {
                "message_id": reply_to_message_id,
                "allow_sending_without_reply": True,
            }
        result = self._call("sendMessage", payload, timeout=10.0)
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if not isinstance(message_id, int):
            raise TelegramBotError("Telegram returned an invalid sent-message response.", retryable=True)
        return message_id

    def _call(self, method: str, payload: dict[str, Any], *, timeout: float) -> Any:
        try:
            response = self._http.post(
                f"https://api.telegram.org/bot{self._bot_token}/{method}",
                json=payload,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise TelegramBotError("Telegram request timed out.", retryable=True) from exc
        except httpx.TransportError as exc:
            raise TelegramBotError("Telegram transport failed.", retryable=True) from exc

        retryable_status = response.status_code == 429 or response.status_code >= 500
        if not 200 <= response.status_code < 300:
            raise TelegramBotError(
                f"Telegram API returned HTTP {response.status_code}.",
                retryable=retryable_status,
                retry_after_seconds=self._retry_after(response),
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise TelegramBotError("Telegram returned invalid JSON.", retryable=True) from exc
        if not isinstance(body, dict) or body.get("ok") is not True:
            error_code = body.get("error_code") if isinstance(body, dict) else None
            retryable = isinstance(error_code, int) and (error_code == 429 or error_code >= 500)
            raise TelegramBotError(
                f"Telegram API returned an unsuccessful response ({error_code or 'unknown'}).",
                retryable=retryable,
                retry_after_seconds=self._retry_after_body(body),
            )
        return body.get("result")

    @staticmethod
    def _retry_after(response: Any) -> int | None:
        try:
            body = response.json()
        except ValueError:
            return None
        return TelegramBotClient._retry_after_body(body)

    @staticmethod
    def _retry_after_body(body: Any) -> int | None:
        parameters = body.get("parameters") if isinstance(body, dict) else None
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if not isinstance(retry_after, int):
            return None
        return min(max(retry_after, 1), 300)


class TelegramCommandAPIClient:
    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        *,
        http_client: Any | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._http = http_client or httpx.Client()

    def close(self) -> None:
        close = getattr(self._http, "close", None)
        if close is not None:
            close()

    def submit_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/internal/telegram-commands", payload, timeout=30.0, allow_disabled=True)

    def record_acknowledgment(self, request_id: int, message_id: int) -> dict[str, Any]:
        return self._post(
            f"/internal/telegram-commands/{request_id}/acknowledgment",
            {"acknowledgment_message_id": message_id},
            timeout=10.0,
        )

    def process_next(self) -> dict[str, Any]:
        return self._post(
            "/internal/telegram-commands/process-next",
            {},
            timeout=WORKER_REQUEST_TIMEOUT_SECONDS,
            allow_busy=True,
        )

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout: float,
        allow_disabled: bool = False,
        allow_busy: bool = False,
    ) -> dict[str, Any]:
        try:
            response = self._http.post(
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {self._bearer_token}"},
                json=payload,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise LocalAPIError("Local API request timed out.", retryable=True) from exc
        except httpx.TransportError as exc:
            raise LocalAPIError("Local API transport failed.", retryable=True) from exc

        if response.status_code == 409 and (allow_disabled or allow_busy):
            if allow_disabled:
                raise LocalAPIError("Telegram commands are disabled.", status_code=409, retryable=False)
            raise LocalAPIError("Local worker is busy.", status_code=409, retryable=True)
        if not 200 <= response.status_code < 300:
            raise LocalAPIError(
                f"Local API returned HTTP {response.status_code}.",
                status_code=response.status_code,
                retryable=response.status_code >= 500,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise LocalAPIError("Local API returned invalid JSON.", retryable=True) from exc
        if not isinstance(body, dict):
            raise LocalAPIError("Local API returned an invalid object.", retryable=True)
        return body


class TelegramCommandListener:
    def __init__(
        self,
        config: ListenerConfig,
        bot: TelegramBotClient,
        api: TelegramCommandAPIClient,
        *,
        sleep: Callable[[float], None] | None = None,
    ):
        self.config = config
        self.bot = bot
        self.api = api
        self._sleep = sleep
        self._stop = threading.Event()
        self._wake_worker = threading.Event()
        self._fatal_error: BaseException | None = None

    def stop(self) -> None:
        self._stop.set()
        self._wake_worker.set()

    def validate_bot(self) -> None:
        bot_user = self.bot.get_me()
        username = str(bot_user.get("username", "")).strip().lstrip("@").lower()
        if not username or username != self.config.bot_username:
            raise RuntimeError("Telegram bot username does not match TELEGRAM_BOT_USERNAME.")

    def configure_bot(self, *, drop_pending_updates: bool = False) -> None:
        self.validate_bot()
        self.bot.delete_webhook(drop_pending_updates=drop_pending_updates)
        self.bot.set_my_commands()

    def handle_update(self, update: dict[str, Any]) -> bool:
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            logger.error("Telegram update without a valid update_id; stopping intake.")
            return False

        message = update.get("message")
        if not isinstance(message, dict):
            return True

        payload = self._command_payload(update_id, message)
        if payload is None:
            return True

        token = str(payload["text"]).strip().split(maxsplit=1)[0]
        if not self._is_configured_summary_token(token):
            return True

        try:
            intake = self.api.submit_command(payload)
        except LocalAPIError as exc:
            if exc.status_code == 409:
                logger.info("Telegram commands are disabled; update_id=%s ignored.", update_id)
                return True
            logger.warning("Telegram intake API unavailable; update_id=%s; retryable=%s.", update_id, exc.retryable)
            if not exc.retryable:
                self._fatal_error = exc
                self.stop()
            return False

        if not intake.get("accepted_for_offset"):
            logger.warning("Telegram intake did not accept update_id=%s.", update_id)
            return False

        request_id = intake.get("request_id")
        outcome = intake.get("outcome", "unknown")
        logger.info("Telegram intake outcome=%s request_id=%s.", outcome, request_id)
        self._wake_worker.set()

        if intake.get("acknowledgment_required") and intake.get("acknowledged_at") is None:
            self._acknowledge(intake, payload)
        return True

    def process_update_batch(self, updates: list[dict[str, Any]], offset: int | None) -> int | None:
        next_offset = offset
        for update in sorted(updates, key=lambda item: item.get("update_id", -1)):
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                return next_offset
            if next_offset is not None and update_id < next_offset:
                continue
            if not self.handle_update(update):
                return next_offset
            next_offset = update_id + 1
            logger.info("Telegram update handled through update_id=%s.", update_id)
        return next_offset

    def run(self) -> None:
        if not self.config.commands_enabled:
            logger.warning("TELEGRAM_COMMANDS_ENABLED=false; listener is not starting.")
            return

        self.validate_bot()
        self.bot.delete_webhook(drop_pending_updates=False)
        intake_thread = threading.Thread(target=self._intake_loop, name="telegram-intake")
        worker_thread = threading.Thread(target=self._worker_loop, name="telegram-worker")
        intake_thread.start()
        worker_thread.start()
        try:
            while not self._stop.is_set():
                intake_thread.join(timeout=0.5)
                worker_thread.join(timeout=0.5)
                if not intake_thread.is_alive() or not worker_thread.is_alive():
                    if not self._stop.is_set():
                        self._fatal_error = RuntimeError("Telegram listener loop stopped unexpectedly.")
                        self.stop()
                    break
        finally:
            self.stop()
            join_timeout = max(WORKER_REQUEST_TIMEOUT_SECONDS, POLL_READ_TIMEOUT_SECONDS) + 10.0
            intake_thread.join(timeout=join_timeout)
            worker_thread.join(timeout=join_timeout)
            if intake_thread.is_alive() or worker_thread.is_alive():
                self._fatal_error = RuntimeError("Telegram listener loops did not stop within the shutdown timeout.")
        if self._fatal_error is not None:
            raise self._fatal_error

    def _intake_loop(self) -> None:
        offset: int | None = None
        polling_backoff_index = 0
        handling_backoff_index = 0
        while not self._stop.is_set():
            try:
                updates = self.bot.get_updates(offset=offset)
                polling_backoff_index = 0
            except TelegramBotError as exc:
                if not exc.retryable:
                    logger.error("Non-retryable Telegram polling error; stopping listener.")
                    self._fatal_error = exc
                    self.stop()
                    return
                delay = self._backoff_delay(polling_backoff_index, exc.retry_after_seconds)
                polling_backoff_index = min(polling_backoff_index + 1, len(BACKOFF_SECONDS) - 1)
                logger.warning("Telegram polling retry in %.1fs.", delay)
                self._sleep_or_stop(delay)
                continue

            if not updates:
                continue
            new_offset = self.process_update_batch(updates, offset)
            if new_offset == offset and updates:
                delay = self._backoff_delay(handling_backoff_index)
                handling_backoff_index = min(handling_backoff_index + 1, len(BACKOFF_SECONDS) - 1)
                logger.warning("Telegram intake API retry in %.1fs.", delay)
                self._sleep_or_stop(delay)
            else:
                offset = new_offset
                handling_backoff_index = 0

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            self._wake_worker.wait(timeout=WORKER_INTERVAL_SECONDS)
            self._wake_worker.clear()
            if self._stop.is_set():
                return
            self._drain_worker()

    def _drain_worker(self) -> None:
        backoff_index = 0
        while not self._stop.is_set():
            try:
                result = self.api.process_next()
                backoff_index = 0
            except LocalAPIError as exc:
                if exc.status_code == 409:
                    logger.info("Telegram worker busy; retrying later.")
                    self._sleep_or_stop(1.0)
                    return
                if not exc.retryable:
                    logger.error("Non-retryable Telegram worker API error; stopping listener.")
                    self._fatal_error = exc
                    self.stop()
                    return
                delay = self._backoff_delay(backoff_index)
                backoff_index = min(backoff_index + 1, len(BACKOFF_SECONDS) - 1)
                logger.warning("Telegram worker API retry in %.1fs.", delay)
                self._sleep_or_stop(delay)
                return

            if not result.get("claimed") or not result.get("work_remaining"):
                logger.info("Telegram worker has no due work.")
                return
            logger.info("Telegram worker completed request_id=%s; continuing drain.", result.get("request_id"))

    def _acknowledge(self, intake: dict[str, Any], payload: dict[str, Any]) -> None:
        request_id = intake.get("request_id")
        message_id = payload.get("telegram_message_id")
        text = intake.get("user_message")
        if not isinstance(request_id, int) or not isinstance(message_id, int) or not isinstance(text, str) or not text:
            logger.warning("Telegram intake requested an invalid acknowledgment; request_id=%s.", request_id)
            return

        sent_message_id: int | None = None
        for attempt, delay in enumerate(BACKOFF_SECONDS[:4]):
            try:
                sent_message_id = self.bot.send_message(
                    chat_id=self.config.chat_id,
                    text=text,
                    reply_to_message_id=message_id,
                )
                break
            except TelegramBotError as exc:
                if not exc.retryable or attempt == 3:
                    logger.warning("Telegram acknowledgment delivery failed; request_id=%s.", request_id)
                    return
                self._sleep_or_stop(self._backoff_delay(attempt, exc.retry_after_seconds))
        if sent_message_id is None:
            return

        for attempt, delay in enumerate(BACKOFF_SECONDS[:4]):
            try:
                self.api.record_acknowledgment(request_id, sent_message_id)
                return
            except LocalAPIError as exc:
                if not exc.retryable or attempt == 3:
                    logger.warning("Telegram acknowledgment recording failed; request_id=%s.", request_id)
                    return
                self._sleep_or_stop(delay)

    def _command_payload(self, update_id: int, message: dict[str, Any]) -> dict[str, Any] | None:
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return None
        if chat.get("type") != "private":
            return None
        if chat.get("id") != self.config.chat_id or sender.get("id") != self.config.allowed_user_id:
            return None
        if message.get("sender_chat") is not None or self._is_forwarded(message):
            return None
        text = message.get("text")
        message_id = message.get("message_id")
        if not isinstance(text, str) or not text.strip() or not isinstance(message_id, int):
            return None
        if not text.lstrip().startswith("/"):
            return None
        return {
            "telegram_update_id": update_id,
            "telegram_chat_id": chat["id"],
            "telegram_user_id": sender["id"],
            "telegram_message_id": message_id,
            "telegram_chat_type": "private",
            "telegram_update_type": "message",
            "is_forwarded": False,
            "is_edited": False,
            "sender_chat_id": None,
            "text": text,
        }

    def _is_configured_summary_token(self, token: str) -> bool:
        if token == "/summary":
            return True
        prefix, separator, suffix = token.partition("@")
        return prefix == "/summary" and bool(separator) and suffix.lower() == self.config.bot_username

    @staticmethod
    def _is_forwarded(message: dict[str, Any]) -> bool:
        return any(
            key in message
            for key in ("forward_origin", "forward_from", "forward_from_chat", "forward_sender_name")
        )

    def _sleep_or_stop(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self._sleep is not None:
            if not self._stop.is_set():
                self._sleep(seconds)
            return
        self._stop.wait(timeout=seconds)

    @staticmethod
    def _backoff_delay(index: int, retry_after: int | None = None) -> float:
        base = BACKOFF_SECONDS[min(index, len(BACKOFF_SECONDS) - 1)]
        if retry_after is not None:
            return min(max(float(retry_after), base), BACKOFF_SECONDS[-1])
        return base
