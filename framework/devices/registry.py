from __future__ import annotations

from typing import Type

from framework.devices.base import BaseDevice
from framework.devices.cisco import CiscoDevice
from framework.devices.linux import LinuxDevice

DEVICE_CLASS_REGISTRY: dict[str, Type[BaseDevice]] = {
    "CiscoDevice": CiscoDevice,
    "LinuxDevice": LinuxDevice,
}