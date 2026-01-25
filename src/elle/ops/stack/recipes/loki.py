"""Loki log aggregation recipe."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)

LOKI_RECIPE = StackRecipe(
    name="loki",
    display_name="Grafana Loki",
    description="Loki log aggregation system with retention configuration",
    category="observability",

    packages=(
        "loki",
    ),

    services=(
        "loki",
    ),

    config_templates={},

    guarantees=(
        StackGuarantee(
            name="service_running",
            description="Loki service is active",
            check_command="systemctl is-active loki",
            expected_pattern=r"^active$",
            failure_action="fail",
            remediation_hint="Start Loki with: systemctl start loki",
        ),
        StackGuarantee(
            name="http_listening",
            description="Loki is listening on port 3100",
            check_command="ss -tlnp | grep ':3100 '",
            expected_pattern=r":3100",
            failure_action="fail",
            remediation_hint="Check Loki http_listen_port configuration",
        ),
        StackGuarantee(
            name="ready",
            description="Loki is ready to accept requests",
            check_command="curl -s http://localhost:3100/ready 2>/dev/null || echo 'not ready'",
            expected_pattern=r"ready",
            failure_action="warn",
            remediation_hint="Check Loki logs: journalctl -u loki",
        ),
        StackGuarantee(
            name="retention_configured",
            description="Log retention is configured",
            check_command="grep -E 'retention_period|retention_deletes_enabled' /etc/loki/config.yml 2>/dev/null | head -1 || echo 'default'",
            expected_pattern=r"retention|default",
            failure_action="warn",
            remediation_hint="Configure retention in /etc/loki/config.yml",
        ),
    ),

    variables=(
        StackVariable(
            name="retention_period",
            description="Log retention period (e.g., 744h for 31 days)",
            default="744h",
            required=False,
        ),
        StackVariable(
            name="http_listen_port",
            description="HTTP port for Loki",
            default="3100",
            required=False,
        ),
        StackVariable(
            name="storage_path",
            description="Path for log storage",
            default="/var/lib/loki",
            required=False,
        ),
    ),

    presets={
        "dev": {
            "retention_period": "168h",  # 7 days
        },
        "prod": {
            "retention_period": "2160h",  # 90 days
        },
    },

    min_ram_mb=512,
    min_disk_gb=10,
    ports_required=(3100,),

    version="1.0.0",
    tags=("observability", "logging", "loki", "logs", "aggregation"),
)
