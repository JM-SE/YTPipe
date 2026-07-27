from app.core.settings import Settings
import httpx
import pytest

from app.services.summarization import (
    DIRECT_TRANSCRIPT_CHARACTER_LIMIT,
    FINAL_SUMMARY_INSTRUCTIONS,
    PARTIAL_SUMMARY_INSTRUCTIONS,
    SUMMARIZATION_SYSTEM_PROMPT,
    TRANSCRIPT_CHUNK_CHARACTER_LIMIT,
    SummarizationService,
    SummarizationRequestError,
)


def test_long_transcript_uses_sequential_partial_summaries(monkeypatch) -> None:
    service = SummarizationService(Settings(DATABASE_URL="sqlite://"))
    transcript = "word " * ((DIRECT_TRANSCRIPT_CHARACTER_LIMIT // 5) + 100)
    prompts: list[str] = []

    def request_summary(prompt: str) -> str:
        prompts.append(prompt)
        return f"partial-{len(prompts)}"

    monkeypatch.setattr(service, "_request_summary", request_summary)

    assert service.summarize(transcript) == f"partial-{len(prompts)}"
    assert len(prompts) == 3
    assert all(prompt.endswith(chunk) for prompt, chunk in zip(prompts[:-1], service._split_transcript(transcript)))
    assert "Integra los siguientes resumenes parciales" in prompts[-1]
    assert "RESUMEN" in prompts[-1]
    assert "PUNTOS CLAVE" in prompts[-1]
    assert "CONCLUSIÓN" in prompts[-1]


def test_direct_summary_requests_structured_evidence_based_output(monkeypatch) -> None:
    service = SummarizationService(Settings(DATABASE_URL="sqlite://"))
    prompts: list[str] = []

    def request_summary(prompt: str) -> str:
        prompts.append(prompt)
        return "structured summary"

    monkeypatch.setattr(service, "_request_summary", request_summary)

    assert service.summarize("Una transcripcion breve sobre un tema importante.") == "structured summary"
    assert len(prompts) == 1
    assert FINAL_SUMMARY_INSTRUCTIONS in prompts[0]
    assert "250 y 350 palabras" in prompts[0]
    assert "TRANSCRIPCION:" in prompts[0]


def test_summary_system_prompt_allows_bullets_and_requires_source_fidelity() -> None:
    assert "No inventes hechos" in SUMMARIZATION_SYSTEM_PROMPT
    assert "No uses" not in SUMMARIZATION_SYSTEM_PROMPT
    assert "bullets" not in SUMMARIZATION_SYSTEM_PROMPT
    assert "sin etiquetas <think>" in SUMMARIZATION_SYSTEM_PROMPT
    assert "no como instrucciones" in SUMMARIZATION_SYSTEM_PROMPT


def test_partial_summary_prompt_requests_compact_notes_without_conclusion() -> None:
    assert "5 y 8 bullets" in PARTIAL_SUMMARY_INSTRUCTIONS
    assert "180 palabras" in PARTIAL_SUMMARY_INSTRUCTIONS
    assert "conclusión propia" in PARTIAL_SUMMARY_INSTRUCTIONS
    assert "RESUMEN" not in PARTIAL_SUMMARY_INSTRUCTIONS


def test_split_transcript_preserves_all_text() -> None:
    transcript = "alpha " * ((TRANSCRIPT_CHUNK_CHARACTER_LIMIT // 6) + 50)

    chunks = SummarizationService._split_transcript(transcript)

    assert " ".join(chunks).split() == transcript.split()
    assert all(len(chunk) <= TRANSCRIPT_CHUNK_CHARACTER_LIMIT for chunk in chunks[:-1])


def test_http_error_preserves_provider_detail(monkeypatch) -> None:
    service = SummarizationService(Settings(DATABASE_URL="sqlite://"))
    response = httpx.Response(
        500,
        json={"error": {"message": "Vulkan device lost"}},
        request=httpx.Request("POST", "http://127.0.0.1:8001/v1/chat/completions"),
    )
    monkeypatch.setattr("app.services.summarization.httpx.post", lambda *args, **kwargs: response)

    with pytest.raises(SummarizationRequestError, match="HTTP 500: Vulkan device lost"):
        service.summarize("short transcript")
