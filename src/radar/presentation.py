"""Small presentation helpers that keep weekly Radar reports easy to scan."""

from __future__ import annotations

import re


CONCISE_BRIEF_MAX_CHARS = 100
CONCISE_BRIEF_MAX_SENTENCES = 2


def concise_brief_text(
    value: str,
    *,
    max_chars: int = CONCISE_BRIEF_MAX_CHARS,
    max_sentences: int = CONCISE_BRIEF_MAX_SENTENCES,
) -> str:
    """Keep a user-facing explanation to at most two short sentences.

    The LLM is asked to be concise, but the report must stay comfortable to
    read even when a provider ignores that instruction.  Whitespace is folded,
    then only the first complete sentences are retained; a character cap is a
    final safety net for text without sentence punctuation.
    """

    if max_chars < 1 or max_sentences < 1:
        raise ValueError("max_chars and max_sentences must be positive")
    normalized = " ".join(value.split())
    sentences = [segment.strip() for segment in re.split(r"(?<=[。！？!?])", normalized) if segment.strip()]
    summary = "".join(sentences[:max_sentences])
    if len(summary) <= max_chars:
        return summary
    if max_chars == 1:
        return "…"
    return f"{summary[: max_chars - 1].rstrip()}…"
