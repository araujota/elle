"""MySQL database server recipe."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)

MYSQL_RECIPE = StackRecipe(
    name="mysql",
    display_name="MySQL Server",
    description="MySQL relational database server with localhost-only access and secure defaults",
    category="data",

    packages=(
        "mysql-server",
    ),

    services=(
        "mysql",
    ),

    config_templates={},

    guarantees=(
        StackGuarantee(
            name="service_running",
            description="MySQL service is active",
            check_command="systemctl is-active mysql",
            expected_pattern=r"^active$",
            failure_action="fail",
            remediation_hint="Start MySQL with: systemctl start mysql",
        ),
        StackGuarantee(
            name="localhost_only",
            description="MySQL bound to localhost only",
            check_command="grep -E '^bind-address' /etc/mysql/mysql.conf.d/mysqld.cnf 2>/dev/null || echo '127.0.0.1'",
            expected_pattern=r"127\.0\.0\.1",
            failure_action="warn",
            remediation_hint="Edit mysqld.cnf to set bind-address = 127.0.0.1",
        ),
        StackGuarantee(
            name="root_secured",
            description="Root user requires password",
            check_command="mysql -u root -e 'SELECT 1' 2>&1",
            expected_pattern=r"Access denied|ERROR 1045",
            failure_action="warn",
            remediation_hint="Run mysql_secure_installation to set root password",
        ),
        StackGuarantee(
            name="socket_exists",
            description="MySQL Unix socket is available",
            check_command="ls /var/run/mysqld/mysqld.sock 2>/dev/null && echo OK",
            expected_pattern=r"OK",
            failure_action="warn",
            remediation_hint="Check service status with: systemctl status mysql",
        ),
    ),

    variables=(
        StackVariable(
            name="root_password",
            description="MySQL root password",
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
            name="max_connections",
            description="Maximum connections (default: 151)",
            default="151",
            required=False,
        ),
    ),

    presets={
        "dev": {
            "max_connections": "50",
        },
        "prod": {
            "max_connections": "200",
        },
    },

    min_ram_mb=512,
    min_disk_gb=5,
    ports_required=(3306,),

    version="1.0.0",
    tags=("database", "sql", "mysql", "data", "relational"),
)
