"""Tests for stack recipes."""

import pytest

from elle.ops.stack.models import StackGuarantee, StackRecipe, StackVariable
from elle.ops.stack.recipes import (
    APACHE_RECIPE,
    ELASTICSEARCH_RECIPE,
    FAIL2BAN_RECIPE,
    GRAFANA_RECIPE,
    LEMP_RECIPE,
    LOKI_RECIPE,
    MONGODB_RECIPE,
    MYSQL_RECIPE,
    NGINX_RECIPE,
    NODEJS_RECIPE,
    POSTGRES_RECIPE,
    PROMETHEUS_GRAFANA_RECIPE,
    RABBITMQ_RECIPE,
    REDIS_RECIPE,
    TRAEFIK_RECIPE,
    get_all_recipes,
    get_recipe_by_name,
)


class TestRecipeRegistry:
    """Tests for recipe registry functions."""

    def test_get_all_recipes_count(self):
        """Test that we have 15 recipes total."""
        recipes = get_all_recipes()
        assert len(recipes) == 15

    def test_get_all_recipes_returns_list(self):
        """Test that get_all_recipes returns a list."""
        recipes = get_all_recipes()
        assert isinstance(recipes, list)

    def test_all_recipes_are_stack_recipe(self):
        """Test that all recipes are StackRecipe instances."""
        for recipe in get_all_recipes():
            assert isinstance(recipe, StackRecipe)

    def test_unique_recipe_names(self):
        """Test that all recipe names are unique."""
        names = [r.name for r in get_all_recipes()]
        assert len(names) == len(set(names))

    def test_get_recipe_by_name_found(self):
        """Test getting a recipe by name."""
        recipe = get_recipe_by_name("postgres")
        assert recipe is not None
        assert recipe.name == "postgres"

    def test_get_recipe_by_name_not_found(self):
        """Test getting a non-existent recipe."""
        recipe = get_recipe_by_name("nonexistent")
        assert recipe is None


class TestRecipeCategories:
    """Tests for recipe category distribution."""

    def test_data_category_recipes(self):
        """Test recipes in data category."""
        data_recipes = [r for r in get_all_recipes() if r.category == "data"]
        names = {r.name for r in data_recipes}
        expected = {"postgres", "redis", "mysql", "mongodb", "elasticsearch", "rabbitmq"}
        assert expected.issubset(names)

    def test_web_category_recipes(self):
        """Test recipes in web category."""
        web_recipes = [r for r in get_all_recipes() if r.category == "web"]
        names = {r.name for r in web_recipes}
        assert "nginx" in names
        assert "apache" in names
        assert "traefik" in names

    def test_observability_category_recipes(self):
        """Test recipes in observability category."""
        obs_recipes = [r for r in get_all_recipes() if r.category == "observability"]
        names = {r.name for r in obs_recipes}
        assert "grafana" in names
        assert "loki" in names


class TestRecipeStructure:
    """Tests for recipe structure validation."""

    @pytest.mark.parametrize("recipe", get_all_recipes(), ids=lambda r: r.name)
    def test_recipe_has_required_fields(self, recipe):
        """Test that each recipe has required fields."""
        assert recipe.name
        assert recipe.display_name
        assert recipe.description
        assert recipe.category

    @pytest.mark.parametrize("recipe", get_all_recipes(), ids=lambda r: r.name)
    def test_recipe_has_packages(self, recipe):
        """Test that each recipe has at least one package."""
        assert len(recipe.packages) >= 1

    @pytest.mark.parametrize("recipe", get_all_recipes(), ids=lambda r: r.name)
    def test_recipe_has_services(self, recipe):
        """Test that each recipe has at least one service."""
        assert len(recipe.services) >= 1

    @pytest.mark.parametrize("recipe", get_all_recipes(), ids=lambda r: r.name)
    def test_recipe_has_guarantees(self, recipe):
        """Test that each recipe has at least one guarantee."""
        assert len(recipe.guarantees) >= 1

    @pytest.mark.parametrize("recipe", get_all_recipes(), ids=lambda r: r.name)
    def test_recipe_guarantees_have_checks(self, recipe):
        """Test that all guarantees have check commands."""
        for guarantee in recipe.guarantees:
            assert guarantee.check_command
            assert guarantee.name

    @pytest.mark.parametrize("recipe", get_all_recipes(), ids=lambda r: r.name)
    def test_recipe_has_tags(self, recipe):
        """Test that each recipe has tags."""
        assert len(recipe.tags) >= 1


class TestMySQLRecipe:
    """Tests for MySQL recipe."""

    def test_mysql_recipe_exists(self):
        """Test that MySQL recipe exists."""
        assert MYSQL_RECIPE is not None
        assert MYSQL_RECIPE.name == "mysql"

    def test_mysql_category(self):
        """Test MySQL is in data category."""
        assert MYSQL_RECIPE.category == "data"

    def test_mysql_packages(self):
        """Test MySQL packages."""
        assert "mysql-server" in MYSQL_RECIPE.packages

    def test_mysql_service(self):
        """Test MySQL service."""
        assert "mysql" in MYSQL_RECIPE.services

    def test_mysql_port(self):
        """Test MySQL default port."""
        assert 3306 in MYSQL_RECIPE.ports_required

    def test_mysql_guarantees(self):
        """Test MySQL has security guarantees."""
        guarantee_names = {g.name for g in MYSQL_RECIPE.guarantees}
        assert "service_running" in guarantee_names
        assert "localhost_only" in guarantee_names


class TestMongoDBRecipe:
    """Tests for MongoDB recipe."""

    def test_mongodb_recipe_exists(self):
        """Test that MongoDB recipe exists."""
        assert MONGODB_RECIPE is not None
        assert MONGODB_RECIPE.name == "mongodb"

    def test_mongodb_port(self):
        """Test MongoDB default port."""
        assert 27017 in MONGODB_RECIPE.ports_required

    def test_mongodb_guarantees(self):
        """Test MongoDB has auth guarantee."""
        guarantee_names = {g.name for g in MONGODB_RECIPE.guarantees}
        assert "auth_enabled" in guarantee_names


class TestElasticsearchRecipe:
    """Tests for Elasticsearch recipe."""

    def test_elasticsearch_recipe_exists(self):
        """Test that Elasticsearch recipe exists."""
        assert ELASTICSEARCH_RECIPE is not None
        assert ELASTICSEARCH_RECIPE.name == "elasticsearch"

    def test_elasticsearch_ports(self):
        """Test Elasticsearch ports."""
        assert 9200 in ELASTICSEARCH_RECIPE.ports_required
        assert 9300 in ELASTICSEARCH_RECIPE.ports_required

    def test_elasticsearch_memory_requirement(self):
        """Test Elasticsearch has memory requirement."""
        assert ELASTICSEARCH_RECIPE.min_ram_mb >= 2048


class TestRabbitMQRecipe:
    """Tests for RabbitMQ recipe."""

    def test_rabbitmq_recipe_exists(self):
        """Test that RabbitMQ recipe exists."""
        assert RABBITMQ_RECIPE is not None
        assert RABBITMQ_RECIPE.name == "rabbitmq"

    def test_rabbitmq_ports(self):
        """Test RabbitMQ ports."""
        assert 5672 in RABBITMQ_RECIPE.ports_required  # AMQP
        assert 15672 in RABBITMQ_RECIPE.ports_required  # Management

    def test_rabbitmq_management_guarantee(self):
        """Test RabbitMQ management guarantee."""
        guarantee_names = {g.name for g in RABBITMQ_RECIPE.guarantees}
        assert "management_enabled" in guarantee_names


class TestNginxRecipe:
    """Tests for Nginx recipe."""

    def test_nginx_recipe_exists(self):
        """Test that Nginx recipe exists."""
        assert NGINX_RECIPE is not None
        assert NGINX_RECIPE.name == "nginx"

    def test_nginx_category(self):
        """Test Nginx is in web category."""
        assert NGINX_RECIPE.category == "web"

    def test_nginx_ports(self):
        """Test Nginx ports."""
        assert 80 in NGINX_RECIPE.ports_required
        assert 443 in NGINX_RECIPE.ports_required

    def test_nginx_config_valid_guarantee(self):
        """Test Nginx has config validation guarantee."""
        guarantee_names = {g.name for g in NGINX_RECIPE.guarantees}
        assert "config_valid" in guarantee_names


class TestApacheRecipe:
    """Tests for Apache recipe."""

    def test_apache_recipe_exists(self):
        """Test that Apache recipe exists."""
        assert APACHE_RECIPE is not None
        assert APACHE_RECIPE.name == "apache"

    def test_apache_package(self):
        """Test Apache package name."""
        assert "apache2" in APACHE_RECIPE.packages

    def test_apache_mpm_guarantee(self):
        """Test Apache has MPM guarantee."""
        guarantee_names = {g.name for g in APACHE_RECIPE.guarantees}
        assert "mpm_configured" in guarantee_names


class TestTraefikRecipe:
    """Tests for Traefik recipe."""

    def test_traefik_recipe_exists(self):
        """Test that Traefik recipe exists."""
        assert TRAEFIK_RECIPE is not None
        assert TRAEFIK_RECIPE.name == "traefik"

    def test_traefik_dashboard_port(self):
        """Test Traefik dashboard port."""
        assert 8080 in TRAEFIK_RECIPE.ports_required

    def test_traefik_dashboard_guarantee(self):
        """Test Traefik has dashboard guarantee."""
        guarantee_names = {g.name for g in TRAEFIK_RECIPE.guarantees}
        assert "dashboard_accessible" in guarantee_names


class TestGrafanaRecipe:
    """Tests for Grafana recipe."""

    def test_grafana_recipe_exists(self):
        """Test that Grafana recipe exists."""
        assert GRAFANA_RECIPE is not None
        assert GRAFANA_RECIPE.name == "grafana"

    def test_grafana_category(self):
        """Test Grafana is in observability category."""
        assert GRAFANA_RECIPE.category == "observability"

    def test_grafana_port(self):
        """Test Grafana default port."""
        assert 3000 in GRAFANA_RECIPE.ports_required

    def test_grafana_admin_password_variable(self):
        """Test Grafana has admin password variable."""
        var_names = {v.name for v in GRAFANA_RECIPE.variables}
        assert "admin_password" in var_names
        admin_var = next(v for v in GRAFANA_RECIPE.variables if v.name == "admin_password")
        assert admin_var.required is True
        assert admin_var.secret is True


class TestLokiRecipe:
    """Tests for Loki recipe."""

    def test_loki_recipe_exists(self):
        """Test that Loki recipe exists."""
        assert LOKI_RECIPE is not None
        assert LOKI_RECIPE.name == "loki"

    def test_loki_port(self):
        """Test Loki default port."""
        assert 3100 in LOKI_RECIPE.ports_required

    def test_loki_retention_variable(self):
        """Test Loki has retention configuration."""
        var_names = {v.name for v in LOKI_RECIPE.variables}
        assert "retention_period" in var_names


class TestRecipePresets:
    """Tests for recipe presets."""

    @pytest.mark.parametrize("recipe", get_all_recipes(), ids=lambda r: r.name)
    def test_recipe_has_presets(self, recipe):
        """Test that each recipe has dev and/or prod presets."""
        # Most recipes should have presets
        assert isinstance(recipe.presets, dict)

    def test_mysql_dev_preset(self):
        """Test MySQL dev preset."""
        assert "dev" in MYSQL_RECIPE.presets

    def test_elasticsearch_prod_preset(self):
        """Test Elasticsearch prod preset has higher heap."""
        assert "prod" in ELASTICSEARCH_RECIPE.presets
        prod = ELASTICSEARCH_RECIPE.presets["prod"]
        assert "heap_size" in prod
        assert prod["heap_size"] == "4g"
