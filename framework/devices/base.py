from __future__ import annotations

import abc
from typing import Any

from framework.commands.base import Command
from framework.connections.base import BaseConnection, CommandResult
from framework.models.config import DeviceConfig


class BaseDevice(abc.ABC):
    def __init__(self, config: DeviceConfig, conn: BaseConnection) -> None:
        self.config = config
        self.conn = conn

    @property
    def device_id(self) -> str:
        return self.config.device_id

    async def run_command(self, command: str, **kwargs: Any) -> CommandResult:
        return await self.conn.run_command(command, **kwargs)

    async def run_parsed(self, command: Command) -> dict[str, Any]:
        return await self.conn.run_parsed(command)

    async def login_as_root(self):
        raise NotImplemented("Base device should not be used")