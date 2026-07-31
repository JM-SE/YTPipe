from __future__ import annotations

from app.services.youtube_video_metadata import classify_short, title_indicates_short


def test_title_indicates_short_accepts_broadened_markers() -> None:
    assert title_indicates_short("Receta en 30s #short") is True
    assert title_indicates_short("Clip #ytshorts") is True
    assert title_indicates_short("Funny ytshorts moment") is True
    assert title_indicates_short("Classic #shorts upload") is True
    assert title_indicates_short("Normal long-form title") is False


def test_classify_short_uses_title_before_duration() -> None:
    assert classify_short("Quick tip #short", None) is True
    assert classify_short("No marker", 45) is True
    assert classify_short("No marker", 120) is False
    assert classify_short("No marker", None) is None
