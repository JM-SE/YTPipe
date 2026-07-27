from __future__ import annotations

import logging
import json

import httpx

from app.core.settings import Settings

logger = logging.getLogger(__name__)
DIRECT_TRANSCRIPT_CHARACTER_LIMIT = 30_000
TRANSCRIPT_CHUNK_CHARACTER_LIMIT = 24_000

SUMMARIZATION_SYSTEM_PROMPT = (
    "Eres un asistente que resume transcripciones de videos de YouTube. "
    "Resume en español, capturando las ideas principales y novedades. "
    "Sé informativo pero conciso. "
    "No agregues opiniones ni comentarios personales. "
    "Responde únicamente con el resumen, sin introducciones ni despedidas. "
    "No uses markdown, listas numeradas ni bullets. "
    "Escribe el resumen como texto corrido, en párrafos claros."
)


class SummarizationService:
    def __init__(self, settings: Settings):
        self._base_url = settings.llama_cpp_base_url.rstrip("/")
        self._timeout = settings.llama_cpp_timeout
        self._max_tokens = settings.llama_cpp_max_tokens

    def summarize(self, transcript: str) -> str | None:
        if len(transcript) <= DIRECT_TRANSCRIPT_CHARACTER_LIMIT:
            return self._request_summary(f"Resume la siguiente transcripcion:\n\n{transcript}")

        partial_summaries = [
            self._request_summary(
                "Resume este fragmento de una transcripcion. Conserva hechos, cifras, "
                f"nombres y conclusiones para integrarlos despues:\n\n{chunk}"
            )
            for chunk in self._split_transcript(transcript)
        ]
        return self._request_summary(
            "Integra los siguientes resumenes parciales en un unico resumen completo, "
            "sin repetir ideas y conservando los hechos importantes:\n\n"
            + "\n\n---\n\n".join(partial_summaries)
        )

    def _request_summary(self, prompt: str) -> str | None:
        payload: dict = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": self._max_tokens,
            "stop": [],
        }

        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                timeout=self._timeout,
            )
        except httpx.TimeoutException:
            logger.warning("Summarization timed out for prompt of length %d", len(prompt))
            raise SummarizationRequestError("Summarization request timed out.") from None
        except httpx.TransportError as exc:
            logger.warning("Summarization network error: %s", exc)
            raise SummarizationRequestError(f"Summarization network error: {exc}") from exc

        if response.status_code != 200:
            logger.warning("Summarization failed with status %d", response.status_code)
            detail = self._extract_error_detail(response)
            message = f"Summarization server returned HTTP {response.status_code}"
            if detail:
                message = f"{message}: {detail}"
            raise SummarizationRequestError(f"{message}.")

        try:
            data = response.json()
        except ValueError:
            logger.warning("Summarization returned invalid JSON")
            raise SummarizationRequestError("Summarization server returned invalid JSON.") from None

        if "error" in data:
            logger.warning("Summarization server error: %s", data["error"])
            raise SummarizationRequestError(f"Summarization server error: {data['error']}")

        content: str = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content.strip():
            raise SummarizationRequestError("Summarization server returned empty content.")
        return content.strip()

    @staticmethod
    def _extract_error_detail(response: httpx.Response) -> str | None:
        try:
            payload = response.json()
        except ValueError:
            payload = None

        detail: object = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                detail = error.get("message") or error.get("detail") or error.get("type")
            elif isinstance(error, str):
                detail = error
            detail = detail or payload.get("message") or payload.get("detail")

        if detail is None:
            text = response.text.strip()
            detail = text or None
        if detail is None:
            return None

        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=True)
        detail = " ".join(detail.replace("\n", " ").replace("\r", " ").split())
        return detail[:400] or None

    @staticmethod
    def _split_transcript(transcript: str) -> list[str]:
        chunks: list[str] = []
        remaining = transcript.strip()
        while len(remaining) > TRANSCRIPT_CHUNK_CHARACTER_LIMIT:
            split_at = remaining.rfind(" ", 0, TRANSCRIPT_CHUNK_CHARACTER_LIMIT)
            if split_at <= 0:
                split_at = TRANSCRIPT_CHUNK_CHARACTER_LIMIT
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks


class SummarizationRequestError(Exception):
    """A failed local request whose reason belongs on its pipeline stage."""
