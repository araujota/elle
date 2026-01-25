"""Stack recipes for common infrastructure patterns.

Each recipe defines a complete, deployable infrastructure stack
with packages, configuration, services, and verification guarantees.
"""

from elle.ops.stack.models import StackRecipe

from elle.ops.stack.recipes.postgres import POSTGRES_RECIPE
from elle.ops.stack.recipes.redis import REDIS_RECIPE
from elle.ops.stack.recipes.lemp import LEMP_RECIPE
from elle.ops.stack.recipes.nodejs import NODEJS_RECIPE
from elle.ops.stack.recipes.prometheus import PROMETHEUS_GRAFANA_RECIPE
from elle.ops.stack.recipes.fail2ban import FAIL2BAN_RECIPE


def get_all_recipes() -> list[StackRecipe]:
    """Get all available stack recipes."""
    return [
        POSTGRES_RECIPE,
        REDIS_RECIPE,
        LEMP_RECIPE,
        NODEJS_RECIPE,
        PROMETHEUS_GRAFANA_RECIPE,
        FAIL2BAN_RECIPE,
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
    "POSTGRES_RECIPE",
    "REDIS_RECIPE",
    "LEMP_RECIPE",
    "NODEJS_RECIPE",
    "PROMETHEUS_GRAFANA_RECIPE",
    "FAIL2BAN_RECIPE",
]
