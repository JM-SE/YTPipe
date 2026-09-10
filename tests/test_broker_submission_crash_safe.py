"""Focused crash-safe broker submission evidence (Y02c addendum).

Covers the persistence-owned state machine:
- ``prepared`` key is durable BEFORE the POST;
- ``accepted`` task ID + same key are durable BEFORE the first result GET;
- a stage found in ``prepared``/``accepted`` (crash residue) is converted to a
  durable quarantine without POST or GET;
- pre-Location ambiguity quarantines key-without-ID and blocks replay;
- post-Location ambiguity quarantines both and blocks new POST;
- transient broker classes are preserved with retry/due-time;
- explicit operator transition enables GET-only reconciliation with zero POST.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import select

from app.models.channel import Channel
from app.models.pipeline_stage import PipelineStage
from app.models.user import User
from app.models.video import Video
from app.services.broker_errors import BrokerSummarizationError
from app.services.broker_gateway import AcceptedTask, BrokerTaskClient
from app.services.broker_profile import load_y01_profile
from app.services.broker_traffic import BrokerTrafficSummarizationService
from app.services.pipeline import (
    STAGE_SUMMARY,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PENDING_RETRY,
    PipelineService,
)
from app.services.summarization_gateway import SummaryGatewayContext

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


def _task(task_id: str = "t-1") -> dict:
    return {**TASK, "id": task_id}


def _result(content: str = VALID) -> dict:
    return {
        "status": "succeeded",
        "result": {
            "content": content,
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        },
    }


def _response(status: int, *, json: object | None = None, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=json, headers=headers, request=httpx.Request("GET", "http://broker"))


def _traffic_service(handler) -> BrokerTrafficSummarizationService:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://broker.test")
    task_client = BrokerTaskClient.from_client(
        client,
        timeout=5,
        profile=load_y01_profile(),
        monotonic=lambda: 0.0,
        sleep=lambda _: None,
    )
    return BrokerTrafficSummarizationService(task_client, profile=load_y01_profile())


def _seed_stage(db_session, user, video, **overrides) -> PipelineStage:
    stage = PipelineStage(
        video_id=video.id,
        user_id=user.id,
        stage=STAGE_SUMMARY,
        **overrides,
    )
    db_session.add(stage)
    db_session.commit()
    return stage


@pytest.fixture
def user(db_session) -> User:
    u = User(email="crash@example.com")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def video(db_session, user) -> Video:
    channel = Channel(youtube_channel_id="UC-CRASH", title="Crash")
    v = Video(
        youtube_video_id="crash-video",
        channel=channel,
        title="Crash Video",
        transcript="un texto corto",
        published_at=datetime.now(UTC),
    )
    db_session.add_all([channel, v])
    db_session.commit()
    return v


def _fresh_video(db_session, user, suffix: str) -> Video:
    channel = Channel(youtube_channel_id=f"UC-CRASH-{suffix}", title=f"Crash {suffix}")
    v = Video(
        youtube_video_id=f"crash-video-{suffix}",
        channel=channel,
        title=f"Crash Video {suffix}",
        transcript="un texto corto",
        published_at=datetime.now(UTC),
    )
    db_session.add_all([channel, v])
    db_session.commit()
    return v


def test_accepted_post_persists_task_and_key_before_first_get(db_session, user, video) -> None:
    """The accepted task handle is durably stored before any result GET."""
    seen: list[str] = []
    stage_ids: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        if request.method == "POST":
            return _response(202, json=_task(), headers={"Location": "/v1/tasks/t-1"})
        # First GET: the stage must already carry task id + key.
        stage = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_SUMMARY,
            )
        )
        assert stage is not None
        stage_ids["task_id"] = stage.broker_task_id or ""
        stage_ids["status"] = stage.reconciliation_status or ""
        return _response(200, json=_result())

    service = _traffic_service(handler)
    try:
        pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
        stage = _seed_stage(db_session, user, video)
        assert pipeline._attempt_summary_stage(db_session, stage, video) is True
        db_session.commit()
    finally:
        service.close()

    assert seen == ["POST", "GET"]
    assert stage_ids["task_id"] == "t-1"
    assert stage_ids["status"] == "submission_accepted"
    persisted = db_session.scalar(
        select(PipelineStage).where(
            PipelineStage.video_id == video.id,
            PipelineStage.user_id == user.id,
            PipelineStage.stage == STAGE_SUMMARY,
        )
    )
    assert persisted is not None
    assert persisted.status == STATUS_COMPLETED
    assert video.summary == VALID
    assert persisted.reconciliation_status is None


def test_crash_residue_prepared_quarantines_key_only_without_any_request(db_session, user, video) -> None:
    """A stage left in ``prepared`` (key durable, no task ID) is a crash residue:
    convert to durable key-without-ID quarantine; never POST/GET again."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return _response(200, json=_result())

    service = _traffic_service(handler)
    try:
        pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
        stage = _seed_stage(
            db_session,
            user,
            video,
            status=STATUS_PENDING_RETRY,
            broker_idempotency_key="durable-key",
            reconciliation_status="submission_prepared",
            failure_class=None,
            failure_code=None,
        )
        assert pipeline._attempt_summary_stage(db_session, stage, video) is True
        db_session.commit()
    finally:
        service.close()

    assert seen == []
    assert stage.quarantined_at is not None
    assert stage.reconciliation_status == "awaiting_operator_resolution"
    assert stage.broker_task_id is None
    assert stage.broker_idempotency_key == "durable-key"
    assert stage.status == STATUS_PENDING_RETRY


def test_crash_residue_accepted_quarantines_both_without_any_request(db_session, user, video) -> None:
    """A stage left in ``accepted`` (task ID durable) is post-Location ambiguity:
    convert to durable quarantine of both, never POST/GET again."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return _response(200, json=_result())

    service = _traffic_service(handler)
    try:
        pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
        stage = _seed_stage(
            db_session,
            user,
            video,
            status=STATUS_PENDING_RETRY,
            broker_task_id="t-1",
            broker_idempotency_key="durable-key",
            reconciliation_status="submission_accepted",
        )
        assert pipeline._attempt_summary_stage(db_session, stage, video) is True
        db_session.commit()
    finally:
        service.close()

    assert seen == []
    assert stage.quarantined_at is not None
    assert stage.reconciliation_status == "awaiting_operator_resolution"
    assert stage.broker_task_id == "t-1"
    assert stage.broker_idempotency_key == "durable-key"
    assert stage.status == STATUS_PENDING_RETRY


def test_pre_location_transport_quarantines_key_without_id_and_blocks_replay(db_session, user, video) -> None:
    """A pre-Location transport ambiguity persists the key and no task ID, and
    must never re-POST (subsequent attempts are blocked by the quarantine)."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("down", request=request)

    service = _traffic_service(handler)
    try:
        pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
        stage = _seed_stage(db_session, user, video)
        assert pipeline._attempt_summary_stage(db_session, stage, video) is True
        db_session.commit()
        # Second attempt must be a no-op: quarantined stages never re-submit.
        assert pipeline._attempt_summary_stage(db_session, stage, video) is False
        db_session.commit()
    finally:
        service.close()

    assert calls == 1
    assert stage.quarantined_at is not None
    assert stage.reconciliation_status == "awaiting_operator_resolution"
    assert stage.broker_task_id is None
    assert stage.broker_idempotency_key is not None
    assert stage.status == STATUS_PENDING_RETRY
    assert stage.failure_class == "indeterminate"
    assert stage.failure_code == "broker_transport_error"


def test_post_location_transport_quarantines_both_and_blocks_new_post(db_session, user, video) -> None:
    """A post-Location transport failure persists both IDs, quarantines both,
    and never issues another POST."""
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "POST":
            return _response(202, json=_task(), headers={"Location": "/v1/tasks/t-1"})
        raise httpx.ConnectError("down", request=request)

    service = _traffic_service(handler)
    try:
        pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
        stage = _seed_stage(db_session, user, video)
        assert pipeline._attempt_summary_stage(db_session, stage, video) is True
        db_session.commit()
    finally:
        service.close()

    assert methods == ["POST", "GET"]
    assert stage.quarantined_at is not None
    assert stage.reconciliation_status == "awaiting_operator_resolution"
    assert stage.broker_task_id == "t-1"
    assert stage.broker_idempotency_key is not None
    assert stage.status == STATUS_PENDING_RETRY
    assert stage.failure_class == "indeterminate"
    assert stage.failure_code == "broker_transport_error"


def test_post_location_protocol_error_preserves_task_for_get_only_resolution(db_session, user, video) -> None:
    """A malformed accepted envelope is post-Location ambiguity, not a new POST."""
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "POST":
            malformed = {**_task(), "unexpected": "field"}
            return _response(202, json=malformed, headers={"Location": "/v1/tasks/t-1"})
        raise AssertionError("protocol failure must not GET an unvalidated task")

    service = _traffic_service(handler)
    try:
        pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
        stage = _seed_stage(db_session, user, video)
        assert pipeline._attempt_summary_stage(db_session, stage, video) is True
        db_session.commit()
    finally:
        service.close()

    assert methods == ["POST"]
    assert stage.status == STATUS_PENDING_RETRY
    assert stage.quarantined_at is not None
    assert stage.reconciliation_status == "awaiting_operator_resolution"
    assert stage.broker_task_id == "t-1"
    assert stage.failure_class == "indeterminate"
    assert stage.failure_code == "broker_protocol_error"


def test_post_location_deadline_quarantines_both_with_broker_timeout(db_session, user, video) -> None:
    """Polling until the deadline without a terminal result quarantines both
    with broker_timeout; no new POST is issued."""
    from app.services.broker_submission import BrokerSubmissionCoordinator

    methods: list[str] = []
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "POST":
            return _response(202, json=_task(), headers={"Location": "/v1/tasks/t-1"})
        return _response(200, json={"status": "pending"})

    service = _traffic_service(handler)
    try:
        stage = _seed_stage(db_session, user, video)
        coordinator = BrokerSubmissionCoordinator(
            service,
            timeout_seconds=2.0,
            clock=clock,
            sleep=sleep,
        )
        outcome = coordinator.attempt(
            db_session,
            stage,
            video,
            context=SummaryGatewayContext(stage_id=stage.id),
            attempted_at=datetime.now(UTC),
        )
        db_session.commit()
    finally:
        service.close()

    assert outcome.disposition == "quarantine"
    assert methods == ["POST", "GET"]
    assert stage.quarantined_at is not None
    assert stage.reconciliation_status == "awaiting_operator_resolution"
    assert stage.broker_task_id == "t-1"
    assert stage.status == STATUS_PENDING_RETRY
    assert stage.failure_code == "broker_timeout"
    assert stage.failure_class == "indeterminate"


def test_transient_broker_classes_preserved_with_retry_and_correlation(db_session, user, video) -> None:
    """transient_unsent / transient_safe stay on the stage with a retry
    disposition, due time, and the opaque broker correlation retained."""
    import pytest as _pytest

    for failure_class in ("transient_unsent", "transient_safe"):
        db_session.expire_all()

        def handler(request: httpx.Request, failure_class: str = failure_class) -> httpx.Response:
            if request.method == "POST":
                return _response(202, json=_task(), headers={"Location": "/v1/tasks/t-1"})
            return _response(
                200,
                json={
                    "status": "failed",
                    "error": {
                        "class": failure_class,
                        "code": "backend_dial_failed",
                        "message": "safe detail",
                    },
                },
            )

        service = _traffic_service(handler)
        try:
            pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
            stage = _seed_stage(db_session, user, video)
            assert pipeline._attempt_summary_stage(db_session, stage, video) is True
            db_session.commit()
        finally:
            service.close()

        assert stage.status == STATUS_PENDING_RETRY
        assert stage.failure_class == failure_class
        assert stage.next_attempt_at is not None
        assert stage.broker_task_id is None
        assert stage.broker_idempotency_key is None
        assert stage.broker_submission_epoch == 1
        assert stage.quarantined_at is None
        # cleanup for the next parametrized iteration
        db_session.delete(stage)
        db_session.commit()


def test_confirmed_safe_terminal_failure_creates_new_epoch_task(db_session, user, video) -> None:
    """A confirmed safe terminal task failure gets one fresh POST on retry."""
    methods: list[str] = []
    post_keys: list[str] = []
    get_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_count
        methods.append(request.method)
        if request.method == "POST":
            post_keys.append(request.headers["Idempotency-Key"])
            task_id = "t-1" if len(post_keys) == 1 else "t-2"
            return _response(202, json=_task(task_id), headers={"Location": f"/v1/tasks/{task_id}"})
        get_count += 1
        if get_count == 1:
            return _response(
                200,
                json={
                    "status": "failed",
                    "error": {
                        "class": "transient_safe",
                        "code": "backend_error",
                        "message": "safe detail",
                    },
                },
            )
        return _response(200, json=_result())

    service = _traffic_service(handler)
    try:
        pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
        stage = _seed_stage(db_session, user, video)
        assert pipeline._attempt_summary_stage(db_session, stage, video) is True
        assert stage.status == STATUS_PENDING_RETRY
        assert stage.broker_submission_epoch == 1
        assert stage.broker_task_id is None
        assert stage.broker_idempotency_key is None
        assert stage.broker_submission_history is not None
        assert stage.broker_submission_history[0]["epoch"] == 0
        assert "task_id" not in stage.broker_submission_history[0]
        assert "idempotency_key" not in stage.broker_submission_history[0]

        stage.next_attempt_at = None
        db_session.commit()
        assert pipeline._attempt_summary_stage(db_session, stage, video) is True
        db_session.commit()
    finally:
        service.close()

    assert methods == ["POST", "GET", "POST", "GET"]
    assert len(post_keys) == 2
    assert post_keys[0] != post_keys[1]
    assert stage.broker_submission_epoch == 1
    assert stage.status == STATUS_COMPLETED
    assert video.summary == VALID


def test_explicit_operator_transition_then_get_only_reconciliation(db_session, user, video) -> None:
    """After an explicit operator transition, reconciliation issues GETs only
    (zero POST) with bounded backoff, then completes the stage."""
    from app.services.broker_submission import BrokerSubmissionCoordinator

    methods: list[str] = []
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        methods.append(request.method)
        if request.method == "POST":
            return _response(202, json=_task(), headers={"Location": "/v1/tasks/t-1"})
        if request.method == "GET":
            poll_count += 1
            if poll_count == 1:
                return _response(200, json={"status": "pending"})
            return _response(200, json=_result())
        raise AssertionError("unexpected method")

    service = _traffic_service(handler)
    try:
        pipeline = PipelineService(summarization_service=service)  # type: ignore[arg-type]
        # Seed the quarantine directly (post-Location ambiguity outcome).
        stage = _seed_stage(
            db_session,
            user,
            video,
            status=STATUS_PENDING_RETRY,
            quarantined_at=datetime.now(UTC),
            reconciliation_status="awaiting_operator_resolution",
            broker_task_id="t-1",
            broker_idempotency_key="opaque-key",
            failure_class="indeterminate",
            failure_code="broker_timeout",
            reconciliation_reason="El broker no confirmó el resultado dentro del plazo.",
        )
        # Zero GET/POST while awaiting operator resolution.
        assert pipeline.reconcile_quarantined_summaries(db_session, user) == 0
        assert methods == []

        # Explicit operator transition (known-ID only).
        assert pipeline.make_quarantine_get_eligible(stage) is True
        db_session.commit()

        # GET-only reconciliation: first poll pending (backoff), then success.
        assert pipeline.reconcile_quarantined_summaries(db_session, user) == 0
        # The pending poll pushed the next reconciliation out; clear it to
        # simulate the bounded backoff elapsing, then a second GET completes.
        stage.next_reconcile_at = None
        db_session.commit()
        assert pipeline.reconcile_quarantined_summaries(db_session, user) == 1
        db_session.commit()
    finally:
        service.close()

    assert methods == ["GET", "GET"]
    assert video.summary == VALID
    assert stage.status == STATUS_COMPLETED
    assert stage.reconciliation_status is None
    assert stage.quarantined_at is None


def test_make_quarantine_get_eligible_rejects_unknown_id_and_wrong_state(db_session, user) -> None:
    pipeline = PipelineService()  # type: ignore[arg-type]
    no_id_video = _fresh_video(db_session, user, "no-id")
    no_id = _seed_stage(
        db_session,
        user,
        no_id_video,
        status=STATUS_PENDING_RETRY,
        quarantined_at=datetime.now(UTC),
        reconciliation_status="awaiting_operator_resolution",
        broker_task_id=None,
    )
    assert pipeline.make_quarantine_get_eligible(no_id) is False

    wrong_state_video = _fresh_video(db_session, user, "wrong-state")
    wrong_state = _seed_stage(
        db_session,
        user,
        wrong_state_video,
        status=STATUS_PENDING_RETRY,
        quarantined_at=datetime.now(UTC),
        reconciliation_status="get_eligible",
        broker_task_id="t-1",
    )
    assert pipeline.make_quarantine_get_eligible(wrong_state) is False

    not_quarantined_video = _fresh_video(db_session, user, "not-quarantined")
    not_quarantined = _seed_stage(
        db_session,
        user,
        not_quarantined_video,
        status=STATUS_PENDING_RETRY,
        broker_task_id="t-1",
        reconciliation_status="awaiting_operator_resolution",
    )
    assert pipeline.make_quarantine_get_eligible(not_quarantined) is False
