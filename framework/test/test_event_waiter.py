from __future__ import annotations

import asyncio

import pytest

from framework.connections.exceptions import EventWaitTimeoutError
from framework.events.base import EventWaiter, RegexEvent, WaitMode


class FakeConnection:
    def __init__(self, device_id: str = "fake-device") -> None:
        self.device_id = device_id
        self._buffer = ""


class EventA(RegexEvent):
    def __init__(self) -> None:
        super().__init__(r"\bA\b", name="EventA")


class EventB(RegexEvent):
    def __init__(self) -> None:
        super().__init__(r"\bB\b", name="EventB")


class EventC(RegexEvent):
    def __init__(self) -> None:
        super().__init__(r"\bC\b", name="EventC")


async def feed_sequence(
    waiter: EventWaiter,
    connection: FakeConnection,
    chunks: list[str],
    delay: float = 0.01,
) -> None:
    """
    Simulate data arriving from a live stream.
    """
    for chunk in chunks:
        await asyncio.sleep(delay)
        connection._buffer += chunk
        waiter.feed()


@pytest.mark.asyncio
async def test_ordered_all_strict_passes_for_exact_sequence() -> None:
    connection = FakeConnection()

    waiter = EventWaiter(
        connection=connection,
        events=[EventA(), EventB(), EventC()],
        mode=WaitMode.ORDERED_ALL_STRICT,
    )

    feeder = asyncio.create_task(
        feed_sequence(waiter, connection, ["A\n", "B\n", "C\n"])
    )

    matches = await waiter.wait(timeout=0.1)
    await feeder

    assert [m.event_name for m in matches] == [
        "EventA",
        "EventB",
        "EventC",
    ]


@pytest.mark.asyncio
async def test_ordered_all_strict_fails_when_last_duplicate_is_missing() -> None:
    """
    expected: A, B, C, C
    actual:   A, B, C
    result:   fail
    """
    connection = FakeConnection()

    waiter = EventWaiter(
        connection=connection,
        events=[EventA(), EventB(), EventC(), EventC()],
        mode=WaitMode.ORDERED_ALL_STRICT,
    )

    feeder = asyncio.create_task(
        feed_sequence(waiter, connection, ["A\n", "B\n", "C\n"])
    )

    with pytest.raises(EventWaitTimeoutError) as exc_info:
        await waiter.wait(timeout=0.1)

    await feeder

    message = str(exc_info.value)
    assert "Missing events" in message
    assert "EventC" in message


@pytest.mark.asyncio
async def test_ordered_all_strict_fails_when_extra_duplicate_arrives() -> None:
    """
    expected: A, B, C
    actual:   A, B, C, C
    result:   fail
    """
    connection = FakeConnection()

    waiter = EventWaiter(
        connection=connection,
        events=[EventA(), EventB(), EventC()],
        mode=WaitMode.ORDERED_ALL_STRICT,
    )

    feeder = asyncio.create_task(
        feed_sequence(waiter, connection, ["A\n", "B\n", "C\n", "C\n"])
    )

    with pytest.raises(EventWaitTimeoutError) as exc_info:
        await waiter.wait(timeout=0.1)

    await feeder

    message = str(exc_info.value)
    assert "Unexpected extra event" in message
    assert "EventC" in message


@pytest.mark.asyncio
async def test_ordered_all_strict_fails_when_wrong_order() -> None:
    """
    expected: A, B, C
    actual:   A, C, B
    result:   fail
    """
    connection = FakeConnection()

    waiter = EventWaiter(
        connection=connection,
        events=[EventA(), EventB(), EventC()],
        mode=WaitMode.ORDERED_ALL_STRICT,
    )

    feeder = asyncio.create_task(
        feed_sequence(waiter, connection, ["A\n", "C\n", "B\n"])
    )

    with pytest.raises(EventWaitTimeoutError) as exc_info:
        await waiter.wait(timeout=0.1)

    await feeder

    message = str(exc_info.value)
    assert "Missing events" in message
    assert "EventB" in message


@pytest.mark.asyncio
async def test_ordered_all_non_strict_passes_even_if_extra_event_arrives_later() -> None:
    """
    Non-strict mode should succeed as soon as A, B, C is found,
    even if another C appears afterwards.
    """
    connection = FakeConnection()

    waiter = EventWaiter(
        connection=connection,
        events=[EventA(), EventB(), EventC()],
        mode=WaitMode.ORDERED_ALL,
    )

    feeder = asyncio.create_task(
        feed_sequence(waiter, connection, ["A\n", "B\n", "C\n", "C\n"])
    )

    matches = await waiter.wait(timeout=0.1)
    await feeder

    assert [m.event_name for m in matches] == [
        "EventA",
        "EventB",
        "EventC",
    ]


@pytest.mark.asyncio
async def test_ordered_all_strict_fails_when_extra_a_arrives_after_completion() -> None:
    """
    expected: A, B, C
    actual:   A, B, C, A
    result:   fail
    """
    connection = FakeConnection()

    waiter = EventWaiter(
        connection=connection,
        events=[EventA(), EventB(), EventC()],
        mode=WaitMode.ORDERED_ALL_STRICT,
    )

    feeder = asyncio.create_task(
        feed_sequence(waiter, connection, ["A\n", "B\n", "C\n", "A\n"])
    )

    with pytest.raises(EventWaitTimeoutError) as exc_info:
        await waiter.wait(timeout=0.1)

    await feeder

    message = str(exc_info.value)
    assert "Unexpected extra event" in message
    assert "EventA" in message


@pytest.mark.asyncio
async def test_ordered_all_strict_fails_when_only_prefix_is_seen() -> None:
    """
    expected: A, B, C
    actual:   A, B
    result:   fail
    """
    connection = FakeConnection()

    waiter = EventWaiter(
        connection=connection,
        events=[EventA(), EventB(), EventC()],
        mode=WaitMode.ORDERED_ALL_STRICT,
    )

    feeder = asyncio.create_task(
        feed_sequence(waiter, connection, ["A\n", "B\n"])
    )

    with pytest.raises(EventWaitTimeoutError) as exc_info:
        await waiter.wait(timeout=0.1)

    await feeder

    message = str(exc_info.value)
    assert "Missing events" in message
    assert "EventC" in message