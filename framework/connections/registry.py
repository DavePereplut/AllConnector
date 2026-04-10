from __future__ import annotations

import asyncio

from framework.connections.ssh import SSHConnection
from framework.models.config import SSHConnectionConfig


class ConnectionRegistry:
    """
    Process-wide singleton registry.
    One device_id -> one connection instance.
    """

    _connections: dict[str, SSHConnection] = {}
    _lock = asyncio.Lock()

    @classmethod
    async def get_or_create_ssh(
        cls,
        *,
        device_id: str,
        config: SSHConnectionConfig,
    ) -> SSHConnection:
        async with cls._lock:
            existing = cls._connections.get(device_id)
            if existing is not None:
                return existing

            conn = SSHConnection(device_id=device_id, config=config)
            cls._connections[device_id] = conn

        try:
            await conn.open()
            return conn
        except Exception:
            cls.discard(device_id)
            raise

    @classmethod
    def get(cls, device_id: str) -> SSHConnection | None:
        return cls._connections.get(device_id)

    @classmethod
    def discard(cls, device_id: str) -> None:
        cls._connections.pop(device_id, None)