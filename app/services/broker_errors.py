from __future__ import annotations

from app.services.summarization import SummarizationRequestError

BROKER_ERROR_MESSAGE = "Broker summarization failed."


class BrokerSummarizationError(SummarizationRequestError):
    recovery_target = "none"

    def __init__(self, code: str = "broker_protocol_error"):
        super().__init__(BROKER_ERROR_MESSAGE)
        self.code = code


def broker_error(code: str) -> BrokerSummarizationError:
    return BrokerSummarizationError(code)
