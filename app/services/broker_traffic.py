from __future__ import annotations

from app.services.broker_errors import BrokerSummarizationError, broker_error
from app.services.broker_gateway import AcceptedTask, BrokerOperation, BrokerResult, BrokerTaskClient
from app.services.broker_profile import BrokerRequestProfile, load_y01_profile
from app.services.broker_summary import validate_broker_output
from app.services.summarization import FINAL_SUMMARY_INSTRUCTIONS, SUMMARIZATION_SYSTEM_PROMPT
from app.services.broker_idempotency import broker_idempotency_key
from app.services.summarization_gateway import SummaryGatewayContext

TRAFFIC_OPERATION_KIND = "traffic"
TRAFFIC_OPERATION_ORDINAL = 0


class BrokerTrafficSummarizationService:
    """Pipeline traffic adapter over the broker task client.

    Mirrors the manual probe's submit/validate semantics (Y01 profile,
    stop-only finish reason, strict Spanish oracle) but derives idempotency
    from the pipeline stage context instead of a probe id. Exactly one
    operation per transcript; oversized requests fail closed before any
    submission. Long transcripts that exceed the frozen request-content
    budget fail closed rather than being chunked: chunked aggregation is
    deliberately out of scope so canary behavior stays observable and
    abort criteria apply.

    Re-attempts after a terminal outcome require a new stage row (a new
    `stage_id`): the idempotency key re-derives identically for the same
    stage by design, so resubmitting the same stage is idempotent replay,
    never a new invocation.

    Two-phase contract for the pipeline persistence owner:
    ``submit_task`` performs the POST only and returns an accepted-task
    handle (or a validated sync result); ``poll_task`` performs a single
    GET. The adapter never polls on its own for the pipeline path, never
    performs session I/O, and never owns durable transitions.
    """

    def __init__(self, task_client: BrokerTaskClient, *, profile: BrokerRequestProfile | None = None):
        self._client = task_client
        self._profile = profile or load_y01_profile()

    def close(self) -> None:
        self._client.close()

    @property
    def timeout_seconds(self) -> float:
        return self._client.timeout_seconds

    def idempotency_key_for(self, transcript: str, context: SummaryGatewayContext) -> str:
        operation = self._operation(transcript)
        return broker_idempotency_key(context, operation)

    def submit_task(self, transcript: str, *, context: SummaryGatewayContext) -> AcceptedTask:
        """POST only. Return an accepted handle; the coordinator owns polling."""
        if context is None:
            raise broker_error("broker_context_missing")
        operation = self._operation(transcript)
        self._assert_request_size(operation)
        try:
            accepted = self._client.submit_task(operation, broker_idempotency_key(context, operation))
        except BrokerSummarizationError:
            raise
        except Exception:
            raise broker_error(
                "broker_task_indeterminate",
                idempotency_key=broker_idempotency_key(context, operation),
            ) from None
        if not isinstance(accepted, AcceptedTask):
            raise broker_error("broker_protocol_error")
        return accepted

    def poll_task(
        self,
        task_id: str,
        *,
        idempotency_key_value: str,
        timeout: float | None = None,
    ) -> str | None:
        """Single GET result poll; None while the task is still pending."""
        try:
            result = self._client.poll_result(task_id, idempotency_key_value, timeout=timeout)
        except BrokerSummarizationError:
            raise
        except Exception:
            raise broker_error(
                "broker_task_indeterminate",
                task_id=task_id,
                idempotency_key=idempotency_key_value,
            ) from None
        if result is None:
            return None
        return self._validated_result(result)

    def summarize(self, transcript: str, *, context: SummaryGatewayContext | None = None) -> str:
        """Legacy blocking convenience (probes/tests only).

        The pipeline persistence owner uses ``submit_task``/``poll_task`` so
        the accepted task handle is durably stored before the first GET. This
        fused method exists for non-pipeline callers and preserves the client's
        injected clock/sleep semantics.
        """
        if context is None:
            raise broker_error("broker_context_missing")
        operation = self._operation(transcript)
        self._assert_request_size(operation)
        try:
            result = self._client.submit_result(operation, broker_idempotency_key(context, operation))
        except BrokerSummarizationError:
            raise
        except Exception:
            raise broker_error("broker_error") from None
        return self._validated_result(result)

    def reconcile(self, task_id: str, *, idempotency_key: str | None = None) -> str | None:
        """Reconcile an operator-resolved task using GET only."""
        result = self._client.reconcile_result(task_id, idempotency_key=idempotency_key)
        if result is None:
            return None
        return self._validated_result(result)

    def _operation(self, transcript: str) -> BrokerOperation:
        return BrokerOperation(
            TRAFFIC_OPERATION_KIND,
            TRAFFIC_OPERATION_ORDINAL,
            SUMMARIZATION_SYSTEM_PROMPT,
            FINAL_SUMMARY_INSTRUCTIONS + "\n\nTRANSCRIPCION:\n\n" + transcript,
            self._profile.max_tokens,
        )

    def _assert_request_size(self, operation: BrokerOperation) -> None:
        request_bytes = len(operation.system_prompt.encode("utf-8")) + len(operation.user_prompt.encode("utf-8"))
        if request_bytes > self._profile.max_request_content_bytes:
            raise broker_error("broker_input_too_large")

    def _validated_result(self, result: BrokerResult) -> str:
        if result.finish_reason != self._profile.accepted_finish_reason:
            if result.finish_reason == "length":
                raise broker_error("broker_output_incomplete")
            raise broker_error("broker_output_invalid")
        try:
            return validate_broker_output(result.content)
        except ValueError:
            raise broker_error("broker_output_invalid") from None
