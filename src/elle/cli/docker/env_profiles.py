"""Docker Image Environment Variable Profiles.

Knowledge base of common Docker images and their environment variables.
These profiles provide:
- Required vs optional variable classification
- Descriptions explaining each variable's purpose
- Sensitivity flags for passwords and secrets
- Default values where applicable
"""

from __future__ import annotations

from elle.cli.docker.env_models import EnvVarSpec, ImageEnvProfile


# =============================================================================
# Database Images
# =============================================================================

POSTGRES_PROFILE = ImageEnvProfile(
    image_family="postgres",
    display_name="PostgreSQL",
    description="PostgreSQL database server",
    documentation_url="https://hub.docker.com/_/postgres",
    env_vars=(
        EnvVarSpec(
            name="POSTGRES_PASSWORD",
            required=True,
            sensitive=True,
            description="Password for the PostgreSQL superuser (postgres)",
            example="mysecretpassword",
        ),
        EnvVarSpec(
            name="POSTGRES_USER",
            required=False,
            default="postgres",
            description="Username for the superuser (creates new user if not 'postgres')",
            example="myuser",
        ),
        EnvVarSpec(
            name="POSTGRES_DB",
            required=False,
            description="Name of the default database (defaults to POSTGRES_USER)",
            example="myapp",
        ),
        EnvVarSpec(
            name="POSTGRES_INITDB_ARGS",
            required=False,
            description="Arguments to pass to postgres initdb",
            example="--data-checksums",
        ),
        EnvVarSpec(
            name="POSTGRES_INITDB_WALDIR",
            required=False,
            description="Custom location for the transaction log",
        ),
        EnvVarSpec(
            name="POSTGRES_HOST_AUTH_METHOD",
            required=False,
            description="Auth method for host connections (use 'trust' for no password)",
            example="scram-sha-256",
        ),
        EnvVarSpec(
            name="PGDATA",
            required=False,
            default="/var/lib/postgresql/data",
            description="Location for the database files",
        ),
    ),
)

MYSQL_PROFILE = ImageEnvProfile(
    image_family="mysql",
    display_name="MySQL",
    description="MySQL database server",
    documentation_url="https://hub.docker.com/_/mysql",
    env_vars=(
        EnvVarSpec(
            name="MYSQL_ROOT_PASSWORD",
            required=True,
            sensitive=True,
            description="Password for the MySQL root user",
            example="my-secret-pw",
        ),
        EnvVarSpec(
            name="MYSQL_DATABASE",
            required=False,
            description="Name of a database to create on startup",
            example="myapp",
        ),
        EnvVarSpec(
            name="MYSQL_USER",
            required=False,
            description="Username for a new user (requires MYSQL_PASSWORD)",
            example="appuser",
        ),
        EnvVarSpec(
            name="MYSQL_PASSWORD",
            required=False,
            sensitive=True,
            description="Password for the new user (requires MYSQL_USER)",
            example="userpassword",
        ),
        EnvVarSpec(
            name="MYSQL_ALLOW_EMPTY_PASSWORD",
            required=False,
            description="Set to 'yes' to allow root with no password (not recommended)",
            example="yes",
        ),
        EnvVarSpec(
            name="MYSQL_RANDOM_ROOT_PASSWORD",
            required=False,
            description="Set to 'yes' to generate a random root password (printed to logs)",
            example="yes",
        ),
        EnvVarSpec(
            name="MYSQL_ONETIME_PASSWORD",
            required=False,
            description="Set to 'yes' to require root password change on first login",
            example="yes",
        ),
    ),
)

MARIADB_PROFILE = ImageEnvProfile(
    image_family="mariadb",
    display_name="MariaDB",
    description="MariaDB database server (MySQL fork)",
    documentation_url="https://hub.docker.com/_/mariadb",
    env_vars=(
        EnvVarSpec(
            name="MARIADB_ROOT_PASSWORD",
            required=True,
            sensitive=True,
            description="Password for the MariaDB root user",
            example="my-secret-pw",
        ),
        EnvVarSpec(
            name="MARIADB_DATABASE",
            required=False,
            description="Name of a database to create on startup",
            example="myapp",
        ),
        EnvVarSpec(
            name="MARIADB_USER",
            required=False,
            description="Username for a new user (requires MARIADB_PASSWORD)",
            example="appuser",
        ),
        EnvVarSpec(
            name="MARIADB_PASSWORD",
            required=False,
            sensitive=True,
            description="Password for the new user (requires MARIADB_USER)",
            example="userpassword",
        ),
        EnvVarSpec(
            name="MARIADB_ALLOW_EMPTY_ROOT_PASSWORD",
            required=False,
            description="Set to 'yes' to allow root with no password (not recommended)",
            example="yes",
        ),
        EnvVarSpec(
            name="MARIADB_RANDOM_ROOT_PASSWORD",
            required=False,
            description="Set to 'yes' to generate a random root password",
            example="yes",
        ),
        EnvVarSpec(
            name="MARIADB_AUTO_UPGRADE",
            required=False,
            description="Set to '1' to automatically upgrade system tables",
            example="1",
        ),
    ),
)

MONGODB_PROFILE = ImageEnvProfile(
    image_family="mongo",
    display_name="MongoDB",
    description="MongoDB document database",
    documentation_url="https://hub.docker.com/_/mongo",
    env_vars=(
        EnvVarSpec(
            name="MONGO_INITDB_ROOT_USERNAME",
            required=False,
            description="Username for the root admin user",
            example="admin",
        ),
        EnvVarSpec(
            name="MONGO_INITDB_ROOT_PASSWORD",
            required=False,
            sensitive=True,
            description="Password for the root admin user",
            example="adminpassword",
        ),
        EnvVarSpec(
            name="MONGO_INITDB_DATABASE",
            required=False,
            description="Database name for initialization scripts",
            example="myapp",
        ),
    ),
)

REDIS_PROFILE = ImageEnvProfile(
    image_family="redis",
    display_name="Redis",
    description="Redis in-memory data store",
    documentation_url="https://hub.docker.com/_/redis",
    env_vars=(
        EnvVarSpec(
            name="REDIS_PASSWORD",
            required=False,
            sensitive=True,
            description="Password for Redis authentication (requirepass)",
            example="redispassword",
        ),
        EnvVarSpec(
            name="REDIS_ARGS",
            required=False,
            description="Additional arguments to pass to redis-server",
            example="--maxmemory 256mb",
        ),
    ),
)


# =============================================================================
# Message Queue Images
# =============================================================================

RABBITMQ_PROFILE = ImageEnvProfile(
    image_family="rabbitmq",
    display_name="RabbitMQ",
    description="RabbitMQ message broker",
    documentation_url="https://hub.docker.com/_/rabbitmq",
    env_vars=(
        EnvVarSpec(
            name="RABBITMQ_DEFAULT_USER",
            required=False,
            default="guest",
            description="Default username for the message broker",
            example="myuser",
        ),
        EnvVarSpec(
            name="RABBITMQ_DEFAULT_PASS",
            required=False,
            default="guest",
            sensitive=True,
            description="Default password for the message broker",
            example="mypassword",
        ),
        EnvVarSpec(
            name="RABBITMQ_DEFAULT_VHOST",
            required=False,
            default="/",
            description="Default virtual host",
            example="myvhost",
        ),
        EnvVarSpec(
            name="RABBITMQ_ERLANG_COOKIE",
            required=False,
            sensitive=True,
            description="Erlang cookie for clustering (must match across nodes)",
            example="SECRETCOOKIE",
        ),
        EnvVarSpec(
            name="RABBITMQ_NODENAME",
            required=False,
            description="Node name for clustering",
            example="rabbit@hostname",
        ),
    ),
)


# =============================================================================
# Search/Analytics Images
# =============================================================================

ELASTICSEARCH_PROFILE = ImageEnvProfile(
    image_family="elasticsearch",
    display_name="Elasticsearch",
    description="Elasticsearch search and analytics engine",
    documentation_url="https://hub.docker.com/_/elasticsearch",
    env_vars=(
        EnvVarSpec(
            name="discovery.type",
            required=False,
            description="Discovery type (use 'single-node' for dev)",
            example="single-node",
        ),
        EnvVarSpec(
            name="ELASTIC_PASSWORD",
            required=False,
            sensitive=True,
            description="Password for the elastic user (8.x+)",
            example="changeme",
        ),
        EnvVarSpec(
            name="xpack.security.enabled",
            required=False,
            description="Enable X-Pack security features",
            example="true",
        ),
        EnvVarSpec(
            name="ES_JAVA_OPTS",
            required=False,
            description="JVM options for Elasticsearch",
            example="-Xms512m -Xmx512m",
        ),
        EnvVarSpec(
            name="cluster.name",
            required=False,
            description="Elasticsearch cluster name",
            example="my-cluster",
        ),
        EnvVarSpec(
            name="node.name",
            required=False,
            description="Node name within the cluster",
            example="es-node-1",
        ),
    ),
)


# =============================================================================
# Web Server Images
# =============================================================================

NGINX_PROFILE = ImageEnvProfile(
    image_family="nginx",
    display_name="Nginx",
    description="Nginx web server and reverse proxy",
    documentation_url="https://hub.docker.com/_/nginx",
    env_vars=(
        EnvVarSpec(
            name="NGINX_HOST",
            required=False,
            description="Server hostname (for templating)",
            example="localhost",
        ),
        EnvVarSpec(
            name="NGINX_PORT",
            required=False,
            default="80",
            description="Listen port (for templating)",
            example="8080",
        ),
        EnvVarSpec(
            name="NGINX_ENVSUBST_TEMPLATE_DIR",
            required=False,
            description="Directory containing template files",
            example="/etc/nginx/templates",
        ),
        EnvVarSpec(
            name="NGINX_ENVSUBST_TEMPLATE_SUFFIX",
            required=False,
            default=".template",
            description="Suffix for template files",
        ),
        EnvVarSpec(
            name="NGINX_ENVSUBST_OUTPUT_DIR",
            required=False,
            default="/etc/nginx/conf.d",
            description="Output directory for processed templates",
        ),
    ),
)

HTTPD_PROFILE = ImageEnvProfile(
    image_family="httpd",
    display_name="Apache HTTP Server",
    description="Apache HTTP web server",
    documentation_url="https://hub.docker.com/_/httpd",
    env_vars=(
        # Apache httpd doesn't have many standard env vars
        EnvVarSpec(
            name="HTTPD_PREFIX",
            required=False,
            default="/usr/local/apache2",
            description="Apache installation prefix",
        ),
    ),
)


# =============================================================================
# Runtime/Language Images
# =============================================================================

NODE_PROFILE = ImageEnvProfile(
    image_family="node",
    display_name="Node.js",
    description="Node.js JavaScript runtime",
    documentation_url="https://hub.docker.com/_/node",
    env_vars=(
        EnvVarSpec(
            name="NODE_ENV",
            required=False,
            default="production",
            description="Node environment (development, production, test)",
            example="production",
        ),
        EnvVarSpec(
            name="NODE_OPTIONS",
            required=False,
            description="Additional Node.js CLI options",
            example="--max-old-space-size=4096",
        ),
        EnvVarSpec(
            name="NPM_CONFIG_LOGLEVEL",
            required=False,
            description="npm log level",
            example="warn",
        ),
        EnvVarSpec(
            name="NPM_CONFIG_REGISTRY",
            required=False,
            description="npm registry URL",
            example="https://registry.npmjs.org/",
        ),
    ),
)

PYTHON_PROFILE = ImageEnvProfile(
    image_family="python",
    display_name="Python",
    description="Python programming language runtime",
    documentation_url="https://hub.docker.com/_/python",
    env_vars=(
        EnvVarSpec(
            name="PYTHONDONTWRITEBYTECODE",
            required=False,
            description="Prevent Python from writing .pyc files",
            example="1",
        ),
        EnvVarSpec(
            name="PYTHONUNBUFFERED",
            required=False,
            description="Force stdout/stderr streams to be unbuffered",
            example="1",
        ),
        EnvVarSpec(
            name="PYTHONPATH",
            required=False,
            description="Additional paths for Python module search",
            example="/app/lib",
        ),
        EnvVarSpec(
            name="PIP_NO_CACHE_DIR",
            required=False,
            description="Disable pip cache",
            example="1",
        ),
        EnvVarSpec(
            name="PIP_DISABLE_PIP_VERSION_CHECK",
            required=False,
            description="Disable pip version check",
            example="1",
        ),
    ),
)


# =============================================================================
# Other Common Images
# =============================================================================

MINIO_PROFILE = ImageEnvProfile(
    image_family="minio",
    display_name="MinIO",
    description="MinIO S3-compatible object storage",
    documentation_url="https://hub.docker.com/r/minio/minio",
    env_vars=(
        EnvVarSpec(
            name="MINIO_ROOT_USER",
            required=True,
            description="Root username (access key)",
            example="minioadmin",
        ),
        EnvVarSpec(
            name="MINIO_ROOT_PASSWORD",
            required=True,
            sensitive=True,
            description="Root password (secret key) - minimum 8 characters",
            example="minioadmin",
        ),
        EnvVarSpec(
            name="MINIO_BROWSER",
            required=False,
            default="on",
            description="Enable/disable web browser console",
            example="off",
        ),
        EnvVarSpec(
            name="MINIO_DOMAIN",
            required=False,
            description="Domain name for virtual-hosted-style requests",
            example="minio.example.com",
        ),
    ),
)

GRAFANA_PROFILE = ImageEnvProfile(
    image_family="grafana",
    display_name="Grafana",
    description="Grafana observability platform",
    documentation_url="https://hub.docker.com/r/grafana/grafana",
    env_vars=(
        EnvVarSpec(
            name="GF_SECURITY_ADMIN_USER",
            required=False,
            default="admin",
            description="Admin username",
            example="admin",
        ),
        EnvVarSpec(
            name="GF_SECURITY_ADMIN_PASSWORD",
            required=False,
            default="admin",
            sensitive=True,
            description="Admin password",
            example="strongpassword",
        ),
        EnvVarSpec(
            name="GF_INSTALL_PLUGINS",
            required=False,
            description="Comma-separated list of plugins to install",
            example="grafana-clock-panel,grafana-piechart-panel",
        ),
        EnvVarSpec(
            name="GF_SERVER_ROOT_URL",
            required=False,
            description="Full URL used to access Grafana",
            example="https://grafana.example.com/",
        ),
        EnvVarSpec(
            name="GF_PATHS_DATA",
            required=False,
            default="/var/lib/grafana",
            description="Data storage path",
        ),
    ),
)

PROMETHEUS_PROFILE = ImageEnvProfile(
    image_family="prometheus",
    display_name="Prometheus",
    description="Prometheus monitoring system",
    documentation_url="https://hub.docker.com/r/prom/prometheus",
    env_vars=(
        # Prometheus uses config files rather than env vars
        EnvVarSpec(
            name="PROMETHEUS_RETENTION_TIME",
            required=False,
            description="Data retention time (via --storage.tsdb.retention.time)",
            example="15d",
        ),
    ),
)

VAULT_PROFILE = ImageEnvProfile(
    image_family="vault",
    display_name="HashiCorp Vault",
    description="HashiCorp Vault secrets management",
    documentation_url="https://hub.docker.com/_/vault",
    env_vars=(
        EnvVarSpec(
            name="VAULT_DEV_ROOT_TOKEN_ID",
            required=False,
            sensitive=True,
            description="Root token for dev server mode",
            example="myroot",
        ),
        EnvVarSpec(
            name="VAULT_DEV_LISTEN_ADDRESS",
            required=False,
            default="0.0.0.0:8200",
            description="Address for dev server to listen on",
            example="0.0.0.0:8200",
        ),
        EnvVarSpec(
            name="VAULT_ADDR",
            required=False,
            description="Vault server address for CLI",
            example="http://127.0.0.1:8200",
        ),
        EnvVarSpec(
            name="VAULT_TOKEN",
            required=False,
            sensitive=True,
            description="Vault authentication token",
        ),
    ),
)

CONSUL_PROFILE = ImageEnvProfile(
    image_family="consul",
    display_name="HashiCorp Consul",
    description="HashiCorp Consul service mesh",
    documentation_url="https://hub.docker.com/_/consul",
    env_vars=(
        EnvVarSpec(
            name="CONSUL_BIND_INTERFACE",
            required=False,
            description="Network interface to bind to",
            example="eth0",
        ),
        EnvVarSpec(
            name="CONSUL_CLIENT_INTERFACE",
            required=False,
            description="Network interface for client connections",
            example="eth0",
        ),
        EnvVarSpec(
            name="CONSUL_HTTP_TOKEN",
            required=False,
            sensitive=True,
            description="ACL token for HTTP API",
        ),
    ),
)


# =============================================================================
# Profile Registry
# =============================================================================

# All known image profiles indexed by family name
IMAGE_PROFILES: dict[str, ImageEnvProfile] = {
    # Databases
    "postgres": POSTGRES_PROFILE,
    "mysql": MYSQL_PROFILE,
    "mariadb": MARIADB_PROFILE,
    "mongo": MONGODB_PROFILE,
    "mongodb": MONGODB_PROFILE,  # Alias
    "redis": REDIS_PROFILE,
    # Message queues
    "rabbitmq": RABBITMQ_PROFILE,
    # Search
    "elasticsearch": ELASTICSEARCH_PROFILE,
    # Web servers
    "nginx": NGINX_PROFILE,
    "httpd": HTTPD_PROFILE,
    "apache": HTTPD_PROFILE,  # Alias
    # Runtimes
    "node": NODE_PROFILE,
    "python": PYTHON_PROFILE,
    # Other
    "minio": MINIO_PROFILE,
    "grafana": GRAFANA_PROFILE,
    "prometheus": PROMETHEUS_PROFILE,
    "prom": PROMETHEUS_PROFILE,  # Alias
    "vault": VAULT_PROFILE,
    "consul": CONSUL_PROFILE,
}


def get_profile(image_family: str) -> ImageEnvProfile | None:
    """Get the environment variable profile for an image family.

    Args:
        image_family: The base image name without registry/tag.
                      e.g., "postgres", "mysql", "nginx"

    Returns:
        ImageEnvProfile if known, None otherwise.
    """
    # Normalize to lowercase
    family = image_family.lower().strip()

    # Handle common registry prefixes
    for prefix in ("library/", "docker.io/library/", "docker.io/"):
        if family.startswith(prefix):
            family = family[len(prefix) :]

    # Handle organization prefixes for known images
    # e.g., "bitnami/postgres" -> check for "postgres"
    if "/" in family:
        _, name = family.rsplit("/", 1)
        if name in IMAGE_PROFILES:
            return IMAGE_PROFILES[name]

    return IMAGE_PROFILES.get(family)


def extract_image_family(image: str) -> str:
    """Extract the image family name from a full image reference.

    Removes registry, organization, and tag to get the base family name.

    Args:
        image: Full image reference like "postgres:15",
               "docker.io/library/mysql:8.0",
               "myregistry/myorg/myapp:v1"

    Returns:
        Family name like "postgres", "mysql", "myapp"

    Examples:
        >>> extract_image_family("postgres:15")
        'postgres'
        >>> extract_image_family("docker.io/library/mysql:8.0")
        'mysql'
        >>> extract_image_family("myregistry.com/myorg/myapp:v1")
        'myorg/myapp'
    """
    # Remove tag
    if ":" in image:
        image = image.split(":")[0]

    # Remove digest
    if "@" in image:
        image = image.split("@")[0]

    # Handle known registry prefixes
    for prefix in ("docker.io/library/", "library/", "docker.io/"):
        if image.startswith(prefix):
            return image[len(prefix) :]

    # For other registries, check if first part looks like a registry
    parts = image.split("/")
    if len(parts) >= 2:
        # If first part contains a dot or colon, it's likely a registry
        if "." in parts[0] or ":" in parts[0]:
            # Return everything after the registry
            return "/".join(parts[1:])

    return image


def list_known_families() -> list[str]:
    """Get a list of all known image families.

    Returns:
        Sorted list of image family names with profiles.
    """
    # Return unique profile names (excluding aliases)
    seen: set[str] = set()
    families: list[str] = []

    for name, profile in IMAGE_PROFILES.items():
        if profile.image_family not in seen:
            seen.add(profile.image_family)
            families.append(profile.image_family)

    return sorted(families)
