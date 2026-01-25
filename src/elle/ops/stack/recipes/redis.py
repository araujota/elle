"""Redis cache server recipe."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)

REDIS_RECIPE = StackRecipe(
    name="redis",
    display_name="Redis Cache Server",
    description="Redis with localhost-only binding, memory management, and optional persistence",
    category="data",

    packages=(
        "redis-server",
    ),

    services=(
        "redis-server",
    ),

    config_templates={
        # Redis will use system defaults with localhost binding
    },

    guarantees=(
        StackGuarantee(
            name="localhost_only",
            description="Redis only listens on localhost",
            check_command="ss -tlnp | grep 6379",
            expected_pattern=r"127\.0\.0\.1:6379",
            failure_action="fail",
            remediation_hint="Redis should only listen on localhost. Check bind directive in /etc/redis/redis.conf",
        ),
        StackGuarantee(
            name="ping_works",
            description="Redis responds to PING",
            check_command="redis-cli ping",
            expected_pattern=r"PONG",
            failure_action="fail",
            remediation_hint="Redis not responding. Check service with: systemctl status redis-server",
        ),
        StackGuarantee(
            name="service_running",
            description="Redis service is active",
            check_command="systemctl is-active redis-server",
            expected_pattern=r"active",
            failure_action="fail",
            remediation_hint="Service not running. Start with: systemctl start redis-server",
        ),
        StackGuarantee(
            name="memory_policy_set",
            description="Maxmemory policy is configured",
            check_command="redis-cli config get maxmemory-policy | tail -1",
            expected_pattern=r"(noeviction|allkeys-lru|volatile-lru)",
            failure_action="warn",
            remediation_hint="Consider setting maxmemory and maxmemory-policy in /etc/redis/redis.conf",
        ),
    ),

    variables=(
        StackVariable(
            name="maxmemory",
            description="Maximum memory limit (e.g., 256mb, 1gb)",
            default="256mb",
            required=False,
        ),
        StackVariable(
            name="maxmemory_policy",
            description="Eviction policy when maxmemory is reached",
            default="allkeys-lru",
            required=False,
        ),
        StackVariable(
            name="appendonly",
            description="Enable AOF persistence (yes/no)",
            default="no",
            required=False,
        ),
    ),

    presets={
        "cache": {
            "maxmemory": "256mb",
            "maxmemory_policy": "allkeys-lru",
            "appendonly": "no",
        },
        "persistent": {
            "maxmemory": "1gb",
            "maxmemory_policy": "noeviction",
            "appendonly": "yes",
        },
    },

    min_ram_mb=128,
    min_disk_gb=1,
    ports_required=(6379,),

    version="1.0.0",
    tags=("cache", "redis", "data", "memory"),
)
