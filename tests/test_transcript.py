from __future__ import annotations

from types import SimpleNamespace

import requests
from youtube_transcript_api._errors import NoTranscriptFound

from app.services.transcript import DEFAULT_LANGUAGES, TranscriptService


def test_http_timeout_is_finite_connect_read_and_retryable(monkeypatch):
    observed = []

    def request(session, method, url, **kwargs):
        observed.append(kwargs.get("timeout"))
        raise requests.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr(requests.Session, "request", request)
    service = TranscriptService(None)
    result = service.fetch_transcript_result("video-id")

    assert result.outcome == "retryable"
    assert result.text is None
    assert observed
    assert all(timeout == (10, 30) for timeout in observed)


def test_fetch_success_preserves_language_order(monkeypatch):
    service = TranscriptService(None)
    expected = SimpleNamespace()
    calls = []
    monkeypatch.setattr(service._api, "fetch", lambda video_id, languages: (calls.append((video_id, languages)), expected)[1])
    # Transcript snippets are the only successful result data consumed by the service.
    expected.snippets = [type("Snippet", (), {"text": "hola"})()]

    result = service.fetch_transcript_result("video-id")

    assert result.outcome == "completed"
    assert result.text == "hola"
    assert calls == [("video-id", ["es", "en"])]


def test_unavailable_transcript_remains_unavailable(monkeypatch):
    service = TranscriptService(None)
    monkeypatch.setattr(service._api, "fetch", lambda *args, **kwargs: (_ for _ in ()).throw(NoTranscriptFound("video-id", ["es", "en"], None)))

    result = service.fetch_transcript_result("video-id")

    assert result.outcome == "unavailable"
    assert result.text is None
