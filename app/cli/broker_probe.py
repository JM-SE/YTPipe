from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from typing import TextIO

from app.core.settings import Settings
from app.services.broker_connection_config import BrokerConfigurationError, broker_connection_config, build_broker_client
from app.services.broker_gateway import BrokerTaskClient
from app.services.broker_probe import BrokerProbeService
from app.services.broker_profile import load_y01_profile
from app.services.transcript import TranscriptService
from app.services.youtube_video_url import YouTubeURLValidationError, parse_youtube_video_url

CONSENT = "I CONSENT TO SEND THIS TRANSCRIPT"


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None,
         stdin: TextIO | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="broker-probe")
    parser.add_argument("--youtube", action="store_true", help="run the interactive YouTube acceptance probe")
    parser.add_argument("--probe-id", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    if not stdin.isatty() or not stdout.isatty():
        stderr.write("Broker probe requires an interactive terminal.\n")
        return 2
    settings = settings or Settings()
    if not settings.broker_probes_enabled or (args.youtube and not settings.broker_acceptance_probe_enabled):
        stderr.write("Broker probe is disabled.\n")
        return 2
    try:
        profile = load_y01_profile()
        config = broker_connection_config(settings)
    except (BrokerConfigurationError, ValueError):
        stderr.write("Broker probe configuration is invalid.\n")
        return 2
    client = build_broker_client(config)
    task_client = BrokerTaskClient.from_client(client, timeout=config.timeout_seconds, profile=profile)
    try:
        service = BrokerProbeService(task_client, profile=profile,
                                     max_transcript_characters=settings.broker_probe_max_transcript_characters)
        if args.youtube:
            stdout.write("Enter a YouTube video URL: ")
            raw_url = stdin.readline()
            try:
                parsed = parse_youtube_video_url(raw_url)
            except YouTubeURLValidationError:
                stderr.write("The YouTube URL is invalid.\n")
                return 2
            stdout.write(f"Canonical URL: {parsed.canonical_url}\nType {CONSENT} to continue: ")
            if stdin.readline().strip() != CONSENT:
                stderr.write("Consent was not provided.\n")
                return 2
            try:
                transcript_logger = logging.getLogger("app.services.transcript")
                was_disabled = transcript_logger.disabled
                transcript_logger.disabled = True
                try:
                    result = service.youtube(parsed, TranscriptService(settings), args.probe_id)
                finally:
                    transcript_logger.disabled = was_disabled
            except ValueError:
                stderr.write("Probe ID is invalid.\n")
                return 2
        else:
            try:
                result = service.synthetic(args.probe_id)
            except ValueError:
                stderr.write("Probe ID is invalid.\n")
                return 2
        stdout.write(f"Probe {result.status}: {result.category}\n")
        return 0 if result.status == "succeeded" else 1
    finally:
        task_client.close()


if __name__ == "__main__":
    raise SystemExit(main())
