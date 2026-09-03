from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

CONTRACT_VERSION = "ytpipe-summary-v1"


@dataclass(frozen=True, slots=True)
class SummaryGatewayContext:
    stage_id: int


@dataclass(frozen=True, slots=True)
class SummaryOperation:
    kind: str
    ordinal: int
    system_prompt: str
    user_prompt: str
    max_tokens: int
    stop: tuple[str, ...] = ()


class SummarizationGateway(Protocol):
    def summarize(self, transcript: str, *, context: SummaryGatewayContext | None = None) -> str | None: ...


def idempotency_key(context: SummaryGatewayContext, operation: SummaryOperation) -> str:
    fields = (CONTRACT_VERSION, str(context.stage_id), operation.kind, str(operation.ordinal))
    encoded = b"".join(len(value.encode()).to_bytes(4, "big") + value.encode() for value in fields)
    return sha256(encoded).hexdigest()
