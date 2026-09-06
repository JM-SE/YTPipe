from __future__ import annotations

from app.services.broker_errors import BrokerSummarizationError, broker_error
from app.services.broker_gateway import BrokerOperation, BrokerResult, BrokerTaskClient
from app.services.broker_profile import BrokerRequestProfile, load_y01_profile
from app.services.broker_summary import validate_broker_output
from app.services.summarization import FINAL_SUMMARY_INSTRUCTIONS, SUMMARIZATION_SYSTEM_PROMPT
from app.services.summarization_gateway import SummaryGatewayContext, idempotency_key

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
    """

    def __init__(self, task_client: BrokerTaskClient, *, profile: BrokerRequestProfile | None = None):
        self._client = task_client
        self._profile = profile or load_y01_profile()

    def close(self) -> None:
        self._client.close()

    def summarize(self, transcript: str, *, context: SummaryGatewayContext | None = None) -> str:
        if context is None:
            raise broker_error("broker_context_missing")
        operation = BrokerOperation(
            TRAFFIC_OPERATION_KIND,
            TRAFFIC_OPERATION_ORDINAL,
            SUMMARIZATION_SYSTEM_PROMPT,
            FINAL_SUMMARY_INSTRUCTIONS + "\n\nTRANSCRIPCION:\n\n" + transcript,
            self._profile.max_tokens,
        )
        request_bytes = len(operation.system_prompt.encode("utf-8")) + len(operation.user_prompt.encode("utf-8"))
        if request_bytes > self._profile.max_request_content_bytes:
            raise broker_error("broker_input_too_large")
        try:
            result = self._client.submit_result(operation, idempotency_key(context, operation))
        except BrokerSummarizationError:
            raise
        except Exception as exc:
            raise broker_error("broker_error") from None
        if not isinstance(result, BrokerResult):
            raise broker_error("broker_protocol_error")
        if result.finish_reason != self._profile.accepted_finish_reason:
            if result.finish_reason == "length":
                raise broker_error("broker_output_incomplete")
            raise broker_error("broker_output_invalid")
        try:
            return validate_broker_output(result.content)
        except ValueError:
            raise broker_error("broker_output_invalid") from None
