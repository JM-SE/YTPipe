from app.services.broker_idempotency import broker_idempotency_key
from app.services.summarization_gateway import SummaryGatewayContext, SummaryOperation, idempotency_key


def _operation() -> SummaryOperation:
    return SummaryOperation("traffic", 0, "system", "user", 1024)


def test_epoch_zero_preserves_legacy_key_fixture() -> None:
    context = SummaryGatewayContext(stage_id=42)
    operation = SummaryOperation("direct-final", 0, "s", "u", 1)
    assert broker_idempotency_key(context, operation) == idempotency_key(context, operation)
    assert broker_idempotency_key(context, operation) == "bc564a0eebf1d46ad06aaf4baed5abce0c3ec1978901c560c8c6b21446edd1e3"


def test_positive_epochs_are_deterministic_and_distinct() -> None:
    operation = _operation()
    first = broker_idempotency_key(SummaryGatewayContext(42, 1), operation)
    assert first == broker_idempotency_key(SummaryGatewayContext(42, 1), operation)
    assert first != broker_idempotency_key(SummaryGatewayContext(42, 2), operation)
    assert first != broker_idempotency_key(SummaryGatewayContext(43, 1), operation)
