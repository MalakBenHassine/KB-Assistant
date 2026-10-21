"""Instruction-like passages in documents (SECURITY.md T2, ADR-0017).

A document is data. A paragraph that talks to the assistant instead of to
employees ("ignore all previous instructions", "you are now in maintenance
mode", "reveal your system prompt") is removed from the text that is chunked
and indexed. The original file stays unchanged in object storage, and the
version records how many passages were quarantined, so an administrator can
see that something was removed and review it.

A paragraph is quarantined on one *strong* signal (a phrasing with no
legitimate use in a company document) or two *weak* ones (phrasings that are
suspicious together but ordinary alone, like "tell every user").

This filter catches known phrasings; it is not the security boundary. The
boundaries are authorization (the model only ever sees what the asker may read)
and citation validation. A false positive hides one paragraph from search,
visibly; a false negative is what the other layers are for.
"""

import re
from dataclasses import dataclass

from kb_assistant.domain.documents import ParsedDocument

_STRONG = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # "ignore all previous instructions", "disregard the above rules"
        r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+)?(?:of\s+)?"
        r"(?:the\s+|your\s+|my\s+)?(?:previous|prior|above|earlier|preceding|system|original)\s+"
        r"(?:instructions?|prompts?|rules|directions|guidelines)\b",
        r"\b(?:ignore|disregard|forget)\s+(?:all|everything)\s+(?:you\s+)?(?:were|have\s+been)\s+told\b",
        # "you are now in maintenance mode", "switch to developer mode"
        r"\byou\s+are\s+now\s+(?:in\s+\w+\s+mode|an?\s+(?:ai|assistant|chatbot|unrestricted)\b)",
        r"\b(?:enter|switch\s+to|activate|enable)\s+(?:developer|maintenance|debug|jailbreak|god|dan)\s+mode\b",
        # "print your system prompt"
        r"\b(?:reveal|print|show|repeat|output|disclose)\b[^.!?\n]{0,30}"
        r"\b(?:system|developer|hidden)\s+(?:prompt|message|instructions)\b",
    )
)
_WEAK = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:tell|inform|answer|reply\s+to|respond\s+to|say\s+to)\s+(?:every|all|any|each)\s+users?\b",
        r"\b(?:new|updated|real|actual)\s+instructions?\s*:",
        r"\b(?:as\s+an?|you\s+are\s+an?)\s+(?:ai|assistant|chatbot|language\s+model|llm)\b",
        r"\b(?:do\s+not|don't|never)\s+(?:tell|reveal|mention|disclose)\b[^.!?\n]{0,30}"
        r"\b(?:the\s+user|users|anyone)\b",
        r"\b(?:maintenance|developer|debug)\s+mode\b",
    )
)
WEAK_SIGNALS_TO_QUARANTINE = 2


def looks_like_instructions(text: str) -> bool:
    if any(p.search(text) for p in _STRONG):
        return True
    return sum(1 for p in _WEAK if p.search(text)) >= WEAK_SIGNALS_TO_QUARANTINE


@dataclass(frozen=True, slots=True)
class Quarantined:
    document: ParsedDocument
    passages: int  # blocks removed


def quarantine_instructions(parsed: ParsedDocument) -> Quarantined:
    """Remove every block that reads as instructions to the assistant."""
    kept = tuple(b for b in parsed.blocks if not looks_like_instructions(b.text))
    return Quarantined(
        document=ParsedDocument(blocks=kept, page_count=parsed.page_count),
        passages=len(parsed.blocks) - len(kept),
    )
