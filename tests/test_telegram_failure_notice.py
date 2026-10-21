from datetime import UTC, datetime

from app.services.summary_failure_policy import classify_summary_failure
from app.services.telegram_failure_notice import (
    format_persisted_summary_failure,
    format_summary_failure_notice,
)


def test_context_failure_notice_includes_safe_reason_and_actual_attempts() -> None:
    outcome = classify_summary_failure(
        route="broker",
        code="backend_context_exceeded",
        broker_failure_class="backend_rejected",
        attempt_count=1,
        max_attempts=3,
        now=datetime.now(UTC),
    )

    message = format_summary_failure_notice(
        outcome,
        "Fable Vs Astra Debate Is Over",
        attempt_count=1,
        max_attempts=3,
    )

    assert "El transcript excede el límite de contexto del modelo." in message
    assert "Intentos realizados: 1 de 3." in message
    assert "request (18672 tokens)" not in message


def test_incomplete_output_notice_explains_token_limit_without_promising_retry() -> None:
    outcome = classify_summary_failure(
        route="broker",
        code="broker_output_incomplete",
        attempt_count=1,
        max_attempts=3,
        now=datetime.now(UTC),
    )
    expected = (
        "Resumen no disponible.\n"
        "Motivo: El modelo alcanzó el límite de tokens de salida antes de completar el resumen.\n"
        "Estado: No se reintentará automáticamente.\n"
        "Intentos realizados: 1 de 3."
    )

    assert format_summary_failure_notice(
        outcome, attempt_count=1, max_attempts=3,
    ) == expected
    assert format_persisted_summary_failure(
        failure_code="broker_output_incomplete",
        quarantined=False,
        attempt_count=1,
        max_attempts=3,
    ) == expected
    assert outcome.disposition == "terminal"
    assert outcome.failure_code == "broker_output_incomplete"
    assert outcome.retry_at is None
    assert outcome.recovery_action == "none"
