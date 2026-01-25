"""MongoDB database server recipe."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)

MONGODB_RECIPE = StackRecipe(
    name="mongodb",
    display_name="MongoDB Server",
    description="MongoDB document database with authentication enabled and localhost binding",
    category="data",

    packages=(
        "mongodb-org",
    ),

    services=(
        "mongod",
    ),

    config_templates={},

    guarantees=(
        StackGuarantee(
            name="service_running",
            description="MongoDB service is active",
            check_command="systemctl is-active mongod",
            expected_pattern=r"^active$",
            failure_action="fail",
            remediation_hint="Start MongoDB with: systemctl start mongod",
        ),
        StackGuarantee(
            name="localhost_only",
            description="MongoDB bound to localhost only",
            check_command="grep -E '^\\s+bindIp:' /etc/mongod.conf",
            expected_pattern=r"127\.0\.0\.1|localhost",
            failure_action="warn",
            remediation_hint="Set bindIp to 127.0.0.1 in /etc/mongod.conf",
        ),
        StackGuarantee(
            name="auth_enabled",
            description="Authentication is enabled",
            check_command="grep -A5 'security:' /etc/mongod.conf | grep authorization",
            expected_pattern=r"enabled",
            failure_action="warn",
            remediation_hint="Enable authorization in /etc/mongod.conf under security section",
        ),
        StackGuarantee(
            name="port_listening",
            description="MongoDB is listening on port 27017",
            check_command="ss -tlnp | grep 27017",
            expected_pattern=r"27017",
            failure_action="fail",
            remediation_hint="Check mongod.conf port setting and service status",
        ),
    ),

    variables=(
        StackVariable(
            name="admin_password",
            description="MongoDB admin password",
            required=True,
            secret=True,
        ),
        StackVariable(
            name="database",
            description="Initial database to create",
            default="app",
            required=False,
        ),
        StackVariable(
            name="wired_tiger_cache_gb",
            description="WiredTiger cache size in GB (default: auto)",
            default="",
            required=False,
        ),
    ),

    presets={
        "dev": {},
        "prod": {},
    },

    min_ram_mb=1024,
    min_disk_gb=10,
    ports_required=(27017,),

    version="1.0.0",
    tags=("database", "nosql", "mongodb", "data", "document"),
)
