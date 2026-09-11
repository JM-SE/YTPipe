from datetime import UTC, datetime

from app.services.summary_failure_policy import classify_summary_failure
from app.services.telegram_failure_notice import format_summary_failure_notice


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
