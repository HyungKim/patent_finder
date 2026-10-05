"""분할기가 쓰는 tokenizer 인터페이스.

스펙 5.1: 글자 수를 토큰 수로 간주하지 않고 실제 모델 tokenizer로 길이를 잰다.
운영에서는 embeddings.e5.E5Tokenizer만 사용한다.
"""
from __future__ import annotations

from typing import Protocol


class TokenCounter(Protocol):
    #: 접두사("query: ")와 특수 토큰이 차지하는 토큰 수
    overhead: int

    def offsets(self, text: str) -> list[tuple[int, int]]:
        """특수 토큰을 제외한 각 토큰의 문자 구간."""
        ...

    def count(self, text: str) -> int:
        """특수 토큰을 제외한 토큰 수."""
        ...


def head(text: str, counter: TokenCounter, max_tokens: int) -> str:
    """앞에서부터 max_tokens 토큰까지만 남긴다."""
    if max_tokens <= 0:
        return ""
    spans = counter.offsets(text)
    if len(spans) <= max_tokens:
        return text
    return text[: spans[max_tokens - 1][1]].rstrip()


def tail(text: str, counter: TokenCounter, max_tokens: int) -> str:
    """뒤에서부터 max_tokens 토큰까지만 남긴다."""
    if max_tokens <= 0:
        return ""
    spans = counter.offsets(text)
    if len(spans) <= max_tokens:
        return text
    return text[spans[-max_tokens][0]:].lstrip()
