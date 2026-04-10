from __future__ import annotations

from framework.commands.base import Command
from framework.devices.base import BaseDevice


class CiscoDevice(BaseDevice):
    USER_PROMPTS = [
        r"(?m)^[^\n\r]+>\s*$",
    ]
    PRIV_PROMPTS = [
        r"(?m)^[^\n\r]+#\s*$",
    ]
    ANY_CLI_PROMPTS = USER_PROMPTS + PRIV_PROMPTS

    async def initialize_session(self) -> None:
        await self.conn.send_line(
            "terminal length 0",
            expected_prompts=self.ANY_CLI_PROMPTS,
            timeout=10.0,
        )

    async def enable_priv_mode(self, enable_password: str | None = None) -> None:
        await self.conn.send_line(
            "enable",
            expected_prompts=self.PRIV_PROMPTS,
            timeout=10.0,
        )

    async def get_controllers_cpu_interface(self) -> dict[str, object]:
        def parser(output: str) -> dict[str, object]:
            rows = [
                line.strip()
                for line in output.splitlines()
                if line.strip()
            ]
            return {"lines": rows}

        command = Command(
            command="show controllers cpu-interface",
            timeout=20.0,
            expected_prompts=self.ANY_CLI_PROMPTS,
            parser=parser,
        )
        return await self.run_parsed(command)