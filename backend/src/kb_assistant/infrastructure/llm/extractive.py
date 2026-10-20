"""An offline, deterministic stand-in for a language model.

It lets the whole system (tests, CI, the demo stack) run without a paid key,
and it behaves like a very cautious model:
- intent: greetings are chitchat, everything else is a knowledge question;
- rewriting: the question itself;
- answering: for each of the first sources, the sentence sharing the most
  content words with the question, cited; `INSUFFICIENT_EVIDENCE` if none
  shares any.

It reads the prompt format of `application/answering/prompts.py`, which it is
a test double for. It is extractive by construction, so it cannot invent a
fact; it also cannot paraphrase, summarise or combine sources.
"""

import re
from collections.abc import AsyncIterator

from kb_assistant.domain.chat import JsonSchema, StreamChunk, StructuredReply, TokenUsage

_GREETING = re.compile(
    r"^\s*(hi|hello|hey|bonjour|salut|good (morning|afternoon|evening)|thanks?|thank you|merci)"
    r"\b[\s!.,?]*$",
    re.IGNORECASE,
)
_QUESTION = re.compile(r"<question>\n(.*?)\n</question>", re.DOTALL)
_DOCUMENT = re.compile(r'<document id="(S\d+)"[^>]*>\n(.*?)\n</document nonce=', re.DOTALL)
_SENTENCES = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-zà-ÿ0-9]+", re.IGNORECASE)
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "many",
        "much",
        "of",
        "on",
        "or",
        "our",
        "the",
        "to",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
        "my",
        "we",
    ]
)
_MAX_SOURCES_USED = 2


def _content_words(text: str) -> set[str]:
    return {w for w in (m.lower() for m in _WORD.findall(text)) if w not in _STOPWORDS}


class ExtractiveChatModel:
    @property
    def model_id(self) -> str:
        return "local-extractive-v1"

    async def structured(
        self, *, system: str, user: str, schema: JsonSchema, max_output_tokens: int
    ) -> StructuredReply:
        del system, max_output_tokens  # the port's signature; unused offline
        question_match = _QUESTION.search(user)
        question = question_match.group(1).strip() if question_match else ""
        if schema.name == "intent":
            intent = "chitchat" if _GREETING.match(question) else "kb_question"
            return StructuredReply({"intent": intent}, TokenUsage())
        if schema.name == "rewrite":
            return StructuredReply({"query": question}, TokenUsage())
        if schema.name == "relevance":
            return StructuredReply({"relevant": True}, TokenUsage())
        return StructuredReply({}, TokenUsage())

    async def stream(
        self, *, system: str, user: str, max_output_tokens: int
    ) -> AsyncIterator[StreamChunk]:
        del system, max_output_tokens  # the port's signature; unused offline
        question_match = _QUESTION.search(user)
        wanted = _content_words(question_match.group(1)) if question_match else set()
        # One shared word is a coincidence ("leave" in a table of leave types); ask
        # for two when the question has two or more content words.
        needed = min(2, len(wanted)) or 1
        answer: list[str] = []
        for alias, text in _DOCUMENT.findall(user)[:_MAX_SOURCES_USED]:
            best, best_overlap = "", needed - 1
            # A paragraph may be wrapped over several lines: sentences end at punctuation.
            for sentence in _SENTENCES.split(" ".join(text.split())):
                overlap = len(_content_words(sentence) & wanted)
                if overlap > best_overlap:
                    best, best_overlap = sentence.strip(), overlap
            if best:
                body = best.rstrip(".!?")
                answer.append(f"{body} [{alias}].")
        text = " ".join(answer) if answer else "INSUFFICIENT_EVIDENCE"
        # Emit in small pieces, as a real stream would.
        for start in range(0, len(text), 12):
            yield StreamChunk(text=text[start : start + 12])
        yield StreamChunk(usage=TokenUsage())
