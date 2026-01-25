"""Traefik reverse proxy recipe."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)


TRAEFIK_RECIPE = StackRecipe(
    name="traefik",
    display_name="Traefik Reverse Proxy",
    description="Traefik cloud-native reverse proxy with dashboard and auto-discovery",
    category="web",

    packages=(
        "traefik",
    ),

    services=(
        "traefik",
    ),

    config_templates={},

    guarantees=(
        StackGuarantee(
            name="service_running",
            description="Traefik service is active",
            check_command="systemctl is-active traefik",
            expected_pattern=r"^active$",
            failure_action="fail",
            remediation_hint="Start Traefik with: systemctl start traefik",
        ),
        StackGuarantee(
            name="http_listening",
            description="Traefik is listening on port 80",
            check_command="ss -tlnp | grep ':80 '",
            expected_pattern=r":80",
            failure_action="warn",
            remediation_hint="Check Traefik entrypoints configuration",
        ),
        StackGuarantee(
            name="dashboard_accessible",
            description="Dashboard is accessible",
            check_command="curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/dashboard/ 2>/dev/null || echo '000'",
            expected_pattern=r"200|301",
            failure_action="warn",
            remediation_hint="Enable dashboard in traefik.yml and check api.dashboard setting",
        ),
        StackGuarantee(
            name="providers_loaded",
            description="At least one provider is configured",
            check_command="curl -s http://localhost:8080/api/overview 2>/dev/null | grep -o 'providers' || echo 'none'",
            expected_pattern=r"providers",
            failure_action="warn",
            remediation_hint="Configure at least one provider (file, docker, etc.)",
        ),
    ),

    variables=(
        StackVariable(
            name="dashboard_enabled",
            description="Enable the Traefik dashboard",
            default="true",
            required=False,
        ),
        StackVariable(
            name="dashboard_port",
            description="Dashboard port",
            default="8080",
            required=False,
        ),
        StackVariable(
            name="docker_provider",
            description="Enable Docker provider for auto-discovery",
            default="false",
            required=False,
        ),
    ),

    presets={
        "dev": {
            "dashboard_enabled": "true",
            "docker_provider": "true",
        },
        "prod": {
            "dashboard_enabled": "false",
            "docker_provider": "true",
        },
    },

    min_ram_mb=64,
    min_disk_gb=1,
    ports_required=(80, 443, 8080),

    version="1.0.0",
    tags=("web", "proxy", "traefik", "loadbalancer", "ingress"),
)
