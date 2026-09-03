from __future__ import annotations

from app.services.summarization import (
    DIRECT_TRANSCRIPT_CHARACTER_LIMIT,
    FINAL_SUMMARY_INSTRUCTIONS,
    PARTIAL_SUMMARY_INSTRUCTIONS,
    SUMMARIZATION_SYSTEM_PROMPT,
    TRANSCRIPT_CHUNK_CHARACTER_LIMIT,
)
from app.services.summarization_gateway import SummaryOperation


def split_transcript(transcript: str) -> list[str]:
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


def plan_operations(transcript: str, max_tokens: int) -> list[SummaryOperation]:
    if len(transcript) <= DIRECT_TRANSCRIPT_CHARACTER_LIMIT:
        return [SummaryOperation("direct-final", 0, SUMMARIZATION_SYSTEM_PROMPT,
                                 f"{FINAL_SUMMARY_INSTRUCTIONS}\n\nTRANSCRIPCION:\n\n{transcript}", max_tokens)]
    chunks = split_transcript(transcript)
    operations = [SummaryOperation("partial", i, SUMMARIZATION_SYSTEM_PROMPT,
                                    f"{PARTIAL_SUMMARY_INSTRUCTIONS}\n\nFRAGMENTO:\n\n{chunk}", max_tokens)
                  for i, chunk in enumerate(chunks)]
    aggregate = ("Integra los siguientes resumenes parciales en un unico resumen completo, sin repetir ideas y "
                 "conservando los hechos importantes.\n\n" f"{FINAL_SUMMARY_INSTRUCTIONS}\n\n"
                 "RESUMENES PARCIALES:\n\n" + "\n\n---\n\n".join("" for _ in chunks))
    # The final prompt is completed by the gateway after partial results exist.
    operations.append(SummaryOperation("aggregate-final", len(chunks), SUMMARIZATION_SYSTEM_PROMPT,
                                        aggregate, max_tokens))
    return operations
