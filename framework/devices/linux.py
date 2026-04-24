from __future__ import annotations

from framework.devices.base import BaseDevice
from framework.models.config import compile_prompt_patterns


class LinuxDevice(BaseDevice):
    SHELL_PROMPTS = [
        r"(?m)^[^\n\r]+[$#]\s*$",
    ]

    async def initialize_session(self) -> None:
        pass

    async def login_as_root(self) -> None:
        await self.conn.send_line(
            "sudo -i",
            expected_prompts=[r"(?m)^[^\n\r]+#\s*$"],
            timeout=20.0,
        )

    async def start_tail_file(self, file_path: str) -> None:
        """
        Start tail -f and validate that the host is alive by requiring some output.
        This does NOT require a prompt, because tail -f is a streaming command.
        """
        await self.conn.send_raw(
            f"tail -f {file_path}\n",
            validate_output=True,
            timeout=5.0,
        )

    async def stop_streaming_command(self) -> None:
        """
        Ctrl+C should return us to a prompt.
        """
        await self.conn.send_raw(
            "\x03",
            validate_prompt=True,
            expected_prompts=self.SHELL_PROMPTS,
            timeout=10.0,
        )