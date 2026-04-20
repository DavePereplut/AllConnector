from __future__ import annotations

import asyncio

from framework.devices.factory import create_device
from framework.devices.linux import LinuxDevice
from framework.events.base import WaitMode, wait_until_completed
from framework.events.parsed_console_events import EventLogin, EventLoginSuccess


async def main() -> None:
    linux_1 = {
        "device_id": "linux_1",
        "class_name": "LinuxDevice",
        "ssh": {
            "host": "192.168.1.50",
            "port": 22,
            "username": "tester",
            "password": "secret",
            "timeout": 10.0,
            "keepalive_interval": 30,
            "host_key_policy": "warning",
            "expected_prompts": [
                r"(?m)^[^\n\r]+[$#]\s*$",
            ],
        },
    }

    device = await create_device(linux_1)
    assert isinstance(device, LinuxDevice)

    waiter = None
    try:
        async with wait_until_completed(
            connection=device.conn,
            events=[
                EventLogin(),
                EventLoginSuccess(),
            ],
            timeout=30.0,
            mode=WaitMode.ORDERED_ALL,
        ) as waiter:
            await device.start_tail_file("/var/log/specific_events.log")

        print("Events captured successfully.")
        print()

        for event in waiter.parsed_events:
            print(f"event_name={event.event_name}")
            print(f"flow={event.data['flow']}")
            print(f"time={event.data['time']}")
            print(f"message={event.data['message']}")
            print(f"fields={event.data['fields']}")
            print("-" * 60)

        login_event = waiter.parsed_events[0]
        success_event = waiter.parsed_events[1]

        login_fields = login_event.data["fields"]
        success_fields = success_event.data["fields"]

        print("Comparison example:")
        print(f"Login MID: {login_fields.get('MID')}")
        print(f"Success MID: {success_fields.get('MID')}")
        print(f"Login User: {login_fields.get('User')}")
        print(f"Success User: {success_fields.get('User')}")

    finally:
        await device.stop_streaming_command()


if __name__ == "__main__":
    asyncio.run(main())