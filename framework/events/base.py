from __future__ import annotations

import abc
import asyncio
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Pattern, Sequence


class WaitMode(str, Enum):
    ORDERED_ALL = "ordered_all"
    UNORDERED_ALL = "unordered_all"
    ANY = "any"


class BaseEvent(abc.ABC):
    name: str

    def __init__(self, name: str | None = None) -> None:
        self.name = name or self.__class__.__name__

    @abc.abstractmethod
    def match(
        self,
        buffer_text: str,
        *,
        connection: "BaseConnection",
        start_pos: int = 0,
    ) -> Any:
        raise NotImplementedError


class RegexEvent(BaseEvent):
    def __init__(self, pattern: str | Pattern[str], name: str | None = None) -> None:
        super().__init__(name=name)
        self.pattern = re.compile(pattern, re.MULTILINE) if isinstance(pattern, str) else pattern

    def match(
        self,
        buffer_text: str,
        *,
        connection: "BaseConnection",
        start_pos: int = 0,
    ) -> Any:
        return self.pattern.search(buffer_text, start_pos)


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


def _normalize_event_match(
    result: Any,
    buffer_text: str,
    fallback_start: int = 0,
) -> tuple[bool, int, int]:
    if result is None or result is False:
        return False, fallback_start, fallback_start
    if result is True:
        return True, fallback_start, len(buffer_text)
    if hasattr(result, "start") and hasattr(result, "end"):
        return True, int(result.start()), int(result.end())
    raise TypeError(f"Unsupported match result type: {type(result)!r}")


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
        self.error: Exception | None = None

        self._ordered_index = 0
        self._ordered_cursor = 0
        self._remaining_unordered = set(range(len(self.events)))
        self._registration_pos = len(connection._buffer)

    def feed(self) -> None:
        if self.done.is_set() or self.failed.is_set():
            return

        text = self.connection._buffer
        try:
            if self.mode == WaitMode.ORDERED_ALL:
                self._feed_ordered(text)
            elif self.mode == WaitMode.UNORDERED_ALL:
                self._feed_unordered(text)
            elif self.mode == WaitMode.ANY:
                self._feed_any(text)
            else:
                raise ValueError(f"Unsupported wait mode: {self.mode}")
        except Exception as exc:  # noqa: BLE001
            self.error = exc
            self.failed.set()

    def _feed_ordered(self, text: str) -> None:
        while self._ordered_index < len(self.events):
            event = self.events[self._ordered_index]
            result = event.match(
                text,
                connection=self.connection,
                start_pos=self._ordered_cursor,
            )
            matched, start, end = _normalize_event_match(
                result, text, fallback_start=self._ordered_cursor
            )
            if not matched:
                return

            self.matches.append(
                EventMatchRecord(
                    event_name=event.name,
                    start=start,
                    end=end,
                    matched_text=text[start:end],
                    timestamp=time.time(),
                )
            )
            self._ordered_index += 1
            self._ordered_cursor = max(end, self._ordered_cursor)

        self.done.set()

    def _feed_unordered(self, text: str) -> None:
        satisfied: list[int] = []
        for idx in list(self._remaining_unordered):
            event = self.events[idx]
            result = event.match(
                text,
                connection=self.connection,
                start_pos=self._registration_pos,
            )
            matched, start, end = _normalize_event_match(
                result, text, fallback_start=self._registration_pos
            )
            if matched:
                satisfied.append(idx)
                self.matches.append(
                    EventMatchRecord(
                        event_name=event.name,
                        start=start,
                        end=end,
                        matched_text=text[start:end],
                        timestamp=time.time(),
                    )
                )

        for idx in satisfied:
            self._remaining_unordered.discard(idx)

        if not self._remaining_unordered:
            self.done.set()

    def _feed_any(self, text: str) -> None:
        for event in self.events:
            result = event.match(
                text,
                connection=self.connection,
                start_pos=self._registration_pos,
            )
            matched, start, end = _normalize_event_match(
                result, text, fallback_start=self._registration_pos
            )
            if matched:
                self.matches.append(
                    EventMatchRecord(
                        event_name=event.name,
                        start=start,
                        end=end,
                        matched_text=text[start:end],
                        timestamp=time.time(),
                    )
                )
                self.done.set()
                return

    async def wait(self, timeout: float) -> list[EventMatchRecord]:
        from framework.connections.exceptions import EventWaitTimeoutError

        try:
            await asyncio.wait_for(self.done.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise EventWaitTimeoutError(
                f"Timed out waiting for events on device={self.connection.device_id!r}, "
                f"mode={self.mode.value}, events={[e.name for e in self.events]!r}"
            ) from exc

        if self.failed.is_set() and self.error is not None:
            raise self.error

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