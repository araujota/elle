"""Chaos test fixtures - fault injection helpers for resilience testing.

All fault injection uses unittest.mock and test doubles; no real system
faults are created.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class FaultInjector:
    """Test fixture that provides fault injection primitives.

    Usage in tests::

        def test_disk_full(fault: FaultInjector):
            with fault.disk_full():
                # Code under test sees ENOSPC on writes
                ...
    """

    @contextmanager
    def missing_directory(self, path: str) -> Generator[None, None, None]:
        """Simulate a missing directory by making os.path.isdir return False
        and Path.mkdir / os.makedirs raise FileNotFoundError."""
        original_isdir = os.path.isdir

        def fake_isdir(p: str) -> bool:
            if str(p) == path:
                return False
            return original_isdir(p)

        with (
            patch("os.path.isdir", side_effect=fake_isdir),
            patch("pathlib.Path.mkdir", side_effect=FileNotFoundError(errno.ENOENT, "No such file or directory", path)),
        ):
            yield

    @contextmanager
    def corrupted_toml(self, path: str, content: str = "{{{{invalid toml") -> Generator[None, None, None]:
        """Simulate a corrupted TOML config file."""
        with patch("builtins.open", side_effect=self._corrupted_open(path, content)):
            yield

    @contextmanager
    def read_only_storage(self) -> Generator[None, None, None]:
        """Simulate read-only filesystem for database writes."""
        original_open = open

        def readonly_open(name: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            if "w" in mode or "a" in mode:
                raise PermissionError(errno.EACCES, "Read-only file system", str(name))
            return original_open(name, mode, *args, **kwargs)

        with patch("builtins.open", side_effect=readonly_open):
            yield

    @contextmanager
    def service_unavailable(self, target: str) -> Generator[MagicMock, None, None]:
        """Simulate a service (Ollama, PostgreSQL, etc.) refusing connections."""
        mock = AsyncMock(side_effect=ConnectionRefusedError("Connection refused"))
        with patch(target, mock):
            yield mock

    @contextmanager
    def subprocess_crash(self, exit_code: int = -11) -> Generator[MagicMock, None, None]:
        """Simulate a subprocess that crashes (e.g., SIGSEGV)."""
        mock_result = MagicMock()
        mock_result.returncode = exit_code
        mock_result.stdout = b""
        mock_result.stderr = b"Segmentation fault (core dumped)"
        mock_result.communicate = AsyncMock(return_value=(b"", b"Segmentation fault"))

        with patch("subprocess.run", return_value=mock_result) as mock:
            yield mock

    @contextmanager
    def subprocess_timeout(self, timeout_sec: float = 0.1) -> Generator[MagicMock, None, None]:
        """Simulate a subprocess that hangs and times out."""
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", timeout_sec)) as mock:
            yield mock

    @contextmanager
    def permission_denied(self, target: str = "subprocess.run") -> Generator[MagicMock, None, None]:
        """Simulate EACCES permission denied."""
        with patch(target, side_effect=PermissionError(errno.EACCES, "Permission denied")) as mock:
            yield mock

    @contextmanager
    def socket_disconnect(self) -> Generator[MagicMock, None, None]:
        """Simulate a Unix socket that disconnects mid-read."""
        mock_socket = MagicMock()
        mock_socket.recv = MagicMock(return_value=b"")  # EOF = disconnect
        mock_socket.send = MagicMock(side_effect=BrokenPipeError("Broken pipe"))
        mock_socket.connect = MagicMock()

        with patch("socket.socket", return_value=mock_socket):
            yield mock_socket

    @contextmanager
    def malformed_json_stream(self, bad_lines: list[str] | None = None) -> Generator[MagicMock, None, None]:
        """Simulate a stream of malformed JSON (e.g., from telemetryd socket)."""
        if bad_lines is None:
            bad_lines = [
                '{"incomplete": true',  # Missing closing brace
                "not json at all",
                '{"ts": null, "category": 12345}',  # Wrong types
                "",  # Empty line
                '{"valid": true}\n{"also": "valid"}',  # Two on one line
            ]

        mock_socket = MagicMock()
        mock_socket.makefile.return_value.__iter__ = MagicMock(return_value=iter(bad_lines))
        mock_socket.recv = MagicMock(side_effect=lambda n: bad_lines.pop(0).encode() if bad_lines else b"")

        with patch("socket.socket", return_value=mock_socket):
            yield mock_socket

    @contextmanager
    def dns_failure(self) -> Generator[MagicMock, None, None]:
        """Simulate DNS resolution failure."""
        import socket

        with patch("socket.getaddrinfo", side_effect=socket.gaierror(socket.EAI_NONAME, "Name or service not known")):
            yield

    @contextmanager
    def clock_skew(self, offset_seconds: float = 3600.0) -> Generator[None, None, None]:
        """Simulate system clock skew by shifting time.time() and time.monotonic()."""
        import time

        original_time = time.time
        original_monotonic = time.monotonic

        with (
            patch("time.time", side_effect=lambda: original_time() + offset_seconds),
            patch("time.monotonic", side_effect=lambda: original_monotonic() + offset_seconds),
        ):
            yield

    @contextmanager
    def database_refused(self) -> Generator[MagicMock, None, None]:
        """Simulate PostgreSQL connection refused."""
        try:
            import psycopg

            exc = psycopg.OperationalError("connection refused")
        except ImportError:
            exc = ConnectionRefusedError("connection refused")  # type: ignore[assignment]

        with patch("elle.storage.engine.get_conn", side_effect=exc) as mock:
            yield mock

    @staticmethod
    def _corrupted_open(target_path: str, content: str):  # type: ignore[no-untyped-def]
        """Return a side_effect function that returns corrupted content for target_path."""
        original_open = open

        def fake_open(name: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            if str(name) == target_path and "r" in mode:
                from io import StringIO

                return StringIO(content)
            return original_open(name, mode, *args, **kwargs)

        return fake_open

    @contextmanager
    def event_flood(self, count: int = 100_000) -> Generator[list[dict[str, Any]], None, None]:
        """Generate a flood of synthetic telemetry events for backpressure testing."""
        events = [
            {
                "ts": 1706300000000000000 + i,
                "source": "journal",
                "severity": "info",
                "category": "other",
                "message": f"Flood event {i}",
                "entity": "",
                "fingerprint": f"{i:016x}",
            }
            for i in range(count)
        ]
        yield events

    @contextmanager
    def expired_mtls_cert(self) -> Generator[MagicMock, None, None]:
        """Simulate an expired mTLS certificate."""
        import ssl

        with patch(
            "ssl.SSLContext.load_cert_chain",
            side_effect=ssl.SSLError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired"),
        ) as mock:
            yield mock

    @contextmanager
    def unreachable_endpoint(self, url: str = "https://ntfy.example.com") -> Generator[MagicMock, None, None]:
        """Simulate an unreachable HTTP endpoint (e.g., notification service)."""
        import httpx

        with patch(
            "httpx.AsyncClient.post",
            side_effect=httpx.ConnectError(f"Failed to connect to {url}"),
        ) as mock:
            yield mock


@pytest.fixture
def fault() -> FaultInjector:
    """Provide a FaultInjector instance for chaos tests."""
    return FaultInjector()
