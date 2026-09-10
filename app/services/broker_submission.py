from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pipeline_stage import PipelineStage
from app.models.video import Video
from app.services.broker_errors import BrokerSummarizationError
from app.services.broker_gateway import AcceptedTask
from app.services.broker_safe_retry import can_advance_submission_epoch
from app.services.summarization_gateway import SummaryGatewayContext
from app.services.summary_failure_policy import SummaryFailureOutcome, classify_summary_failure

logger = logging.getLogger(__name__)

# Durable submission-state markers. `reconciliation_status` carries the
# crash-safe state machine: prepared (key durable, before POST) and accepted
# (task ID durable, before the first result GET). Any stage found in these
# states during a later attempt is a crash residue: it must be converted to a
# durable quarantine (operator-controlled) instead of re-submitting or
# guessing, because the broker outcome is unknown.
RECONCILIATION_PREPARED = "submission_prepared"
RECONCILIATION_ACCEPTED = "submission_accepted"
RECONCILIATION_AWAITING = "awaiting_operator_resolution"

STATUS_PENDING_RETRY = "pending_retry"
STATUS_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BrokerSubmissionOutcome:
    disposition: Literal["completed", "terminal", "quarantine", "retry"]
    summary: str | None = None
    failure_class: str | None = None
    failure_code: str | None = None
    display_reason: str | None = None
    retry_at: datetime | None = None


class BrokerSubmissionCoordinator:
    """Persistence-owned, crash-safe broker submission state machine.

    Owns the durable transitions of one summary stage across the broker task
    protocol:

    - ``prepared``: the deterministic idempotency key and a conservative
      pre-submit marker are persisted and committed BEFORE the POST.
    - ``accepted``: after a validated ``Location``, the task ID and the same
      key are persisted and committed BEFORE the first result GET.
    - only then may ``polling``/``reconcile`` occur (GET only).

    A stage found already in ``prepared``/``accepted`` is a crash residue and
    is converted to a durable quarantine without submitting or polling:
    ``prepared`` keeps key-without-ID (pre-Location ambiguity), ``accepted``
    keeps both (post-Location ambiguity). Opaque IDs/keys never reach user
    messages or logs here.
    """

    def __init__(
        self,
        gateway: object,
        *,
        timeout_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._gateway = gateway
        self._timeout_seconds = timeout_seconds or getattr(gateway, "timeout_seconds", 300.0)
        self._clock = clock
        self._sleep = sleep

    def attempt(
        self,
        session: Session,
        stage: PipelineStage,
        video: Video,
        *,
        context: SummaryGatewayContext,
        attempted_at: datetime,
    ) -> BrokerSubmissionOutcome:
        status = getattr(stage, "reconciliation_status", None)
        if status == RECONCILIATION_PREPARED:
            return self._quarantine(
                session,
                stage,
                attempted_at,
                code="broker_timeout",
                broker_class="indeterminate",
                key=getattr(stage, "broker_idempotency_key", None),
            )
        if status == RECONCILIATION_ACCEPTED:
            return self._quarantine(
                session,
                stage,
                attempted_at,
                code="broker_timeout",
                broker_class="indeterminate",
                key=getattr(stage, "broker_idempotency_key", None),
                task_id=getattr(stage, "broker_task_id", None),
            )

        key_builder = getattr(self._gateway, "idempotency_key_for", None)
        if not callable(key_builder):
            raise BrokerSummarizationError("broker_context_missing")
        key = key_builder(video.transcript, context)
        stage.broker_idempotency_key = key[:128]
        stage.reconciliation_status = RECONCILIATION_PREPARED
        session.flush()
        session.commit()

        try:
            accepted = self._gateway.submit_task(video.transcript, context=context)
        except Exception as exc:
            return self._classify(session, stage, exc, attempted_at, key=key)
        if not isinstance(accepted, AcceptedTask):
            return self._classify(
                session,
                stage,
                BrokerSummarizationError("broker_protocol_error"),
                attempted_at,
                key=key,
            )

        task_id = accepted.task_id
        stage.broker_task_id = task_id[:128]
        stage.broker_idempotency_key = (accepted.idempotency_key or key)[:128]
        stage.reconciliation_status = RECONCILIATION_ACCEPTED
        session.flush()
        session.commit()

        deadline = self._clock() + self._timeout_seconds
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                return self._quarantine(
                    session,
                    stage,
                    attempted_at,
                    code="broker_timeout",
                    broker_class="indeterminate",
                    key=accepted.idempotency_key or key,
                    task_id=task_id,
                )
            self._sleep(min(1.0, remaining))
            remaining = deadline - self._clock()
            if remaining <= 0:
                return self._quarantine(
                    session,
                    stage,
                    attempted_at,
                    code="broker_timeout",
                    broker_class="indeterminate",
                    key=accepted.idempotency_key or key,
                    task_id=task_id,
                )
            try:
                result = self._gateway.poll_task(
                    task_id,
                    idempotency_key_value=accepted.idempotency_key or key,
                    timeout=remaining,
                )
            except Exception as exc:
                return self._classify(
                    session,
                    stage,
                    exc,
                    attempted_at,
                    key=accepted.idempotency_key or key,
                    task_id=task_id,
                )
            if result is None:
                continue
            return self._complete(session, stage, video, result)

    def _classify(
        self,
        session: Session,
        stage: PipelineStage,
        exc: Exception,
        attempted_at: datetime,
        *,
        key: str | None,
        task_id: str | None = None,
    ) -> BrokerSubmissionOutcome:
        task_id = task_id or getattr(exc, "task_id", None)
        key = key or getattr(exc, "idempotency_key", None)
        code = getattr(exc, "broker_code", None) or getattr(exc, "code", None) or "broker_protocol_error"
        broker_class = getattr(exc, "failure_class", None)
        if task_id:
            stage.broker_task_id = task_id[:128]
        if key:
            stage.broker_idempotency_key = key[:128]
        if task_id and getattr(exc, "code", None) == "broker_protocol_error":
            # A valid Location followed by an invalid task envelope is
            # post-Location ambiguity: retain the task correlation and allow
            # only the explicit GET-only reconciliation workflow.
            outcome = SummaryFailureOutcome(
                route="broker",
                failure_class="indeterminate",
                failure_code="broker_protocol_error",
                disposition="quarantine",
                retry_at=None,
                recovery_owner="operator",
                recovery_action="broker_resolve",
                display_reason="El broker devolvió una respuesta incompatible.",
            )
        else:
            outcome = classify_summary_failure(
                route="broker",
                code=code,
                attempt_count=stage.attempt_count,
                max_attempts=stage.max_attempts,
                now=attempted_at,
                broker_failure_class=broker_class,
            )
        return self._apply_outcome(
            session,
            stage,
            outcome,
            attempted_at,
            task_id=task_id,
            terminal_confirmed=getattr(exc, "code", None) == "broker_task_failed",
        )

    def _quarantine(
        self,
        session: Session,
        stage: PipelineStage,
        attempted_at: datetime,
        *,
        code: str,
        broker_class: str,
        key: str | None,
        task_id: str | None = None,
    ) -> BrokerSubmissionOutcome:
        if task_id:
            stage.broker_task_id = task_id[:128]
        if key:
            stage.broker_idempotency_key = key[:128]
        outcome = classify_summary_failure(
            route="broker",
            code=code,
            attempt_count=stage.attempt_count,
            max_attempts=stage.max_attempts,
            now=attempted_at,
            broker_failure_class=broker_class,
        )
        return self._apply_outcome(
            session, stage, outcome, attempted_at, task_id=task_id, terminal_confirmed=False
        )

    def _apply_outcome(
        self,
        session: Session,
        stage: PipelineStage,
        outcome: SummaryFailureOutcome,
        attempted_at: datetime,
        *,
        task_id: str | None,
        terminal_confirmed: bool,
    ) -> BrokerSubmissionOutcome:
        if terminal_confirmed and task_id:
            # Serialize terminal handling across processes. The process-local
            # inference lock is not sufficient when more than one API worker
            # can drain the same stage.
            with session.no_autoflush:
                locked_stage = session.scalar(
                    select(PipelineStage)
                    .where(PipelineStage.id == stage.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            if locked_stage is None or locked_stage.broker_task_id != task_id:
                # Another worker already handled this terminal task. Do not
                # overwrite its epoch, retry generation, or audit history.
                return BrokerSubmissionOutcome(
                    disposition=outcome.disposition,
                    failure_class=outcome.failure_class,
                    failure_code=outcome.failure_code,
                    display_reason=outcome.display_reason,
                    retry_at=outcome.retry_at,
                )
            stage = locked_stage

        stage.failure_class = outcome.failure_class
        stage.failure_code = outcome.failure_code
        stage.last_error = outcome.display_reason
        stage.next_attempt_at = outcome.retry_at
        if terminal_confirmed and task_id and outcome.failure_class in {"transient_safe", "transient_unsent"}:
            history = list(stage.broker_submission_history or [])
            history.append(
                {
                    "epoch": stage.broker_submission_epoch,
                    "failure_class": outcome.failure_class,
                    "failure_code": outcome.failure_code,
                    "reason": outcome.display_reason,
                    "observed_at": attempted_at.isoformat(),
                }
            )
            stage.broker_submission_history = history[-max(1, stage.max_attempts) :]
        if can_advance_submission_epoch(
            route="broker",
            terminal_confirmed=terminal_confirmed,
            task_id=task_id,
            current_task_id=getattr(stage, "broker_task_id", None),
            failure_class=outcome.failure_class,
            attempt_count=stage.attempt_count,
            max_attempts=stage.max_attempts,
            current_epoch=getattr(stage, "broker_submission_epoch", 0),
        ):
            stage.broker_submission_epoch += 1
            # The old terminal task is no longer the active correlation. The
            # typed failure fields above retain the historical audit outcome;
            # the next due attempt writes its new PREPARED key before POST.
            stage.broker_task_id = None
            stage.broker_idempotency_key = None
        if outcome.disposition == "quarantine":
            stage.quarantined_at = attempted_at
            stage.reconciliation_status = RECONCILIATION_AWAITING
            stage.next_reconcile_at = None
            stage.reconciliation_reason = outcome.display_reason
            stage.status = STATUS_PENDING_RETRY
        elif outcome.disposition == "terminal":
            stage.status = STATUS_FAILED
            stage.reconciliation_status = None
        else:
            # transient retry: keep the stage per-video retryable and due, with
            # the opaque broker correlation retained for observability.
            stage.status = STATUS_PENDING_RETRY
            stage.reconciliation_status = None
        session.flush()
        session.commit()
        return BrokerSubmissionOutcome(
            disposition=outcome.disposition,
            failure_class=outcome.failure_class,
            failure_code=outcome.failure_code,
            display_reason=outcome.display_reason,
            retry_at=outcome.retry_at,
        )

    def _complete(
        self,
        session: Session,
        stage: PipelineStage,
        video: Video,
        summary: str,
    ) -> BrokerSubmissionOutcome:
        video.summary = summary
        stage.status = "completed"
        stage.last_error = None
        stage.failure_class = None
        stage.failure_code = None
        stage.next_attempt_at = None
        stage.quarantined_at = None
        stage.reconciliation_status = None
        stage.next_reconcile_at = None
        stage.reconciliation_reason = None
        session.flush()
        session.commit()
        return BrokerSubmissionOutcome(disposition="completed", summary=summary)
