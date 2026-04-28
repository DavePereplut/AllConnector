from __future__ import annotations

import asyncio

from framework.devices.factory import create_device
from framework.devices.linux import LinuxDevice


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

    # Upload local file from framework host -> remote machine
    await device.upload_file(
        local_path=r"C:\temp\local_config.txt",
        remote_path="/tmp/test_data/local_config.txt",
        create_remote_dirs=True,
        overwrite=True,
        verify_size=True,
    )

    # Download remote file from remote machine -> framework host
    await device.download_file(
        remote_path="/tmp/test_data/result.log",
        local_path=r"C:\temp\downloads\result.log",
        create_local_dirs=True,
        overwrite=True,
        verify_size=True,
    )

    print("File upload and download completed successfully.")


if __name__ == "__main__":
    asyncio.run(main())