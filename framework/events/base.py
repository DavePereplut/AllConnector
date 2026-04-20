from __future__ import annotations

import abc
import asyncio
import time
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Pattern, Sequence


class WaitMode(str, Enum):
    ORDERED_ALL = "ordered_all"
    ORDERED_ALL_STRICT = "ordered_all_strict"
    UNORDERED_ALL = "unordered_all"
    ANY = "any"


@dataclass(slots=True)
class ParsedEvent:
    event_name: str
    raw_text: str
    start: int
    end: int
    timestamp: float
    data: dict[str, Any] = field(default_factory=dict)


class BaseEvent(abc.ABC):
    name: str

    def __init__(self, name: str | None = None) -> None:
        self.name = name or self.__class__.__name__

    @abc.abstractmethod
    def parse(
        self,
        buffer_text: str,
        *,
        connection: "BaseConnection",
        start_pos: int = 0,
        previous_events: Sequence[ParsedEvent] | None = None,
    ) -> ParsedEvent | None:
        """
        Return ParsedEvent if matched+parsed, otherwise None.
        """
        raise NotImplementedError


class RegexEvent(BaseEvent):
    def __init__(self, pattern: str | Pattern[str], name: str | None = None) -> None:
        super().__init__(name=name)
        self.pattern = re.compile(pattern, re.MULTILINE) if isinstance(pattern, str) else pattern

    def parse(
        self,
        buffer_text: str,
        *,
        connection: "BaseConnection",
        start_pos: int = 0,
        previous_events: Sequence[ParsedEvent] | None = None,
    ) -> ParsedEvent | None:
        match = self.pattern.search(buffer_text, start_pos)
        if not match:
            return None

        return ParsedEvent(
            event_name=self.name,
            raw_text=match.group(0),
            start=match.start(),
            end=match.end(),
            timestamp=time.time(),
            data={},
        )


@dataclass(slots=True)
class EventMatchRecord:
    event_name: str
    start: int
    end: int
    matched_text: str
    timestamp: float


@dataclass(slots=True)
class DialogueStep:
    expect: str | Pattern[str]
    reply: str
    timeout: float = 20.0

    def compiled(self) -> Pattern[str]:
        return re.compile(self.expect, re.MULTILINE) if isinstance(self.expect, str) else self.expect


class EventWaiter:
    def __init__(
        self,
        connection: "BaseConnection",
        events: Sequence[BaseEvent],
        mode: WaitMode = WaitMode.ORDERED_ALL,
    ) -> None:
        if not events:
            raise ValueError("At least one event must be provided.")
        self.connection = connection
        self.events = list(events)
        self.mode = mode
        self.done = asyncio.Event()
        self.failed = asyncio.Event()
        self.matches: list[EventMatchRecord] = []
        self.parsed_events: list[ParsedEvent] = []
        self.error: Exception | None = None

        self._ordered_index = 0
        self._ordered_cursor = 0
        self._remaining_unordered = set(range(len(self.events)))
        self._registration_pos = len(connection._buffer)

        self._strict_sequence_complete = False
        self._strict_violation: str | None = None

    def feed(self) -> None:
        if self.done.is_set() or self.failed.is_set():
            return

        text = self.connection._buffer
        try:
            if self.mode == WaitMode.ORDERED_ALL:
                self._feed_ordered(text, strict=False)
            elif self.mode == WaitMode.ORDERED_ALL_STRICT:
                self._feed_ordered(text, strict=True)
            elif self.mode == WaitMode.UNORDERED_ALL:
                self._feed_unordered(text)
            elif self.mode == WaitMode.ANY:
                self._feed_any(text)
            else:
                raise ValueError(f"Unsupported wait mode: {self.mode}")
        except Exception as exc:  # noqa: BLE001
            self.error = exc
            self.failed.set()

    def _feed_ordered(self, text: str, *, strict: bool) -> None:
        while True:
            if self._ordered_index >= len(self.events):
                if strict:
                    extra = self._find_next_matching_event(
                        text=text,
                        start_pos=self._ordered_cursor,
                    )
                    if extra is not None:
                        self._strict_violation = (
                            f"Unexpected extra event {extra.event_name!r} detected after the "
                            f"expected ordered sequence."
                        )
                        self.matches.append(
                            EventMatchRecord(
                                event_name=extra.event_name,
                                start=extra.start,
                                end=extra.end,
                                matched_text=extra.raw_text,
                                timestamp=extra.timestamp,
                            )
                        )
                        self.parsed_events.append(extra)
                        self.failed.set()
                    else:
                        self._strict_sequence_complete = True
                    return

                self.done.set()
                return

            event = self.events[self._ordered_index]
            parsed = event.parse(
                text,
                connection=self.connection,
                start_pos=self._ordered_cursor,
                previous_events=self.parsed_events,
            )
            if parsed is None:
                return

            self.matches.append(
                EventMatchRecord(
                    event_name=parsed.event_name,
                    start=parsed.start,
                    end=parsed.end,
                    matched_text=parsed.raw_text,
                    timestamp=parsed.timestamp,
                )
            )
            self.parsed_events.append(parsed)
            self._ordered_index += 1
            self._ordered_cursor = max(parsed.end, self._ordered_cursor)

    def _find_next_matching_event(
        self,
        *,
        text: str,
        start_pos: int,
    ) -> ParsedEvent | None:
        earliest: ParsedEvent | None = None

        for event in self.events:
            parsed = event.parse(
                text,
                connection=self.connection,
                start_pos=start_pos,
                previous_events=self.parsed_events,
            )
            if parsed is None:
                continue

            if earliest is None or parsed.start < earliest.start:
                earliest = parsed

        return earliest

    def _feed_unordered(self, text: str) -> None:
        satisfied: list[int] = []
        for idx in list(self._remaining_unordered):
            event = self.events[idx]
            parsed = event.parse(
                text,
                connection=self.connection,
                start_pos=self._registration_pos,
                previous_events=self.parsed_events,
            )
            if parsed is None:
                continue

            satisfied.append(idx)
            self.matches.append(
                EventMatchRecord(
                    event_name=parsed.event_name,
                    start=parsed.start,
                    end=parsed.end,
                    matched_text=parsed.raw_text,
                    timestamp=parsed.timestamp,
                )
            )
            self.parsed_events.append(parsed)

        for idx in satisfied:
            self._remaining_unordered.discard(idx)

        if not self._remaining_unordered:
            self.done.set()

    def _feed_any(self, text: str) -> None:
        for event in self.events:
            parsed = event.parse(
                text,
                connection=self.connection,
                start_pos=self._registration_pos,
                previous_events=self.parsed_events,
            )
            if parsed is None:
                continue

            self.matches.append(
                EventMatchRecord(
                    event_name=parsed.event_name,
                    start=parsed.start,
                    end=parsed.end,
                    matched_text=parsed.raw_text,
                    timestamp=parsed.timestamp,
                )
            )
            self.parsed_events.append(parsed)
            self.done.set()
            return

    def _get_missing_event_names(self) -> list[str]:
        if self.mode in (WaitMode.ORDERED_ALL, WaitMode.ORDERED_ALL_STRICT):
            return [event.name for event in self.events[self._ordered_index:]]

        if self.mode == WaitMode.UNORDERED_ALL:
            return [self.events[idx].name for idx in sorted(self._remaining_unordered)]

        if self.mode == WaitMode.ANY:
            return [event.name for event in self.events]

        return [event.name for event in self.events]

    async def wait(self, timeout: float) -> list[EventMatchRecord]:
        from framework.connections.exceptions import EventWaitTimeoutError

        if self.mode == WaitMode.ORDERED_ALL_STRICT:
            return await self._wait_ordered_strict(timeout)

        try:
            await asyncio.wait_for(self.done.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            missed_events = self._get_missing_event_names()
            raise EventWaitTimeoutError(
                f"Timed out waiting for events on device={self.connection.device_id!r}, "
                f"mode={self.mode.value}, missed_events={missed_events!r}"
            ) from exc

        if self.failed.is_set():
            if self.error is not None:
                raise self.error
            raise EventWaitTimeoutError(
                f"Event waiter failed for device={self.connection.device_id!r}"
            )

        return list(self.matches)

    async def _wait_ordered_strict(self, timeout: float) -> list[EventMatchRecord]:
        from framework.connections.exceptions import EventWaitTimeoutError

        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            raise

        if self.failed.is_set():
            if self._strict_violation:
                raise EventWaitTimeoutError(
                    f"Strict ordered event validation failed on "
                    f"device={self.connection.device_id!r}: {self._strict_violation}"
                )
            if self.error is not None:
                raise self.error
            raise EventWaitTimeoutError(
                f"Strict ordered event validation failed on "
                f"device={self.connection.device_id!r}"
            )

        if self._ordered_index < len(self.events):
            missed_events = self._get_missing_event_names()
            raise EventWaitTimeoutError(
                f"Strict ordered event validation timed out on "
                f"device={self.connection.device_id!r}, "
                f"missed_events={missed_events!r}"
            )

        return list(self.matches)


class wait_until_completed:
    def __init__(
        self,
        *,
        connection: "BaseConnection",
        events: Sequence[BaseEvent],
        timeout: float,
        mode: WaitMode = WaitMode.ORDERED_ALL,
    ) -> None:
        self.connection = connection
        self.events = list(events)
        self.timeout = timeout
        self.mode = mode
        self.waiter: EventWaiter | None = None

    async def __aenter__(self) -> EventWaiter:
        self.waiter = self.connection.subscribe_waiter(self.events, mode=self.mode)
        return self.waiter

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc is None and self.waiter is not None:
                await self.waiter.wait(timeout=self.timeout)
        finally:
            if self.waiter is not None:
                self.connection.unsubscribe_waiter(self.waiter)
        return False


from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from framework.connections.base import BaseConnection