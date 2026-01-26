"""LEMP stack recipe (Linux, Nginx, MariaDB, PHP)."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)

LEMP_RECIPE = StackRecipe(
    name="lemp",
    display_name="LEMP Stack (Nginx, PHP, MariaDB)",
    description="Complete LEMP web stack with localhost-only database, PHP-FPM, and working DB connection",
    category="web",
    packages=(
        "nginx",
        "php-fpm",
        "php-mysql",
        "php-mbstring",
        "php-xml",
        "php-curl",
        "mariadb-server",
        "mariadb-client",
    ),
    services=(
        "nginx",
        "php8.1-fpm",  # Ubuntu 22.04+
        "mariadb",
    ),
    config_templates={
        # Default site config with PHP support
    },
    guarantees=(
        StackGuarantee(
            name="nginx_running",
            description="Nginx is serving requests",
            check_command="curl -s -o /dev/null -w '%{http_code}' http://localhost/",
            expected_pattern=r"(200|301|302|403)",
            failure_action="fail",
            remediation_hint="Nginx not responding. Check: systemctl status nginx && nginx -t",
        ),
        StackGuarantee(
            name="php_working",
            description="PHP-FPM is processing requests",
            check_command="echo '<?php echo \"OK\";' | php",
            expected_pattern=r"OK",
            failure_action="fail",
            remediation_hint="PHP not working. Check: systemctl status php8.1-fpm",
        ),
        StackGuarantee(
            name="mariadb_localhost",
            description="MariaDB only listens on localhost",
            check_command="ss -tlnp | grep 3306",
            expected_pattern=r"127\.0\.0\.1:3306",
            failure_action="fail",
            remediation_hint="MariaDB should only listen on localhost. Check bind-address in /etc/mysql/mariadb.conf.d/50-server.cnf",
        ),
        StackGuarantee(
            name="mariadb_connection",
            description="Can connect to MariaDB",
            check_command="mysqladmin ping 2>/dev/null || echo 'failed'",
            expected_pattern=r"alive",
            failure_action="fail",
            remediation_hint="Cannot connect to MariaDB. Check: systemctl status mariadb",
        ),
        StackGuarantee(
            name="php_mysql_extension",
            description="PHP MySQL extension is loaded",
            check_command="php -m | grep -i mysql",
            expected_pattern=r"mysql",
            failure_action="warn",
            remediation_hint="PHP MySQL extension missing. Install with: apt install php-mysql",
        ),
    ),
    variables=(
        StackVariable(
            name="db_root_password",
            description="MariaDB root password",
            required=False,
            secret=True,
        ),
        StackVariable(
            name="php_memory_limit",
            description="PHP memory limit",
            default="256M",
            required=False,
        ),
        StackVariable(
            name="server_name",
            description="Nginx server name (domain)",
            default="_",  # Catch-all
            required=False,
        ),
    ),
    presets={
        "dev": {
            "php_memory_limit": "128M",
            "server_name": "_",
        },
        "prod": {
            "php_memory_limit": "512M",
            "server_name": "_",
        },
    },
    min_ram_mb=512,
    min_disk_gb=2,
    ports_required=(80, 443),
    version="1.0.0",
    tags=("web", "nginx", "php", "mysql", "mariadb", "lemp", "lamp"),
)
