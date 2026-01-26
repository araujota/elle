"""Node.js with reverse proxy recipe."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)

NODEJS_RECIPE = StackRecipe(
    name="nodejs",
    display_name="Node.js with Nginx Reverse Proxy",
    description="Node.js LTS with Nginx reverse proxy, PM2 process manager, and correct proxy headers",
    category="web",
    packages=(
        "nginx",
        "nodejs",
        "npm",
    ),
    services=("nginx",),
    config_templates={
        # Nginx proxy config will be generated
    },
    guarantees=(
        StackGuarantee(
            name="nginx_running",
            description="Nginx is serving as reverse proxy",
            check_command="systemctl is-active nginx",
            expected_pattern=r"active",
            failure_action="fail",
            remediation_hint="Nginx not running. Check: systemctl status nginx",
        ),
        StackGuarantee(
            name="node_installed",
            description="Node.js is installed and accessible",
            check_command="node --version",
            expected_pattern=r"v\d+\.\d+\.\d+",
            failure_action="fail",
            remediation_hint="Node.js not installed. Install with: apt install nodejs",
        ),
        StackGuarantee(
            name="npm_installed",
            description="npm is installed",
            check_command="npm --version",
            expected_pattern=r"\d+\.\d+\.\d+",
            failure_action="fail",
            remediation_hint="npm not installed. Install with: apt install npm",
        ),
        StackGuarantee(
            name="pm2_available",
            description="PM2 is installed globally",
            check_command="pm2 --version 2>/dev/null || echo 'not installed'",
            expected_pattern=r"\d+\.\d+\.\d+",
            failure_action="warn",
            remediation_hint="PM2 not installed. Install with: npm install -g pm2",
        ),
    ),
    variables=(
        StackVariable(
            name="app_port",
            description="Port where Node.js app will run",
            default="3000",
            required=False,
            validator=r"^\d{4,5}$",
        ),
        StackVariable(
            name="server_name",
            description="Nginx server name (domain)",
            default="_",
            required=False,
        ),
        StackVariable(
            name="node_env",
            description="Node environment (development/production)",
            default="production",
            required=False,
        ),
    ),
    presets={
        "dev": {
            "app_port": "3000",
            "node_env": "development",
            "server_name": "_",
        },
        "prod": {
            "app_port": "3000",
            "node_env": "production",
            "server_name": "_",
        },
    },
    min_ram_mb=256,
    min_disk_gb=1,
    ports_required=(80,),
    version="1.0.0",
    tags=("web", "nodejs", "node", "javascript", "nginx", "proxy"),
)
