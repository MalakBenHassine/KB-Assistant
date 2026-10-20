"""Token counting with the embedding model's own tokenizer (ADR-0004).

`text-embedding-3-*` uses `cl100k_base`. tiktoken downloads the encoding on
first use; the container image pre-fetches it (TIKTOKEN_CACHE_DIR) so that no
process needs network access to count tokens.
"""

import tiktoken


class TiktokenTokenizer:
    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self._encoding = tiktoken.get_encoding(encoding_name)

    def count(self, text: str) -> int:
        # Special-token strings in a document are text, not control tokens.
        return len(self._encoding.encode(text, disallowed_special=()))

    def split(self, text: str, max_tokens: int) -> list[str]:
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        ids = self._encoding.encode(text, disallowed_special=())
        return [
            self._encoding.decode(ids[start : start + max_tokens])
            for start in range(0, len(ids), max_tokens)
        ]

    def tail(self, text: str, max_tokens: int) -> str:
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        ids = self._encoding.encode(text, disallowed_special=())
        return self._encoding.decode(ids[-max_tokens:])
