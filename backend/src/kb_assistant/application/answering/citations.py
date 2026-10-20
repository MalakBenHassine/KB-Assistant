"""Sentence-level citation checking (ARCHITECTURE §5.4).

The model sees sources under short aliases (`S1`…`Sn`) and must cite them in
square brackets. A sentence is kept only if it cites at least one alias that
was actually in the prompt; citations to anything else are removed. A sentence
with no valid citation is dropped: it is either filler or a claim with no
source, and neither belongs in a grounded answer.

Streaming is sentence-granular: `SentenceBuffer` releases a sentence only once
it is complete (including citation markers written after the full stop), so
the client never sees text that this check would later retract.
"""

import re
from dataclasses import dataclass

_MARKER = re.compile(r"\[(S\d{1,3})\]")
# A sentence ends at . ! ? (plus any citation markers written after it) followed
# by whitespace, or at a line break. Group 1 is what stays with the sentence.
_BOUNDARY = re.compile(r"(?<=[.!?])((?:[ \t]*\[S\d{1,3}\])*)\s+|\n+")
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "mr.", "mrs.", "dr.", "no.", "approx.")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?])")
_SPACES = re.compile(r"[ \t]{2,}")


@dataclass(frozen=True, slots=True)
class CheckedSentence:
    text: str
    aliases: tuple[str, ...]  # valid aliases, in order of first use


def check_sentence(raw: str, allowed: set[str]) -> CheckedSentence | None:
    cited = list(dict.fromkeys(_MARKER.findall(raw)))
    valid = tuple(a for a in cited if a in allowed)
    if not valid:
        return None
    text = _MARKER.sub(lambda m: m.group(0) if m.group(1) in allowed else "", raw)
    text = _SPACES.sub(" ", _SPACE_BEFORE_PUNCT.sub(r"\1", text)).strip()
    return CheckedSentence(text=text, aliases=valid)


class SentenceBuffer:
    """Accumulates streamed text and releases complete sentences."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        released: list[str] = []
        while True:
            cut = self._next_boundary()
            if cut is None:
                return released
            sentence, self._buffer = self._buffer[: cut[0]].strip(), self._buffer[cut[1] :]
            if sentence:
                released.append(sentence)

    def flush(self) -> list[str]:
        rest, self._buffer = self._buffer.strip(), ""
        return [rest] if rest else []

    def _next_boundary(self) -> tuple[int, int] | None:
        for match in _BOUNDARY.finditer(self._buffer):
            end = match.end()
            if end >= len(self._buffer):
                return None  # need the next character to decide
            if self._buffer[end] == "[":
                continue  # more citation markers may still be arriving for this sentence
            head = self._buffer[: match.start()].lower()
            if head.endswith(_ABBREVIATIONS):
                continue
            markers = match.group(1)
            return (match.end(1) if markers else match.start()), end
        return None
