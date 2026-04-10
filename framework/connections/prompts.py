from __future__ import annotations

import re
from dataclasses import dataclass

ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
BACKSPACE_RE = re.compile(r".\x08")
CRLF_RE = re.compile(r"\r\n?")
MORE_RE = re.compile(r"--More--|-- More --|\(q to quit\)", re.IGNORECASE)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def normalize_text(text: str) -> str:
    """
    Normalize common terminal noise:
    - CRLF -> LF
    - ANSI escapes removed
    - backspace overstrikes removed iteratively
    """
    text = CRLF_RE.sub("\n", text)
    text = strip_ansi(text)
    prev = None
    while prev != text:
        prev = text
        text = BACKSPACE_RE.sub("", text)
    return text


@dataclass(slots=True, frozen=True)
class PromptMatcher:
    patterns: tuple[re.Pattern[str], ...]

    @classmethod
    def from_patterns(cls, patterns: tuple[str, ...]) -> "PromptMatcher":
        return cls(patterns=tuple(re.compile(p, re.MULTILINE) for p in patterns))

    def search(self, text: str) -> re.Match[str] | None:
        for rx in self.patterns:
            match = rx.search(text)
            if match:
                return match
        return None

    def matches_tail(self, text: str) -> bool:
        tail = text[-2048:] if len(text) > 2048 else text
        return self.search(tail) is not None