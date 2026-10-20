import pytest

from kb_assistant.application.answering.citations import SentenceBuffer, check_sentence

ALLOWED = {"S1", "S2"}


def test_a_cited_sentence_is_kept() -> None:
    checked = check_sentence("Annual leave is 25 days [S1].", ALLOWED)
    assert checked is not None
    assert checked.text == "Annual leave is 25 days [S1]."
    assert checked.aliases == ("S1",)


def test_unknown_aliases_are_removed_from_a_supported_sentence() -> None:
    checked = check_sentence("Leave is 25 days [S1][S7].", ALLOWED)
    assert checked is not None
    assert checked.text == "Leave is 25 days [S1]."
    assert checked.aliases == ("S1",)


@pytest.mark.parametrize(
    "sentence",
    ["Leave is 25 days.", "Leave is 25 days [S9].", "As an AI model, I think [Source 1]."],
)
def test_a_sentence_without_a_valid_citation_is_dropped(sentence: str) -> None:
    assert check_sentence(sentence, ALLOWED) is None


def test_aliases_are_listed_once_in_order_of_use() -> None:
    checked = check_sentence("A [S2] and B [S1] and C [S2].", ALLOWED)
    assert checked is not None
    assert checked.aliases == ("S2", "S1")


def feed_all(text: str, step: int = 5) -> list[str]:
    buffer = SentenceBuffer()
    out: list[str] = []
    for i in range(0, len(text), step):
        out += buffer.feed(text[i : i + step])
    return out + buffer.flush()


def test_sentences_are_released_whole_across_arbitrary_chunks() -> None:
    text = "Leave is 25 days [S1]. Carry-over ends in March [S1]! Ask HR [S2]."
    assert feed_all(text, step=3) == [
        "Leave is 25 days [S1].",
        "Carry-over ends in March [S1]!",
        "Ask HR [S2].",
    ]


def test_citations_after_the_full_stop_stay_with_their_sentence() -> None:
    assert feed_all("Leave is 25 days. [S1] Ask HR. [S2]") == [
        "Leave is 25 days. [S1]",
        "Ask HR. [S2]",
    ]


def test_abbreviations_and_decimals_do_not_end_a_sentence() -> None:
    assert feed_all("Use e.g. the portal for 2.5 days [S1]. Done [S2].") == [
        "Use e.g. the portal for 2.5 days [S1].",
        "Done [S2].",
    ]


def test_list_items_on_separate_lines_are_separate_sentences() -> None:
    assert feed_all("- Tell your manager [S1]\n- Submit the form [S2]\n") == [
        "- Tell your manager [S1]",
        "- Submit the form [S2]",
    ]


def test_nothing_is_released_until_the_sentence_is_complete() -> None:
    buffer = SentenceBuffer()
    assert buffer.feed("Leave is 25 days [S1].") == []  # could still be followed by "[S2]"
    assert buffer.feed(" Next") == ["Leave is 25 days [S1]."]
    assert buffer.flush() == ["Next"]
