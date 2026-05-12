from __future__ import annotations

import html
import os
import posixpath
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class CodecConfig:
    name: str
    root_dir: Path
    config_path: Path
    benchmark_files: list[Path]


@dataclass(frozen=True)
class Scenario:
    name: str
    path: Path
    steps: list[dict[str, Any]]


@dataclass(frozen=True)
class RemoteSettings:
    root_dir: str
    cleanup_after_test: bool


@dataclass(frozen=True)
class ReportsSettings:
    log_archive_dir: Path


@dataclass(frozen=True)
class SuiteSetup:
    name: str
    selected_device_ids: list[str]
    device_configs: dict[str, dict[str, Any]]
    codecs_dir: Path
    scenario: Scenario
    remote: RemoteSettings
    reports: ReportsSettings


@dataclass(frozen=True)
class RemoteCodecCase:
    id: str
    device_id: str
    codec: CodecConfig
    scenario: Scenario
    remote_root_dir: str
    cleanup_after_test: bool


@dataclass
class RemoteTestWorkspace:
    case_id: str
    local_tmp_dir: Path
    archive_dir: Path
    log_file: Path
    remote_case_dir: str
    uploaded_remote_files: list[str]


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if not isinstance(data, dict):
        raise ValueError(f"YAML file must contain an object: {path}")

    return data


def load_scenario(path: Path) -> Scenario:
    data = load_yaml(path)

    name = data.get("name")
    steps = data.get("steps")

    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"Scenario has missing or invalid 'name': {path}")

    if not isinstance(steps, list) or not steps:
        raise ValueError(f"Scenario has missing or invalid 'steps': {path}")

    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError(f"Scenario step #{index} must be an object")

        if not isinstance(step.get("name"), str):
            raise ValueError(f"Scenario step #{index} has invalid 'name'")

        if not isinstance(step.get("action"), str):
            raise ValueError(f"Scenario step #{index} has invalid 'action'")

    return Scenario(name=name, path=path, steps=steps)


def load_suite_setup(setup_path: Path, devices_path: Path) -> SuiteSetup:
    setup_raw = load_yaml(setup_path)
    devices_raw = load_yaml(devices_path)

    devices_section = devices_raw.get("devices")
    if not isinstance(devices_section, dict):
        raise ValueError(f"'devices' section must be a mapping: {devices_path}")

    name = setup_raw.get("name", setup_path.stem)
    selected_devices = setup_raw.get("devices")
    codecs_dir_raw = setup_raw.get("codecs_dir")
    scenario_raw = setup_raw.get("scenario")

    if not isinstance(name, str) or not name.strip():
        raise ValueError("'name' must be a non-empty string")

    if not isinstance(selected_devices, list) or not selected_devices:
        raise ValueError("'devices' must be a non-empty list")

    if not all(isinstance(device_id, str) for device_id in selected_devices):
        raise ValueError("'devices' must contain only strings")

    missing_devices = [
        device_id for device_id in selected_devices if device_id not in devices_section
    ]
    if missing_devices:
        raise ValueError(f"Selected devices are missing in devices.yaml: {missing_devices}")

    if not isinstance(codecs_dir_raw, str):
        raise ValueError("'codecs_dir' must be a string")

    if not isinstance(scenario_raw, str):
        raise ValueError("'scenario' must be a string")

    setup_dir = setup_path.parent.resolve()

    codecs_dir = (setup_dir / codecs_dir_raw).resolve()
    scenario_path = (setup_dir / scenario_raw).resolve()

    if not codecs_dir.exists():
        raise ValueError(f"codecs_dir does not exist: {codecs_dir}")

    if not scenario_path.exists():
        raise ValueError(f"scenario does not exist: {scenario_path}")

    remote_raw = setup_raw.get("remote", {})
    if not isinstance(remote_raw, dict):
        raise ValueError("'remote' must be an object")

    reports_raw = setup_raw.get("reports", {})
    if not isinstance(reports_raw, dict):
        raise ValueError("'reports' must be an object")

    remote_root_dir = str(remote_raw.get("root_dir", "/tmp/codec_benchmarks"))
    cleanup_after_test = bool(remote_raw.get("cleanup_after_test", True))

    log_archive_dir_raw = str(
        reports_raw.get("log_archive_dir", "reports/remote-codec-logs")
    )
    log_archive_dir = (setup_dir / log_archive_dir_raw).resolve()

    return SuiteSetup(
        name=name,
        selected_device_ids=selected_devices,
        device_configs={
            device_id: devices_section[device_id]
            for device_id in selected_devices
        },
        codecs_dir=codecs_dir,
        scenario=load_scenario(scenario_path),
        remote=RemoteSettings(
            root_dir=remote_root_dir,
            cleanup_after_test=cleanup_after_test,
        ),
        reports=ReportsSettings(
            log_archive_dir=log_archive_dir,
        ),
    )


def discover_codecs(codecs_dir: Path) -> list[CodecConfig]:
    config_paths = sorted(codecs_dir.glob("*/codec.yaml"))
    codecs: list[CodecConfig] = []

    for config_path in config_paths:
        codec_root = config_path.parent
        data = load_yaml(config_path)

        name = data.get("name", codec_root.name)
        benchmark_files_raw = data.get("benchmark_files")

        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid codec name in {config_path}")

        if not isinstance(benchmark_files_raw, list) or not benchmark_files_raw:
            raise ValueError(f"Invalid benchmark_files in {config_path}")

        if not all(isinstance(item, str) for item in benchmark_files_raw):
            raise ValueError(f"benchmark_files must contain strings: {config_path}")

        benchmark_files = [
            (codec_root / relative_path).resolve()
            for relative_path in benchmark_files_raw
        ]

        missing = [path for path in benchmark_files if not path.exists()]
        if missing:
            raise ValueError(
                f"Codec {name!r} has missing benchmark files: "
                + ", ".join(str(path) for path in missing)
            )

        codecs.append(
            CodecConfig(
                name=name,
                root_dir=codec_root,
                config_path=config_path,
                benchmark_files=benchmark_files,
            )
        )

    if not codecs:
        raise ValueError(f"No codec.yaml files found under: {codecs_dir}")

    return codecs


def generate_cases(setup: SuiteSetup, codecs: list[CodecConfig]) -> list[RemoteCodecCase]:
    cases: list[RemoteCodecCase] = []

    for device_id in setup.selected_device_ids:
        for codec in codecs:
            case_id = sanitize_case_id(
                f"{setup.scenario.name}__{device_id}__{codec.name}"
            )

            cases.append(
                RemoteCodecCase(
                    id=case_id,
                    device_id=device_id,
                    codec=codec,
                    scenario=setup.scenario,
                    remote_root_dir=setup.remote.root_dir,
                    cleanup_after_test=setup.remote.cleanup_after_test,
                )
            )

    return cases


def sanitize_case_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def remote_join(*parts: str) -> str:
    cleaned = [part.strip("/") for part in parts if part]
    if not cleaned:
        return "/"
    prefix = "/" if parts[0].startswith("/") else ""
    return prefix + posixpath.join(*cleaned)


def append_log(log_file: Path, message: str) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as file:
        file.write(message.rstrip() + os.linesep)


def archive_workspace(workspace: RemoteTestWorkspace) -> None:
    workspace.archive_dir.mkdir(parents=True, exist_ok=True)

    for source in workspace.local_tmp_dir.glob("*"):
        if source.is_file():
            shutil.copy2(source, workspace.archive_dir / source.name)


def write_case_metadata(
    workspace: RemoteTestWorkspace,
    case: RemoteCodecCase,
) -> None:
    metadata_file = workspace.local_tmp_dir / "metadata.txt"

    benchmark_files = os.linesep.join(
        f"  - {path}" for path in case.codec.benchmark_files
    )

    uploaded_files = os.linesep.join(
        f"  - {path}" for path in workspace.uploaded_remote_files
    )

    content = f"""case_id: {case.id}
scenario_name: {case.scenario.name}
scenario_path: {case.scenario.path}
device_id: {case.device_id}
codec_name: {case.codec.name}
codec_config_path: {case.codec.config_path}
remote_case_dir: {workspace.remote_case_dir}
cleanup_after_test: {case.cleanup_after_test}
local_benchmark_files:
{benchmark_files}
uploaded_remote_files:
{uploaded_files}
"""

    metadata_file.write_text(content, encoding="utf-8")


def build_artifact_links(
    html_report_path: Path | None,
    archive_dir: Path,
) -> dict[str, str]:
    artifacts = {
        "Log": archive_dir / "test.log",
        "Metadata": archive_dir / "metadata.txt",
        "ls_output": archive_dir / "ls_output.txt",
        "Folder": archive_dir,
    }

    if html_report_path is None:
        return {label: path.resolve().as_uri() for label, path in artifacts.items()}

    report_dir = html_report_path.parent.resolve()
    links: dict[str, str] = {}

    for label, path in artifacts.items():
        resolved = path.resolve()
        try:
            links[label] = resolved.relative_to(report_dir).as_posix()
        except ValueError:
            links[label] = resolved.as_uri()

    return links


def compact_links_html(links: dict[str, str]) -> str:
    parts: list[str] = []

    for label, href in links.items():
        safe_label = html.escape(label)
        safe_href = html.escape(href, quote=True)
        parts.append(f'<a href="{safe_href}">{safe_label}</a>')

    return " | ".join(parts)