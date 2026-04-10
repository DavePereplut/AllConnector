from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Pattern

import paramiko


class HostKeyPolicyName(str, Enum):
    REJECT = "reject"
    AUTO_ADD = "auto_add"
    WARNING = "warning"


def build_host_key_policy(policy_name: HostKeyPolicyName) -> paramiko.MissingHostKeyPolicy:
    if policy_name == HostKeyPolicyName.REJECT:
        return paramiko.RejectPolicy()
    if policy_name == HostKeyPolicyName.AUTO_ADD:
        return paramiko.AutoAddPolicy()
    if policy_name == HostKeyPolicyName.WARNING:
        return paramiko.WarningPolicy()
    raise ValueError(f"Unsupported host key policy: {policy_name}")


@dataclass(slots=True)
class PromptSpec:
    patterns: list[str | Pattern[str]]

    def compile(self) -> list[Pattern[str]]:
        compiled: list[Pattern[str]] = []
        for item in self.patterns:
            if isinstance(item, re.Pattern):
                compiled.append(item)
            else:
                compiled.append(re.compile(item, re.MULTILINE))
        return compiled


def compile_prompt_patterns(prompts: list[str | Pattern[str]]) -> list[Pattern[str]]:
    compiled: list[Pattern[str]] = []
    for prompt in prompts:
        if isinstance(prompt, re.Pattern):
            compiled.append(prompt)
        else:
            compiled.append(re.compile(prompt, re.MULTILINE))
    return compiled


@dataclass(slots=True)
class SSHConnectionConfig:
    host: str
    port: int = 22
    username: str = ""
    password: str = ""
    timeout: float = 15.0
    banner_timeout: float = 15.0
    auth_timeout: float = 15.0
    keepalive_interval: int = 30
    expected_prompts: list[str] = field(default_factory=list)
    known_hosts_file: str | None = None
    host_key_policy: HostKeyPolicyName = HostKeyPolicyName.REJECT
    allow_agent: bool = False
    look_for_keys: bool = False
    term: str = "vt100"
    terminal_width: int = 200
    terminal_height: int = 1000
    read_chunk_size: int = 65535
    read_sleep: float = 0.05
    prompt_timeout: float = 20.0
    reconnect_retries: int = 1
    command_retry_on_disconnect: int = 1


@dataclass(slots=True)
class DeviceConfig:
    device_id: str
    class_name: str
    ssh: SSHConnectionConfig
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "DeviceConfig":
        ssh_raw = data.get("ssh", {})
        if isinstance(ssh_raw, SSHConnectionConfig):
            ssh_cfg = ssh_raw
        else:
            host_key_policy = ssh_raw.get("host_key_policy", HostKeyPolicyName.REJECT)
            if isinstance(host_key_policy, str):
                ssh_raw = dict(ssh_raw)
                ssh_raw["host_key_policy"] = HostKeyPolicyName(host_key_policy)
            ssh_cfg = SSHConnectionConfig(**ssh_raw)

        return cls(
            device_id=data["device_id"],
            class_name=data["class_name"],
            ssh=ssh_cfg,
            metadata=data.get("metadata", {}),
        )