"""The evaluation dataset: questions, who asks them, and what a correct reply is.

One JSON object per line in `eval/dataset.jsonl`. Every question names the
asker's tenant, role and departments, because the same question has different
correct replies for different people: "How many days of paid leave?" is
answerable for HR at Acme, answerable with another number at Globex, and must
be refused to an Acme engineer.
"""

import json
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Category(StrEnum):
    ANSWERABLE = "answerable"  # the answer is in a document the asker can read
    RESTRICTED = "restricted"  # the answer exists, but only where the asker cannot read
    UNANSWERABLE = "unanswerable"  # a work question no document answers
    OUT_OF_SCOPE = "out_of_scope"  # not a question about the organization
    CHITCHAT = "chitchat"  # a greeting or thanks


class Behaviour(StrEnum):
    ANSWER = "answer"  # a cited answer
    ABSTAIN = "abstain"  # "I don't know", a clarification request or a refusal
    REPLY = "reply"  # a friendly reply with no sources


EXPECTED_BEHAVIOUR = {
    Category.ANSWERABLE: Behaviour.ANSWER,
    Category.RESTRICTED: Behaviour.ABSTAIN,
    Category.UNANSWERABLE: Behaviour.ABSTAIN,
    Category.OUT_OF_SCOPE: Behaviour.ABSTAIN,
    Category.CHITCHAT: Behaviour.REPLY,
}


class EvalQuestion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    category: Category
    tenant: str
    role: Literal["employee", "tenant_admin"] = "employee"
    departments: tuple[str, ...] = ()
    question: str = Field(min_length=1)
    # Corpus paths (`<tenant>/<department>/<file>`) that answer the question.
    expected_documents: tuple[str, ...] = ()
    # Facts a correct answer states, and facts that would mean a leak or a mix-up.
    must_include: tuple[str, ...] = ()
    must_not_include: tuple[str, ...] = ()

    @property
    def expected(self) -> Behaviour:
        return EXPECTED_BEHAVIOUR[self.category]

    @model_validator(mode="after")
    def _consistent(self) -> "EvalQuestion":
        answerable = self.category is Category.ANSWERABLE
        if answerable and not (self.expected_documents and self.must_include):
            raise ValueError(f"{self.id}: an answerable question needs documents and facts")
        if not answerable and (self.expected_documents or self.must_include):
            raise ValueError(f"{self.id}: only answerable questions have expected answers")
        if self.role == "tenant_admin" and self.departments:
            raise ValueError(f"{self.id}: a tenant admin reads every department; list none")
        return self


def load_dataset(path: Path, corpus: Path) -> list[EvalQuestion]:
    """Parse and check the dataset against the corpus it refers to."""
    questions: list[EvalQuestion] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            questions.append(EvalQuestion.model_validate(json.loads(line)))
        except ValueError as exc:
            raise ValueError(f"{path.name}:{number}: {exc}") from exc
    ids = [q.id for q in questions]
    if duplicates := sorted({i for i in ids if ids.count(i) > 1}):
        raise ValueError(f"duplicate question ids: {duplicates}")
    for question in questions:
        for document in question.expected_documents:
            if not (corpus / document).is_file():
                raise ValueError(f"{question.id}: {document} is not in the corpus")
            tenant, department, _ = document.split("/")
            readable = question.role == "tenant_admin" or department in question.departments
            if tenant != question.tenant or not readable:
                raise ValueError(f"{question.id}: the asker cannot read {document}")
    return questions


def corpus_documents(corpus: Path) -> list[str]:
    """Every document of the corpus as `<tenant>/<department>/<file>`, sorted."""
    return sorted(p.relative_to(corpus).as_posix() for p in corpus.glob("*/*/*.md"))
