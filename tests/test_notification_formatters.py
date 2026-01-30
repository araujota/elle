"""Tests for notification formatters (formatters.py).

Covers entity formatting, metric formatting, domain-specific formatters,
forecast formatting, incident formatting, health alert formatting,
and event message formatting.
"""

from __future__ import annotations

import pytest

from elle.daemon.notifications.formatters import (
    _format_domain_metrics,
    _metric_to_label,
    format_bytes,
    format_disk_issue,
    format_docker_issue,
    format_duration_seconds,
    format_entities,
    format_entity,
    format_event_message,
    format_forecast_body,
    format_forecast_title,
    format_health_body,
    format_health_title,
    format_incident_body,
    format_incident_title,
    format_mb,
    format_memory_issue,
    format_network_issue,
    format_oom_event,
    format_percentage,
    format_service_issue,
    format_temperature_issue,
)


# ---------------------------------------------------------------------------
# Tests: format_entity
# ---------------------------------------------------------------------------


class TestFormatEntity:
    def test_none_entity(self):
        assert format_entity(None) == ""

    def test_empty_entity(self):
        assert format_entity("") == ""

    def test_entity_without_colon(self):
        assert format_entity("simple") == "simple"

    def test_service_entity(self):
        result = format_entity("service:nginx")
        assert result == "the nginx service"

    def test_interface_entity(self):
        result = format_entity("interface:eth0")
        assert result == "network interface eth0"

    def test_disk_entity(self):
        result = format_entity("disk:/dev/sda")
        assert result == "disk /dev/sda"

    def test_mount_entity(self):
        result = format_entity("mount:/var")
        assert result == "mount point /var"

    def test_container_entity(self):
        result = format_entity("container:webapp")
        assert result == "container webapp"

    def test_process_entity(self):
        result = format_entity("process:mysql")
        assert result == "process mysql"

    def test_user_entity(self):
        result = format_entity("user:root")
        assert result == "user root"

    def test_package_entity(self):
        result = format_entity("package:nginx")
        assert result == "package nginx"

    def test_file_entity(self):
        result = format_entity("file:/etc/hosts")
        assert result == "file /etc/hosts"

    def test_unknown_entity_type(self):
        result = format_entity("custom:thing")
        assert result == "custom thing"


# ---------------------------------------------------------------------------
# Tests: format_entities
# ---------------------------------------------------------------------------


class TestFormatEntities:
    def test_empty_list(self):
        assert format_entities([]) == ""
        assert format_entities(()) == ""

    def test_single_entity(self):
        result = format_entities(["service:nginx"])
        assert result == "the nginx service"

    def test_two_entities(self):
        result = format_entities(["service:nginx", "service:postgres"])
        assert "and" in result

    def test_three_entities(self):
        result = format_entities(["service:nginx", "service:postgres", "service:redis"])
        assert "," in result

    def test_more_than_max_show(self):
        entities = [f"service:svc{i}" for i in range(5)]
        result = format_entities(entities, max_show=3)
        assert "and 2 more" in result

    def test_custom_max_show(self):
        entities = ["service:a", "service:b", "service:c"]
        result = format_entities(entities, max_show=1)
        assert "and 2 more" in result


# ---------------------------------------------------------------------------
# Tests: format_percentage
# ---------------------------------------------------------------------------


class TestFormatPercentage:
    def test_fraction_value(self):
        result = format_percentage(0.92, "disk")
        assert result == "disk at 92%"

    def test_percentage_value(self):
        result = format_percentage(92.0, "disk")
        assert result == "disk at 92%"

    def test_zero(self):
        result = format_percentage(0, "memory")
        assert "0%" in result


# ---------------------------------------------------------------------------
# Tests: format_bytes
# ---------------------------------------------------------------------------


class TestFormatBytes:
    def test_bytes(self):
        assert format_bytes(500) == "500 B"

    def test_kilobytes(self):
        result = format_bytes(2048)
        assert "KB" in result

    def test_megabytes(self):
        result = format_bytes(5 * 1024 * 1024)
        assert "MB" in result

    def test_gigabytes(self):
        result = format_bytes(3 * 1024 * 1024 * 1024)
        assert "GB" in result

    def test_with_label(self):
        result = format_bytes(1024, label="Used")
        assert result.startswith("Used")

    def test_without_label(self):
        result = format_bytes(1024)
        assert not result.startswith(" ")


# ---------------------------------------------------------------------------
# Tests: format_mb
# ---------------------------------------------------------------------------


class TestFormatMb:
    def test_mb_under_1024(self):
        result = format_mb(512)
        assert result == "512 MB"

    def test_mb_over_1024(self):
        result = format_mb(2048)
        assert "GB" in result

    def test_mb_with_label(self):
        result = format_mb(256, label="Available")
        assert "Available" in result

    def test_mb_without_label(self):
        result = format_mb(256)
        assert not result.startswith(" ")


# ---------------------------------------------------------------------------
# Tests: format_duration_seconds
# ---------------------------------------------------------------------------


class TestFormatDurationSeconds:
    def test_seconds(self):
        assert format_duration_seconds(30) == "30 seconds"

    def test_one_minute(self):
        result = format_duration_seconds(60)
        assert "1 minute" in result
        assert "minutes" not in result

    def test_multiple_minutes(self):
        result = format_duration_seconds(300)
        assert "5 minutes" in result

    def test_one_hour(self):
        result = format_duration_seconds(3600)
        assert "1 hour" in result

    def test_hours_and_minutes(self):
        result = format_duration_seconds(5400)  # 1.5 hours
        assert "hour" in result
        assert "min" in result

    def test_days(self):
        result = format_duration_seconds(86400)
        assert "1 day" in result

    def test_multiple_days(self):
        result = format_duration_seconds(172800)
        assert "2 days" in result


# ---------------------------------------------------------------------------
# Tests: format_disk_issue
# ---------------------------------------------------------------------------


class TestFormatDiskIssue:
    def test_full_info(self):
        result = format_disk_issue(mount="/var", used_pct=95, avail_gb=2.1)
        assert "Disk /var" in result
        assert "95%" in result
        assert "2.1 GB" in result

    def test_no_mount(self):
        result = format_disk_issue(used_pct=90)
        assert result.startswith("Disk")

    def test_low_avail(self):
        result = format_disk_issue(mount="/", avail_gb=0.5)
        assert "MB free" in result

    def test_fraction_used_pct(self):
        result = format_disk_issue(mount="/", used_pct=0.95)
        assert "95%" in result


# ---------------------------------------------------------------------------
# Tests: format_memory_issue
# ---------------------------------------------------------------------------


class TestFormatMemoryIssue:
    def test_available_and_total(self):
        result = format_memory_issue(available_mb=256, total_mb=8192)
        assert "Memory" in result
        assert "256 MB" in result

    def test_only_available(self):
        result = format_memory_issue(available_mb=128)
        assert "128 MB" in result
        assert "Only" in result

    def test_with_swap(self):
        result = format_memory_issue(
            available_mb=256, total_mb=8192, swap_used_mb=1500, swap_total_mb=2048
        )
        assert "Swap" in result

    def test_swap_not_shown_when_low(self):
        result = format_memory_issue(
            available_mb=4096, total_mb=8192, swap_used_mb=100, swap_total_mb=2048
        )
        assert "Swap" not in result

    def test_no_info_fallback(self):
        result = format_memory_issue()
        assert "Memory pressure detected" in result


# ---------------------------------------------------------------------------
# Tests: format_service_issue
# ---------------------------------------------------------------------------


class TestFormatServiceIssue:
    def test_failed_service(self):
        result = format_service_issue("nginx.service", state="failed")
        assert "nginx.service" in result
        assert "has failed" in result

    def test_inactive_service(self):
        result = format_service_issue("nginx.service", state="inactive")
        assert "is stopped" in result

    def test_with_exit_code(self):
        result = format_service_issue("nginx.service", state="failed", exit_code=1)
        assert "exit code 1" in result

    def test_exit_code_zero_not_shown(self):
        result = format_service_issue("nginx.service", state="failed", exit_code=0)
        assert "exit code" not in result

    def test_no_state_defaults_to_failed(self):
        result = format_service_issue("nginx.service")
        assert "has failed" in result

    def test_unknown_state(self):
        result = format_service_issue("nginx.service", state="custom_state")
        assert "is custom_state" in result

    def test_active_state(self):
        result = format_service_issue("nginx.service", state="active")
        assert "is running" in result


# ---------------------------------------------------------------------------
# Tests: format_network_issue
# ---------------------------------------------------------------------------


class TestFormatNetworkIssue:
    def test_interface_down(self):
        result = format_network_issue(interface="eth0", state="down")
        assert "Interface eth0" in result
        assert "is down" in result

    def test_interface_up(self):
        result = format_network_issue(interface="eth0", state="up")
        assert "is up" in result

    def test_with_errors(self):
        result = format_network_issue(interface="eth0", rx_errors=15, tx_errors=3)
        assert "15 receive errors" in result
        assert "3 transmit errors" in result

    def test_no_interface(self):
        result = format_network_issue(state="down")
        assert "Network interface" in result

    def test_unknown_state(self):
        result = format_network_issue(interface="wlan0", state="dormant")
        assert "state is dormant" in result


# ---------------------------------------------------------------------------
# Tests: format_docker_issue
# ---------------------------------------------------------------------------


class TestFormatDockerIssue:
    def test_container_exited_oom(self):
        result = format_docker_issue(container="webapp", state="exited", exit_code=137)
        assert "was killed" in result
        assert "OOM" in result

    def test_container_exited_sigterm(self):
        result = format_docker_issue(container="webapp", state="exited", exit_code=143)
        assert "SIGTERM" in result

    def test_container_exited_normal(self):
        result = format_docker_issue(container="webapp", state="exited", exit_code=0)
        assert "exited normally" in result

    def test_container_exited_other_code(self):
        result = format_docker_issue(container="webapp", state="exited", exit_code=1)
        assert "exited with code 1" in result

    def test_container_exited_no_code(self):
        result = format_docker_issue(container="webapp", state="exited")
        assert "has exited" in result

    def test_container_dead(self):
        result = format_docker_issue(container="webapp", state="dead")
        assert "dead state" in result

    def test_container_restarting(self):
        result = format_docker_issue(container="webapp", state="restarting")
        assert "restarting" in result

    def test_container_paused(self):
        result = format_docker_issue(container="webapp", state="paused")
        assert "paused" in result

    def test_container_running(self):
        result = format_docker_issue(container="webapp", state="running")
        assert "is running" in result

    def test_no_container_with_image(self):
        result = format_docker_issue(image="nginx:latest", state="exited", exit_code=1)
        assert "nginx:latest" in result

    def test_no_container_no_image(self):
        result = format_docker_issue(state="exited")
        assert "Container" in result

    def test_unknown_state(self):
        result = format_docker_issue(container="webapp", state="created")
        assert "is created" in result


# ---------------------------------------------------------------------------
# Tests: format_oom_event
# ---------------------------------------------------------------------------


class TestFormatOomEvent:
    def test_full_info(self):
        result = format_oom_event(process="mysql", pid=1234, score=800)
        assert "Process mysql" in result
        assert "PID 1234" in result
        assert "OOM killer" in result
        assert "score: 800" in result

    def test_only_process(self):
        result = format_oom_event(process="mysql")
        assert "Process mysql" in result

    def test_only_pid(self):
        result = format_oom_event(pid=1234)
        assert "PID 1234" in result

    def test_no_info(self):
        result = format_oom_event()
        assert "A process" in result
        assert "OOM killer" in result

    def test_with_score_zero(self):
        result = format_oom_event(process="test", score=0)
        assert "score: 0" in result


# ---------------------------------------------------------------------------
# Tests: format_temperature_issue
# ---------------------------------------------------------------------------


class TestFormatTemperatureIssue:
    def test_cpu_core_sensor(self):
        result = format_temperature_issue(sensor="core_0", temp_c=85)
        assert "CPU core" in result
        assert "85" in result

    def test_gpu_sensor(self):
        result = format_temperature_issue(sensor="gpu-thermal", temp_c=80)
        assert "GPU" in result

    def test_generic_sensor(self):
        result = format_temperature_issue(sensor="ambient", temp_c=40)
        assert "ambient" in result

    def test_with_threshold(self):
        result = format_temperature_issue(sensor="core_0", temp_c=85, threshold_c=80)
        assert "threshold" in result
        assert "80" in result

    def test_no_sensor(self):
        result = format_temperature_issue(temp_c=90)
        assert "Temperature" in result

    def test_no_temp(self):
        result = format_temperature_issue(sensor="core_0")
        assert "CPU core" in result


# ---------------------------------------------------------------------------
# Tests: _metric_to_label
# ---------------------------------------------------------------------------


class TestMetricToLabel:
    def test_disk_metric(self):
        assert _metric_to_label("disk./.used_pct") == "Disk /"

    def test_memory_metric(self):
        assert _metric_to_label("mem.used_pct") == "Memory"

    def test_swap_metric(self):
        assert _metric_to_label("swap.used_pct") == "Swap"

    def test_cpu_metric(self):
        assert _metric_to_label("cpu.load_1m") == "CPU"

    def test_network_metric(self):
        result = _metric_to_label("net.eth0.rx_bytes")
        assert "Network" in result

    def test_thermal_metric(self):
        assert _metric_to_label("thermal.max_temp_c") == "Temperature"

    def test_unknown_metric(self):
        assert _metric_to_label("unknown.metric") == "unknown.metric"

    def test_empty_metric(self):
        assert _metric_to_label("") == ""

    def test_memory_alias(self):
        assert _metric_to_label("memory.used_pct") == "Memory"


# ---------------------------------------------------------------------------
# Tests: format_forecast_title
# ---------------------------------------------------------------------------


class TestFormatForecastTitle:
    def test_warning_title(self):
        result = format_forecast_title("disk./.used_pct")
        assert "Warning" in result
        assert "Disk" in result

    def test_auto_remediated_title(self):
        result = format_forecast_title("disk./.used_pct", auto_remediated=True)
        assert "Auto-Remediated" in result


# ---------------------------------------------------------------------------
# Tests: format_forecast_body
# ---------------------------------------------------------------------------


class TestFormatForecastBody:
    def test_basic_body(self):
        result = format_forecast_body(
            metric="disk./.used_pct",
            current_value=88.0,
            threshold=95.0,
            time_to_threshold_hours=None,
        )
        assert "88%" in result
        assert "95%" in result

    def test_with_time(self):
        result = format_forecast_body(
            metric="disk./.used_pct",
            current_value=88.0,
            threshold=95.0,
            time_to_threshold_hours=12.0,
        )
        assert "12.0 hours" in result

    def test_with_time_less_than_one_hour(self):
        result = format_forecast_body(
            metric="disk./.used_pct",
            current_value=93.0,
            threshold=95.0,
            time_to_threshold_hours=0.5,
        )
        assert "minutes" in result

    def test_with_cause(self):
        result = format_forecast_body(
            metric="disk./.used_pct",
            current_value=88.0,
            threshold=95.0,
            time_to_threshold_hours=None,
            cause_description="Large log files",
        )
        assert "Cause: Large log files" in result

    def test_with_plan(self):
        result = format_forecast_body(
            metric="disk./.used_pct",
            current_value=88.0,
            threshold=95.0,
            time_to_threshold_hours=None,
            plan_summary="Clean up old logs",
        )
        assert "Plan: Clean up old logs" in result

    def test_with_outcome(self):
        result = format_forecast_body(
            metric="disk./.used_pct",
            current_value=88.0,
            threshold=95.0,
            time_to_threshold_hours=None,
            outcome="Freed 5 GB",
        )
        assert "Result: Freed 5 GB" in result

    def test_outcome_overrides_plan(self):
        result = format_forecast_body(
            metric="disk./.used_pct",
            current_value=88.0,
            threshold=95.0,
            time_to_threshold_hours=None,
            plan_summary="Clean up logs",
            outcome="Freed 5 GB",
        )
        assert "Result" in result
        assert "Plan" not in result

    def test_long_plan_truncated(self):
        long_plan = "A" * 100
        result = format_forecast_body(
            metric="disk./.used_pct",
            current_value=88.0,
            threshold=95.0,
            time_to_threshold_hours=None,
            plan_summary=long_plan,
        )
        assert "..." in result

    def test_non_percentage_metric(self):
        result = format_forecast_body(
            metric="cpu.load_1m",
            current_value=3.5,
            threshold=4.0,
            time_to_threshold_hours=None,
        )
        assert "3.5" in result
        assert "4.0" in result


# ---------------------------------------------------------------------------
# Tests: format_incident_title
# ---------------------------------------------------------------------------


class TestFormatIncidentTitle:
    def test_disk_full(self):
        result = format_incident_title("disk", "Disk space full", "critical", entity="mount:/var")
        assert "Disk Full on /var" in result

    def test_disk_issue_generic(self):
        result = format_incident_title("disk", "Disk degraded", "warning", entity="mount:/var")
        assert "Disk Issue on /var" in result

    def test_disk_no_entity(self):
        result = format_incident_title("disk", "Disk problem", "warning")
        assert result == "Disk problem"

    def test_oom(self):
        result = format_incident_title("oom", "OOM kill", "critical")
        assert result == "Out of Memory"

    def test_service_failed(self):
        result = format_incident_title("service", "Service failed", "warning", entity="service:nginx")
        assert "Service Failed: nginx" in result

    def test_service_issue_generic(self):
        result = format_incident_title("service", "Service degraded", "warning", entity="service:nginx")
        assert "Service Issue: nginx" in result

    def test_service_no_entity(self):
        result = format_incident_title("service", "Service problem", "warning")
        assert result == "Service problem"

    def test_network_down(self):
        result = format_incident_title("net", "Interface down", "warning", entity="interface:eth0")
        assert "Network Down: eth0" in result

    def test_network_issue(self):
        result = format_incident_title("net", "Network errors", "warning", entity="interface:eth0")
        assert "Network Issue: eth0" in result

    def test_network_no_entity(self):
        result = format_incident_title("net", "Network problem", "warning")
        assert result == "Network Issue"

    def test_docker_failed(self):
        result = format_incident_title("docker", "Container exit", "warning", entity="container:webapp")
        assert "Container Failed: webapp" in result

    def test_docker_issue(self):
        result = format_incident_title("docker", "Container degraded", "warning", entity="container:webapp")
        assert "Container Issue: webapp" in result

    def test_docker_no_entity(self):
        result = format_incident_title("docker", "Container problem", "warning")
        assert result == "Container Issue"

    def test_auth_domain(self):
        result = format_incident_title("auth", "Auth issue", "warning")
        assert result == "Authentication Issue"

    def test_pkg_domain(self):
        result = format_incident_title("pkg", "Package issue", "warning")
        assert result == "Package Issue"

    def test_fs_domain(self):
        result = format_incident_title("fs", "FS issue", "warning")
        assert result == "Filesystem Issue"

    def test_critical_severity_unknown_domain(self):
        result = format_incident_title("unknown", "Something broke", "critical")
        assert result == "CRITICAL: Something broke"

    def test_default_domain(self):
        result = format_incident_title("other", "Some issue", "warning")
        assert result == "Some issue"


# ---------------------------------------------------------------------------
# Tests: format_incident_body
# ---------------------------------------------------------------------------


class TestFormatIncidentBody:
    def test_with_symptoms(self):
        result = format_incident_body("disk", symptoms=("Disk 95% full",))
        assert "Disk 95% full" in result

    def test_with_summary_no_symptoms(self):
        result = format_incident_body("disk", summary="Disk running low on space")
        assert "Disk running low" in result

    def test_long_summary_truncated(self):
        long_summary = "A" * 200
        result = format_incident_body("disk", summary=long_summary)
        assert "..." in result

    def test_with_metrics(self):
        result = format_incident_body("disk", metrics={"disk_pct": 95, "mount": "/var"})
        assert "/var at 95%" in result

    def test_with_entities(self):
        result = format_incident_body("disk", entities=["mount:/var"])
        assert "Affected" in result

    def test_with_suspected_causes(self):
        result = format_incident_body(
            "disk",
            symptoms=("Disk full",),
            suspected_causes=("Large log files",),
        )
        assert "Possible cause:" in result

    def test_max_lines_respected(self):
        result = format_incident_body(
            "disk",
            metrics={"disk_pct": 95, "mount": "/var"},
            symptoms=("Full",),
            suspected_causes=("Logs",),
            max_lines=2,
        )
        lines = result.strip().split("\n")
        assert len(lines) <= 2


# ---------------------------------------------------------------------------
# Tests: _format_domain_metrics
# ---------------------------------------------------------------------------


class TestFormatDomainMetrics:
    def test_disk_with_pct(self):
        result = _format_domain_metrics("disk", {"disk_pct": 95, "mount": "/var"})
        assert "/var at 95%" in result

    def test_disk_with_avail_gb(self):
        result = _format_domain_metrics("disk", {"avail_gb": 2.5})
        assert "2.5 GB" in result

    def test_oom_with_pressure(self):
        result = _format_domain_metrics("oom", {"mem_pressure": 92})
        assert "92%" in result

    def test_oom_with_count(self):
        result = _format_domain_metrics("oom", {"oom_count_1h": 3})
        assert "3 OOM kills" in result

    def test_oom_single_count(self):
        result = _format_domain_metrics("oom", {"oom_count_1h": 1})
        assert "1 OOM kill " in result

    def test_service_failed(self):
        result = _format_domain_metrics("service", {"failed_services": 2})
        assert "2 services failed" in result

    def test_network_errors(self):
        result = _format_domain_metrics("net", {"rx_errors": 10, "tx_errors": 5})
        assert "15 network errors" in result

    def test_network_flaps(self):
        result = _format_domain_metrics("net", {"net_flaps_1h": 3})
        assert "3 network state changes" in result

    def test_docker_exited(self):
        result = _format_domain_metrics("docker", {"docker_exited_count": 2})
        assert "2 containers exited" in result

    def test_empty_metrics(self):
        result = _format_domain_metrics("disk", {})
        assert result == ""

    def test_unknown_domain(self):
        result = _format_domain_metrics("custom", {"key": "value"})
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: format_health_title
# ---------------------------------------------------------------------------


class TestFormatHealthTitle:
    def test_disk_warning(self):
        result = format_health_title("disk", "warning")
        assert result == "Disk Space Warning"

    def test_memory_critical(self):
        result = format_health_title("memory", "critical")
        assert result == "CRITICAL: Memory"

    def test_cpu_recovered(self):
        result = format_health_title("cpu", "recovered")
        assert result == "CPU Load Recovered"

    def test_unknown_component(self):
        result = format_health_title("custom", "warning")
        assert "Custom" in result

    def test_unknown_severity(self):
        result = format_health_title("disk", "info")
        assert result == "Disk Space"

    def test_swap_component(self):
        result = format_health_title("swap", "warning")
        assert "Swap Usage Warning" in result

    def test_temperature_component(self):
        result = format_health_title("temperature", "critical")
        assert "Temperature" in result

    def test_docker_component(self):
        result = format_health_title("docker", "warning")
        assert "Container Health" in result


# ---------------------------------------------------------------------------
# Tests: format_health_body
# ---------------------------------------------------------------------------


class TestFormatHealthBody:
    def test_disk_metrics(self):
        result = format_health_body(
            "disk", "Disk is full", metrics={"mount": "/var", "used_pct": 95, "avail_gb": 1.5}
        )
        assert "/var is 95%" in result
        assert "1.5 GB" in result

    def test_memory_metrics(self):
        result = format_health_body(
            "memory", "Low memory", metrics={"available_mb": 256, "used_pct": 88}
        )
        assert "256 MB" in result
        assert "88%" in result

    def test_swap_metrics(self):
        result = format_health_body("swap", "Swap high", metrics={"used_pct": 80})
        assert "80%" in result

    def test_cpu_metrics(self):
        result = format_health_body("cpu", "High load", metrics={"load_1m": 5.25})
        assert "5.25" in result

    def test_temperature_metrics(self):
        result = format_health_body("temperature", "Hot!", metrics={"temp_c": 90})
        assert "90" in result

    def test_no_metrics(self):
        result = format_health_body("disk", "Disk issue")
        assert result == "Disk issue"

    def test_message_not_duplicated(self):
        result = format_health_body("custom", "Alert message")
        assert result.count("Alert message") == 1


# ---------------------------------------------------------------------------
# Tests: format_event_message
# ---------------------------------------------------------------------------


class TestFormatEventMessage:
    def test_readable_message_passthrough(self):
        result = format_event_message("disk", "Disk /var is 95% full")
        assert result == "Disk /var is 95% full"

    def test_readable_message_with_entity(self):
        result = format_event_message("service", "Service failed", entity="service:nginx")
        assert "nginx" in result

    def test_entity_appended_when_not_exact_match(self):
        result = format_event_message("service", "nginx has failed", entity="service:nginx")
        # The entity_str is "the nginx service" which is NOT in the message,
        # so the entity context is appended
        assert "nginx has failed" in result
        assert "nginx service" in result

    def test_entity_not_duplicated_when_exact_match(self):
        result = format_event_message("service", "the nginx service has failed", entity="service:nginx")
        # "the nginx service" is already in the message, so no duplication
        assert result == "the nginx service has failed"

    def test_oom_category_with_raw(self):
        result = format_event_message("oom", "", raw={"process": "mysql", "pid": 123})
        assert "Process mysql" in result

    def test_oom_category_no_raw(self):
        result = format_event_message("oom", "")
        assert "Out of memory" in result

    def test_disk_category_with_raw(self):
        result = format_event_message("disk", "", raw={"mount": "/var", "used_pct": 95})
        assert "Disk /var" in result

    def test_disk_category_no_raw(self):
        result = format_event_message("disk", "")
        assert "Disk issue" in result

    def test_service_category_with_entity(self):
        result = format_event_message("service", "", entity="service:nginx", raw={"state": "failed"})
        assert "nginx" in result

    def test_service_category_no_entity(self):
        result = format_event_message("service", "")
        assert "Service issue" in result

    def test_net_category_with_entity(self):
        result = format_event_message("net", "", entity="interface:eth0", raw={"state": "down"})
        assert "eth0" in result

    def test_net_category_no_entity(self):
        result = format_event_message("net", "")
        assert "Network issue" in result

    def test_docker_category_with_entity(self):
        result = format_event_message(
            "docker", "", entity="container:webapp", raw={"state": "exited", "exit_code": 1}
        )
        assert "webapp" in result

    def test_docker_category_no_entity(self):
        result = format_event_message("docker", "")
        assert "Container issue" in result

    def test_thermal_category_with_raw(self):
        result = format_event_message("thermal", "", raw={"sensor": "core_0", "temp_c": 90})
        assert "90" in result

    def test_thermal_category_no_raw(self):
        result = format_event_message("thermal", "")
        assert "Temperature" in result

    def test_long_message_truncated(self):
        long_msg = "A" * 250
        result = format_event_message("unknown", long_msg)
        assert result.endswith("...")
        assert len(result) <= 200

    def test_default_category(self):
        result = format_event_message("custom", "")
        assert "Custom event" in result

    def test_json_message_falls_through(self):
        result = format_event_message("disk", '{"mount":"/"}', raw={"mount": "/", "used_pct": 80})
        # Starts with '{', so it falls through to category-based formatting
        assert "Disk" in result
