from __future__ import annotations

import abc
import asyncio
import contextlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Pattern, Sequence

import paramiko

from framework.commands.base import Command
from framework.connections.exceptions import (
    ConnectionClosedError,
    PromptTimeoutError,
    ReconnectFailedError,
)
from framework.events.base import BaseEvent, DialogueStep, EventWaiter, WaitMode
from framework.models.config import SSHConnectionConfig, compile_prompt_patterns
from framework.utils.logging import LOGGER


@dataclass(slots=True)
class CommandResult:
    command: str
    raw_output: str
    prompt_match: str | None
    parsed: dict[str, Any] | None = None


class BaseConnection(abc.ABC):
    """
    Async-facing connection abstraction.

    Command transactions are serialized because an interactive shell is one stream.
    Subscribers/event waiters always see the same live stream.
    """

    def __init__(self, *, device_id: str, config: SSHConnectionConfig) -> None:
        self.device_id = device_id
        self.config = config

        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = False
        self._closed = False

        self._buffer = ""
        self._condition = asyncio.Condition()

        self._transaction_lock = asyncio.Lock()
        self._context_lock = asyncio.Lock()
        self._reconnect_lock = asyncio.Lock()

        self._subscribers: dict[int, Callable[[str], Any]] = {}
        self._subscriber_seq = 0

        self._waiters: list[EventWaiter] = []

        self._last_prompt: str | None = None

    async def __aenter__(self) -> "BaseConnection":
        await self._context_lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._context_lock.release()
        return False

    @abc.abstractmethod
    async def open(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def is_alive(self) -> bool:
        raise NotImplementedError

    @abc.abstractmethod
    async def _send_raw(self, data: str) -> None:
        raise NotImplementedError

    async def ensure_connected(self) -> None:
        if self._closed:
            raise ConnectionClosedError(f"Connection for {self.device_id!r} is closed.")
        if self.is_alive():
            return

        async with self._reconnect_lock:
            if self.is_alive():
                return

            LOGGER.warning("Connection lost for device=%s. Reconnecting.", self.device_id)
            try:
                await self.open()
            except Exception as exc:  # noqa: BLE001
                await self.close()
                from framework.connections.registry import ConnectionRegistry
                ConnectionRegistry.discard(self.device_id)
                raise ReconnectFailedError(
                    f"Reconnect failed for device={self.device_id!r}"
                ) from exc

    async def send_line(
        self,
        line: str,
        *,
        expected_prompts: Sequence[str | Pattern[str]] | None = None,
        timeout: float | None = None,
    ) -> None:
        await self._execute_with_reconnect(
            self._send_line_impl,
            line,
            expected_prompts=expected_prompts,
            timeout=timeout,
        )

    async def run_command(
        self,
        line: str,
        *,
        expected_prompts: Sequence[str | Pattern[str]] | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        return await self._execute_with_reconnect(
            self._run_command_impl,
            line,
            expected_prompts=expected_prompts,
            timeout=timeout,
        )

    async def run_parsed(self, command: Command) -> dict[str, Any]:
        result = await self.run_command(
            command.command,
            expected_prompts=command.expected_prompts,
            timeout=command.timeout,
        )
        parsed = command.parse(result.raw_output)
        result.parsed = parsed
        return parsed

    async def run_dialogue(self, steps: Sequence[DialogueStep]) -> None:
        await self.ensure_connected()
        async with self._transaction_lock:
            for step in steps:
                await self._wait_for_patterns(
                    patterns=[step.compiled()],
                    timeout=step.timeout,
                )
                await self._send_raw(step.reply + "\n")

    def subscribe(self, callback: Callable[[str], Any]) -> int:
        token = self._subscriber_seq
        self._subscriber_seq += 1
        self._subscribers[token] = callback
        return token

    def unsubscribe(self, token: int) -> None:
        self._subscribers.pop(token, None)

    def subscribe_waiter(
        self,
        events: Sequence[BaseEvent],
        mode: WaitMode = WaitMode.ORDERED_ALL,
    ) -> EventWaiter:
        waiter = EventWaiter(self, events, mode=mode)
        self._waiters.append(waiter)
        waiter.feed()
        return waiter

    def unsubscribe_waiter(self, waiter: EventWaiter) -> None:
        with contextlib.suppress(ValueError):
            self._waiters.remove(waiter)

    async def _execute_with_reconnect(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        retries = self.config.command_retry_on_disconnect
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            try:
                await self.ensure_connected()
                return await func(*args, **kwargs)
            except (
                ConnectionClosedError,
                ConnectionResetError,
                EOFError,
                paramiko.SSHException,
                PromptTimeoutError,
            ) as exc:
                last_error = exc
                LOGGER.warning(
                    "Command attempt %s failed on device=%s: %s",
                    attempt + 1,
                    self.device_id,
                    exc,
                )
                if attempt >= retries:
                    break
                await self._force_reconnect()

        raise ReconnectFailedError(
            f"Command failed after reconnect attempts on device={self.device_id!r}"
        ) from last_error

    async def _force_reconnect(self) -> None:
        async with self._reconnect_lock:
            self._closed = False
            await self.close()
            self._closed = False
            await self.open()

    async def _send_line_impl(
        self,
        line: str,
        *,
        expected_prompts: Sequence[str | Pattern[str]] | None = None,
        timeout: float | None = None,
    ) -> None:
        await self.ensure_connected()
        timeout = timeout or self.config.prompt_timeout
        patterns = compile_prompt_patterns(
            list(expected_prompts or self.config.expected_prompts)
        )

        async with self._transaction_lock:
            start_pos = len(self._buffer)
            await self._send_raw(line + "\n")
            prompt = await self._wait_for_patterns(
                patterns=patterns,
                timeout=timeout,
                start_pos=start_pos,
            )
            self._last_prompt = prompt

    async def _run_command_impl(
        self,
        line: str,
        *,
        expected_prompts: Sequence[str | Pattern[str]] | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        await self.ensure_connected()
        timeout = timeout or self.config.prompt_timeout
        patterns = compile_prompt_patterns(
            list(expected_prompts or self.config.expected_prompts)
        )

        async with self._transaction_lock:
            start_pos = len(self._buffer)
            await self._send_raw(line + "\n")
            prompt = await self._wait_for_patterns(
                patterns=patterns,
                timeout=timeout,
                start_pos=start_pos,
            )
            self._last_prompt = prompt

            full_slice = self._buffer[start_pos:]
            raw_output = self._strip_command_and_prompt(full_slice, line, prompt)

            return CommandResult(
                command=line,
                raw_output=raw_output,
                prompt_match=prompt,
            )

    async def _wait_for_patterns(
        self,
        *,
        patterns: Sequence[re.Pattern[str]],
        timeout: float,
        start_pos: int = 0,
    ) -> str:
        if not patterns:
            raise ValueError("At least one expected prompt must be configured.")

        async def waiter() -> str:
            while True:
                text = self._buffer[start_pos:]
                for pattern in patterns:
                    match = pattern.search(text)
                    if match:
                        return match.group(0)
                async with self._condition:
                    await self._condition.wait()

        try:
            return await asyncio.wait_for(waiter(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            tail = self._buffer[max(0, len(self._buffer) - 800):]
            raise PromptTimeoutError(
                f"Timeout waiting for expected prompt on device={self.device_id!r}. "
                f"Patterns={[p.pattern for p in patterns]!r}. "
                f"Buffer tail={tail!r}"
            ) from exc

    def _strip_command_and_prompt(self, text: str, command: str, prompt: str | None) -> str:
        cleaned = text

        if cleaned.startswith(command):
            cleaned = cleaned[len(command):]
        if cleaned.startswith("\n"):
            cleaned = cleaned[1:]

        if prompt and cleaned.endswith(prompt):
            cleaned = cleaned[: -len(prompt)]

        return cleaned.strip("\n")

    def _publish_data(self, text: str) -> None:
        self._buffer += text

        for callback in list(self._subscribers.values()):
            try:
                callback(text)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Subscriber callback failed on device=%s", self.device_id)

        for waiter in list(self._waiters):
            waiter.feed()

        async def notify() -> None:
            async with self._condition:
                self._condition.notify_all()

        asyncio.create_task(notify())

    async def send_raw(
        self,
        data: str,
        *,
        timeout: float | None = None,
        expected_prompts: Sequence[str | Pattern[str]] | None = None,
        validate_prompt: bool = False,
        validate_output: bool = False,
    ) -> str | None:
        """
        Send raw data and optionally validate that:
        - a prompt appears, or
        - some new output appears

        Returns:
            - matched prompt string if validate_prompt=True and prompt appears
            - "OUTPUT_RECEIVED" if validate_output=True and any new output appears
            - None if no validation requested
        """
        if validate_prompt and validate_output:
            raise ValueError(
                "Use either validate_prompt=True or validate_output=True, not both."
            )

        await self.ensure_connected()
        start_pos = len(self._buffer)

        await self._send_raw(data)

        if validate_prompt:
            patterns = compile_prompt_patterns(
                list(expected_prompts or self.config.expected_prompts)
            )
            prompt = await self._wait_for_patterns(
                patterns=patterns,
                timeout=timeout or self.config.prompt_timeout,
                start_pos=start_pos,
            )
            self._last_prompt = prompt
            return prompt

        if validate_output:
            await self._wait_for_any_output(
                timeout=timeout or self.config.prompt_timeout,
                start_pos=start_pos,
            )
            return "OUTPUT_RECEIVED"

        return None

    async def _wait_for_any_output(
        self,
        *,
        timeout: float,
        start_pos: int,
    ) -> str:
        async def waiter() -> str:
            while True:
                text = self._buffer[start_pos:]
                if text:
                    return text
                async with self._condition:
                    await self._condition.wait()

        try:
            return await asyncio.wait_for(waiter(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            tail = self._buffer[max(0, len(self._buffer) - 800):]
            raise PromptTimeoutError(
                f"Timeout waiting for any output on device={self.device_id!r}. "
                f"Buffer tail={tail!r}"
            ) from exc

    async def _wait_for_meaningful_output(
        self,
        *,
        timeout: float,
        start_pos: int,
        ignore_text: str | None = None,
    ) -> str:
        async def waiter() -> str:
            while True:
                text = self._buffer[start_pos:]
                if text:
                    meaningful = text
                    if ignore_text:
                        meaningful = meaningful.replace(ignore_text, "")
                    if meaningful.strip():
                        return meaningful
                async with self._condition:
                    await self._condition.wait()

        return await asyncio.wait_for(waiter(), timeout=timeout)
