from app.core.settings import Settings
import httpx
import pytest

from app.services.summarization import (
    DIRECT_TRANSCRIPT_CHARACTER_LIMIT,
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
    assert all(len(prompt) <= TRANSCRIPT_CHUNK_CHARACTER_LIMIT + 200 for prompt in prompts[:-1])
    assert "Integra los siguientes resumenes parciales" in prompts[-1]


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
