from __future__ import annotations

import re

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
BACKSPACE_RE = re.compile(r".\x08")


def normalize_output(text: str) -> str:
    """
    Normalize interactive CLI output:
      - remove ANSI escapes
      - normalize CRLF / CR to LF
      - collapse backspace artifacts
    """
    text = ANSI_ESCAPE_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    while True:
        new_text = BACKSPACE_RE.sub("", text)
        if new_text == text:
            break
        text = new_text
    return text