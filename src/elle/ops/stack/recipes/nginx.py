"""Nginx web server recipe (standalone)."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)

NGINX_RECIPE = StackRecipe(
    name="nginx",
    display_name="Nginx Web Server",
    description="Nginx HTTP server with secure defaults and optimized configuration",
    category="web",

    packages=(
        "nginx",
    ),

    services=(
        "nginx",
    ),

    config_templates={},

    guarantees=(
        StackGuarantee(
            name="service_running",
            description="Nginx service is active",
            check_command="systemctl is-active nginx",
            expected_pattern=r"^active$",
            failure_action="fail",
            remediation_hint="Start Nginx with: systemctl start nginx",
        ),
        StackGuarantee(
            name="config_valid",
            description="Nginx configuration is valid",
            check_command="nginx -t 2>&1",
            expected_pattern=r"syntax is ok.*test is successful",
            failure_action="fail",
            remediation_hint="Fix syntax errors in nginx configuration",
        ),
        StackGuarantee(
            name="http_listening",
            description="Nginx is listening on port 80",
            check_command="ss -tlnp | grep ':80 '",
            expected_pattern=r":80",
            failure_action="warn",
            remediation_hint="Check nginx configuration and firewall settings",
        ),
        StackGuarantee(
            name="responding",
            description="Nginx responds to HTTP requests",
            check_command="curl -s -o /dev/null -w '%{http_code}' http://localhost/ 2>/dev/null || echo '000'",
            expected_pattern=r"200|301|302|403",
            failure_action="warn",
            remediation_hint="Check nginx error logs: journalctl -u nginx",
        ),
    ),

    variables=(
        StackVariable(
            name="worker_processes",
            description="Number of worker processes (auto = CPU cores)",
            default="auto",
            required=False,
        ),
        StackVariable(
            name="worker_connections",
            description="Max connections per worker",
            default="1024",
            required=False,
        ),
        StackVariable(
            name="server_name",
            description="Server name for default vhost",
            default="_",
            required=False,
        ),
    ),

    presets={
        "dev": {
            "worker_processes": "1",
            "worker_connections": "512",
        },
        "prod": {
            "worker_processes": "auto",
            "worker_connections": "4096",
        },
    },

    min_ram_mb=64,
    min_disk_gb=1,
    ports_required=(80, 443),

    version="1.0.0",
    tags=("web", "http", "nginx", "proxy", "server"),
)
