"""Structure-aware, token-bounded chunking (ADR-0004).

1. Group blocks into sections by heading path.
2. Inside a section, pack blocks into chunks of at most `max_tokens`, cutting
   only between blocks; a block too large on its own is split on lines, then
   sentences, then (last resort) token boundaries.
3. Each new chunk starts with up to `overlap_tokens` from the end of the
   previous one, so an answer straddling a boundary is still retrievable.
4. A section's last chunk with less than `min_tokens` of its own content is
   rebalanced with its predecessor, so no chunk is a near-empty leftover.

Deterministic: the same document always yields the same chunks, which makes
re-ingestion idempotent and evaluation reproducible. Bump `CHUNKER_VERSION`
whenever the output can change; it is stored on every indexed version.
"""

import re
from dataclasses import dataclass, field

from kb_assistant.domain.documents import Block, BlockKind, ChunkDraft, ParsedDocument
from kb_assistant.domain.ports import Tokenizer

CHUNKER_VERSION = "structure-v2"
_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")
_JOIN = "\n\n"


@dataclass(frozen=True, slots=True)
class ChunkingPolicy:
    max_tokens: int = 512
    overlap_tokens: int = 64
    min_tokens: int = 50

    def __post_init__(self) -> None:
        if not 0 <= self.overlap_tokens < self.max_tokens:
            raise ValueError("overlap_tokens must be smaller than max_tokens")
        if not 0 <= self.min_tokens <= self.max_tokens:
            raise ValueError("min_tokens must be between 0 and max_tokens")


@dataclass(slots=True)
class _Piece:
    text: str
    page: int | None
    tokens: int


@dataclass(slots=True)
class _Section:
    heading_path: tuple[str, ...]
    blocks: list[Block] = field(default_factory=list)


def _sections(blocks: tuple[Block, ...]) -> list[_Section]:
    sections = [_Section(heading_path=())]
    stack: list[tuple[int, str]] = []
    for block in blocks:
        if block.kind is BlockKind.HEADING:
            level = max(block.level, 1)
            stack = [(lvl, txt) for lvl, txt in stack if lvl < level]
            stack.append((level, block.text))
            sections.append(_Section(heading_path=tuple(txt for _, txt in stack)))
        else:
            sections[-1].blocks.append(block)
    return [s for s in sections if s.blocks]


class Chunker:
    def __init__(self, tokenizer: Tokenizer, policy: ChunkingPolicy) -> None:
        self._tok = tokenizer
        self._policy = policy

    def chunk(self, document: ParsedDocument) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        for section in _sections(document.blocks):
            for pieces in self._pack(self._pieces(section.blocks)):
                drafts.append(self._draft(len(drafts), section.heading_path, pieces))
        return drafts

    # ------------------------------------------------------------------ pieces

    def _pieces(self, blocks: list[Block]) -> list[_Piece]:
        pieces: list[_Piece] = []
        for block in blocks:
            for text in self._fit(block.text):
                pieces.append(_Piece(text=text, page=block.page, tokens=self._tok.count(text)))
        return pieces

    def _fit(self, text: str) -> list[str]:
        """Split `text` into parts that each fit in `max_tokens`, coarsest boundary first."""
        limit = self._policy.max_tokens
        if self._tok.count(text) <= limit:
            return [text]
        for separator in ("\n", _SENTENCE_END):
            parts = text.split(separator) if isinstance(separator, str) else separator.split(text)
            parts = [p for p in (part.strip() for part in parts) if p]
            if len(parts) > 1:
                return self._regroup(parts, separator if isinstance(separator, str) else " ")
        return self._tok.split(text, limit)

    def _regroup(self, parts: list[str], glue: str) -> list[str]:
        """Re-join small parts greedily, recursing into parts still too large."""
        out: list[str] = []
        current = ""
        for part in parts:
            for fitted in self._fit(part):
                candidate = f"{current}{glue}{fitted}" if current else fitted
                if current and self._tok.count(candidate) > self._policy.max_tokens:
                    out.append(current)
                    current = fitted
                else:
                    current = candidate
        if current:
            out.append(current)
        return out

    # ------------------------------------------------------------------ packing

    def _pack(self, pieces: list[_Piece]) -> list[list[_Piece]]:
        # Each chunk is kept with the number of leading pieces carried over as overlap.
        chunks: list[tuple[list[_Piece], int]] = []
        current: list[_Piece] = []
        carried = 0
        for piece in pieces:
            if len(current) > carried and (
                self._tokens([*current, piece]) > self._policy.max_tokens
            ):
                chunks.append((current, carried))
                current = self._overlap(current, piece)
                carried = len(current)
            current.append(piece)
        if len(current) > carried:
            chunks.append((current, carried))
        return self._rebalance_tail(chunks)

    def _overlap(self, previous: list[_Piece], following: _Piece) -> list[_Piece]:
        budget = self._policy.overlap_tokens
        if budget == 0:
            return []
        carried: list[_Piece] = []
        for piece in reversed(previous):
            if self._tokens([piece, *carried]) > budget:
                if not carried:
                    tail = self._tok.tail(piece.text, budget)
                    carried = [_Piece(tail, piece.page, self._tok.count(tail))]
                break
            carried.insert(0, piece)
        # Never let the overlap push the next chunk over the limit.
        while carried and self._tokens([*carried, following]) > self._policy.max_tokens:
            carried.pop(0)
        return carried

    def _rebalance_tail(self, chunks: list[tuple[list[_Piece], int]]) -> list[list[_Piece]]:
        """Even out the last two chunks when the last one has almost no content of its own.

        Greedy packing only opens a chunk when the next piece does not fit, so
        merging a small tail back can never fit either. Instead, the pieces of
        the last two chunks are re-cut at the boundary that balances them best,
        keeping both under `max_tokens`.
        """
        plain = [pieces for pieces, _ in chunks]
        if len(chunks) < 2:
            return plain
        (previous, previous_carried), (last, last_carried) = chunks[-2], chunks[-1]
        if self._tokens(last[last_carried:]) >= self._policy.min_tokens:
            return plain
        head, pool = previous[:previous_carried], previous[previous_carried:] + last[last_carried:]
        best: tuple[int, list[_Piece], list[_Piece]] | None = None
        for cut in range(1, len(pool)):
            left = head + pool[:cut]
            right = self._overlap(left, pool[cut]) + pool[cut:]
            left_tokens, right_tokens = self._tokens(left), self._tokens(right)
            if left_tokens > self._policy.max_tokens or right_tokens > self._policy.max_tokens:
                continue
            gap = abs(left_tokens - self._tokens(pool[cut:]))
            if best is None or gap < best[0]:
                best = (gap, left, right)
        if best is None:
            return plain
        return [*plain[:-2], best[1], best[2]]

    def _tokens(self, pieces: list[_Piece]) -> int:
        return self._tok.count(_JOIN.join(p.text for p in pieces))

    def _draft(self, index: int, path: tuple[str, ...], pieces: list[_Piece]) -> ChunkDraft:
        text = _JOIN.join(p.text for p in pieces)
        pages = [p.page for p in pieces if p.page is not None]
        return ChunkDraft(
            index=index,
            text=text,
            heading_path=path,
            page_start=min(pages) if pages else None,
            page_end=max(pages) if pages else None,
            token_count=self._tok.count(text),
        )
