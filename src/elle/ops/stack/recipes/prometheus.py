"""Prometheus + Grafana observability stack recipe."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)

PROMETHEUS_GRAFANA_RECIPE = StackRecipe(
    name="prometheus-grafana",
    display_name="Prometheus + Grafana Monitoring Stack",
    description="Complete observability stack with Prometheus metrics collection, node_exporter, and Grafana dashboards",
    category="observability",
    packages=(
        "prometheus",
        "prometheus-node-exporter",
        "grafana",
    ),
    services=(
        "prometheus",
        "prometheus-node-exporter",
        "grafana-server",
    ),
    config_templates={
        # Prometheus scrape config will be generated
        # Grafana datasource config will be generated
    },
    guarantees=(
        StackGuarantee(
            name="prometheus_running",
            description="Prometheus is active and serving metrics",
            check_command="curl -s http://localhost:9090/-/healthy",
            expected_pattern=r"Prometheus Server is Healthy",
            failure_action="fail",
            remediation_hint="Prometheus not healthy. Check: systemctl status prometheus && journalctl -u prometheus",
        ),
        StackGuarantee(
            name="node_exporter_running",
            description="Node exporter is exposing system metrics",
            check_command="curl -s http://localhost:9100/metrics | head -5",
            expected_pattern=r"# HELP",
            failure_action="fail",
            remediation_hint="Node exporter not running. Check: systemctl status prometheus-node-exporter",
        ),
        StackGuarantee(
            name="grafana_accessible",
            description="Grafana web interface is accessible",
            check_command="curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/login",
            expected_pattern=r"200",
            failure_action="fail",
            remediation_hint="Grafana not accessible. Check: systemctl status grafana-server",
        ),
        StackGuarantee(
            name="prometheus_scraping",
            description="Prometheus is successfully scraping targets",
            check_command='curl -s http://localhost:9090/api/v1/targets | grep -o \'"health":"up"\' | head -1',
            expected_pattern=r'"health":"up"',
            failure_action="warn",
            remediation_hint="No healthy scrape targets. Check prometheus.yml configuration",
        ),
        StackGuarantee(
            name="prometheus_localhost",
            description="Prometheus only listens on localhost",
            check_command="ss -tlnp | grep 9090",
            expected_pattern=r"127\.0\.0\.1:9090",
            failure_action="warn",
            remediation_hint="Prometheus may be exposed externally. Check --web.listen-address in service config",
        ),
    ),
    variables=(
        StackVariable(
            name="grafana_admin_password",
            description="Grafana admin password (auto-generated if not provided)",
            required=False,
            secret=True,
        ),
        StackVariable(
            name="retention_days",
            description="Prometheus data retention period in days",
            default="15",
            required=False,
            validator=r"^\d+$",
        ),
        StackVariable(
            name="scrape_interval",
            description="How often Prometheus scrapes targets",
            default="15s",
            required=False,
        ),
        StackVariable(
            name="grafana_port",
            description="Grafana web interface port",
            default="3000",
            required=False,
            validator=r"^\d{4,5}$",
        ),
    ),
    presets={
        "dev": {
            "retention_days": "7",
            "scrape_interval": "30s",
            "grafana_port": "3000",
        },
        "prod": {
            "retention_days": "30",
            "scrape_interval": "15s",
            "grafana_port": "3000",
        },
    },
    min_ram_mb=512,
    min_disk_gb=5,
    ports_required=(9090, 9100, 3000),
    version="1.0.0",
    tags=("observability", "monitoring", "prometheus", "grafana", "metrics"),
)
