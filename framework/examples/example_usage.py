from __future__ import annotations

import asyncio

from framework.devices.factory import create_device
from framework.events.base import WaitMode, wait_until_completed
from framework.events.examples import EventLogin, EventLoginSuccess


async def main() -> None:

    controller_1 = {
        "device_id": "controller_1",
        "class_name": "LinuxDevice",
        "ssh": {
            "host": "192.168.100.1",
            "port": 22,
            "username": "root",
            "password": "Setente&7",
            "timeout": 10.0,
            "keepalive_interval": 30,
            "host_key_policy": "warning",
            "expected_prompts": [
                r"(?m)^[^\n\r]+[$#]\s*$",
            ],
        },
    }

    # device_cisco = await create_device(cisco_1)
    # assert isinstance(device_cisco, CiscoDevice)

    # await device_cisco.enable_priv_mode()

    # async with device_cisco.conn as conn:
    #     await conn.send_line("show processes cpu sorted")
    #     cpu_controllers = await device_cisco.get_controllers_cpu_interface()
    #     print(cpu_controllers)

    device_ctrl_1 = await create_device(controller_1)

    async with wait_until_completed(
        connection=device_ctrl_1.conn,
        events=[EventLogin(), EventLoginSuccess()],
        timeout=300,
        mode=WaitMode.ORDERED_ALL,
    ) as waiter:
        await device_ctrl_1.login_as_root()

    print([match.event_name for match in waiter.matches])


if __name__ == "__main__":
    asyncio.run(main())