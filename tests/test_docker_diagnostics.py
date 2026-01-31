from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.docker.diagnostics import (
    ContainerInfo,
    _analyze_logs_for_errors,
    _format_size,
    _get_container_info,
    _get_container_logs,
    _parse_size,
    _run_docker_command,
    diagnose_container_restart,
    explain_resource_usage,
)

# ---------------------------------------------------------------------------
# _run_docker_command
# ---------------------------------------------------------------------------


class TestRunDockerCommand:
    def test_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "hello"
        with patch("elle.cli.docker.diagnostics.subprocess.run", return_value=mock_result):
            result = _run_docker_command(["ps"])
        assert result == "hello"

    def test_nonzero_return(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"
        with patch("elle.cli.docker.diagnostics.subprocess.run", return_value=mock_result):
            result = _run_docker_command(["ps"])
        assert result is None

    def test_timeout(self):
        import subprocess as sp

        with patch("elle.cli.docker.diagnostics.subprocess.run", side_effect=sp.TimeoutExpired("cmd", 30)):
            result = _run_docker_command(["ps"])
        assert result is None

    def test_file_not_found(self):
        with patch("elle.cli.docker.diagnostics.subprocess.run", side_effect=FileNotFoundError):
            result = _run_docker_command(["ps"])
        assert result is None

    def test_general_exception(self):
        with patch("elle.cli.docker.diagnostics.subprocess.run", side_effect=RuntimeError("err")):
            result = _run_docker_command(["ps"])
        assert result is None


# ---------------------------------------------------------------------------
# _get_container_info
# ---------------------------------------------------------------------------


class TestGetContainerInfo:
    def test_success(self):
        data = {
            "Id": "abcdef123456789",
            "Name": "/mycontainer",
            "Config": {"Image": "nginx:latest"},
            "State": {
                "Status": "running",
                "ExitCode": 0,
                "OOMKilled": False,
                "StartedAt": "2024-01-01T00:00:00Z",
                "FinishedAt": "0001-01-01T00:00:00Z",
                "Health": {"Status": "healthy"},
            },
            "RestartCount": 2,
        }
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value=json.dumps(data)):
            info = _get_container_info("mycontainer")
        assert info is not None
        assert info.name == "mycontainer"
        assert info.image == "nginx:latest"
        assert info.restart_count == 2
        assert info.health_status == "healthy"

    def test_not_found(self):
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value=None):
            info = _get_container_info("nonexistent")
        assert info is None

    def test_invalid_json(self):
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value="NOT JSON"):
            info = _get_container_info("bad")
        assert info is None

    def test_bad_timestamps(self):
        data = {
            "Id": "abc123",
            "Name": "/test",
            "Config": {"Image": "test"},
            "State": {
                "Status": "exited",
                "ExitCode": 1,
                "OOMKilled": False,
                "StartedAt": "INVALID_TS",
                "FinishedAt": "ALSO_BAD",
            },
            "RestartCount": 0,
        }
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value=json.dumps(data)):
            info = _get_container_info("test")
        assert info is not None
        assert info.started_at is None
        assert info.finished_at is None

    def test_no_health(self):
        data = {
            "Id": "abc123",
            "Name": "/test",
            "Config": {"Image": "test"},
            "State": {
                "Status": "running",
                "ExitCode": 0,
                "OOMKilled": False,
            },
            "RestartCount": 0,
        }
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value=json.dumps(data)):
            info = _get_container_info("test")
        assert info is not None
        assert info.health_status is None


# ---------------------------------------------------------------------------
# _get_container_logs
# ---------------------------------------------------------------------------


class TestGetContainerLogs:
    def test_success(self):
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value="log output"):
            logs = _get_container_logs("test")
        assert logs == "log output"

    def test_none(self):
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value=None):
            logs = _get_container_logs("test")
        assert logs == ""


# ---------------------------------------------------------------------------
# _analyze_logs_for_errors
# ---------------------------------------------------------------------------


class TestAnalyzeLogsForErrors:
    @pytest.mark.parametrize(
        "log_line,expected_issue",
        [
            ("Connection refused to localhost:5432", "Connection refused to dependency"),
            ("Permission denied when reading /etc/secret", "Permission denied error"),
            ("OOM killer invoked", "Memory exhaustion"),
            ("disk full error", "Disk space exhaustion"),
            ("Request timed out", "Timeout waiting for resource"),
            ("Segmentation fault (core dumped)", "Application crash (segfault)"),
            ("Process killed by SIGKILL", "Process killed by signal"),
            ("failed to bind to port 8080", "Port already in use"),
            ("database connection error", "Database connection issue"),
            ("SSL certificate error", "SSL/TLS certificate issue"),
            ("authentication failed 401", "Authentication failure"),
            ("dns resolution failure", "DNS resolution failure"),
        ],
    )
    def test_pattern_detection(self, log_line, expected_issue):
        issues = _analyze_logs_for_errors(log_line)
        assert expected_issue in issues

    def test_no_issues(self):
        issues = _analyze_logs_for_errors("Everything is fine")
        assert len(issues) == 0


# ---------------------------------------------------------------------------
# diagnose_container_restart
# ---------------------------------------------------------------------------


class TestDiagnoseContainerRestart:
    def test_container_not_found(self):
        with patch("elle.cli.docker.diagnostics._get_container_info", return_value=None):
            diag = diagnose_container_restart("nonexistent")
        assert "not found" in diag.summary
        assert diag.severity == "error"

    def test_oom_killed(self):
        info = ContainerInfo(
            id="abc123",
            name="myapp",
            image="myapp:latest",
            status="exited",
            state="exited",
            exit_code=137,
            oom_killed=True,
            restart_count=3,
        )
        with (
            patch("elle.cli.docker.diagnostics._get_container_info", return_value=info),
            patch("elle.cli.docker.diagnostics._get_container_logs", return_value=""),
            patch("elle.cli.docker.diagnostics._analyze_logs_for_errors", return_value=()),
        ):
            diag = diagnose_container_restart("myapp")
        assert "OOM" in diag.likely_cause or "memory" in diag.summary.lower()
        assert diag.severity == "error"

    def test_exit_code_137(self):
        info = ContainerInfo(
            id="abc123",
            name="myapp",
            image="myapp:latest",
            status="exited",
            state="exited",
            exit_code=137,
            oom_killed=False,
            restart_count=1,
        )
        with (
            patch("elle.cli.docker.diagnostics._get_container_info", return_value=info),
            patch("elle.cli.docker.diagnostics._get_container_logs", return_value=""),
            patch("elle.cli.docker.diagnostics._analyze_logs_for_errors", return_value=()),
        ):
            diag = diagnose_container_restart("myapp")
        assert "SIGKILL" in diag.likely_cause

    def test_exit_code_127(self):
        info = ContainerInfo(
            id="abc123",
            name="myapp",
            image="myapp:latest",
            status="exited",
            state="exited",
            exit_code=127,
            oom_killed=False,
            restart_count=0,
        )
        with (
            patch("elle.cli.docker.diagnostics._get_container_info", return_value=info),
            patch("elle.cli.docker.diagnostics._get_container_logs", return_value=""),
            patch("elle.cli.docker.diagnostics._analyze_logs_for_errors", return_value=()),
        ):
            diag = diagnose_container_restart("myapp")
        assert any("entrypoint" in r.lower() or "image" in r.lower() for r in diag.recommendations)

    def test_unhealthy(self):
        info = ContainerInfo(
            id="abc123",
            name="myapp",
            image="myapp:latest",
            status="running",
            state="running",
            exit_code=0,
            oom_killed=False,
            health_status="unhealthy",
            restart_count=0,
        )
        with (
            patch("elle.cli.docker.diagnostics._get_container_info", return_value=info),
            patch("elle.cli.docker.diagnostics._get_container_logs", return_value=""),
            patch("elle.cli.docker.diagnostics._analyze_logs_for_errors", return_value=()),
        ):
            diag = diagnose_container_restart("myapp")
        assert "health" in diag.likely_cause.lower()

    def test_log_issues(self):
        info = ContainerInfo(
            id="abc123",
            name="myapp",
            image="myapp:latest",
            status="exited",
            state="exited",
            exit_code=1,
            oom_killed=False,
            restart_count=0,
        )
        with (
            patch("elle.cli.docker.diagnostics._get_container_info", return_value=info),
            patch("elle.cli.docker.diagnostics._get_container_logs", return_value="connection refused"),
            patch(
                "elle.cli.docker.diagnostics._analyze_logs_for_errors",
                return_value=("Connection refused to dependency",),
            ),
        ):
            diag = diagnose_container_restart("myapp")
        assert "Connection refused" in diag.likely_cause

    def test_high_restart_count(self):
        info = ContainerInfo(
            id="abc123",
            name="myapp",
            image="myapp:latest",
            status="exited",
            state="exited",
            exit_code=1,
            oom_killed=False,
            restart_count=10,
        )
        with (
            patch("elle.cli.docker.diagnostics._get_container_info", return_value=info),
            patch("elle.cli.docker.diagnostics._get_container_logs", return_value=""),
            patch("elle.cli.docker.diagnostics._analyze_logs_for_errors", return_value=()),
        ):
            diag = diagnose_container_restart("myapp")
        assert diag.severity == "error"
        assert any("10" in c for c in diag.causes)

    def test_unknown_cause(self):
        info = ContainerInfo(
            id="abc123",
            name="myapp",
            image="myapp:latest",
            status="exited",
            state="exited",
            exit_code=0,
            oom_killed=False,
            restart_count=0,
        )
        with (
            patch("elle.cli.docker.diagnostics._get_container_info", return_value=info),
            patch("elle.cli.docker.diagnostics._get_container_logs", return_value=""),
            patch("elle.cli.docker.diagnostics._analyze_logs_for_errors", return_value=()),
        ):
            diag = diagnose_container_restart("myapp")
        assert "Unknown" in diag.likely_cause

    def test_long_logs_truncated(self):
        info = ContainerInfo(
            id="abc123",
            name="myapp",
            image="myapp:latest",
            status="exited",
            state="exited",
            exit_code=1,
            oom_killed=False,
            restart_count=0,
        )
        long_logs = "x" * 2000
        with (
            patch("elle.cli.docker.diagnostics._get_container_info", return_value=info),
            patch("elle.cli.docker.diagnostics._get_container_logs", return_value=long_logs),
            patch("elle.cli.docker.diagnostics._analyze_logs_for_errors", return_value=()),
        ):
            diag = diagnose_container_restart("myapp")
        assert len(diag.logs_excerpt) <= 1000

    def test_short_container_id(self):
        with patch("elle.cli.docker.diagnostics._get_container_info", return_value=None):
            diag = diagnose_container_restart("abc")
        assert diag.container.id == "abc"


# ---------------------------------------------------------------------------
# explain_resource_usage
# ---------------------------------------------------------------------------


class TestExplainResourceUsage:
    def test_no_docker(self):
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value=None):
            result = explain_resource_usage()
        assert "unable" in result.summary.lower() or "Docker" in result.summary

    def test_no_containers(self):
        # Empty string is falsy, so _run_docker_command returning "" gives "Unable..."
        # To get "No running containers", we need valid output but no containers parsed
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value="\n"):
            result = explain_resource_usage()
        assert "No running containers" in result.summary

    def test_with_specific_container(self):
        data = json.dumps(
            {
                "ID": "abc123456789",
                "Name": "postgres",
                "CPUPerc": "10.5%",
                "MemUsage": "500MiB / 1GiB",
                "MemPerc": "50.0%",
                "NetIO": "100KB / 200KB",
                "BlockIO": "50MB / 100MB",
            }
        )
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value=data):
            result = explain_resource_usage("abc123")
        assert len(result.containers) == 1
        assert result.containers[0].name == "postgres"

    def test_high_memory_warning(self):
        data = json.dumps(
            {
                "ID": "abc123456789",
                "Name": "postgres",
                "CPUPerc": "5.0%",
                "MemUsage": "900MiB / 1GiB",
                "MemPerc": "90.0%",
                "NetIO": "0B / 0B",
                "BlockIO": "0B / 0B",
            }
        )
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value=data):
            result = explain_resource_usage()
        assert any("memory" in w.lower() or "HIGH" in w for w in result.warnings)
        assert any("HIGH" in m for m in result.top_memory)

    def test_high_cpu_warning(self):
        data = json.dumps(
            {
                "ID": "abc123456789",
                "Name": "worker",
                "CPUPerc": "95.0%",
                "MemUsage": "100MiB / 1GiB",
                "MemPerc": "10.0%",
                "NetIO": "0B / 0B",
                "BlockIO": "0B / 0B",
            }
        )
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value=data):
            result = explain_resource_usage()
        assert any("CPU" in w for w in result.warnings)
        assert any("CPU-bound" in c for c in result.top_cpu)

    def test_moderate_usage(self):
        data = json.dumps(
            {
                "ID": "abc123456789",
                "Name": "app",
                "CPUPerc": "55.0%",
                "MemUsage": "600MiB / 1GiB",
                "MemPerc": "60.0%",
                "NetIO": "0B / 0B",
                "BlockIO": "0B / 0B",
            }
        )
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value=data):
            result = explain_resource_usage()
        assert result.summary != ""  # Has a non-empty summary

    def test_normal_usage(self):
        data = json.dumps(
            {
                "ID": "abc123456789",
                "Name": "app",
                "CPUPerc": "2.0%",
                "MemUsage": "50MiB / 1GiB",
                "MemPerc": "5.0%",
                "NetIO": "0B / 0B",
                "BlockIO": "0B / 0B",
            }
        )
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value=data):
            result = explain_resource_usage()
        assert "normal" in result.summary.lower()

    def test_bad_json_line(self):
        output = "NOT JSON\n" + json.dumps(
            {
                "ID": "abc123456789",
                "Name": "app",
                "CPUPerc": "2.0%",
                "MemUsage": "50MiB / 1GiB",
                "MemPerc": "5.0%",
                "NetIO": "0B / 0B",
                "BlockIO": "0B / 0B",
            }
        )
        with patch("elle.cli.docker.diagnostics._run_docker_command", return_value=output):
            result = explain_resource_usage()
        assert len(result.containers) == 1


# ---------------------------------------------------------------------------
# _parse_size / _format_size
# ---------------------------------------------------------------------------


class TestParseSizeFormatSize:
    @pytest.mark.parametrize(
        "input_str,expected",
        [
            ("0B", 0),
            ("100B", 100),
            ("1KB", 1000),
            ("1KiB", 1024),
            ("1MB", 1000000),
            ("1MiB", 1048576),
            ("1.5GiB", int(1.5 * 1024 * 1024 * 1024)),
            ("1TB", 1000000000000),
            ("1TiB", 1024 * 1024 * 1024 * 1024),
            ("500", 500),
            ("invalid", 0),
        ],
    )
    def test_parse_size(self, input_str, expected):
        assert _parse_size(input_str) == expected

    @pytest.mark.parametrize(
        "bytes_val,expected",
        [
            (500, "500B"),
            (1500, "1.5KB"),
            (1500000, "1.4MB"),
            (1500000000, "1.4GB"),
        ],
    )
    def test_format_size(self, bytes_val, expected):
        assert _format_size(bytes_val) == expected
