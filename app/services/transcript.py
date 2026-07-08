from __future__ import annotations

import logging

from youtube_transcript_api import YouTubeTranscriptApi

from app.core.settings import Settings

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGES = ["es", "en"]


class TranscriptService:
    def __init__(self, settings: Settings):  # noqa: ARG002
        self._api = YouTubeTranscriptApi()

    def fetch_transcript(self, youtube_video_id: str) -> str | None:
        try:
            transcript = self._api.fetch(
                youtube_video_id,
                languages=DEFAULT_LANGUAGES,
            )
        except Exception:
            logger.debug("Transcript not available for video %s", youtube_video_id)
            return None

        if not transcript.snippets:
            return None

        return " ".join(snippet.text for snippet in transcript.snippets)
