from __future__ import annotations

from framework.connections.exceptions import DeviceCreationError
from framework.connections.registry import ConnectionRegistry
from framework.devices.base import BaseDevice
from framework.devices.registry import DEVICE_CLASS_REGISTRY
from framework.models.config import DeviceConfig


async def create_device(config_like: DeviceConfig | dict) -> BaseDevice:
    """
    Immediate connect. Failure => raise and stop test execution.
    """
    config = config_like if isinstance(config_like, DeviceConfig) else DeviceConfig.from_mapping(config_like)

    device_cls = DEVICE_CLASS_REGISTRY.get(config.class_name)
    if device_cls is None:
        raise DeviceCreationError(
            f"Unknown class_name={config.class_name!r}. "
            f"Known={list(DEVICE_CLASS_REGISTRY)!r}"
        )

    conn = await ConnectionRegistry.get_or_create_ssh(
        device_id=config.device_id,
        config=config.ssh,
    )
    device = device_cls(config=config, conn=conn)

    init = getattr(device, "initialize_session", None)
    if callable(init):
        await init()

    return device