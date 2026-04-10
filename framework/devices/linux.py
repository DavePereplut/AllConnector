from __future__ import annotations

from framework.devices.base import BaseDevice


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