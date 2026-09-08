from __future__ import annotations

from app.services.summary_failure_policy import SummaryFailureOutcome


def format_summary_failure_notice(outcome: SummaryFailureOutcome, title: str | None = None) -> str:
    prefix = f"Video: {title}\n" if title else ""
    if outcome.disposition == "quarantine":
        state = "Quarantined: requiere resolución operativa antes de continuar."
    elif outcome.disposition == "retry":
        state = "Se reintentará automáticamente con backoff."
    else:
        state = "No se reintentará automáticamente."
    return f"{prefix}Resumen no disponible.\nMotivo: {outcome.display_reason}\nEstado: {state}"


def format_summary_failure_code(code: str | None) -> str:
    """Return a stable user-safe reason for a persisted stage code."""
    from app.services.summary_failure_policy import _display_reason

    return _display_reason(code or "summary_failure")
