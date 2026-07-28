from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit


VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
SHORTS_PATHS = {"shorts", "live"}


class YouTubeURLValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ParsedYouTubeVideoURL:
    video_id: str
    canonical_url: str


@dataclass(frozen=True)
class ParsedSummaryCommand:
    video: ParsedYouTubeVideoURL


def parse_youtube_video_url(value: str) -> ParsedYouTubeVideoURL:
    if not isinstance(value, str) or not value.strip():
        raise YouTubeURLValidationError("missing_url", "A YouTube video URL is required.")

    raw = value.strip()
    if len(raw) > 4096:
        raise YouTubeURLValidationError("url_too_long", "The YouTube URL is too long.")
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname.lower() if parsed.hostname else None
        port = parsed.port
    except ValueError as exc:
        raise YouTubeURLValidationError("invalid_url", "The YouTube URL is invalid.") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise YouTubeURLValidationError("invalid_scheme", "The URL must use HTTP or HTTPS.")
    expected_port = 80 if parsed.scheme.lower() == "http" else 443
    if parsed.username is not None or parsed.password is not None or port not in {None, expected_port}:
        raise YouTubeURLValidationError("invalid_authority", "The URL must not contain credentials or a port.")
    if parsed.fragment:
        raise YouTubeURLValidationError("fragment_not_allowed", "The URL must not contain a fragment.")
    if hostname not in YOUTUBE_HOSTS and hostname != "youtu.be":
        raise YouTubeURLValidationError("invalid_host", "Only YouTube video URLs are accepted.")

    if hostname == "youtu.be":
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) != 1 or _raw_query_values(parsed.query, "v"):
            raise YouTubeURLValidationError("invalid_video_path", "The URL must identify exactly one video.")
        video_id = path_parts[0]
    elif parsed.path == "/watch":
        video_ids = _raw_query_values(parsed.query, "v")
        if len(video_ids) != 1 or not video_ids[0]:
            raise YouTubeURLValidationError("invalid_video_query", "The watch URL must contain one video ID.")
        video_id = video_ids[0]
    else:
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) != 2 or path_parts[0].lower() not in SHORTS_PATHS:
            raise YouTubeURLValidationError("unsupported_url", "Use a YouTube watch, shorts, live, or youtu.be URL.")
        if _raw_query_values(parsed.query, "v"):
            raise YouTubeURLValidationError("conflicting_video_id", "The URL contains conflicting video identifiers.")
        video_id = path_parts[1]

    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise YouTubeURLValidationError("invalid_video_id", "The YouTube video ID must contain exactly 11 characters.")

    return ParsedYouTubeVideoURL(
        video_id=video_id,
        canonical_url=f"https://www.youtube.com/watch?v={video_id}",
    )


def _raw_query_values(query: str, key: str) -> list[str]:
    values: list[str] = []
    for parameter in query.split("&"):
        raw_key, separator, raw_value = parameter.partition("=")
        if separator and raw_key == key:
            values.append(raw_value)
    return values


def parse_summary_command(text: str, bot_username: str) -> ParsedSummaryCommand:
    parts = text.strip().split()
    if len(parts) != 2:
        raise YouTubeURLValidationError("invalid_command", "Use /summary followed by one YouTube video URL.")

    command, raw_url = parts
    command_parts = command.split("@", 1)
    if command_parts[0] != "/summary":
        raise YouTubeURLValidationError("unsupported_command", "Only the /summary command is supported.")

    configured_username = bot_username.strip().lstrip("@").casefold()
    if len(command_parts) == 2 and command_parts[1].casefold() != configured_username:
        raise YouTubeURLValidationError("invalid_command_target", "This command is not addressed to this bot.")

    return ParsedSummaryCommand(video=parse_youtube_video_url(raw_url))
