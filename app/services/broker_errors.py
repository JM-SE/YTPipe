from __future__ import annotations

from app.services.summarization import SummarizationRequestError

BROKER_ERROR_MESSAGE = "Broker summarization failed."


class BrokerSummarizationError(SummarizationRequestError):
    recovery_target = "none"

    def __init__(
        self,
        code: str = "broker_protocol_error",
        *,
        failure_class: str | None = None,
        broker_code: str | None = None,
        task_id: str | None = None,
        idempotency_key: str | None = None,
    ):
        super().__init__(BROKER_ERROR_MESSAGE)
        self.code = code
        self.failure_class = failure_class
        self.broker_code = broker_code
        self.task_id = task_id
        self.idempotency_key = idempotency_key


def broker_error(
    code: str,
    *,
    failure_class: str | None = None,
    broker_code: str | None = None,
    task_id: str | None = None,
    idempotency_key: str | None = None,
) -> BrokerSummarizationError:
    return BrokerSummarizationError(
        code,
        failure_class=failure_class,
        broker_code=broker_code,
        task_id=task_id,
        idempotency_key=idempotency_key,
    )
