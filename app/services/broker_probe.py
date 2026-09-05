from __future__ import annotations

import secrets
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable, Protocol

from app.services.broker_errors import BrokerSummarizationError
from app.services.broker_gateway import BrokerOperation, BrokerResult, BrokerTaskClient
from app.services.broker_profile import BrokerRequestProfile, load_y01_profile
from app.services.broker_summary import validate_broker_output
from app.services.summarization import FINAL_SUMMARY_INSTRUCTIONS, SUMMARIZATION_SYSTEM_PROMPT
from app.services.transcript import TranscriptFetchResult
from app.services.youtube_video_url import ParsedYouTubeVideoURL

PROBE_NAMESPACE = "ytpipe-broker-probe-v1"
SYNTHETIC_TRANSCRIPT = (
    "Un proyecto piloto instaló paneles solares en una escuela rural para reducir su consumo de la red. "
    "La instalación tiene una capacidad de 20 kilovatios y empezó a operar en marzo. "
    "El equipo docente incorporó el proyecto a las clases de ciencias y registra la producción cada semana. "
    "Durante los primeros tres meses, la escuela redujo en un 30 por ciento la electricidad comprada. "
    "La comunidad aportó mano de obra para preparar el tejado y el municipio financió los equipos. "
    "El informe recomienda revisar las baterías antes de ampliar el sistema a otros edificios."
)
SYNTHETIC_PROMPT = FINAL_SUMMARY_INSTRUCTIONS + "\n\nTRANSCRIPCION:\n\n" + SYNTHETIC_TRANSCRIPT
SYNTHETIC_SYSTEM_PROMPT = SUMMARIZATION_SYSTEM_PROMPT
class ProbeTranscriptService(Protocol):
    def fetch_transcript_result(self, youtube_video_id: str) -> TranscriptFetchResult: ...


@dataclass(frozen=True, slots=True)
class BrokerProbeResult:
    status: str
    category: str


def probe_idempotency_key(probe_kind: str, probe_id: str, operation: str, ordinal: int) -> str:
    values = (PROBE_NAMESPACE, "b00-v0.1", probe_kind, probe_id, operation, str(ordinal))
    encoded = b"".join(len(v.encode()).to_bytes(4, "big") + v.encode() for v in values)
    return sha256(encoded).hexdigest()


class BrokerProbeService:
    # F7 acceptance disposition: the CLI no longer exposes --show-summary, so
    # no accepted output reaches a terminal through this service. The in-memory
    # summary is retained only as a deliberate seam for offline unit tests to
    # assert oracle acceptance without touching broker content paths.
    def __init__(self, task_client: BrokerTaskClient, *, max_transcript_characters: int = 30_000,
                 profile: BrokerRequestProfile | None = None,
                 probe_id_factory: Callable[[], str] = lambda: secrets.token_hex(16)):
        if max_transcript_characters < 1:
            raise ValueError("Invalid transcript cap.")
        self._client = task_client
        self._profile = profile or load_y01_profile()
        self._cap = max_transcript_characters
        self._probe_id_factory = probe_id_factory
        self._last_summary: str | None = None

    @property
    def summary_for_display(self) -> str | None:
        """Content is available only for the CLI's explicit TTY display path."""
        return self._last_summary

    def synthetic(self, probe_id: str | None = None) -> BrokerProbeResult:
        self._last_summary = None
        pid = self._probe_id(probe_id)
        operation = BrokerOperation("synthetic", 0, SYNTHETIC_SYSTEM_PROMPT, SYNTHETIC_PROMPT,
                                    self._profile.max_tokens)
        result = self._submit_and_validate(operation, pid, "synthetic")
        if isinstance(result, BrokerProbeResult):
            return result
        summary = result
        self._last_summary = summary
        return BrokerProbeResult("succeeded", "synthetic_accepted")

    def youtube(self, parsed_url: ParsedYouTubeVideoURL, transcript_service: ProbeTranscriptService,
                probe_id: str | None = None) -> BrokerProbeResult:
        self._last_summary = None
        pid = self._probe_id(probe_id)
        fetched = transcript_service.fetch_transcript_result(parsed_url.video_id)
        if fetched.outcome == "retryable":
            return BrokerProbeResult("failed", "transcript_retryable")
        if fetched.outcome != "completed" or not fetched.text:
            return BrokerProbeResult("failed", "transcript_unavailable")
        if len(fetched.text) > self._cap:
            return BrokerProbeResult("failed", "transcript_too_large")
        operation = BrokerOperation("youtube", 0, SUMMARIZATION_SYSTEM_PROMPT,
                                    FINAL_SUMMARY_INSTRUCTIONS + "\n\nTRANSCRIPCION:\n\n" + fetched.text,
                                    self._profile.max_tokens)
        result = self._submit_and_validate(operation, pid, "youtube")
        if isinstance(result, BrokerProbeResult):
            return result
        summary = result
        self._last_summary = summary
        return BrokerProbeResult("succeeded", "youtube_accepted")

    def _submit_and_validate(
        self, operation: BrokerOperation, probe_id: str, probe_kind: str,
    ) -> str | BrokerProbeResult:
        try:
            request_bytes = len(operation.system_prompt.encode("utf-8")) + len(operation.user_prompt.encode("utf-8"))
            if request_bytes > self._profile.max_request_content_bytes:
                return BrokerProbeResult("failed", "broker_input_too_large")
            key = probe_idempotency_key(probe_kind, probe_id, "submit", 0)
            submit_result = getattr(self._client, "submit_result", None)
            if callable(submit_result):
                broker_result = submit_result(operation, key)
            else:
                broker_result = self._client.submit(operation, key)
            if isinstance(broker_result, BrokerResult):
                if broker_result.finish_reason != self._profile.accepted_finish_reason:
                    category = (
                        "broker_output_incomplete"
                        if broker_result.finish_reason == "length"
                        else "broker_output_invalid"
                    )
                    return BrokerProbeResult("failed", category)
                summary = broker_result.content
            else:
                summary = broker_result
            validate_broker_output(summary)
            return summary
        except BrokerSummarizationError as exc:
            return BrokerProbeResult("failed", _diagnostic_category(exc.code))
        except ValueError:
            return BrokerProbeResult("failed", "broker_output_invalid")
        except Exception:
            return BrokerProbeResult("failed", "broker_error")

    def _probe_id(self, value: str | None) -> str:
        candidate = value or self._probe_id_factory()
        if not isinstance(candidate, str) or not candidate or len(candidate) > 128 or any(c.isspace() for c in candidate):
            raise ValueError("Invalid probe ID.")
        return candidate


def _diagnostic_category(code: str) -> str:
    """Expose only stable local/broker classes, never remote error details."""
    allowed = {
        "broker_unauthorized",
        "broker_invalid_request",
        "broker_timeout",
        "broker_transport_error",
        "broker_protocol_error",
        "broker_location_invalid",
        "broker_task_failed",
        "broker_task_cancelled",
        "broker_task_expired",
        "broker_output_invalid",
        "broker_output_incomplete",
    }
    return code if code in allowed else "broker_error"
