from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from framework.connections.exceptions import CommandParseError


@dataclass(slots=True)
class Command:
    command: str
    timeout: float | None = None
    expected_prompts: list[str] | None = None
    parser: Callable[[str], dict[str, Any]] | None = None

    def parse(self, output: str) -> dict[str, Any]:
        if self.parser is None:
            return {"raw_output": output}
        try:
            return self.parser(output)
        except Exception as exc:  # noqa: BLE001
            raise CommandParseError(
                f"Failed to parse output for command: {self.command!r}"
            ) from exc