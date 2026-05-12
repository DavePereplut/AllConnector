from __future__ import annotations

import posixpath
from typing import Awaitable, Callable

import pytest

from framework.devices.base import BaseDevice

from project.remote_codec_framework import (
    RemoteCodecCase,
    RemoteTestWorkspace,
    append_log,
)


StepHandler = Callable[
    [BaseDevice, RemoteCodecCase, RemoteTestWorkspace],
    Awaitable[None],
]


@pytest.mark.remote_codec
@pytest.mark.asyncio
async def test_remote_codec_scenario(
    remote_codec_case: RemoteCodecCase,
    remote_test_workspace: RemoteTestWorkspace,
    connected_devices: dict[str, BaseDevice],
) -> None:
    device = connected_devices[remote_codec_case.device_id]

    step_handlers: dict[str, StepHandler] = {
        "prepare_remote_directory": prepare_remote_directory,
        "upload_benchmark_files": upload_benchmark_files,
        "run_ls_ltr": run_ls_ltr,
        "verify_remote_files_exist": verify_remote_files_exist,
    }

    append_log(
        remote_test_workspace.log_file,
        (
            f"Starting scenario={remote_codec_case.scenario.name} "
            f"device={remote_codec_case.device_id} "
            f"codec={remote_codec_case.codec.name}"
        ),
    )

    try:
        for step in remote_codec_case.scenario.steps:
            step_name = step["name"]
            action = step["action"]

            append_log(remote_test_workspace.log_file, f"Starting step: {step_name}")

            handler = step_handlers.get(action)
            assert handler is not None, f"Unknown scenario action: {action}"

            await handler(device, remote_codec_case, remote_test_workspace)

            append_log(remote_test_workspace.log_file, f"Finished step: {step_name}")

    finally:
        if remote_codec_case.cleanup_after_test:
            await cleanup_remote_directory(
                device,
                remote_codec_case,
                remote_test_workspace,
            )


async def prepare_remote_directory(
    device: BaseDevice,
    case: RemoteCodecCase,
    workspace: RemoteTestWorkspace,
) -> None:
    command = f"mkdir -p {shell_quote(workspace.remote_case_dir)}"

    append_log(workspace.log_file, f"[{case.device_id}] $ {command}")

    result = await device.run_command(command, timeout=20.0)

    append_log(workspace.log_file, result.raw_output)

    assert result is not None


async def upload_benchmark_files(
    device: BaseDevice,
    case: RemoteCodecCase,
    workspace: RemoteTestWorkspace,
) -> None:
    for local_file in case.codec.benchmark_files:
        remote_file = posixpath.join(workspace.remote_case_dir, local_file.name)

        append_log(
            workspace.log_file,
            f"Uploading local={local_file} remote={remote_file}",
        )

        await device.upload_file(
            local_path=local_file,
            remote_path=remote_file,
            create_remote_dirs=True,
            overwrite=True,
            verify_size=True,
        )

        workspace.uploaded_remote_files.append(remote_file)


async def run_ls_ltr(
    device: BaseDevice,
    case: RemoteCodecCase,
    workspace: RemoteTestWorkspace,
) -> None:
    command = f"ls -ltr {shell_quote(workspace.remote_case_dir)}"

    append_log(workspace.log_file, f"[{case.device_id}] $ {command}")

    result = await device.run_command(command, timeout=20.0)

    append_log(workspace.log_file, "--- ls -ltr output ---")
    append_log(workspace.log_file, result.raw_output)

    ls_output_file = workspace.local_tmp_dir / "ls_output.txt"
    ls_output_file.write_text(result.raw_output, encoding="utf-8")

    assert result.raw_output.strip(), "ls -ltr returned empty output"


async def verify_remote_files_exist(
    device: BaseDevice,
    case: RemoteCodecCase,
    workspace: RemoteTestWorkspace,
) -> None:
    missing_files: list[str] = []

    for remote_file in workspace.uploaded_remote_files:
        command = f"test -f {shell_quote(remote_file)} && echo EXISTS || echo MISSING"

        append_log(workspace.log_file, f"[{case.device_id}] $ {command}")

        result = await device.run_command(command, timeout=20.0)

        append_log(workspace.log_file, result.raw_output)

        if "EXISTS" not in result.raw_output:
            missing_files.append(remote_file)

    assert not missing_files, (
        f"Missing remote benchmark files on {case.device_id}: "
        + ", ".join(missing_files)
    )


async def cleanup_remote_directory(
    device: BaseDevice,
    case: RemoteCodecCase,
    workspace: RemoteTestWorkspace,
) -> None:
    command = f"rm -rf {shell_quote(workspace.remote_case_dir)}"

    append_log(workspace.log_file, f"[{case.device_id}] cleanup $ {command}")

    result = await device.run_command(command, timeout=20.0)

    append_log(workspace.log_file, result.raw_output)


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"