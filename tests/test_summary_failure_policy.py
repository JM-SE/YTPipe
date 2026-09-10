from datetime import UTC, datetime, timedelta

from app.services.summary_failure_policy import classify_summary_failure


def test_direct_failure_is_bounded_and_due() -> None:
    now = datetime.now(UTC)
    outcome = classify_summary_failure(
        route="direct",
        code="direct_transport_error",
        attempt_count=1,
        max_attempts=3,
        now=now,
        base_delay_seconds=30,
    )
    assert outcome.disposition == "retry"
    assert outcome.retry_at == now + timedelta(seconds=30)
    assert outcome.recovery_owner == "ytpipe"


def test_last_direct_attempt_becomes_terminal() -> None:
    outcome = classify_summary_failure(
        route="direct",
        code="direct_timeout",
        attempt_count=3,
        max_attempts=3,
        now=datetime.now(UTC),
    )
    assert outcome.disposition == "terminal"
    assert outcome.retry_at is None


def test_broker_ambiguity_is_quarantined_without_runtime_recovery() -> None:
    outcome = classify_summary_failure(
        route="broker",
        code="broker_timeout",
        attempt_count=1,
        max_attempts=3,
        now=datetime.now(UTC),
    )
    assert outcome.disposition == "quarantine"
    assert outcome.recovery_owner == "operator"
    assert outcome.recovery_action == "broker_resolve"
    assert outcome.retry_at is None


def test_broker_invalid_location_is_quarantined_without_task_id() -> None:
    outcome = classify_summary_failure(
        route="broker",
        code="broker_location_invalid",
        attempt_count=1,
        max_attempts=3,
        now=datetime.now(UTC),
    )
    assert outcome.disposition == "quarantine"
    assert outcome.recovery_owner == "operator"
    assert outcome.recovery_action == "broker_resolve"


def test_broker_output_failure_is_terminal() -> None:
    outcome = classify_summary_failure(
        route="broker",
        code="broker_output_incomplete",
        attempt_count=1,
        max_attempts=3,
        now=datetime.now(UTC),
    )
    assert outcome.disposition == "terminal"
    assert outcome.recovery_owner == "broker"


def test_unknown_broker_class_is_conservatively_quarantined() -> None:
    outcome = classify_summary_failure(
        route="broker",
        code="provider_new_code",
        broker_failure_class="future_class",
        attempt_count=1,
        max_attempts=3,
        now=datetime.now(UTC),
    )
    assert outcome.disposition == "quarantine"
    assert outcome.failure_class == "future_class"


def test_broker_transient_classes_are_preserved_while_retrying() -> None:
    now = datetime.now(UTC)
    for failure_class in ("transient_unsent", "transient_safe"):
        outcome = classify_summary_failure(
            route="broker",
            code="broker_transport_error",
            broker_failure_class=failure_class,
            attempt_count=1,
            max_attempts=3,
            now=now,
        )
        assert outcome.disposition == "retry"
        assert outcome.failure_class == failure_class
        assert outcome.retry_at == now + timedelta(seconds=30)


def test_broker_transient_class_is_preserved_when_stage_budget_exhausts() -> None:
    for failure_class in ("transient_unsent", "transient_safe"):
        outcome = classify_summary_failure(
            route="broker",
            code="backend_error",
            broker_failure_class=failure_class,
            attempt_count=3,
            max_attempts=3,
            now=datetime.now(UTC),
        )
        assert outcome.disposition == "terminal"
        assert outcome.failure_class == failure_class
        assert outcome.failure_code == "backend_error"
