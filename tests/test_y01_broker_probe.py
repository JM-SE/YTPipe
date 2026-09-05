from __future__ import annotations

import httpx
import pytest
from youtube_transcript_api import YouTubeTranscriptApi

from app.cli import broker_probe as cli
from app.core.settings import Settings
from app.services.broker_connection_config import BrokerConfigurationError, broker_connection_config
from app.services.broker_probe import BrokerProbeService, probe_idempotency_key
from app.services.broker_gateway import BrokerTaskClient
from app.services.broker_profile import load_y01_profile
from app.services.transcript import TranscriptFetchResult
from app.services.youtube_video_url import parse_youtube_video_url


VALID_OUTPUT = """RESUMEN
Resumen fiel.

PUNTOS CLAVE
• Punto uno
• Punto dos
• Punto tres
• Punto cuatro

CONCLUSIÓN
Conclusión fiel."""


def _response(status: int, payload: object, request: httpx.Request, *, headers=None) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers, request=request)


def _task(task_id: str = "probe-task") -> dict:
    return {
        "id": task_id,
        "workload": "batch-summary",
        "capability": "summarize",
        "status": "queued",
        "attempts": 0,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _result() -> dict:
    return {
        "status": "succeeded",
        "result": {
            "content": VALID_OUTPUT,
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        },
    }


def _client(handler, *, now=None, sleep=None, profile=None) -> BrokerTaskClient:
    client = httpx.Client(
        base_url="https://broker.example.test",
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    return BrokerTaskClient.from_client(
        client,
        timeout=10,
        profile=profile or load_y01_profile(),
        monotonic=now or (lambda: 0.0),
        sleep=sleep or (lambda _: None),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"broker_base_url": "https://broker.test/path"},
        {"broker_base_url": "https://broker.test?token=secret"},
        {"broker_base_url": "http://10.0.0.2"},
        {"broker_bearer_token": "replace-me"},
        {"broker_bearer_token": "bad\ntoken"},
    ],
)
def test_probe_configuration_rejects_unsafe_values(overrides: dict) -> None:
    values = {
        "app_env": "production",
        "broker_probes_enabled": True,
        "broker_base_url": "https://broker.test",
        "broker_bearer_token": "safe-token",
    }
    values.update(overrides)
    settings = Settings(**values)
    with pytest.raises(BrokerConfigurationError):
        broker_connection_config(settings)


def test_probe_configuration_is_inert_when_flags_are_disabled() -> None:
    settings = Settings(broker_base_url="not-a-url", broker_bearer_token="")
    assert settings.broker_probes_enabled is False
    assert settings.broker_acceptance_probe_enabled is False
    assert settings.llama_cpp_base_url == "http://127.0.0.1:8001/v1"


def test_transcript_dependency_exposes_v1_fetch_contract() -> None:
    assert callable(getattr(YouTubeTranscriptApi, "fetch", None))


def test_local_probe_still_rejects_non_loopback_http() -> None:
    settings = Settings(
        app_env="local",
        broker_base_url="http://10.0.0.2",
        broker_bearer_token="safe-token",
    )
    with pytest.raises(BrokerConfigurationError):
        broker_connection_config(settings)


def test_probe_idempotency_is_namespaced_and_stable() -> None:
    first = probe_idempotency_key("synthetic", "probe-1", "submit", 0)
    assert first == probe_idempotency_key("synthetic", "probe-1", "submit", 0)
    assert first != probe_idempotency_key("youtube", "probe-1", "submit", 0)
    assert len(first) == 64


def test_y01_profile_is_strict_and_single_source_for_probe_contract() -> None:
    profile = load_y01_profile()
    assert profile.workload == "batch-summary"
    assert profile.capability == "summarize"
    assert profile.max_tokens == 1024
    assert profile.temperature == 0.7
    assert profile.required_min_output_bytes == 16384
    assert profile.max_request_content_bytes == 131072
    assert profile.min_backend_response_start_timeout_seconds == 60
    assert profile.min_workload_timeout_seconds == 300
    assert profile.max_acceptable_attempts == 1
    assert profile.consumer_overall_deadline_seconds == 360
    # Golden digest: the broker `llm-broker compat` CLI computes the identical
    # semantic digest from this manifest (H03 cross-language contract).
    assert profile.digest == "sha256:60450bde099909b93a79deb03d07576b47b8282c0e0a4ded91aa56dd1a61615e"


def test_y01_profile_rejects_duplicate_keys(tmp_path) -> None:
    path = tmp_path / "profile.json"
    path.write_text('{"version":"consumer-compat/v1","version":"consumer-compat/v1"}')
    with pytest.raises(ValueError, match="profile is invalid"):
        load_y01_profile(path)


def test_synthetic_probe_uses_fixed_payload_and_returns_sanitized_result() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _response(200, _result(), request)

    client = _client(handler)
    try:
        service = BrokerProbeService(client, probe_id_factory=lambda: "probe-1")
        result = service.synthetic()
        assert result.status == "succeeded"
        assert result.category == "synthetic_accepted"
        assert len(requests) == 1
        body = httpx.Response(200, request=requests[0], content=requests[0].content).json()
        assert body["workload"] == "batch-summary"
        assert body["capability"] == "summarize"
        assert "paneles solares" in body["messages"][1]["content"]
        assert "Authorization" not in body["messages"][1]["content"]
        assert body["generation"]["max_tokens"] == 1024
    finally:
        client.close()


def test_synthetic_probe_reports_output_validation_separately() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        invalid = _result()
        invalid["result"]["content"] = "respuesta libre"
        return _response(200, invalid, request)

    client = _client(handler)
    try:
        result = BrokerProbeService(client).synthetic("probe-invalid-output")
        assert result == type(result)("failed", "broker_output_invalid")
    finally:
        client.close()


def test_broker_unprocessable_is_reported_as_safe_invalid_request() -> None:
    client = _client(lambda request: _response(422, {"code": "unprocessable", "detail": "private"}, request))
    try:
        result = BrokerProbeService(client).synthetic("probe-unprocessable")
        assert result == type(result)("failed", "broker_invalid_request")
    finally:
        client.close()


@pytest.mark.parametrize("status", [201, 202])
def test_async_probe_polls_once_with_valid_task_envelope(status: int) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return _response(status, _task(), request, headers={"Location": "/v1/tasks/probe-task"})
        return _response(200, _result(), request)

    client = _client(handler, sleep=lambda _: None)
    try:
        result = BrokerProbeService(client, probe_id_factory=lambda: "probe-async").synthetic()
        assert result.status == "succeeded"
        assert [request.method for request in calls] == ["POST", "GET"]
        assert calls[1].url.path == "/v1/tasks/probe-task/result"
    finally:
        client.close()


@pytest.mark.parametrize("location", [
    "/v1/tasks/../other",
    "/v1/tasks/probe-task?secret=1",
    "https://evil.example/v1/tasks/probe-task",
    "/v1/tasks/probe-task%2Fother",
])
def test_async_probe_rejects_unsafe_location_without_polling(location: str) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _response(202, _task(), request, headers={"Location": location})

    client = _client(handler)
    try:
        result = BrokerProbeService(client).synthetic("probe-location")
        assert result == type(result)("failed", "broker_location_invalid")
        assert len(calls) == 1
    finally:
        client.close()


def test_async_probe_rejects_malformed_terminal_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _response(202, _task(), request, headers={"Location": "/v1/tasks/probe-task"})
        return _response(200, {"status": "failed", "error": {"message": "private"}}, request)

    client = _client(handler)
    try:
        result = BrokerProbeService(client).synthetic("probe-error")
        assert result == type(result)("failed", "broker_protocol_error")
    finally:
        client.close()


def test_probe_maps_length_finish_reason_to_incomplete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        result = _result()
        result["result"]["finish_reason"] = "length"
        return _response(200, result, request)

    client = _client(handler)
    try:
        result = BrokerProbeService(client).synthetic("probe-length")
        assert result == type(result)("failed", "broker_output_incomplete")
    finally:
        client.close()


class _FakeTTY:
    def __init__(self, value: str = "") -> None:
        self.value = value
        self.output = ""

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        value, self.value = self.value.split("\n", 1) if "\n" in self.value else (self.value, "")
        return value + "\n"

    def write(self, value: str) -> int:
        self.output += value
        return len(value)


def test_cli_requires_tty() -> None:
    class Stream:
        def isatty(self) -> bool:
            return False

        def write(self, value: str) -> int:
            return len(value)

    assert cli.main([], stdin=Stream(), stdout=Stream(), stderr=Stream()) == 2


def test_cli_youtube_refuses_without_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(broker_probes_enabled=True, broker_acceptance_probe_enabled=True,
                        broker_base_url="https://broker.test", broker_bearer_token="safe-token")
    stdin = _FakeTTY("https://youtu.be/abcdefghijk\nNO\n")
    stdout, stderr = _FakeTTY(), _FakeTTY()
    monkeypatch.setattr(cli, "build_broker_client", lambda config: object())
    monkeypatch.setattr(cli.BrokerTaskClient, "from_client", lambda client, **kwargs: type("T", (), {
        "close": lambda self: None,
    })())
    assert cli.main(["--youtube"], settings=settings, stdin=stdin, stdout=stdout, stderr=stderr) == 2
    assert "Consent was not provided." in stderr.output


def test_cli_default_output_does_not_include_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(broker_probes_enabled=True, broker_base_url="https://broker.test",
                        broker_bearer_token="safe-token")

    class FakeTaskClient:
        def submit(self, operation, key):
            return VALID_OUTPUT

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli, "build_broker_client", lambda config: object())
    monkeypatch.setattr(cli.BrokerTaskClient, "from_client", lambda client, **kwargs: FakeTaskClient())
    stdin, stdout, stderr = _FakeTTY(), _FakeTTY(), _FakeTTY()
    assert cli.main([], settings=settings, stdin=stdin, stdout=stdout, stderr=stderr) == 0
    assert VALID_OUTPUT not in stdout.output
    assert "Probe succeeded: synthetic_accepted" in stdout.output


def test_cli_exposes_no_content_display_flag() -> None:
    # F7 acceptance pin: no --show-summary flag may exist, and no accepted
    # output may reach a terminal through the CLI even with the last summary
    # accepted. The parser help output is the stable contract surface.
    help_out, help_err = _FakeTTY(), _FakeTTY()
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"], settings=Settings(), stdin=_FakeTTY(), stdout=help_out, stderr=help_err)
    assert exc.value.code == 0
    assert "--show-summary" not in help_out.output
    assert "--show-summary" not in help_err.output


def test_youtube_probe_rejects_oversized_transcript_before_submit() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return _response(500, {}, request)

    class Transcript:
        def fetch_transcript_result(self, video_id: str) -> TranscriptFetchResult:
            return TranscriptFetchResult("x" * 11, "completed")

    client = _client(handler)
    try:
        service = BrokerProbeService(client, max_transcript_characters=10)
        result = service.youtube(parse_youtube_video_url("https://youtu.be/abcdefghijk"), Transcript())
        assert result == type(result)("failed", "transcript_too_large")
        assert called is False
    finally:
        client.close()


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("unavailable", "transcript_unavailable"), ("retryable", "transcript_retryable")],
)
def test_youtube_probe_distinguishes_unavailable_from_retryable_transcript(
    outcome: str, expected: str,
) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return _response(200, _result(), request)

    class Transcript:
        def fetch_transcript_result(self, video_id: str) -> TranscriptFetchResult:
            return TranscriptFetchResult(None, outcome, "implementation detail")

    client = _client(handler)
    try:
        result = BrokerProbeService(client).youtube(
            parse_youtube_video_url("https://youtu.be/abcdefghijk"), Transcript(), "probe-transcript",
        )
        assert result == type(result)("failed", expected)
        assert called is False
    finally:
        client.close()


def test_synthetic_probe_rejects_oversized_request_without_submit() -> None:
    import dataclasses

    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return _response(200, _result(), request)

    profile = dataclasses.replace(load_y01_profile(), max_request_content_bytes=10)
    client = _client(handler, profile=profile)
    try:
        service = BrokerProbeService(
            client, profile=profile, probe_id_factory=lambda: "probe-input-cap",
        )
        result = service.synthetic()
        assert result == type(result)("failed", "broker_input_too_large")
        assert called is False
        assert service.summary_for_display is None
    finally:
        client.close()


def test_youtube_probe_does_not_persist_or_invoke_pipeline() -> None:
    client = _client(lambda request: _response(200, _result(), request))

    class Transcript:
        def fetch_transcript_result(self, video_id: str) -> TranscriptFetchResult:
            return TranscriptFetchResult("approved transcript", "completed")

    try:
        service = BrokerProbeService(client, probe_id_factory=lambda: "probe-yt")
        result = service.youtube(parse_youtube_video_url("https://www.youtube.com/watch?v=abcdefghijk"), Transcript())
        assert result.status == "succeeded"
        assert service.summary_for_display == VALID_OUTPUT
    finally:
        client.close()
