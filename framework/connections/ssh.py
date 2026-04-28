from __future__ import annotations

import asyncio
import contextlib
import posixpath
import threading
import time
from pathlib import Path

import paramiko

from framework.connections.base import BaseConnection
from framework.connections.exceptions import (
    AuthenticationError,
    ConnectionClosedError,
    ConnectionOpenError,
    FileTransferError,
    FileVerificationError,
    HostKeyError,
    PromptTimeoutError,
)
from framework.models.config import build_host_key_policy, compile_prompt_patterns
from framework.utils.logging import LOGGER
from framework.utils.normalization import normalize_output


class SSHConnection(BaseConnection):
    def __init__(self, *, device_id: str, config) -> None:
        super().__init__(device_id=device_id, config=config)
        self._client: paramiko.SSHClient | None = None
        self._transport: paramiko.Transport | None = None
        self._channel: paramiko.Channel | None = None

        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()

    async def open(self) -> None:
        self._closed = False
        self._loop = asyncio.get_running_loop()

        await asyncio.to_thread(self._open_blocking)
        self._connected = True

        await self._validate_initial_prompt()

    async def _validate_initial_prompt(self) -> None:
        prompts = compile_prompt_patterns(self.config.expected_prompts)
        if not prompts:
            raise ConnectionOpenError(
                f"No expected_prompts configured for device={self.device_id!r}"
            )

        start_pos = len(self._buffer)

        try:
            prompt = await self._wait_for_patterns(
                patterns=prompts,
                timeout=self.config.prompt_timeout,
                start_pos=start_pos,
            )
            self._last_prompt = prompt
            LOGGER.info(
                "Initial prompt validated for device=%s prompt=%r",
                self.device_id,
                prompt,
            )
            return
        except PromptTimeoutError:
            LOGGER.info(
                "Initial prompt not seen immediately for device=%s. Sending newline.",
                self.device_id,
            )

        start_pos = len(self._buffer)
        await self._send_raw("\n")

        try:
            prompt = await self._wait_for_patterns(
                patterns=prompts,
                timeout=self.config.prompt_timeout,
                start_pos=start_pos,
            )
            self._last_prompt = prompt
            LOGGER.info(
                "Initial prompt validated after newline for device=%s prompt=%r",
                self.device_id,
                prompt,
            )
        except PromptTimeoutError as exc:
            await self.close()
            raise ConnectionOpenError(
                f"SSH connected but no expected prompt was detected for "
                f"device={self.device_id!r}. "
                f"Expected prompts={self.config.expected_prompts!r}"
            ) from exc

    def _open_blocking(self) -> None:
        LOGGER.info(
            "Opening SSH connection to device=%s host=%s port=%s",
            self.device_id,
            self.config.host,
            self.config.port,
        )

        self._reader_stop.set()
        self._stop_reader_blocking()
        self._close_blocking()

        client = paramiko.SSHClient()
        if self.config.known_hosts_file:
            client.load_host_keys(self.config.known_hosts_file)
        else:
            client.load_system_host_keys()

        client.set_missing_host_key_policy(build_host_key_policy(self.config.host_key_policy))

        try:
            client.connect(
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
                timeout=self.config.timeout,
                banner_timeout=self.config.banner_timeout,
                auth_timeout=self.config.auth_timeout,
                allow_agent=self.config.allow_agent,
                look_for_keys=self.config.look_for_keys,
                compress=False,
            )
        except paramiko.AuthenticationException as exc:
            raise AuthenticationError(
                f"Authentication failed for device={self.device_id!r}"
            ) from exc
        except paramiko.BadHostKeyException as exc:
            raise HostKeyError(
                f"Host key validation failed for device={self.device_id!r}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ConnectionOpenError(
                f"Failed to connect to device={self.device_id!r} "
                f"({self.config.host}:{self.config.port})"
            ) from exc

        transport = client.get_transport()
        if transport is None or not transport.is_active():
            client.close()
            raise ConnectionOpenError(
                f"Transport not active after connect for device={self.device_id!r}"
            )

        transport.set_keepalive(self.config.keepalive_interval)

        try:
            channel = client.invoke_shell(
                term=self.config.term,
                width=self.config.terminal_width,
                height=self.config.terminal_height,
            )
            channel.settimeout(0.0)
        except Exception as exc:  # noqa: BLE001
            client.close()
            raise ConnectionOpenError(
                f"Failed to invoke shell for device={self.device_id!r}"
            ) from exc

        self._client = client
        self._transport = transport
        self._channel = channel

        self._reader_stop.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop_blocking,
            name=f"ssh-reader-{self.device_id}",
            daemon=True,
        )
        self._reader_thread.start()

    def _reader_loop_blocking(self) -> None:
        assert self._channel is not None
        assert self._loop is not None

        LOGGER.info("Reader thread started for device=%s", self.device_id)

        try:
            while not self._reader_stop.is_set():
                channel = self._channel
                transport = self._transport

                if channel is None or transport is None or not transport.is_active() or channel.closed:
                    break

                if channel.recv_ready():
                    data = channel.recv(self.config.read_chunk_size)
                    if not data:
                        break

                    text = normalize_output(data.decode("utf-8", errors="replace"))
                    self._loop.call_soon_threadsafe(self._publish_data, text)
                else:
                    time.sleep(self.config.read_sleep)
        except Exception:
            LOGGER.exception("Reader loop crashed for device=%s", self.device_id)
        finally:
            self._loop.call_soon_threadsafe(self._mark_disconnected)
            LOGGER.warning("Reader thread exited for device=%s", self.device_id)

    def _mark_disconnected(self) -> None:
        self._connected = False

    async def close(self) -> None:
        self._closed = True
        await asyncio.to_thread(self._close_all_blocking)

    async def reset(self) -> None:
        self._closed = False
        await asyncio.to_thread(self._close_all_blocking)

    def _close_all_blocking(self) -> None:
        self._reader_stop.set()
        self._stop_reader_blocking()
        self._close_blocking()
        self._connected = False

    def _stop_reader_blocking(self) -> None:
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        self._reader_thread = None

    def _close_blocking(self) -> None:
        with contextlib.suppress(Exception):
            if self._channel is not None:
                self._channel.close()
        with contextlib.suppress(Exception):
            if self._client is not None:
                self._client.close()

        self._channel = None
        self._transport = None
        self._client = None

    def is_alive(self) -> bool:
        return bool(
            self._client is not None
            and self._transport is not None
            and self._transport.is_active()
            and self._channel is not None
            and not self._channel.closed
            and self._connected
            and not self._closed
        )

    async def _send_raw(self, data: str) -> None:
        await asyncio.to_thread(self._send_raw_blocking, data)

    def _send_raw_blocking(self, data: str) -> None:
        if not self.is_alive() or self._channel is None:
            raise ConnectionClosedError(
                f"SSH channel is not alive for device={self.device_id!r}"
            )

        try:
            self._channel.sendall(data)
        except Exception as exc:  # noqa: BLE001
            raise ConnectionClosedError(
                f"Failed to send data for device={self.device_id!r}"
            ) from exc

    async def upload_file(
        self,
        local_path: str | Path,
        remote_path: str,
        *,
        create_remote_dirs: bool = False,
        overwrite: bool = True,
        verify_size: bool = True,
    ) -> None:
        await self.ensure_connected()
        await asyncio.to_thread(
            self._upload_file_blocking,
            Path(local_path),
            remote_path,
            create_remote_dirs,
            overwrite,
            verify_size,
        )

    async def download_file(
        self,
        remote_path: str,
        local_path: str | Path,
        *,
        create_local_dirs: bool = False,
        overwrite: bool = True,
        verify_size: bool = True,
    ) -> None:
        await self.ensure_connected()
        await asyncio.to_thread(
            self._download_file_blocking,
            remote_path,
            Path(local_path),
            create_local_dirs,
            overwrite,
            verify_size,
        )

    def _open_sftp_blocking(self) -> paramiko.SFTPClient:
        if self._client is None or not self.is_alive():
            raise ConnectionClosedError(
                f"SSH client is not alive for device={self.device_id!r}"
            )
        try:
            return self._client.open_sftp()
        except Exception as exc:  # noqa: BLE001
            raise FileTransferError(
                f"Failed to open SFTP session for device={self.device_id!r}"
            ) from exc

    def _mkdir_p_remote_blocking(self, sftp: paramiko.SFTPClient, remote_dir: str) -> None:
        if not remote_dir:
            return

        parts = []
        current = remote_dir
        while current not in ("", "/"):
            parts.append(current)
            current = posixpath.dirname(current)

        if remote_dir.startswith("/"):
            parts.append("/")

        for path in reversed(parts):
            try:
                sftp.stat(path)
            except IOError:
                if path == "/":
                    continue
                sftp.mkdir(path)

    def _upload_file_blocking(
        self,
        local_path: Path,
        remote_path: str,
        create_remote_dirs: bool,
        overwrite: bool,
        verify_size: bool,
    ) -> None:
        if not local_path.exists():
            raise FileTransferError(f"Local file does not exist: {local_path}")

        if not local_path.is_file():
            raise FileTransferError(f"Local path is not a file: {local_path}")

        with self._open_sftp_blocking() as sftp:
            remote_dir = posixpath.dirname(remote_path)
            if create_remote_dirs and remote_dir:
                self._mkdir_p_remote_blocking(sftp, remote_dir)

            if not overwrite:
                try:
                    sftp.stat(remote_path)
                    raise FileTransferError(
                        f"Remote file already exists and overwrite=False: {remote_path}"
                    )
                except IOError:
                    pass

            try:
                sftp.put(str(local_path), remote_path)
            except Exception as exc:  # noqa: BLE001
                raise FileTransferError(
                    f"Failed to upload file to {remote_path!r} for device={self.device_id!r}"
                ) from exc

            if verify_size:
                local_size = local_path.stat().st_size
                remote_size = sftp.stat(remote_path).st_size
                if local_size != remote_size:
                    raise FileVerificationError(
                        f"Upload verification failed for device={self.device_id!r}: "
                        f"local_size={local_size}, remote_size={remote_size}, "
                        f"remote_path={remote_path!r}"
                    )

        LOGGER.info(
            "Uploaded file for device=%s local=%s remote=%s",
            self.device_id,
            local_path,
            remote_path,
        )

    def _download_file_blocking(
        self,
        remote_path: str,
        local_path: Path,
        create_local_dirs: bool,
        overwrite: bool,
        verify_size: bool,
    ) -> None:
        if local_path.exists() and not overwrite:
            raise FileTransferError(
                f"Local file already exists and overwrite=False: {local_path}"
            )

        if create_local_dirs:
            local_path.parent.mkdir(parents=True, exist_ok=True)

        with self._open_sftp_blocking() as sftp:
            try:
                remote_stat = sftp.stat(remote_path)
            except Exception as exc:  # noqa: BLE001
                raise FileTransferError(
                    f"Remote file does not exist or cannot be accessed: {remote_path!r}"
                ) from exc

            try:
                sftp.get(remote_path, str(local_path))
            except Exception as exc:  # noqa: BLE001
                raise FileTransferError(
                    f"Failed to download file from {remote_path!r} for device={self.device_id!r}"
                ) from exc

            if verify_size:
                local_size = local_path.stat().st_size
                remote_size = remote_stat.st_size
                if local_size != remote_size:
                    raise FileVerificationError(
                        f"Download verification failed for device={self.device_id!r}: "
                        f"remote_size={remote_size}, local_size={local_size}, "
                        f"remote_path={remote_path!r}, local_path={str(local_path)!r}"
                    )

        LOGGER.info(
            "Downloaded file for device=%s remote=%s local=%s",
            self.device_id,
            remote_path,
            local_path,
        )