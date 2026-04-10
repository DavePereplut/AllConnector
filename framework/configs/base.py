cisco_1 = {
    "device_id": "cisco_1",
    "class_name": "CiscoDevice",
    "ssh": {
        "host": "10.0.0.10",
        "port": 22,
        "username": "admin",
        "password": "secret",
        "timeout": 10.0,
        "keepalive_interval": 30,
        "host_key_policy": "warning",
        "expected_prompts": [
            r"(?m)^[^\n\r]+>\s*$",
            r"(?m)^[^\n\r]+#\s*$",
        ],
    },
}