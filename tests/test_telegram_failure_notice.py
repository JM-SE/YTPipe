from dataclasses import replace
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
    assert "Intentos realizados: 1 de 3." not in message
    assert "Código: backend_context_exceeded" in message
    assert "Video: https://www.youtube.com/watch?v=dQw4w9WgXcQ" in format_summary_failure_notice(
        outcome, video_reference="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    assert "request (18672 tokens)" not in message


def test_unknown_diagnostic_codes_fall_back_without_leaking_untrusted_text() -> None:
    outcome = classify_summary_failure(
        route="direct",
        code="direct_transport_error",
        attempt_count=1,
        max_attempts=3,
        now=datetime.now(UTC),
    )
    unsafe_codes = ("unknown_secret_value", "unknown_code\nraw-secret-value")

    for code in unsafe_codes:
        fresh = format_summary_failure_notice(replace(outcome, failure_code=code))
        persisted = format_persisted_summary_failure(
            failure_code=code,
            quarantined=False,
        )
        for message in (fresh, persisted):
            assert "Código: summary_failure" in message
            assert code not in message
            assert "raw-secret-value" not in message
            assert "unknown_secret_value" not in message


def test_retry_notice_keeps_actual_retry_policy_and_attempt_count() -> None:
    outcome = classify_summary_failure(
        route="direct",
        code="direct_transport_error",
        attempt_count=1,
        max_attempts=3,
        now=datetime.now(UTC),
    )

    message = format_summary_failure_notice(outcome, attempt_count=1, max_attempts=3)

    assert outcome.disposition == "retry"
    assert "Se reintentará automáticamente con backoff." in message
    assert "Intentos realizados: 1 de 3." in message
    assert "Código: direct_transport_error" in message


def test_broker_timeout_notice_explains_quarantine_for_fresh_and_persisted_failures() -> None:
    outcome = classify_summary_failure(
        route="broker",
        code="broker_timeout",
        attempt_count=1,
        max_attempts=3,
        now=datetime.now(UTC),
    )
    expected = (
        "Resumen no disponible.\n"
        "Motivo: El broker no confirmó el resultado dentro del plazo; requiere reconciliación.\n"
        "Estado: La espera agotó el plazo; se desconoce si el broker completó el trabajo. "
        "Los reintentos están suspendidos para evitar duplicados. "
        "Un operador debe revisar el worker del broker y recuperar el resultado de la tarea existente antes de habilitar nuevos intentos.\n"
        "Código: broker_timeout"
    )

    assert format_summary_failure_notice(
        outcome, attempt_count=1, max_attempts=3,
    ) == expected
    assert format_persisted_summary_failure(
        failure_code="broker_timeout",
        quarantined=True,
        attempt_count=1,
        max_attempts=3,
    ) == expected
    assert outcome.disposition == "quarantine"
    assert outcome.retry_at is None
    assert outcome.recovery_action == "broker_resolve"


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
        "Código: broker_output_incomplete"
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
    assert "Intentos realizados" not in expected
    assert "Código: broker_output_incomplete" in expected
