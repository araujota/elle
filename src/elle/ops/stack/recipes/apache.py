"""Apache HTTP Server recipe."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)


APACHE_RECIPE = StackRecipe(
    name="apache",
    display_name="Apache HTTP Server",
    description="Apache HTTP server with MPM configuration and secure defaults",
    category="web",

    packages=(
        "apache2",
    ),

    services=(
        "apache2",
    ),

    config_templates={},

    guarantees=(
        StackGuarantee(
            name="service_running",
            description="Apache service is active",
            check_command="systemctl is-active apache2",
            expected_pattern=r"^active$",
            failure_action="fail",
            remediation_hint="Start Apache with: systemctl start apache2",
        ),
        StackGuarantee(
            name="config_valid",
            description="Apache configuration is valid",
            check_command="apache2ctl configtest 2>&1",
            expected_pattern=r"Syntax OK",
            failure_action="fail",
            remediation_hint="Fix syntax errors in Apache configuration",
        ),
        StackGuarantee(
            name="http_listening",
            description="Apache is listening on port 80",
            check_command="ss -tlnp | grep ':80 '",
            expected_pattern=r":80",
            failure_action="warn",
            remediation_hint="Check Apache configuration and firewall settings",
        ),
        StackGuarantee(
            name="mpm_configured",
            description="MPM module is loaded",
            check_command="apache2ctl -M 2>/dev/null | grep mpm",
            expected_pattern=r"mpm_(event|prefork|worker)",
            failure_action="warn",
            remediation_hint="Enable an MPM with: a2enmod mpm_event",
        ),
    ),

    variables=(
        StackVariable(
            name="mpm_module",
            description="MPM module to use (event, prefork, worker)",
            default="event",
            required=False,
        ),
        StackVariable(
            name="max_request_workers",
            description="Maximum number of simultaneous connections",
            default="150",
            required=False,
        ),
        StackVariable(
            name="server_admin",
            description="Server admin email address",
            default="webmaster@localhost",
            required=False,
        ),
    ),

    presets={
        "dev": {
            "mpm_module": "prefork",
            "max_request_workers": "50",
        },
        "prod": {
            "mpm_module": "event",
            "max_request_workers": "400",
        },
    },

    min_ram_mb=128,
    min_disk_gb=1,
    ports_required=(80, 443),

    version="1.0.0",
    tags=("web", "http", "apache", "httpd", "server"),
)
