from __future__ import annotations

import logging

import httpx

from app.core.settings import Settings

logger = logging.getLogger(__name__)

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
        payload: dict = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Resume la siguiente transcripcion:\n\n{transcript}"},
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
            logger.warning("Summarization timed out for transcript of length %d", len(transcript))
            return None
        except httpx.TransportError as exc:
            logger.warning("Summarization network error: %s", exc)
            return None

        if response.status_code != 200:
            logger.warning("Summarization failed with status %d", response.status_code)
            return None

        try:
            data = response.json()
        except ValueError:
            logger.warning("Summarization returned invalid JSON")
            return None

        if "error" in data:
            logger.warning("Summarization server error: %s", data["error"])
            return None

        content: str = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content.strip() if content.strip() else None
