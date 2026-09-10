"""Deterministic broker idempotency keys for retry generations."""

from __future__ import annotations

from hashlib import sha256

from app.services.summarization_gateway import SummaryGatewayContext, SummaryOperation, idempotency_key


EPOCH_KEY_VERSION = "ytpipe-broker-epoch-v1"


def broker_idempotency_key(context: SummaryGatewayContext, operation: SummaryOperation) -> str:
    """Return the legacy key for epoch zero and a tagged key thereafter."""
    epoch = context.broker_submission_epoch
    if epoch == 0:
        return idempotency_key(context, operation)
    if epoch < 0:
        raise ValueError("broker submission epoch must be non-negative")

    fields = (
        ("version", EPOCH_KEY_VERSION),
        ("stage_id", str(context.stage_id)),
        ("operation_kind", operation.kind),
        ("operation_ordinal", str(operation.ordinal)),
        ("epoch", str(epoch)),
    )
    encoded = b"".join(
        len(name.encode()).to_bytes(4, "big")
        + name.encode()
        + len(value.encode()).to_bytes(4, "big")
        + value.encode()
        for name, value in fields
    )
    return sha256(encoded).hexdigest()
