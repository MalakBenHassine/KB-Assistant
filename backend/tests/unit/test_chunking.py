"""The chunker, with a one-word-one-token tokenizer so every limit is easy to check by eye."""

import itertools

import pytest

from kb_assistant.application.chunking import Chunker, ChunkingPolicy
from kb_assistant.domain.documents import Block, BlockKind, ParsedDocument
from kb_assistant.infrastructure.tokenizer import TiktokenTokenizer
from tests.fakes import WordTokenizer


def words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def doc(*blocks: Block, pages: int | None = None) -> ParsedDocument:
    return ParsedDocument(blocks=blocks, page_count=pages)


def heading(text: str, level: int) -> Block:
    return Block(BlockKind.HEADING, text, level=level)


def para(text: str, page: int | None = None) -> Block:
    return Block(BlockKind.PARAGRAPH, text, page=page)


def chunker(max_tokens: int = 50, overlap: int = 10, min_tokens: int = 5) -> Chunker:
    return Chunker(WordTokenizer(), ChunkingPolicy(max_tokens, overlap, min_tokens))


def test_heading_path_follows_the_outline() -> None:
    chunks = chunker().chunk(
        doc(
            heading("Leave policy", 1),
            para("Annual leave is 25 days."),
            heading("Parental leave", 2),
            heading("Eligibility", 3),
            para("Six months of service."),
            heading("Sick leave", 2),
            para("Five self-certified days."),
        )
    )
    assert [c.heading_path for c in chunks] == [
        ("Leave policy",),
        ("Leave policy", "Parental leave", "Eligibility"),
        ("Leave policy", "Sick leave"),
    ]


def test_embedding_text_carries_the_heading_path_but_the_chunk_text_does_not() -> None:
    (chunk,) = chunker().chunk(doc(heading("Leave", 1), para("Twenty five days.")))
    assert chunk.text == "Twenty five days."
    assert chunk.embedding_text.startswith("Leave")
    assert chunk.embedding_text.endswith("Twenty five days.")


def test_no_chunk_exceeds_the_limit_and_nothing_is_lost() -> None:
    blocks = [para(words(12, f"p{n}_")) for n in range(20)]
    chunks = chunker(max_tokens=50, overlap=10).chunk(doc(*blocks))
    assert len(chunks) > 1
    assert all(c.token_count <= 50 for c in chunks)
    seen = {w for c in chunks for w in c.text.split()}
    assert seen == {w for b in blocks for w in b.text.split()}


def test_consecutive_chunks_overlap() -> None:
    blocks = [para(words(8, f"p{n}_")) for n in range(12)]
    chunks = chunker(max_tokens=30, overlap=8).chunk(doc(*blocks))
    for previous, following in itertools.pairwise(chunks):
        assert set(previous.text.split()) & set(following.text.split())


def test_zero_overlap_means_disjoint_chunks() -> None:
    blocks = [para(words(8, f"p{n}_")) for n in range(12)]
    chunks = chunker(max_tokens=30, overlap=0).chunk(doc(*blocks))
    for previous, following in itertools.pairwise(chunks):
        assert not set(previous.text.split()) & set(following.text.split())


def test_an_oversized_paragraph_is_split_on_sentences() -> None:
    sentences = [f"Sentence {n} " + words(9, f"s{n}_") + "." for n in range(10)]
    (block,) = [para(" ".join(sentences))]
    chunks = chunker(max_tokens=30, overlap=0).chunk(doc(block))
    assert all(c.token_count <= 30 for c in chunks)
    # Cuts fall between sentences: every chunk ends at a full stop.
    assert all(c.text.endswith(".") for c in chunks)


def test_text_without_any_break_is_split_on_token_boundaries() -> None:
    chunks = chunker(max_tokens=20, overlap=0).chunk(doc(para(words(70))))
    assert [c.token_count for c in chunks] == [20, 20, 20, 10]


def test_a_tiny_tail_is_rebalanced_with_its_predecessor() -> None:
    # Greedy packing alone would give [20 + 9] and a 3-word leftover.
    chunks = chunker(max_tokens=30, overlap=0, min_tokens=5).chunk(
        doc(para(words(20, "a")), para(words(9, "b")), para(words(3, "c")))
    )
    assert [c.token_count for c in chunks] == [20, 12]


def test_rebalancing_keeps_the_overlap_and_the_limit() -> None:
    # Greedy: [a10 b10] then [overlap(4) + c3], whose own content is 3 words.
    chunks = chunker(max_tokens=22, overlap=4, min_tokens=5).chunk(
        doc(para(words(10, "a")), para(words(10, "b")), para(words(3, "c")))
    )
    assert [c.token_count for c in chunks] == [10, 17]
    # The overlap is the last 4 words of the first chunk, not a remainder.
    assert chunks[1].text.split()[:4] == ["a6", "a7", "a8", "a9"]


def test_a_tail_that_cannot_be_rebalanced_is_kept() -> None:
    # One 28-word block then 3 words: no boundary leaves both chunks under 30.
    chunks = chunker(max_tokens=30, overlap=0, min_tokens=5).chunk(
        doc(para(words(28, "a")), para(words(3, "b")))
    )
    assert [c.token_count for c in chunks] == [28, 3]


def test_pages_are_carried_to_the_chunk() -> None:
    chunks = chunker(max_tokens=100).chunk(
        doc(para("First page text.", page=1), para("Second page text.", page=2), pages=2)
    )
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 2)


def test_chunks_are_numbered_across_sections() -> None:
    chunks = chunker(max_tokens=20, overlap=0).chunk(
        doc(heading("A", 1), para(words(30)), heading("B", 1), para(words(30)))
    )
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_output_is_deterministic() -> None:
    document = doc(*[para(words(13, f"p{n}_")) for n in range(15)])
    assert chunker().chunk(document) == chunker().chunk(document)


def test_empty_document_has_no_chunks() -> None:
    assert chunker().chunk(doc(heading("Only a title", 1))) == []


def test_policy_rejects_overlap_not_smaller_than_the_limit() -> None:
    with pytest.raises(ValueError, match="overlap"):
        ChunkingPolicy(max_tokens=100, overlap_tokens=100)


def test_with_the_real_tokenizer_limits_hold_in_model_tokens() -> None:
    tokenizer = TiktokenTokenizer()
    text = " ".join(
        f"Clause {n}: employees may request remote work twice a week." for n in range(200)
    )
    chunks = Chunker(tokenizer, ChunkingPolicy(128, 16, 10)).chunk(doc(para(text)))
    assert all(tokenizer.count(c.text) <= 128 for c in chunks)


def test_tiktoken_tail_returns_the_last_tokens() -> None:
    tokenizer = TiktokenTokenizer()
    text = " ".join(f"word{n}" for n in range(50))
    tail = tokenizer.tail(text, 5)
    assert tokenizer.count(tail) <= 5
    assert text.endswith(tail)
