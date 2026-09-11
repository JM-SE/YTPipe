from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal


FailureDisposition = Literal["terminal", "retry", "quarantine"]


@dataclass(frozen=True)
class SummaryFailureOutcome:
    route: Literal["direct", "broker"]
    failure_class: str
    failure_code: str
    disposition: FailureDisposition
    retry_at: datetime | None
    recovery_owner: Literal["ytpipe", "broker", "operator"]
    recovery_action: str
    display_reason: str


_BROKER_QUARANTINE_CODES = {
    "broker_timeout",
    "broker_transport_error",
    "broker_task_indeterminate",
    # A missing or malformed Location cannot prove whether the POST created a
    # task, so it is an ambiguity even though no task ID is available.
    "broker_location_invalid",
}
_BROKER_TERMINAL_CODES = {
    "broker_output_invalid",
    "broker_output_incomplete",
    "broker_task_failed",
    "broker_task_cancelled",
    "broker_task_expired",
    "broker_invalid_request",
    "broker_input_too_large",
    "broker_protocol_error",
    "broker_location_invalid",
    "broker_unauthorized",
}


def classify_summary_failure(
    *,
    route: Literal["direct", "broker"],
    code: str,
    attempt_count: int,
    max_attempts: int,
    now: datetime,
    base_delay_seconds: int = 30,
    broker_failure_class: str | None = None,
) -> SummaryFailureOutcome:
    """Return a deterministic, route-aware disposition without parsing text."""
    safe_attempts = max(1, max_attempts)
    if route == "broker" and (
        broker_failure_class == "indeterminate"
        or (broker_failure_class is None and code in _BROKER_QUARANTINE_CODES)
    ):
        return SummaryFailureOutcome(
            route, broker_failure_class or "indeterminate", code, "quarantine", None, "operator", "broker_resolve", _display_reason(code)
        )
    if route == "broker" and broker_failure_class in {"transient_unsent", "transient_safe"}:
        pass
    elif route == "broker" and (broker_failure_class in {
        "client_invalid", "policy_rejected", "backend_rejected", "output_invalid", "internal"
    } or code in _BROKER_TERMINAL_CODES):
        return SummaryFailureOutcome(
            route, broker_failure_class or "permanent", code, "terminal", None, "broker", "none", _display_reason(code)
        )
    elif route == "broker" and broker_failure_class is not None:
        return SummaryFailureOutcome(
            route, broker_failure_class, code, "quarantine", None, "operator", "broker_resolve", _display_reason(code)
        )
    if attempt_count >= safe_attempts:
        return SummaryFailureOutcome(
            route,
            broker_failure_class if route == "broker" and broker_failure_class else "transient",
            code,
            "terminal",
            None,
            "ytpipe",
            "none",
            _display_reason(code),
        )
    delay = max(1, base_delay_seconds) * (2 ** min(attempt_count - 1, 5))
    return SummaryFailureOutcome(
        route,
        broker_failure_class if route == "broker" and broker_failure_class else "transient",
        code,
        "retry",
        now + timedelta(seconds=delay),
        "ytpipe",
        "retry",
        _display_reason(code),
    )


def _display_reason(code: str) -> str:
    reasons = {
        "no_transcript": "No se encontró una transcripción utilizable.",
        "direct_timeout": "El servicio directo agotó el tiempo de espera.",
        "direct_transport_error": "El servicio directo no está disponible temporalmente.",
        "direct_http_error": "El servicio directo devolvió un error temporal.",
        "direct_provider_error": "El servicio directo informó un error temporal.",
        "direct_invalid_response": "El servicio directo devolvió una respuesta inválida.",
        "direct_empty_response": "El servicio directo devolvió una respuesta vacía.",
        "broker_timeout": "El broker no confirmó el resultado dentro del plazo; requiere reconciliación.",
        "broker_transport_error": "No se pudo contactar al broker de forma segura.",
        "broker_task_indeterminate": "El resultado del broker es indeterminado y requiere resolución operativa.",
        "broker_output_incomplete": "El broker devolvió una salida incompleta.",
        "broker_output_invalid": "El broker devolvió una salida inválida.",
        "broker_input_too_large": "La entrada supera el límite aceptado por el broker.",
        "broker_protocol_error": "El broker devolvió una respuesta incompatible.",
        "backend_error": "El backend de inferencia no pudo completar la solicitud.",
        "backend_context_exceeded": "El transcript excede el límite de contexto del modelo.",
    }
    return reasons.get(code, "El resumen no pudo generarse por un error controlado.")
