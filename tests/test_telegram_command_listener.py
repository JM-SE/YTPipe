from pathlib import Path

import httpx
import pytest

from app.core.settings import Settings
from app.services.telegram_command_listener import (
    ListenerConfig,
    LocalAPIError,
    TelegramBotClient,
    TelegramBotError,
    TelegramCommandListener,
)


def listener_config(**overrides: object) -> ListenerConfig:
    values: dict[str, object] = {
        "commands_enabled": True,
        "bot_token": "bot-secret",
        "chat_id": 7729262607,
        "allowed_user_id": 7729262607,
        "bot_username": "ytpipe_sender_bot",
        "internal_api_bearer_token": "internal-secret",
        "api_base_url": "http://127.0.0.1:8000",
    }
    values.update(overrides)
    return ListenerConfig(**values)


def telegram_update(
    update_id: int = 100,
    *,
    text: str = "/summary https://youtu.be/dQw4w9WgXcQ",
    **message_overrides: object,
) -> dict[str, object]:
    message: dict[str, object] = {
        "message_id": 200,
        "text": text,
        "chat": {"id": 7729262607, "type": "private"},
        "from": {"id": 7729262607},
    }
    message.update(message_overrides)
    return {"update_id": update_id, "message": message}


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def send_message(self, *, chat_id: int, text: str, reply_to_message_id: int | None = None) -> int:
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        return 900 + len(self.sent)


class FakeAPI:
    def __init__(self, responses: list[dict[str, object]] | None = None) -> None:
        self.responses = responses or [
            {
                "request_id": 1,
                "accepted_for_offset": True,
                "acknowledgment_required": True,
                "acknowledged_at": None,
                "user_message": "Recibido.",
                "outcome": "accepted",
            }
        ]
        self.submitted: list[dict[str, object]] = []
        self.acknowledgments: list[tuple[int, int]] = []
        self.process_results: list[dict[str, object]] = []

    def submit_command(self, payload: dict[str, object]) -> dict[str, object]:
        self.submitted.append(payload)
        return self.responses[min(len(self.submitted) - 1, len(self.responses) - 1)]

    def record_acknowledgment(self, request_id: int, message_id: int) -> dict[str, object]:
        self.acknowledgments.append((request_id, message_id))
        return {
            "request_id": request_id,
            "acknowledged_at": "2026-07-28T00:00:00Z",
            "acknowledgment_message_id": message_id,
        }

    def process_next(self) -> dict[str, object]:
        if self.process_results:
            return self.process_results.pop(0)
        return {"claimed": False, "request_id": None, "work_remaining": False}


def make_listener(
    *,
    bot: FakeBot | None = None,
    api: FakeAPI | None = None,
    sleep=lambda _seconds: None,
) -> tuple[TelegramCommandListener, FakeBot, FakeAPI]:
    fake_bot = bot or FakeBot()
    fake_api = api or FakeAPI()
    listener = TelegramCommandListener(listener_config(), fake_bot, fake_api, sleep=sleep)
    return listener, fake_bot, fake_api


def test_listener_config_rejects_disabled_notifications_and_placeholder_bearer() -> None:
    settings = Settings(
        TELEGRAM_COMMANDS_ENABLED=True,
        TELEGRAM_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="bot-secret",
        TELEGRAM_CHAT_ID="7729262607",
        TELEGRAM_ALLOWED_USER_ID="7729262607",
        TELEGRAM_BOT_USERNAME="ytpipe_sender_bot",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
    )
    with pytest.raises(ValueError, match="TELEGRAM_NOTIFICATIONS_ENABLED"):
        ListenerConfig.from_settings(settings)

    settings.telegram_notifications_enabled = True
    settings.internal_api_bearer_token = "replace-me-internal"
    with pytest.raises(ValueError, match="credentials"):
        ListenerConfig.from_settings(settings)


def test_bot_configuration_preflight_does_not_require_command_identity() -> None:
    settings = Settings(
        TELEGRAM_COMMANDS_ENABLED=False,
        TELEGRAM_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="bot-secret",
        TELEGRAM_CHAT_ID="",
        TELEGRAM_ALLOWED_USER_ID="",
        TELEGRAM_BOT_USERNAME="ytpipe_sender_bot",
    )

    config = ListenerConfig.from_settings(settings, require_command_config=False)

    assert config.chat_id == 0
    assert config.allowed_user_id == 0


class FakeResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        return self._body


class FakeHTTP:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def test_get_updates_uses_approved_long_poll_contract() -> None:
    http = FakeHTTP([FakeResponse(200, {"ok": True, "result": []})])
    bot = TelegramBotClient("bot-secret", http_client=http)

    assert bot.get_updates(offset=42) == []
    payload = http.calls[0]["json"]
    assert payload == {
        "offset": 42,
        "limit": 100,
        "timeout": 50,
        "allowed_updates": ["message"],
    }
    assert http.calls[0]["timeout"] == 65.0


def test_batch_processes_updates_in_order_and_acknowledges_once() -> None:
    api = FakeAPI(
        responses=[
            {
                "request_id": 1,
                "accepted_for_offset": True,
                "acknowledgment_required": True,
                "acknowledged_at": None,
                "user_message": "Recibido.",
                "outcome": "accepted",
            },
            {
                "request_id": 1,
                "accepted_for_offset": True,
                "acknowledgment_required": False,
                "acknowledged_at": "2026-07-28T00:00:00Z",
                "user_message": "",
                "outcome": "duplicate",
            },
        ]
    )
    listener, bot, api = make_listener(api=api)

    next_offset = listener.process_update_batch(
        [telegram_update(102), telegram_update(101, text="hello")],
        offset=None,
    )

    assert next_offset == 103
    assert [item["telegram_update_id"] for item in api.submitted] == [102]
    assert bot.sent == [{
        "chat_id": 7729262607,
        "text": "Recibido.",
        "reply_to_message_id": 200,
    }]
    assert api.acknowledgments == [(1, 901)]

    duplicate = listener.handle_update(telegram_update(102))
    assert duplicate is True
    assert len(api.submitted) == 2
    assert len(bot.sent) == 1


def test_authorized_intake_failure_does_not_advance_offset() -> None:
    class FailingAPI(FakeAPI):
        def submit_command(self, payload: dict[str, object]) -> dict[str, object]:
            raise LocalAPIError("Local API unavailable.", status_code=503, retryable=True)

    listener, _bot, _api = make_listener(api=FailingAPI())
    assert listener.process_update_batch([telegram_update()], offset=100) == 100


def test_permanent_intake_failure_stops_listener_instead_of_blocking_forever() -> None:
    class PermanentAPI(FakeAPI):
        def submit_command(self, payload: dict[str, object]) -> dict[str, object]:
            raise LocalAPIError("Local API rejected the request.", status_code=403, retryable=False)

    listener, _bot, _api = make_listener(api=PermanentAPI())

    assert listener.process_update_batch([telegram_update()], offset=100) == 100
    assert listener._stop.is_set()


@pytest.mark.parametrize(
    "overrides",
    [
        {"chat": {"id": 999, "type": "private"}},
        {"from": {"id": 999}},
        {"chat": {"id": 7729262607, "type": "group"}},
        {"forward_origin": {"type": "user"}},
        {"sender_chat": {"id": -100}},
        {"photo": [{"file_id": "photo"}], "text": None},
    ],
)
def test_unauthorized_or_non_command_messages_create_no_work(overrides: dict[str, object]) -> None:
    listener, _bot, api = make_listener()

    assert listener.handle_update(telegram_update(**overrides)) is True
    assert api.submitted == []


def test_arbitrary_bot_suffix_is_ignored_but_configured_suffix_is_accepted() -> None:
    listener, _bot, api = make_listener()

    assert listener.handle_update(telegram_update(text="/summary@other_bot https://youtu.be/dQw4w9WgXcQ")) is True
    assert api.submitted == []
    assert listener.handle_update(telegram_update(text="/summary@YTPipe_Sender_Bot https://youtu.be/dQw4w9WgXcQ")) is True
    assert len(api.submitted) == 1


def test_worker_drain_continues_until_api_reports_no_work() -> None:
    listener, _bot, api = make_listener()
    api.process_results = [
        {"claimed": True, "request_id": 1, "work_remaining": True},
        {"claimed": True, "request_id": 2, "work_remaining": False},
    ]

    listener._drain_worker()

    assert api.process_results == []


def test_permanent_worker_api_failure_stops_listener() -> None:
    class PermanentWorkerAPI(FakeAPI):
        def process_next(self) -> dict[str, object]:
            raise LocalAPIError("worker authorization failed", status_code=401, retryable=False)

    listener, _bot, api = make_listener(api=PermanentWorkerAPI())

    listener._drain_worker()

    assert listener._stop.is_set()
    assert api.process_results == []


def test_telegram_errors_are_retryable_without_exposing_token() -> None:
    http = FakeHTTP([
        FakeResponse(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 7}}),
    ])
    bot = TelegramBotClient("bot-secret", http_client=http)

    with pytest.raises(TelegramBotError) as raised:
        bot.get_updates(offset=None)

    assert "bot-secret" not in str(raised.value)
    assert "7" not in str(raised.value)
    assert raised.value.retry_after_seconds == 7


def test_acknowledgment_retry_after_is_bounded() -> None:
    class RetryBot(FakeBot):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def send_message(self, *, chat_id: int, text: str, reply_to_message_id: int | None = None) -> int:
            self.attempts += 1
            if self.attempts == 1:
                raise TelegramBotError("rate limited", retryable=True, retry_after_seconds=300)
            return super().send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
            )

    delays: list[float] = []
    bot = RetryBot()
    listener, _unused_bot, api = make_listener(bot=bot, sleep=delays.append)
    listener._acknowledge(
        {
            "request_id": 1,
            "user_message": "Recibido.",
        },
        {
            "telegram_message_id": 200,
        },
    )

    assert delays == [30.0]
    assert api.acknowledgments == [(1, 901)]


def test_local_api_does_not_include_bearer_in_errors() -> None:
    from app.services.telegram_command_listener import TelegramCommandAPIClient

    http = FakeHTTP([FakeResponse(503, {"detail": "secret"})])
    api = TelegramCommandAPIClient("http://127.0.0.1:8000", "internal-secret", http_client=http)

    with pytest.raises(LocalAPIError) as raised:
        api.process_next()

    assert "internal-secret" not in str(raised.value)


def test_systemd_unit_contains_no_secrets_and_uses_unprivileged_listener() -> None:
    unit = Path("systemd/ytpipe-telegram-command-listener.service").read_text()

    assert "User=jmse" in unit
    assert "EnvironmentFile=/home/jmse/labs/YTPipe/.env" in unit
    assert "Restart=on-failure" in unit
    assert "TimeoutStopSec=450" in unit
    assert "ExecStart=" in unit
    assert "TELEGRAM_BOT_TOKEN=" not in unit
    assert "INTERNAL_API_BEARER_TOKEN=" not in unit
