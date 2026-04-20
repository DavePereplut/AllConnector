from __future__ import annotations

import re
import time
from typing import Any, Sequence

from framework.events.base import BaseEvent, ParsedEvent


EVENT_BLOCK_RE = re.compile(
    r"""
    Flow:\s*(?P<flow>.+?)\n
    Time:\s*(?P<time>.+?)\n
    Message:\s*(?P<message>[^\n]+)\n
    (?P<fields>(?:[ \t]+[^\n]+:\s*[^\n]*\n?)*)
    """,
    re.MULTILINE | re.VERBOSE,
)


FIELD_RE = re.compile(r"^[ \t]+(?P<key>[^:\n]+):\s*(?P<value>[^\n]*)$", re.MULTILINE)


def parse_event_block(
    block_text: str,
    *,
    event_name: str,
    start: int,
    end: int,
) -> ParsedEvent:
    match = EVENT_BLOCK_RE.search(block_text)
    if not match:
        raise ValueError(f"Unable to parse event block for {event_name!r}")

    fields_text = match.group("fields")
    fields: dict[str, str] = {}

    for field_match in FIELD_RE.finditer(fields_text):
        key = field_match.group("key").strip()
        value = field_match.group("value").strip()
        fields[key] = value

    return ParsedEvent(
        event_name=event_name,
        raw_text=block_text,
        start=start,
        end=end,
        timestamp=time.time(),
        data={
            "flow": match.group("flow").strip(),
            "time": match.group("time").strip(),
            "message": match.group("message").strip(),
            "fields": fields,
        },
    )


class ParsedConsoleEvent(BaseEvent):
    """
    Base class for console event blocks like:

    Flow: ...
    Time: ...
    Message: EventLogin
      User: admin
      MID: d12
    """

    message_name: str

    def __init__(self, message_name: str, name: str | None = None) -> None:
        super().__init__(name=name or message_name)
        self.message_name = message_name

        self._event_re = re.compile(
            rf"""
            Flow:\s*.+?\n
            Time:\s*.+?\n
            Message:\s*{re.escape(self.message_name)}\n
            (?:[ \t]+[^\n]+:\s*[^\n]*\n?)*
            """,
            re.MULTILINE | re.VERBOSE,
        )

    def parse(
        self,
        buffer_text: str,
        *,
        connection,
        start_pos: int = 0,
        previous_events: Sequence[ParsedEvent] | None = None,
    ) -> ParsedEvent | None:
        match = self._event_re.search(buffer_text, start_pos)
        if not match:
            return None

        parsed = parse_event_block(
            match.group(0),
            event_name=self.name,
            start=match.start(),
            end=match.end(),
        )

        self.validate(parsed, previous_events or [])
        return parsed

    def validate(
        self,
        parsed: ParsedEvent,
        previous_events: Sequence[ParsedEvent],
    ) -> None:
        """
        Override in subclasses if you need cross-event validation.
        """
        return


class EventLogin(ParsedConsoleEvent):
    def __init__(self) -> None:
        super().__init__(message_name="EventLogin", name="EventLogin")


class EventLoginSuccess(ParsedConsoleEvent):
    def __init__(self) -> None:
        super().__init__(message_name="EventLoginSuccess", name="EventLoginSuccess")

    def validate(
        self,
        parsed: ParsedEvent,
        previous_events: Sequence[ParsedEvent],
    ) -> None:
        login_event = next(
            (event for event in reversed(previous_events) if event.event_name == "EventLogin"),
            None,
        )
        if login_event is None:
            raise ValueError("EventLoginSuccess appeared before EventLogin")

        login_fields = login_event.data.get("fields", {})
        current_fields = parsed.data.get("fields", {})

        if login_fields.get("MID") != current_fields.get("MID"):
            raise ValueError(
                f"MID mismatch: EventLogin={login_fields.get('MID')!r}, "
                f"EventLoginSuccess={current_fields.get('MID')!r}"
            )

        if login_fields.get("User") != current_fields.get("User"):
            raise ValueError(
                f"User mismatch: EventLogin={login_fields.get('User')!r}, "
                f"EventLoginSuccess={current_fields.get('User')!r}"
            )