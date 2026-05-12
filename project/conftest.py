from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from framework.devices.base import BaseDevice
from framework.devices.factory import create_device

from remote_codec_framework import (
    RemoteCodecCase,
    RemoteTestWorkspace,
    SuiteSetup,
    append_log,
    archive_workspace,
    build_artifact_links,
    compact_links_html,
    discover_codecs,
    generate_cases,
    load_suite_setup,
    remote_join,
    write_case_metadata,
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--setup",
        action="store",
        required=True,
        help="Path to test setup YAML.",
    )
    parser.addoption(
        "--devices",
        action="store",
        required=True,
        help="Path to devices YAML.",
    )
    parser.addoption(
        "--keep-temp",
        action="store_true",
        default=False,
        help="Keep local temporary files.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """
    Suite collection setup.

    Parses setup, discovers codecs, and generates logical test cases.
    Actual SSH connections are opened in the async session fixture.
    """
    setup_path = Path(config.getoption("--setup")).resolve()
    devices_path = Path(config.getoption("--devices")).resolve()

    setup = load_suite_setup(setup_path=setup_path, devices_path=devices_path)
    codecs = discover_codecs(setup.codecs_dir)
    cases = generate_cases(setup, codecs)

    suite_tmp_dir = Path.cwd() / ".pytest_remote_codec_tmp"
    suite_tmp_dir.mkdir(parents=True, exist_ok=True)
    setup.reports.log_archive_dir.mkdir(parents=True, exist_ok=True)

    config.remote_codec_setup = setup
    config.remote_codec_cases = cases
    config.remote_codec_suite_tmp_dir = suite_tmp_dir


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "remote_codec_case" not in metafunc.fixturenames:
        return

    cases: list[RemoteCodecCase] = getattr(
        metafunc.config,
        "remote_codec_cases",
        [],
    )

    if not cases:
        raise pytest.UsageError("No remote codec test cases were generated.")

    metafunc.parametrize(
        "remote_codec_case",
        cases,
        ids=[case.id for case in cases],
    )


@pytest_asyncio.fixture(scope="session")
async def connected_devices(
    request: pytest.FixtureRequest,
) -> dict[str, BaseDevice]:
    """
    Suite setUp.

    Opens all selected device connections once per test session.
    """
    setup: SuiteSetup = request.config.remote_codec_setup

    devices: dict[str, BaseDevice] = {}

    try:
        for device_id, device_config in setup.device_configs.items():
            device = await create_device(device_config)
            devices[device_id] = device

        yield devices

    finally:
        close_tasks = []

        for device in devices.values():
            close_tasks.append(device.conn.close())

        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)


@pytest.fixture
def remote_test_workspace(
    request: pytest.FixtureRequest,
    remote_codec_case: RemoteCodecCase,
) -> RemoteTestWorkspace:
    """
    Test setUp / Test TearDown.

    Creates local logs and stable archive folder.
    """
    setup: SuiteSetup = request.config.remote_codec_setup
    suite_tmp_dir: Path = request.config.remote_codec_suite_tmp_dir

    local_tmp_dir = suite_tmp_dir / remote_codec_case.id
    archive_dir = setup.reports.log_archive_dir / remote_codec_case.id

    if local_tmp_dir.exists():
        shutil.rmtree(local_tmp_dir, ignore_errors=True)

    if archive_dir.exists():
        shutil.rmtree(archive_dir, ignore_errors=True)

    local_tmp_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    remote_case_dir = remote_join(
        remote_codec_case.remote_root_dir,
        remote_codec_case.device_id,
        remote_codec_case.codec.name,
        remote_codec_case.id,
    )

    workspace = RemoteTestWorkspace(
        case_id=remote_codec_case.id,
        local_tmp_dir=local_tmp_dir,
        archive_dir=archive_dir,
        log_file=local_tmp_dir / "test.log",
        remote_case_dir=remote_case_dir,
        uploaded_remote_files=[],
    )

    append_log(workspace.log_file, f"Created local tmp dir: {local_tmp_dir}")
    append_log(workspace.log_file, f"Archive dir: {archive_dir}")
    append_log(workspace.log_file, f"Remote case dir: {remote_case_dir}")

    yield workspace

    write_case_metadata(workspace, remote_codec_case)
    archive_workspace(workspace)

    keep_temp = bool(request.config.getoption("--keep-temp"))
    if not keep_temp:
        shutil.rmtree(local_tmp_dir, ignore_errors=True)


def pytest_unconfigure(config: pytest.Config) -> None:
    """
    Suite TearDown.

    Cleans local temp files only.
    Remote cleanup is done per test because each test owns its remote case dir.
    """
    keep_temp = bool(config.getoption("--keep-temp", default=False))
    suite_tmp_dir = getattr(config, "remote_codec_suite_tmp_dir", None)

    if suite_tmp_dir and Path(suite_tmp_dir).exists() and not keep_temp:
        shutil.rmtree(suite_tmp_dir, ignore_errors=True)


def pytest_html_report_title(report: Any) -> None:
    report.title = "Remote Codec Test Report"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item,
    call: pytest.CallInfo[Any],
) -> Any:
    outcome = yield
    report = outcome.get_result()

    if call.when != "call":
        return

    case: RemoteCodecCase | None = item.funcargs.get("remote_codec_case")
    workspace: RemoteTestWorkspace | None = item.funcargs.get("remote_test_workspace")

    if case is None or workspace is None:
        return

    html_report_path = _get_html_report_path(item.config)
    links = build_artifact_links(html_report_path, workspace.archive_dir)

    report.scenario_name = case.scenario.name
    report.device_id = case.device_id
    report.codec_name = case.codec.name
    report.remote_artifact_links = links

    pytest_html = item.config.pluginmanager.getplugin("html")
    if pytest_html is None:
        return

    extras = getattr(report, "extras", [])
    extras.append(
        pytest_html.extras.html(
            f"""
            <div>
                <p><strong>Scenario:</strong> {case.scenario.name}</p>
                <p><strong>Device:</strong> {case.device_id}</p>
                <p><strong>Codec:</strong> {case.codec.name}</p>
                <p><strong>Artifacts:</strong> {compact_links_html(links)}</p>
            </div>
            """
        )
    )
    report.extras = extras


@pytest.mark.optionalhook
def pytest_html_results_table_header(cells: list[Any]) -> None:
    cells.insert(2, "<th>Scenario</th>")
    cells.insert(3, "<th>Device</th>")
    cells.insert(4, "<th>Codec</th>")
    cells.insert(5, "<th>Artifacts</th>")


@pytest.mark.optionalhook
def pytest_html_results_table_row(report: Any, cells: list[Any]) -> None:
    cells.insert(2, f"<td>{getattr(report, 'scenario_name', '')}</td>")
    cells.insert(3, f"<td>{getattr(report, 'device_id', '')}</td>")
    cells.insert(4, f"<td>{getattr(report, 'codec_name', '')}</td>")
    cells.insert(
        5,
        f"<td>{compact_links_html(getattr(report, 'remote_artifact_links', {}))}</td>",
    )


def _get_html_report_path(config: pytest.Config) -> Path | None:
    option = getattr(config, "option", None)

    if option is None:
        return None

    htmlpath = getattr(option, "htmlpath", None)

    if htmlpath:
        return Path(htmlpath).resolve()

    return None