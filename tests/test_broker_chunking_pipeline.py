from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.channel import Channel
from app.models.pipeline_stage import PipelineStage
from app.models.user import User
from app.models.video import Video
from app.services.broker_errors import BrokerSummarizationError
from app.services.broker_gateway import AcceptedTask, BrokerResult
from app.services.broker_submission import BrokerSubmissionCoordinator
from app.services.pipeline import PipelineService, STATUS_COMPLETED, STATUS_PENDING_RETRY
from app.services.summarization_gateway import SummaryGatewayContext, SummaryOperation


VALID_SUMMARY = """RESUMEN
El material presenta el tema principal y sus argumentos.

PUNTOS CLAVE
• Primer punto factual del material.
• Segundo punto factual del material.
• Tercer punto factual del material.
• Cuarto punto factual del material.

CONCLUSIÓN
La conclusión resume las implicaciones expresadas en el material.
"""
VALID_PARTIAL = """• Primer hecho del fragmento.
• Segundo hecho del fragmento.
• Tercer hecho del fragmento.
• Cuarto hecho del fragmento.
• Quinto hecho del fragmento.
"""


class ImmediateBroker:
    timeout_seconds = 360.0

    def __init__(self) -> None:
        self.operations: list[SummaryOperation] = []

    def operation_for(self, *, kind: str, ordinal: int, user_prompt: str) -> SummaryOperation:
        return SummaryOperation(kind, ordinal, "system", user_prompt, 1)

    def idempotency_key_for(self, transcript: str, context: SummaryGatewayContext) -> str:
        return "legacy"

    def idempotency_key_for_operation(self, operation: SummaryOperation, context: SummaryGatewayContext) -> str:
        return f"{operation.kind}-{operation.ordinal}"

    def submit_task(self, transcript: str, *, context: SummaryGatewayContext, operation=None, timeout=None) -> AcceptedTask:
        self.operations.append(operation)
        return AcceptedTask(f"task-{len(self.operations)}", f"key-{len(self.operations)}")

    def poll_task(self, task_id: str, *, idempotency_key_value: str, timeout=None, operation_kind=None) -> str:
        return VALID_PARTIAL if operation_kind == "traffic_partial" else VALID_SUMMARY


class FailOnceBroker(ImmediateBroker):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def submit_task(self, transcript: str, *, context: SummaryGatewayContext, operation=None, timeout=None) -> AcceptedTask:
        if not self.failed and operation.ordinal == 1:
            self.failed = True
            raise BrokerSummarizationError("broker_transport_error", failure_class="transient_unsent")
        return super().submit_task(transcript, context=context, operation=operation, timeout=timeout)

    def reconcile(self, task_id: str, *, idempotency_key=None, operation_kind=None) -> str:
        return VALID_PARTIAL if operation_kind == "traffic_partial" else VALID_SUMMARY


@pytest.fixture
def user(db_session) -> User:
    value = User(email="chunk-test@example.com")
    db_session.add(value)
    db_session.commit()
    return value


@pytest.fixture
def video(db_session, user) -> Video:
    channel = Channel(youtube_channel_id="UC-CHUNK", title="Chunk", uploads_playlist_id="UPL-CHUNK")
    db_session.add(channel)
    db_session.flush()
    value = Video(
        youtube_video_id="chunk-video",
        channel_id=channel.id,
        title="Chunk video",
        published_at=datetime.now(UTC),
    )
    db_session.add(value)
    db_session.commit()
    return value


def test_chunked_pipeline_runs_partials_then_final(db_session, user, video) -> None:
    video.transcript = "palabra " * 1_300
    db_session.commit()
    stage = PipelineStage(video_id=video.id, user_id=user.id, stage="summary", status=STATUS_PENDING_RETRY)
    db_session.add(stage)
    db_session.commit()
    gateway = ImmediateBroker()
    pipeline = PipelineService(
        summarization_service=gateway,
        broker_chunking_enabled=True,
        broker_llm_context_tokens=2048,
        broker_chunk_chars=1000,
        broker_chunk_overlap_chars=100,
            broker_max_chunks=15,
        broker_max_tokens=1,
    )

    pipeline._attempt_broker_summary_stage(db_session, stage, video, datetime.now(UTC))

    assert len(gateway.operations) > 2
    assert gateway.operations[-1].kind == "traffic_final"
    assert all(operation.kind == "traffic_partial" for operation in gateway.operations[:-1])
    assert stage.status == STATUS_COMPLETED
    assert video.summary == VALID_SUMMARY
    assert stage.broker_chunk_plan is None
    assert stage.attempt_count == 0


def test_coordinator_partial_completion_does_not_complete_video(db_session, user, video) -> None:
    gateway = ImmediateBroker()
    stage = PipelineStage(video_id=video.id, user_id=user.id, stage="summary", status=STATUS_PENDING_RETRY)
    db_session.add(stage)
    db_session.commit()
    completed: list[str] = []
    coordinator = BrokerSubmissionCoordinator(gateway, sleep=lambda _: None)

    outcome = coordinator.attempt(
        db_session,
        stage,
        video,
        context=SummaryGatewayContext(stage_id=stage.id),
        attempted_at=datetime.now(UTC),
        operation=SummaryOperation("traffic_partial", 0, "system", "partial", 1),
        finalize_stage=False,
        on_completed=completed.append,
    )

    assert outcome.disposition == "completed"
    assert completed == [VALID_PARTIAL]
    assert stage.status == STATUS_PENDING_RETRY
    assert video.summary is None
    assert stage.broker_task_id is None
    assert stage.broker_idempotency_key is None


def test_chunked_pipeline_resumes_after_failed_step_without_repeating_partial(db_session, user, video) -> None:
    video.transcript = "palabra " * 1_300
    db_session.commit()
    stage = PipelineStage(video_id=video.id, user_id=user.id, stage="summary", status=STATUS_PENDING_RETRY)
    db_session.add(stage)
    db_session.commit()
    gateway = FailOnceBroker()
    pipeline = PipelineService(
        summarization_service=gateway,
        broker_chunking_enabled=True,
        broker_llm_context_tokens=2048,
        broker_chunk_chars=1000,
        broker_chunk_overlap_chars=100,
        broker_max_chunks=15,
        broker_max_tokens=1,
    )

    pipeline._attempt_broker_summary_stage(db_session, stage, video, datetime.now(UTC))
    first_plan = stage.broker_chunk_plan
    assert first_plan is not None
    assert first_plan["next_step"] == 1
    assert gateway.operations[0].ordinal == 0
    assert stage.attempt_count == 1

    stage.attempt_count = 1
    db_session.commit()
    pipeline._attempt_broker_summary_stage(db_session, stage, video, datetime.now(UTC))

    assert gateway.operations[1].ordinal == 1
    assert gateway.operations[-1].kind == "traffic_final"
    assert stage.status == STATUS_COMPLETED
    assert stage.attempt_count == 1


def test_partial_reconciliation_advances_plan_without_completing_video(db_session, user, video) -> None:
    video.transcript = "palabra " * 1_300
    db_session.commit()
    config_pipeline = PipelineService(
        broker_chunking_enabled=True,
        broker_llm_context_tokens=2048,
        broker_chunk_chars=1000,
        broker_chunk_overlap_chars=100,
        broker_max_chunks=15,
        broker_max_tokens=1,
    )
    plan = config_pipeline.broker_chunk_config
    from app.services.broker_chunking import build_plan

    persisted_plan = build_plan(video.transcript, plan)
    assert persisted_plan is not None
    stage = PipelineStage(
        video_id=video.id,
        user_id=user.id,
        stage="summary",
        status=STATUS_PENDING_RETRY,
        broker_task_id="task-known",
        broker_idempotency_key="key-known",
        broker_chunk_plan=persisted_plan,
        quarantined_at=datetime.now(UTC),
        reconciliation_status="get_eligible",
    )
    db_session.add(stage)
    db_session.commit()
    gateway = FailOnceBroker()
    pipeline = PipelineService(summarization_service=gateway)

    assert pipeline.reconcile_quarantined_summaries(db_session, user) == 0
    assert stage.broker_chunk_plan["next_step"] == 1
    assert stage.status == STATUS_PENDING_RETRY
    assert video.summary is None
    assert stage.broker_task_id is None
