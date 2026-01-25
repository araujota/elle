"""Stack recipes for common infrastructure patterns.

Each recipe defines a complete, deployable infrastructure stack
with packages, configuration, services, and verification guarantees.
"""

from elle.ops.stack.models import StackRecipe
from elle.ops.stack.recipes.apache import APACHE_RECIPE
from elle.ops.stack.recipes.elasticsearch import ELASTICSEARCH_RECIPE
from elle.ops.stack.recipes.fail2ban import FAIL2BAN_RECIPE

# Observability recipes
from elle.ops.stack.recipes.grafana import GRAFANA_RECIPE
from elle.ops.stack.recipes.lemp import LEMP_RECIPE
from elle.ops.stack.recipes.loki import LOKI_RECIPE
from elle.ops.stack.recipes.mongodb import MONGODB_RECIPE

# Data recipes
from elle.ops.stack.recipes.mysql import MYSQL_RECIPE

# Web recipes
from elle.ops.stack.recipes.nginx import NGINX_RECIPE
from elle.ops.stack.recipes.nodejs import NODEJS_RECIPE

# Original recipes
from elle.ops.stack.recipes.postgres import POSTGRES_RECIPE
from elle.ops.stack.recipes.prometheus import PROMETHEUS_GRAFANA_RECIPE
from elle.ops.stack.recipes.rabbitmq import RABBITMQ_RECIPE
from elle.ops.stack.recipes.redis import REDIS_RECIPE
from elle.ops.stack.recipes.traefik import TRAEFIK_RECIPE


def get_all_recipes() -> list[StackRecipe]:
    """Get all available stack recipes."""
    return [
        # Original recipes
        POSTGRES_RECIPE,
        REDIS_RECIPE,
        LEMP_RECIPE,
        NODEJS_RECIPE,
        PROMETHEUS_GRAFANA_RECIPE,
        FAIL2BAN_RECIPE,
        # Data recipes
        MYSQL_RECIPE,
        MONGODB_RECIPE,
        ELASTICSEARCH_RECIPE,
        RABBITMQ_RECIPE,
        # Web recipes
        NGINX_RECIPE,
        APACHE_RECIPE,
        TRAEFIK_RECIPE,
        # Observability recipes
        GRAFANA_RECIPE,
        LOKI_RECIPE,
    ]


def get_recipe_by_name(name: str) -> StackRecipe | None:
    """Get a recipe by name."""
    for recipe in get_all_recipes():
        if recipe.name == name:
            return recipe
    return None


__all__ = [
    "get_all_recipes",
    "get_recipe_by_name",
    # Original recipes
    "POSTGRES_RECIPE",
    "REDIS_RECIPE",
    "LEMP_RECIPE",
    "NODEJS_RECIPE",
    "PROMETHEUS_GRAFANA_RECIPE",
    "FAIL2BAN_RECIPE",
    # Data recipes
    "MYSQL_RECIPE",
    "MONGODB_RECIPE",
    "ELASTICSEARCH_RECIPE",
    "RABBITMQ_RECIPE",
    # Web recipes
    "NGINX_RECIPE",
    "APACHE_RECIPE",
    "TRAEFIK_RECIPE",
    # Observability recipes
    "GRAFANA_RECIPE",
    "LOKI_RECIPE",
]
