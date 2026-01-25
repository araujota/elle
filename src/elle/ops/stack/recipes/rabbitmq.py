"""RabbitMQ message broker recipe."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)


RABBITMQ_RECIPE = StackRecipe(
    name="rabbitmq",
    display_name="RabbitMQ Message Broker",
    description="RabbitMQ message broker with management plugin and secure defaults",
    category="data",

    packages=(
        "rabbitmq-server",
    ),

    services=(
        "rabbitmq-server",
    ),

    config_templates={},

    guarantees=(
        StackGuarantee(
            name="service_running",
            description="RabbitMQ service is active",
            check_command="systemctl is-active rabbitmq-server",
            expected_pattern=r"^active$",
            failure_action="fail",
            remediation_hint="Start RabbitMQ with: systemctl start rabbitmq-server",
        ),
        StackGuarantee(
            name="management_enabled",
            description="Management plugin is enabled",
            check_command="rabbitmq-plugins list -e | grep rabbitmq_management",
            expected_pattern=r"rabbitmq_management",
            failure_action="warn",
            remediation_hint="Enable with: rabbitmq-plugins enable rabbitmq_management",
        ),
        StackGuarantee(
            name="amqp_listening",
            description="AMQP port is listening",
            check_command="ss -tlnp | grep 5672",
            expected_pattern=r"5672",
            failure_action="fail",
            remediation_hint="Check RabbitMQ service and configuration",
        ),
        StackGuarantee(
            name="management_listening",
            description="Management UI is accessible",
            check_command="curl -s -o /dev/null -w '%{http_code}' http://localhost:15672 2>/dev/null || echo '000'",
            expected_pattern=r"200|301",
            failure_action="warn",
            remediation_hint="Enable management plugin and check firewall",
        ),
    ),

    variables=(
        StackVariable(
            name="admin_password",
            description="Admin user password",
            required=True,
            secret=True,
        ),
        StackVariable(
            name="vhost",
            description="Default virtual host to create",
            default="/",
            required=False,
        ),
        StackVariable(
            name="vm_memory_high_watermark",
            description="Memory high watermark (0.4 = 40%)",
            default="0.4",
            required=False,
        ),
    ),

    presets={
        "dev": {
            "vm_memory_high_watermark": "0.6",
        },
        "prod": {
            "vm_memory_high_watermark": "0.4",
        },
    },

    min_ram_mb=512,
    min_disk_gb=2,
    ports_required=(5672, 15672),

    version="1.0.0",
    tags=("messaging", "queue", "rabbitmq", "amqp", "broker"),
)
