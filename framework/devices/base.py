from __future__ import annotations

import abc
from pathlib import Path
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

    async def upload_file(
        self,
        local_path: str | Path,
        remote_path: str,
        *,
        create_remote_dirs: bool = False,
        overwrite: bool = True,
        verify_size: bool = True,
    ) -> None:
        await self.conn.upload_file(
            local_path,
            remote_path,
            create_remote_dirs=create_remote_dirs,
            overwrite=overwrite,
            verify_size=verify_size,
        )

    async def download_file(
        self,
        remote_path: str,
        local_path: str | Path,
        *,
        create_local_dirs: bool = False,
        overwrite: bool = True,
        verify_size: bool = True,
    ) -> None:
        await self.conn.download_file(
            remote_path,
            local_path,
            create_local_dirs=create_local_dirs,
            overwrite=overwrite,
            verify_size=verify_size,
        )