"""Conversation memory: a rolling summary that lets follow-ups make sense.

"And for parental leave?" means nothing alone. The summary of the previous
questions goes into the classify, rewrite and answer prompts, so the rewrite
can turn it into "How long is parental leave?".

It is built without a model call: the last few questions, verbatim and
bounded. A model-written summary would read better but cost a call per turn
and could drift from what was asked; the questions themselves cannot.
"""

DEFAULT_TITLE = "New conversation"
MEMORY_TURNS = 3
MAX_REMEMBERED_CHARS = 300
MAX_TITLE_CHARS = 60
_HEADER = "Earlier questions in this conversation:"


def remember(previous: str | None, question: str) -> str:
    """The summary after `question`: the last `MEMORY_TURNS` questions, oldest first."""
    earlier = [line[2:] for line in (previous or "").splitlines() if line.startswith("- ")]
    current = " ".join(question.split())[:MAX_REMEMBERED_CHARS]
    kept = [*earlier, current][-MEMORY_TURNS:]
    return "\n".join([_HEADER, *(f"- {q}" for q in kept)])


def title_from(current_title: str, question: str) -> str | None:
    """A title from the first question, unless the user chose one."""
    if current_title != DEFAULT_TITLE:
        return None
    words = " ".join(question.split())
    if len(words) <= MAX_TITLE_CHARS:
        return words
    cut = words[:MAX_TITLE_CHARS].rsplit(" ", 1)[0]
    return f"{cut}…"
