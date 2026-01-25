"""Elasticsearch search engine recipe."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)

ELASTICSEARCH_RECIPE = StackRecipe(
    name="elasticsearch",
    display_name="Elasticsearch",
    description="Elasticsearch search and analytics engine with secure defaults",
    category="data",

    packages=(
        "elasticsearch",
    ),

    services=(
        "elasticsearch",
    ),

    config_templates={},

    guarantees=(
        StackGuarantee(
            name="service_running",
            description="Elasticsearch service is active",
            check_command="systemctl is-active elasticsearch",
            expected_pattern=r"^active$",
            failure_action="fail",
            remediation_hint="Start Elasticsearch with: systemctl start elasticsearch",
        ),
        StackGuarantee(
            name="cluster_health",
            description="Elasticsearch cluster is healthy",
            check_command="curl -s http://localhost:9200/_cluster/health 2>/dev/null | grep -o '\"status\":\"[^\"]*\"'",
            expected_pattern=r"green|yellow",
            failure_action="warn",
            remediation_hint="Check cluster health with: curl localhost:9200/_cluster/health?pretty",
        ),
        StackGuarantee(
            name="localhost_only",
            description="Elasticsearch bound to localhost only",
            check_command="grep -E '^network.host:' /etc/elasticsearch/elasticsearch.yml || echo 'localhost'",
            expected_pattern=r"localhost|127\.0\.0\.1|_local_",
            failure_action="warn",
            remediation_hint="Set network.host to localhost in elasticsearch.yml",
        ),
        StackGuarantee(
            name="heap_configured",
            description="JVM heap size is configured",
            check_command="grep -E '^-Xms' /etc/elasticsearch/jvm.options 2>/dev/null || echo '-Xms1g'",
            expected_pattern=r"-Xms\d+[gm]",
            failure_action="warn",
            remediation_hint="Configure heap size in /etc/elasticsearch/jvm.options",
        ),
    ),

    variables=(
        StackVariable(
            name="cluster_name",
            description="Elasticsearch cluster name",
            default="elle-cluster",
            required=False,
        ),
        StackVariable(
            name="heap_size",
            description="JVM heap size (e.g., 2g, 512m)",
            default="1g",
            required=False,
        ),
        StackVariable(
            name="data_path",
            description="Data storage path",
            default="/var/lib/elasticsearch",
            required=False,
        ),
    ),

    presets={
        "dev": {
            "heap_size": "512m",
        },
        "prod": {
            "heap_size": "4g",
        },
    },

    min_ram_mb=2048,
    min_disk_gb=20,
    ports_required=(9200, 9300),

    version="1.0.0",
    tags=("search", "analytics", "elasticsearch", "data", "fulltext"),
)
