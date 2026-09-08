"""Offline tests for the Y02b agnostic inference-route resolver.

No test here performs network, broker, database, or subprocess activity:
broker interactions run against httpx.MockTransport and client construction
performs no I/O.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

import app.services.summary_route as summary_route_module
from app.core.settings import Settings
from app.services.broker_connection_config import BrokerConfigurationError
from app.services.broker_errors import BrokerSummarizationError
from app.services.broker_gateway import BrokerTaskClient
from app.services.broker_profile import load_y01_profile
from app.services.broker_summary import validate_broker_output
from app.services.broker_traffic import BrokerTrafficSummarizationService
from app.services.direct_summarization import DirectSummarizationGateway
from app.services.pipeline import PipelineService, STATUS_COMPLETED, STATUS_PENDING, STATUS_PENDING_RETRY
from app.services.summarization_gateway import SummaryGatewayContext
from app.services.summary_route import (
    BROKER_ROUTE,
    DIRECT_ROUTE,
    build_routed_summarization_gateway,
    close_routed_summarization_gateway,
    resolve_summary_route,
    summarization_route_name,
)

VALID = (
    "RESUMEN\n"
    "Resumen fiel del contenido.\n"
    "\n"
    "PUNTOS CLAVE\n"
    "• Uno\n"
    "• Dos\n"
    "• Tres\n"
    "• Cuatro\n"
    "\n"
    "CONCLUSIÓN\n"
    "Conclusión fiel."
)


def test_oracle_fixture_is_valid() -> None:
    assert validate_broker_output(VALID) == VALID


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, DIRECT_ROUTE),
        ("", DIRECT_ROUTE),
        ("   ", DIRECT_ROUTE),
        ("direct", DIRECT_ROUTE),
        ("  direct  ", DIRECT_ROUTE),
        ("broker", BROKER_ROUTE),
        ("  broker  ", BROKER_ROUTE),
        ("Broker", DIRECT_ROUTE),
        ("BROKER", DIRECT_ROUTE),
        ("all", DIRECT_ROUTE),
        ("none", DIRECT_ROUTE),
    ],
)
def test_resolve_summary_route(value: str | None, expected: str) -> None:
    assert resolve_summary_route(value) == expected


def _base_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"DATABASE_URL": "sqlite://"}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_helper_defaults_to_direct_with_single_attribution_record(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.summary_route"):
        service = build_routed_summarization_gateway(_base_settings(), root="startup")
    assert isinstance(service, DirectSummarizationGateway)
    assert caplog.text.count("summary_route_resolved") == 1
    assert "route=direct" in caplog.text
    assert "root=startup" in caplog.text


def test_helper_unknown_value_resolves_direct(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.summary_route"):
        service = build_routed_summarization_gateway(_base_settings(SUMMARY_ROUTE="all"), root="polling")
    assert isinstance(service, DirectSummarizationGateway)
    assert "route=direct" in caplog.text


def _broker_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "DATABASE_URL": "sqlite://",
        "SUMMARY_ROUTE": "broker",
        "BROKER_BASE_URL": "http://127.0.0.1",
        "BROKER_BEARER_TOKEN": "h03-test-token-abc123",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_helper_broker_builds_traffic_service_without_network(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.summary_route"):
        service = build_routed_summarization_gateway(_broker_settings(), root="polling")
    try:
        assert isinstance(service, BrokerTrafficSummarizationService)
        assert caplog.text.count("summary_route_resolved") == 1
        assert "route=broker" in caplog.text
        assert "root=polling" in caplog.text
        assert "h03-test-token" not in caplog.text
        assert "127.0.0.1" not in caplog.text
    finally:
        service.close()


def test_helper_broker_with_invalid_config_fails_closed() -> None:
    with pytest.raises((BrokerConfigurationError, ValueError)):
        build_routed_summarization_gateway(_base_settings(SUMMARY_ROUTE="broker"), root="startup")


def _succeeded_result(content: str = VALID, finish_reason: str = "stop") -> dict:
    return {
        "status": "succeeded",
        "result": {
            "content": content,
            "finish_reason": finish_reason,
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        },
    }


def _response(status: int, payload: object | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers={},
        request=httpx.Request("POST", "https://broker.test/v1/tasks"),
    )


def _traffic_service(handler, profile=None) -> BrokerTrafficSummarizationService:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://broker.test")
    task_client = BrokerTaskClient.from_client(
        client,
        timeout=5,
        profile=profile or load_y01_profile(),
        monotonic=lambda: 0.0,
        sleep=lambda _: None,
    )
    service = BrokerTrafficSummarizationService(task_client, profile=profile or load_y01_profile())
    return service


def test_traffic_success_returns_summary_with_stable_stage_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _response(200, _succeeded_result())

    service = _traffic_service(handler)
    try:
        context = SummaryGatewayContext(stage_id=7)
        first = service.summarize("un texto corto", context=context)
        second = service.summarize("un texto corto", context=context)
        other = service.summarize("un texto corto", context=SummaryGatewayContext(stage_id=8))
    finally:
        service.close()
    assert first == VALID == second == other
    assert len(seen) == 3
    bodies = [json.loads(request.content.decode("utf-8")) for request in seen]
    assert all(
        body["workload"] == "batch-summary"
        and body["capability"] == "summarize"
        and body["generation"] == {"max_tokens": 1024, "temperature": 0.7}
        for body in bodies
    )
    keys = [request.headers["Idempotency-Key"] for request in seen]
    assert keys[0] == keys[1]
    assert keys[0] != keys[2]


def test_traffic_requires_context() -> None:
    service = _traffic_service(lambda request: _response(200, _succeeded_result()))
    try:
        with pytest.raises(BrokerSummarizationError) as exc_info:
            service.summarize("un texto corto")
    finally:
        service.close()
    assert exc_info.value.code == "broker_context_missing"


def test_traffic_length_result_fails_incomplete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(200, _succeeded_result(finish_reason="length"))

    service = _traffic_service(handler)
    try:
        with pytest.raises(BrokerSummarizationError) as exc_info:
            service.summarize("un texto corto", context=SummaryGatewayContext(stage_id=7))
    finally:
        service.close()
    assert exc_info.value.code == "broker_output_incomplete"


def test_traffic_invalid_oracle_content_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(200, _succeeded_result(content="nonsense without headings"))

    service = _traffic_service(handler)
    try:
        with pytest.raises(BrokerSummarizationError) as exc_info:
            service.summarize("un texto corto", context=SummaryGatewayContext(stage_id=7))
    finally:
        service.close()
    assert exc_info.value.code == "broker_output_invalid"


def test_traffic_oversized_request_fails_before_submit() -> None:
    seen: list[httpx.Request] = []
    profile = dataclasses.replace(load_y01_profile(), max_request_content_bytes=10)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _response(200, _succeeded_result())

    service = _traffic_service(handler, profile=profile)
    try:
        with pytest.raises(BrokerSummarizationError) as exc_info:
            service.summarize("un texto corto", context=SummaryGatewayContext(stage_id=7))
    finally:
        service.close()
    assert exc_info.value.code == "broker_input_too_large"
    assert seen == []


def test_traffic_transport_failure_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    service = _traffic_service(handler)
    try:
        with pytest.raises(BrokerSummarizationError) as exc_info:
            service.summarize("un texto corto", context=SummaryGatewayContext(stage_id=7))
    finally:
        service.close()
    assert exc_info.value.code == "broker_transport_error"


def test_direct_build_invokes_no_broker_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("broker construction must not run on the direct path")

    monkeypatch.setattr(summary_route_module, "build_broker_client", _forbidden)
    monkeypatch.setattr(summary_route_module, "load_y01_profile", _forbidden)
    monkeypatch.setattr(summary_route_module, "BrokerTaskClient", _forbidden)
    service = build_routed_summarization_gateway(_base_settings(), root="startup")
    assert isinstance(service, DirectSummarizationGateway)


def test_close_routed_is_noop_on_direct_and_closes_broker() -> None:
    direct = build_routed_summarization_gateway(_base_settings(), root="startup")
    assert not hasattr(direct, "close")
    assert close_routed_summarization_gateway(direct) is None
    client = MagicMock()
    broker = BrokerTrafficSummarizationService(client, profile=load_y01_profile())
    close_routed_summarization_gateway(broker)
    client.close.assert_called_once_with()


class _StubDirectService:
    def summarize(self, transcript: str, *, context: SummaryGatewayContext | None = None) -> str:
        assert context is not None and context.stage_id == 7
        return VALID


def _fake_stage_video() -> tuple[object, object, MagicMock]:
    stage = SimpleNamespace(
        id=7,
        status=STATUS_PENDING,
        attempt_count=0,
        max_attempts=3,
        last_error="previous",
        last_attempt_at=None,
        user_id=9,
    )
    video = SimpleNamespace(id=11, transcript="un texto corto", summary=None)
    session = MagicMock()
    session.scalar.return_value = None
    return stage, video, session


def test_pipeline_logs_per_summary_attribution_direct(caplog: pytest.LogCaptureFixture) -> None:
    service = PipelineService(summarization_service=_StubDirectService())  # type: ignore[arg-type]
    stage, video, session = _fake_stage_video()
    with caplog.at_level(logging.INFO, logger="app.services.pipeline"):
        assert service._attempt_summary_stage(session, stage, video) is True
    assert video.summary == VALID
    assert stage.status == STATUS_COMPLETED
    records = [r for r in caplog.records if "summary_route_attributed" in r.getMessage()]
    assert len(records) == 1
    assert "route=direct" in records[0].getMessage()
    assert "stage_id=7" in records[0].getMessage()
    assert "video_id=11" in records[0].getMessage()
    failed = [r for r in caplog.records if "summary_failed" in r.getMessage()]
    assert failed == []


def test_pipeline_logs_per_summary_attribution_broker(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(200, _succeeded_result())

    service = _traffic_service(handler)
    try:
        pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
        stage, video, session = _fake_stage_video()
        with caplog.at_level(logging.INFO, logger="app.services.pipeline"):
            assert pipeline._attempt_summary_stage(session, stage, video) is True
    finally:
        service.close()
    assert video.summary == VALID
    records = [r for r in caplog.records if "summary_route_attributed" in r.getMessage()]
    assert len(records) == 1
    assert "route=broker" in records[0].getMessage()
    assert "stage_id=7" in records[0].getMessage()
    assert "video_id=11" in records[0].getMessage()
    failed = [r for r in caplog.records if "summary_failed" in r.getMessage()]
    assert failed == []


def test_route_name_query_is_attribution_only() -> None:
    assert summarization_route_name(object()) == DIRECT_ROUTE
    assert summarization_route_name(_StubDirectService()) == DIRECT_ROUTE
    service = _traffic_service(lambda request: _response(200, _succeeded_result()))
    try:
        assert summarization_route_name(service) == BROKER_ROUTE
    finally:
        service.close()


def test_polling_second_build_failure_closes_first_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.routes.polling as polling_route_module

    first = SimpleNamespace(close=MagicMock())
    seen_roots: list[str] = []

    def fake_build(settings: Settings, *, root: str) -> object:
        seen_roots.append(root)
        if root == "polling-pipeline":
            raise RuntimeError("second build boom")
        return first

    monkeypatch.setattr(polling_route_module, "build_routed_summarization_gateway", fake_build)
    with pytest.raises(RuntimeError, match="second build boom"):
        polling_route_module._build_polling_service(_base_settings())
    assert seen_roots == ["polling", "polling-pipeline"]
    first.close.assert_called_once_with()


def test_close_gateway_quietly_swallows_close_errors() -> None:
    import app.api.routes.polling as polling_route_module

    def bad_close() -> None:
        raise RuntimeError("close boom")

    assert (
        polling_route_module._close_gateway_quietly(SimpleNamespace(close=bad_close), root="polling")
        is None
    )


class _BoomDirectService:
    def summarize(self, transcript: str, *, context: SummaryGatewayContext | None = None) -> str:
        raise RuntimeError("direct boom")


def test_plain_exception_defaults_direct_llama_on_direct_route() -> None:
    service = PipelineService(summarization_service=_BoomDirectService())  # type: ignore[arg-type]
    stage, video, session = _fake_stage_video()
    assert service._attempt_summary_stage(session, stage, video) is True
    assert stage.status == STATUS_PENDING_RETRY
    assert service.summary_paused is False
    assert service.summary_pause_reason is None
    assert service._summary_recovery_target == "direct_llama"


def test_plain_exception_defaults_none_on_broker_route(caplog: pytest.LogCaptureFixture) -> None:
    service = _traffic_service(lambda request: _response(200, _succeeded_result()))
    try:
        # Simulate a broker-adapter bug outside the sanitized raises: an
        # exception WITHOUT recovery_target on the broker route must still
        # resolve to "none", never "direct_llama".
        def _boom(transcript: str, *, context: SummaryGatewayContext | None = None) -> object:
            raise RuntimeError("broker bug")

        service.submit_task = _boom  # type: ignore[method-assign]
        pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
        stage, video, session = _fake_stage_video()
        with caplog.at_level(logging.INFO, logger="app.services.pipeline"):
            assert pipeline._attempt_summary_stage(session, stage, video) is True
    finally:
        service.close()
    assert stage.status == "failed"
    assert pipeline.summary_paused is False
    assert pipeline.summary_pause_reason is None
    assert pipeline._summary_recovery_target == "none"
    records = [r for r in caplog.records if "summary_route_attributed" in r.getMessage()]
    assert len(records) == 1
    assert "route=broker" in records[0].getMessage()


def test_direct_failure_logs_sanitized_code(caplog: pytest.LogCaptureFixture) -> None:
    service = PipelineService(summarization_service=_BoomDirectService())  # type: ignore[arg-type]
    stage, video, session = _fake_stage_video()
    with caplog.at_level(logging.INFO, logger="app.services.pipeline"):
        assert service._attempt_summary_stage(session, stage, video) is True
    assert stage.status == STATUS_PENDING_RETRY
    records = [r for r in caplog.records if "summary_failed" in r.getMessage()]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "code=direct_transport_error" in message
    assert "route=direct" in message
    assert "direct boom" not in message
    assert "direct boom" not in caplog.text
    attributed = [r for r in caplog.records if "summary_route_attributed" in r.getMessage()]
    assert len(attributed) == 1
    assert "route=direct" in attributed[0].getMessage()
    assert "stage_id=7" in attributed[0].getMessage()
    assert "video_id=11" in attributed[0].getMessage()


def test_broker_classified_failure_logs_its_code(caplog: pytest.LogCaptureFixture) -> None:
    service = _traffic_service(lambda request: _response(200, _succeeded_result()))
    try:
        def _boom(transcript: str, *, context: SummaryGatewayContext | None = None) -> object:
            raise BrokerSummarizationError("broker_output_incomplete")

        service.submit_task = _boom  # type: ignore[method-assign]
        pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
        stage, video, session = _fake_stage_video()
        with caplog.at_level(logging.INFO, logger="app.services.pipeline"):
            assert pipeline._attempt_summary_stage(session, stage, video) is True
    finally:
        service.close()
    assert stage.status == "failed"
    records = [r for r in caplog.records if "summary_failed" in r.getMessage()]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "code=broker_output_incomplete" in message
    assert "route=broker" in message
    attributed = [r for r in caplog.records if "summary_route_attributed" in r.getMessage()]
    assert len(attributed) == 1
    assert "route=broker" in attributed[0].getMessage()
    assert "stage_id=7" in attributed[0].getMessage()
    assert "video_id=11" in attributed[0].getMessage()


def test_broker_unclassified_failure_logs_unknown_without_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = _traffic_service(lambda request: _response(200, _succeeded_result()))
    try:
        def _boom(transcript: str, *, context: SummaryGatewayContext | None = None) -> object:
            raise ValueError("raw transport detail")

        service.submit_task = _boom  # type: ignore[method-assign]
        pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
        stage, video, session = _fake_stage_video()
        with caplog.at_level(logging.INFO, logger="app.services.pipeline"):
            assert pipeline._attempt_summary_stage(session, stage, video) is True
    finally:
        service.close()
    records = [r for r in caplog.records if "summary_failed" in r.getMessage()]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "code=broker_protocol_error" in message
    assert "route=broker" in message
    assert "raw transport detail" not in message
    assert "raw transport detail" not in caplog.text
    attributed = [r for r in caplog.records if "summary_route_attributed" in r.getMessage()]
    assert len(attributed) == 1
    assert "route=broker" in attributed[0].getMessage()
    assert "stage_id=7" in attributed[0].getMessage()
    assert "video_id=11" in attributed[0].getMessage()


def test_broker_length_finish_maps_incomplete_end_to_end(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(200, _succeeded_result(finish_reason="length"))

    service = _traffic_service(handler)
    try:
        pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
        stage, video, session = _fake_stage_video()
        with caplog.at_level(logging.INFO, logger="app.services.pipeline"):
            assert pipeline._attempt_summary_stage(session, stage, video) is True
    finally:
        service.close()
    assert stage.status == "failed"
    records = [r for r in caplog.records if "summary_failed" in r.getMessage()]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "code=broker_output_incomplete" in message
    assert "route=broker" in message
    attributed = [r for r in caplog.records if "summary_route_attributed" in r.getMessage()]
    assert len(attributed) == 1
    assert "route=broker" in attributed[0].getMessage()
    assert "stage_id=7" in attributed[0].getMessage()
    assert "video_id=11" in attributed[0].getMessage()
