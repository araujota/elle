"""Grafana dashboards recipe."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)

GRAFANA_RECIPE = StackRecipe(
    name="grafana",
    display_name="Grafana Dashboards",
    description="Grafana visualization and dashboards with secure defaults",
    category="observability",
    packages=("grafana",),
    services=("grafana-server",),
    config_templates={},
    guarantees=(
        StackGuarantee(
            name="service_running",
            description="Grafana service is active",
            check_command="systemctl is-active grafana-server",
            expected_pattern=r"^active$",
            failure_action="fail",
            remediation_hint="Start Grafana with: systemctl start grafana-server",
        ),
        StackGuarantee(
            name="http_listening",
            description="Grafana is listening on port 3000",
            check_command="ss -tlnp | grep ':3000 '",
            expected_pattern=r":3000",
            failure_action="fail",
            remediation_hint="Check Grafana http_port configuration",
        ),
        StackGuarantee(
            name="ui_accessible",
            description="Grafana UI is accessible",
            check_command="curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/login 2>/dev/null || echo '000'",
            expected_pattern=r"200",
            failure_action="warn",
            remediation_hint="Check Grafana logs: journalctl -u grafana-server",
        ),
        StackGuarantee(
            name="admin_password_changed",
            description="Default admin password has been changed",
            check_command="curl -s -u admin:admin http://localhost:3000/api/org 2>/dev/null | grep -c 'Invalid' || echo '0'",
            expected_pattern=r"1",
            failure_action="warn",
            remediation_hint="Change default password: grafana-cli admin reset-admin-password <newpassword>",
        ),
    ),
    variables=(
        StackVariable(
            name="admin_password",
            description="Grafana admin password",
            required=True,
            secret=True,
        ),
        StackVariable(
            name="http_port",
            description="HTTP port for Grafana",
            default="3000",
            required=False,
        ),
        StackVariable(
            name="domain",
            description="Domain name for Grafana",
            default="localhost",
            required=False,
        ),
    ),
    presets={
        "dev": {
            "http_port": "3000",
        },
        "prod": {
            "http_port": "3000",
        },
    },
    min_ram_mb=256,
    min_disk_gb=1,
    ports_required=(3000,),
    version="1.0.0",
    tags=("observability", "dashboards", "grafana", "visualization", "monitoring"),
)
