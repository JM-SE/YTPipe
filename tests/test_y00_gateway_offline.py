from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import FrozenInstanceError

import httpx
import pytest

from app.core.settings import Settings
from app.models.channel import Channel
from app.models.pipeline_stage import PipelineStage
from app.models.user import User
from app.models.video import Video
from app.models.sync_state import SyncState
from app.services.broker_errors import BrokerSummarizationError
from app.services.broker_gateway import BrokerSummarizationGateway
from app.services.broker_summary import validate_broker_output
from app.services.direct_summarization import DirectSummarizationGateway, build_summarization_gateway
from app.services.pipeline import PipelineService, STAGE_SUMMARY, STATUS_PENDING_RETRY, SUMMARIZATION_PROCESS
from app.services.summarization import (
    FINAL_SUMMARY_INSTRUCTIONS,
    SUMMARIZATION_SYSTEM_PROMPT,
    SummarizationRequestError,
)
from app.services.summarization_gateway import SummaryGatewayContext, SummaryOperation, idempotency_key
from app.services.summarization_planner import plan_operations


VALID = """RESUMEN
Resumen fiel.

PUNTOS CLAVE
• Punto uno
• Punto dos
• Punto tres
• Punto cuatro

CONCLUSIÓN
Conclusión fiel."""

TASK = {
    "id": "t-1",
    "workload": "batch-summary",
    "capability": "summarize",
    "status": "queued",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

def task(task_id="t-1"):
    return {**TASK, "id": task_id}

def task_result():
    return {"status": "succeeded", "result": {
        "content": VALID, "finish_reason": "stop",
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }}


def response(status: int, *, json: object | None = None, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=json, headers=headers, request=httpx.Request("GET", "http://broker"))


def gateway(handler, *, clock=None, sleep=None) -> BrokerSummarizationGateway:
    return BrokerSummarizationGateway(
        base_url="https://broker.test",
        credential="secret-token",
        transport=httpx.MockTransport(handler),
        timeout=5,
        max_tokens=123,
        monotonic=clock or (lambda: 0.0),
        sleep=sleep or (lambda _: None),
    )


def test_direct_gateway_preserves_facade_and_context_compatibility(monkeypatch) -> None:
    service = build_summarization_gateway(Settings(DATABASE_URL="sqlite://"))
    assert isinstance(service, DirectSummarizationGateway)
    seen: list[dict] = []
    payload = {"choices": [{"message": {"content": "  accepted  "}}]}
    monkeypatch.setattr("app.services.summarization.httpx.post", lambda url, **kw: (seen.append(kw["json"]) or httpx.Response(200, json=payload)))

    assert service.summarize("texto", context=SummaryGatewayContext(7)) == "accepted"
    assert seen[0]["model"] == "gpt-3.5-turbo"
    assert seen[0]["messages"] == [
        {"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT},
        {"role": "user", "content": f"{FINAL_SUMMARY_INSTRUCTIONS}\n\nTRANSCRIPCION:\n\ntexto"},
    ]
    assert list(seen[0]) == ["model", "messages", "temperature", "max_tokens", "stop"]


def test_direct_errors_and_thresholds_remain_unchanged(monkeypatch) -> None:
    service = DirectSummarizationGateway(Settings(DATABASE_URL="sqlite://"))
    calls = []
    monkeypatch.setattr("app.services.summarization.httpx.post", lambda *a, **kw: (calls.append((a, kw)) or httpx.Response(200, json={"choices": [{"message": {"content": " x "}}]})))
    assert service.summarize("x" * 30_000) == "x"
    assert len(calls) == 1
    calls.clear()
    assert service.summarize("x" * 30_001) == "x"
    assert len(calls) == 3  # two partials plus aggregate for the no-space boundary

    monkeypatch.setattr("app.services.summarization.httpx.post", lambda *a, **kw: httpx.Response(200, json={"choices": [{}]}))
    with pytest.raises(SummarizationRequestError, match="empty content"):
        service.summarize("brief")


def test_operations_and_idempotency_are_stable_and_ordinal() -> None:
    transcript = "word " * 10_000
    operations = plan_operations(transcript, 123)
    assert [op.ordinal for op in operations] == list(range(len(operations)))
    key = idempotency_key(SummaryGatewayContext(42), SummaryOperation("direct-final", 0, "s", "u", 1))
    assert key == "bc564a0eebf1d46ad06aaf4baed5abce0c3ec1978901c560c8c6b21446edd1e3"
    assert key == idempotency_key(SummaryGatewayContext(42), SummaryOperation("direct-final", 0, "other", "different", 999))
    assert key != idempotency_key(SummaryGatewayContext(43), SummaryOperation("direct-final", 0, "s", "u", 1))
    assert key != idempotency_key(SummaryGatewayContext(42), SummaryOperation("partial", 0, "s", "u", 1))
    assert key != idempotency_key(SummaryGatewayContext(42), SummaryOperation("direct-final", 1, "s", "u", 1))
    with pytest.raises(FrozenInstanceError):
        SummaryGatewayContext(1).stage_id = 2  # type: ignore[misc]


def test_broker_200_uses_result_content_and_exact_generic_envelope() -> None:
    requests = []
    def handler(request: httpx.Request):
        requests.append(request)
        return response(200, json=task_result())
    svc = gateway(handler)
    try:
        assert svc.summarize("short", context=SummaryGatewayContext(9)) == VALID
    finally:
        svc.close()
    req = requests[0]
    assert req.method == "POST" and req.url.path == "/v1/tasks"
    assert req.headers["Authorization"] == "Bearer secret-token"
    assert req.headers["Prefer"] == "wait=30"
    assert set(req.headers) >= {"authorization", "idempotency-key", "content-type", "prefer"}
    body = httpx.Response(200, request=req, content=req.content).json()
    assert body == {"workload": "batch-summary", "capability": "summarize", "messages": body["messages"], "generation": {"max_tokens": 123, "temperature": 0.7}, "response": {"kind": "text"}}
    assert not any(field in body for field in ("provider", "model", "backend", "identity", "task_id", "trace"))
    assert "secret-token" not in req.content.decode()


@pytest.mark.parametrize("status", [201, 202])
def test_broker_async_statuses_poll_pending_then_success(status: int) -> None:
    calls = []
    def handler(request: httpx.Request):
        calls.append(request)
        if request.method == "POST":
            return response(status, json=task(), headers={"Location": "/v1/tasks/t-1"})
        return response(200, json=task_result())
    svc = gateway(handler)
    try:
        assert svc.summarize("short", context=SummaryGatewayContext(1)) == VALID
    finally:
        svc.close()
    assert [r.method for r in calls] == ["POST", "GET"]
    assert calls[1].url.path == "/v1/tasks/t-1/result"


@pytest.mark.parametrize("state", ["failed", "cancelled", "expired", "unknown"])
def test_broker_states_fail_closed_without_cancel(state: str) -> None:
    calls = []
    def handler(request: httpx.Request):
        calls.append(request)
        return response(202, headers={"Location": "/v1/tasks/t"}) if request.method == "POST" else response(200, json={"status": state})
    svc = gateway(handler)
    try:
        with pytest.raises(BrokerSummarizationError) as exc:
            svc.summarize("short", context=SummaryGatewayContext(1))
        assert str(exc.value) == "Broker summarization failed."
        assert exc.value.recovery_target == "none"
    finally:
        svc.close()
    assert all(request.method != "DELETE" for request in calls)


@pytest.mark.parametrize("state", ["failed", "cancelled", "expired"])
def test_broker_valid_terminal_errors_are_sanitized(state: str) -> None:
    error = {"class": "backend_rejected", "code": "backend_unavailable", "message": "safe"}

    def handler(request: httpx.Request):
        if request.method == "POST":
            return response(202, json=task("t"), headers={"Location": "/v1/tasks/t"})
        return response(200, json={"status": state, "error": error})

    svc = gateway(handler)
    try:
        with pytest.raises(BrokerSummarizationError) as exc:
            svc.summarize("short", context=SummaryGatewayContext(1))
        assert exc.value.code == f"broker_task_{state}"
        assert str(exc.value) == "Broker summarization failed."
    finally:
        svc.close()


@pytest.mark.parametrize("value", [
    "", "  ", "a <think>x", "a </THINK>",
    "RESUMEN\nR\nPUNTOS CLAVE\n• a\n• b\n• c\nCONCLUSIÓN\nC",  # 3 bullets
    "RESUMEN\nR\nPUNTOS CLAVE\n" + "\n".join(f"• {i}" for i in range(8)) + "\nCONCLUSIÓN\nC",  # 8
    " RESUMEN\nR\nPUNTOS CLAVE\n• a\n• b\n• c\n• d\nCONCLUSIÓN\nC",  # indented
    "RESUMEN:\nR\nPUNTOS CLAVE\n• a\n• b\n• c\n• d\nCONCLUSIÓN\nC",  # punctuated
    "resumen\nR\nPUNTOS CLAVE\n• a\n• b\n• c\n• d\nCONCLUSIÓN\nC",  # case changed
    "RESUMEN\n\nPUNTOS CLAVE\n• a\n• b\n• c\n• d\nCONCLUSIÓN\nC",  # empty section
    "CONCLUSIÓN\nC\nPUNTOS CLAVE\n• a\n• b\n• c\n• d\nRESUMEN\nR",  # reordered
    "RESUMEN\nR\nRESUMEN\nR\nPUNTOS CLAVE\n• a\n• b\n• c\n• d\nCONCLUSIÓN\nC",  # duplicate
    "RESUMEN\nR\nPUNTOS CLAVE\n• a\n• b\n• c\n• d\nCONCLUSIÓN\nC\nEXTRA HEADING",
    "RESUMEN\nR\nPUNTOS CLAVE\n• a\n• b\n• c\n• d\nCONCLUSIÓN\nC\n# extra",
    "RESUMEN\nR\nPUNTOS CLAVE\n1. a\n• b\n• c\n• d\nCONCLUSIÓN\nC",
])
def test_broker_output_oracle_rejects_invalid_families(value: str) -> None:
    with pytest.raises(ValueError):
        validate_broker_output(value)


def test_broker_output_oracle_normalizes_only_allowed_whitespace() -> None:
    raw = " \r\nRESUMEN  \r\nResumen  fiel.  \r\n\r\nPUNTOS CLAVE\r\n• Uno   \r\n• Dos\r\n• Tres\r\n• Cuatro\r\n\r\nCONCLUSIÓN\r\nConclusión.  \r\n "
    assert validate_broker_output(raw) == "RESUMEN\nResumen  fiel.\n\nPUNTOS CLAVE\n• Uno\n• Dos\n• Tres\n• Cuatro\n\nCONCLUSIÓN\nConclusión."


def test_broker_location_unknown_http_and_malformed_body_are_sanitized() -> None:
    for location in [None, "https://evil.test/v1/tasks/x", "/v1/tasks/../x", "/v1/tasks/x?secret=1", "/v1/tasks/x%2Fother", "/wrong/x"]:
        def handler(request, location=location):
            return response(202, json=task("x"), headers={} if location is None else {"Location": location})
        svc = gateway(handler)
        try:
            with pytest.raises(BrokerSummarizationError, match="Broker summarization failed"):
                svc.summarize("x", context=SummaryGatewayContext(1))
        finally:
            svc.close()

    def bad(request):
        return response(418, json={"code": "provider-secret", "detail": "PRIVATE"})
    svc = gateway(bad)
    try:
        with pytest.raises(BrokerSummarizationError) as exc:
            svc.summarize("x", context=SummaryGatewayContext(1))
        assert exc.value.code == "broker_protocol_error"
        assert "PRIVATE" not in str(exc.value)
    finally:
        svc.close()


@pytest.mark.parametrize("code, expected", [("unauthorized", "broker_unauthorized"), ("invalid_request", "broker_invalid_request")])
def test_known_broker_problem_codes_are_local_only(code: str, expected: str) -> None:
    svc = gateway(lambda request: response(400, json={"code": code, "detail": "remote detail"}))
    try:
        with pytest.raises(BrokerSummarizationError) as exc:
            svc.summarize("x", context=SummaryGatewayContext(1))
        assert exc.value.code == expected
        assert str(exc.value) == "Broker summarization failed."
        assert "remote detail" not in str(exc.value)
    finally:
        svc.close()


def test_broker_deadline_uses_injected_clock_sleep_and_propagates_remaining() -> None:
    now = [0.0]
    sleeps = []
    requests = []
    def clock(): return now[0]
    def sleep(seconds): sleeps.append(seconds); now[0] += seconds
    def handler(request):
        requests.append(request)
        return response(202, json=task("t"), headers={"Location": "/v1/tasks/t"}) if request.method == "POST" else response(200, json={"status": "pending"})
    svc = gateway(handler, clock=clock, sleep=sleep)
    try:
        with pytest.raises(BrokerSummarizationError) as exc:
            svc.summarize("x", context=SummaryGatewayContext(1))
        assert exc.value.code == "broker_timeout"
    finally:
        svc.close()
    assert sleeps == [1.0] * 5
    assert len(requests) == 5  # ceil(5s / 1s), with no poll after the final sleep


def test_broker_deadline_includes_post() -> None:
    now = [0.0]
    seen = []
    def handler(request):
        seen.append(request.extensions["timeout"] if "timeout" in request.extensions else None)
        now[0] = 6.0
        return response(202, json=task("t"), headers={"Location": "/v1/tasks/t"})
    svc = gateway(handler, clock=lambda: now[0])
    try:
        with pytest.raises(BrokerSummarizationError) as exc:
            svc.summarize("x", context=SummaryGatewayContext(1))
        assert exc.value.code == "broker_timeout"
    finally:
        svc.close()


@pytest.mark.parametrize("payload", [
    {"status": "succeeded", "result": {"content": VALID}},
    {"status": "succeeded", "result": {"content": VALID, "finish_reason": "stop", "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}, "extra": True},
])
def test_broker_200_terminal_result_is_fail_closed(payload) -> None:
    svc = gateway(lambda request: response(200, json=payload))
    try:
        with pytest.raises(BrokerSummarizationError) as exc:
            svc.summarize("x", context=SummaryGatewayContext(1))
        assert exc.value.code == "broker_protocol_error"
    finally:
        svc.close()


def test_async_task_id_must_match_location() -> None:
    svc = gateway(lambda request: response(202, json=task("other"), headers={"Location": "/v1/tasks/t-1"}))
    try:
        with pytest.raises(BrokerSummarizationError) as exc:
            svc.summarize("x", context=SummaryGatewayContext(1))
        assert exc.value.code == "broker_protocol_error"
    finally:
        svc.close()


def test_pipeline_persists_broker_recovery_target_none(db_session) -> None:
    user = User(email="y00@example.com")
    channel = Channel(youtube_channel_id="y00", title="Y00")
    video = Video(youtube_video_id="v", channel=channel, title="Video", published_at=datetime.now(UTC), transcript="text")
    db_session.add_all([user, channel, video]); db_session.commit()
    stage = PipelineStage(video_id=video.id, user_id=user.id, stage=STAGE_SUMMARY, status="pending")
    db_session.add(stage); db_session.commit()
    svc = PipelineService(summarization_service=type("Failing", (), {"summarize": lambda *_a, **_k: (_ for _ in ()).throw(BrokerSummarizationError("ignored"))})())
    assert svc._attempt_summary_stage(db_session, stage, video)
    assert stage.status == STATUS_PENDING_RETRY
    assert svc._summary_recovery_target == "none"
    assert stage.last_error == "Broker summarization failed."
    svc._persist_summary_pause_state(db_session, user)
    state = db_session.query(SyncState).filter_by(user_id=user.id, process_type=SUMMARIZATION_PROCESS).one()
    assert state.state_metadata["summary_failure"]["recovery_target"] == "none"


def test_none_recovery_target_does_not_infer(db_session) -> None:
    user = User(email="none-recovery@example.com")
    channel = Channel(youtube_channel_id="none", title="None")
    video = Video(youtube_video_id="none-v", channel=channel, title="Video", published_at=datetime.now(UTC), transcript="text")
    db_session.add_all([user, channel, video]); db_session.commit()
    stage = PipelineStage(video_id=video.id, user_id=user.id, stage=STAGE_SUMMARY, status=STATUS_PENDING_RETRY)
    db_session.add(stage); db_session.commit()
    summary = type("Summary", (), {"summarize": lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("inference"))})()
    svc = PipelineService(summarization_service=summary, summary_paused=True)
    svc._summary_recovery_target = "none"
    assert svc.attempt_summary_recovery(db_session, user) is False
    assert svc.summary_paused is True
    assert stage.status == STATUS_PENDING_RETRY
