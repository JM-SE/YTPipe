from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import InvalidVideoId, NoTranscriptFound, TranscriptsDisabled

from app.core.settings import Settings

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGES = ["es", "en"]
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 30


def _request_with_timeout(request):
    def bounded_request(*args, **kwargs):
        kwargs.setdefault("timeout", (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS))
        return request(*args, **kwargs)

    return bounded_request


@dataclass(frozen=True)
class TranscriptFetchResult:
    text: str | None
    outcome: str
    error: str | None = None

    @property
    def completed(self) -> bool:
        return self.outcome == "completed"

    @property
    def permanent(self) -> bool:
        return self.outcome == "unavailable"


class TranscriptService:
    def __init__(self, settings: Settings):  # noqa: ARG002
        session = requests.Session()
        session.request = _request_with_timeout(session.request)
        self._api = YouTubeTranscriptApi(http_client=session)

    def fetch_transcript(self, youtube_video_id: str) -> str | None:
        return self.fetch_transcript_result(youtube_video_id).text

    def fetch_transcript_result(self, youtube_video_id: str) -> TranscriptFetchResult:
        try:
            transcript = self._api.fetch(
                youtube_video_id,
                languages=DEFAULT_LANGUAGES,
            )
        except (NoTranscriptFound, TranscriptsDisabled) as exc:
            logger.debug("Transcript not available for video %s", youtube_video_id)
            return TranscriptFetchResult(None, "unavailable", str(exc))
        except InvalidVideoId as exc:
            return TranscriptFetchResult(None, "retryable", str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Transcript provider failed for video %s", youtube_video_id)
            return TranscriptFetchResult(None, "retryable", str(exc))

        if not transcript.snippets:
            return TranscriptFetchResult(None, "unavailable", "Transcript contains no snippets.")

        return TranscriptFetchResult(
            " ".join(snippet.text for snippet in transcript.snippets),
            "completed",
        )
