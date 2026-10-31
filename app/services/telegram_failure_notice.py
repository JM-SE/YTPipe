from __future__ import annotations

from app.services.summary_failure_policy import SummaryFailureOutcome


_FAILURE_CODE_FALLBACK_REASON = "El resumen no pudo generarse por un error controlado."


def format_summary_failure_notice(
    outcome: SummaryFailureOutcome,
    title: str | None = None,
    *,
    attempt_count: int | None = None,
    max_attempts: int | None = None,
    video_reference: str | None = None,
) -> str:
    prefix = f"Video: {title}\n" if title else ""
    if video_reference:
        prefix += f"Video: {video_reference}\n"
    if outcome.disposition == "quarantine":
        state = (
            "La espera agotó el plazo; se desconoce si el broker completó el trabajo. "
            "Los reintentos están suspendidos para evitar duplicados. "
            "Un operador debe revisar el worker del broker y recuperar el resultado de la tarea existente antes de habilitar nuevos intentos."
            if outcome.failure_code == "broker_timeout"
            else "Quarantined: requiere resolución operativa antes de continuar."
        )
    elif outcome.disposition == "retry":
        state = "Se reintentará automáticamente con backoff."
    else:
        state = "No se reintentará automáticamente."
    attempts = ""
    if outcome.disposition == "retry" and attempt_count is not None and max_attempts is not None and attempt_count > 0:
        attempts = f"\nIntentos realizados: {attempt_count} de {max_attempts}."
    return (
        f"{prefix}Resumen no disponible.\nMotivo: {outcome.display_reason}"
        f"\nEstado: {state}{attempts}\nCódigo: {format_summary_failure_diagnostic_code(outcome.failure_code)}"
    )


def format_summary_failure_diagnostic_code(code: str | None) -> str:
    """Expose only codes recognized by the existing safe-reason mapping."""
    from app.services.summary_failure_policy import _display_reason

    if not code or (_display_reason(code) == _FAILURE_CODE_FALLBACK_REASON and code != "summary_failure"):
        return "summary_failure"
    return code


def format_summary_failure_code(code: str | None) -> str:
    """Return a stable user-safe reason for a persisted stage code."""
    from app.services.summary_failure_policy import _display_reason

    return _display_reason(code or "summary_failure")


def format_persisted_summary_failure(
    *,
    failure_code: str | None,
    quarantined: bool,
    attempt_count: int | None = None,
    max_attempts: int | None = None,
    video_reference: str | None = None,
) -> str:
    """Format a persisted stage failure without relying on free-form text."""
    state = (
        (
            "La espera agotó el plazo; se desconoce si el broker completó el trabajo. "
            "Los reintentos están suspendidos para evitar duplicados. "
            "Un operador debe revisar el worker del broker y recuperar el resultado de la tarea existente antes de habilitar nuevos intentos."
            if failure_code == "broker_timeout"
            else "Quarantined: requiere resolución operativa antes de continuar."
        )
        if quarantined
        else "No se reintentará automáticamente."
    )
    reference = f"Video: {video_reference}\n" if video_reference else ""
    return (
        f"{reference}Resumen no disponible.\n"
        f"Motivo: {format_summary_failure_code(failure_code)}\n"
        f"Estado: {state}\nCódigo: {format_summary_failure_diagnostic_code(failure_code)}"
    )
