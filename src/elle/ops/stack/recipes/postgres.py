"""PostgreSQL database server recipe."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)


POSTGRES_RECIPE = StackRecipe(
    name="postgres",
    display_name="PostgreSQL Database Server",
    description="Standalone PostgreSQL with localhost-only access and secure defaults",
    category="data",

    packages=(
        "postgresql",
        "postgresql-contrib",
    ),

    services=(
        "postgresql",
    ),

    config_templates={
        # pg_hba.conf for localhost-only access
        # Actual path will be determined at deploy time
    },

    guarantees=(
        StackGuarantee(
            name="localhost_only",
            description="PostgreSQL only listens on localhost",
            check_command="ss -tlnp | grep 5432",
            expected_pattern=r"127\.0\.0\.1:5432",
            failure_action="fail",
            remediation_hint="PostgreSQL should only listen on localhost. Check listen_addresses in postgresql.conf",
        ),
        StackGuarantee(
            name="socket_exists",
            description="PostgreSQL Unix socket is available",
            check_command="ls /var/run/postgresql/.s.PGSQL.5432 2>/dev/null && echo OK",
            expected_pattern=r"OK",
            failure_action="warn",
            remediation_hint="Unix socket missing - check service status with: systemctl status postgresql",
        ),
        StackGuarantee(
            name="connection_works",
            description="Can connect to PostgreSQL",
            check_command="sudo -u postgres psql -c 'SELECT 1' -t",
            expected_pattern=r"1",
            failure_action="fail",
            remediation_hint="Cannot connect to PostgreSQL. Check logs with: journalctl -u postgresql",
        ),
        StackGuarantee(
            name="service_running",
            description="PostgreSQL service is active",
            check_command="systemctl is-active postgresql",
            expected_pattern=r"active",
            failure_action="fail",
            remediation_hint="Service not running. Start with: systemctl start postgresql",
        ),
    ),

    variables=(
        StackVariable(
            name="superuser_password",
            description="Password for postgres superuser (auto-generated if not provided)",
            required=False,
            secret=True,
        ),
        StackVariable(
            name="listen_addresses",
            description="Listen addresses (default: localhost)",
            default="localhost",
            required=False,
        ),
        StackVariable(
            name="max_connections",
            description="Maximum connections (default: 100)",
            default="100",
            required=False,
        ),
    ),

    presets={
        "dev": {
            "max_connections": "20",
            "listen_addresses": "localhost",
        },
        "prod": {
            "max_connections": "200",
            "listen_addresses": "localhost",
        },
    },

    min_ram_mb=256,
    min_disk_gb=1,
    ports_required=(5432,),

    version="1.0.0",
    tags=("database", "sql", "postgresql", "data"),
)
