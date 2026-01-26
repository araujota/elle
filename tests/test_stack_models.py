"""Tests for stack orchestration models and recipes."""

from elle.ops.stack.models import (
    GuaranteeResult,
    PhaseResult,
    PrerequisiteCheck,
    RecipeMatch,
    RollbackResult,
    StackDeployment,
    StackGuarantee,
    StackPhase,
    StackRecipe,
    StackResult,
    StackStep,
    StackVariable,
    StepResult,
)
from elle.ops.stack.recipes import (
    FAIL2BAN_RECIPE,
    LEMP_RECIPE,
    NODEJS_RECIPE,
    POSTGRES_RECIPE,
    PROMETHEUS_GRAFANA_RECIPE,
    REDIS_RECIPE,
    get_all_recipes,
    get_recipe_by_name,
)


class TestStackGuarantee:
    """Tests for StackGuarantee model."""

    def test_create_guarantee(self):
        """Test creating a stack guarantee."""
        guarantee = StackGuarantee(
            name="localhost_only",
            description="PostgreSQL only listens on localhost",
            check_command="ss -tlnp | grep 5432",
            expected_pattern=r"127\.0\.0\.1:5432",
            failure_action="fail",
            remediation_hint="Check listen_addresses in postgresql.conf",
        )

        assert guarantee.name == "localhost_only"
        assert guarantee.failure_action == "fail"
        assert "127" in guarantee.expected_pattern

    def test_guarantee_defaults(self):
        """Test guarantee default values."""
        guarantee = StackGuarantee(
            name="test",
            description="Test guarantee",
            check_command="echo test",
        )

        assert guarantee.expected_pattern is None
        assert guarantee.failure_action == "warn"
        assert guarantee.remediation_hint == ""


class TestStackVariable:
    """Tests for StackVariable model."""

    def test_create_variable(self):
        """Test creating a stack variable."""
        var = StackVariable(
            name="db_password",
            description="Database password",
            required=True,
            secret=True,
        )

        assert var.name == "db_password"
        assert var.required is True
        assert var.secret is True
        assert var.default is None

    def test_variable_with_validator(self):
        """Test variable with validator pattern."""
        var = StackVariable(
            name="port",
            description="Listen port",
            default="5432",
            validator=r"^\d{4,5}$",
        )

        assert var.validator == r"^\d{4,5}$"
        assert var.default == "5432"


class TestStackRecipe:
    """Tests for StackRecipe model."""

    def test_create_recipe(self):
        """Test creating a stack recipe."""
        recipe = StackRecipe(
            name="test-stack",
            display_name="Test Stack",
            description="A test stack for unit tests",
            category="test",
            packages=("pkg1", "pkg2"),
            services=("svc1",),
            config_templates={},
            guarantees=(
                StackGuarantee(
                    name="test_check",
                    description="Test check",
                    check_command="echo ok",
                ),
            ),
            variables=(
                StackVariable(
                    name="test_var",
                    description="Test variable",
                ),
            ),
            presets={"dev": {"test_var": "dev_value"}},
            min_ram_mb=256,
            min_disk_gb=1,
            ports_required=(8080,),
            version="1.0.0",
            tags=("test", "example"),
        )

        assert recipe.name == "test-stack"
        assert len(recipe.packages) == 2
        assert len(recipe.services) == 1
        assert len(recipe.guarantees) == 1
        assert recipe.min_ram_mb == 256
        assert "dev" in recipe.presets


class TestStackStep:
    """Tests for StackStep model."""

    def test_create_step(self):
        """Test creating a stack step."""
        step = StackStep(
            command="apt install -y postgresql",
            description="Install PostgreSQL",
            can_fail=False,
            timeout_sec=600,
        )

        assert "Install" in step.description
        assert step.can_fail is False
        assert step.timeout_sec == 600

    def test_step_defaults(self):
        """Test step default values."""
        step = StackStep(command="echo test")
        assert step.description == ""
        assert step.can_fail is False
        assert step.timeout_sec == 300


class TestStackPhase:
    """Tests for StackPhase model."""

    def test_create_phase(self):
        """Test creating a stack phase."""
        step = StackStep(
            description="Install package",
            command="apt install -y nginx",
        )

        phase = StackPhase(
            name="install",
            layer="package",
            steps=(step,),
            rollback_on_failure=True,
        )

        assert phase.name == "install"
        assert phase.layer == "package"
        assert len(phase.steps) == 1
        assert phase.rollback_on_failure is True


class TestStackDeployment:
    """Tests for StackDeployment model."""

    def test_create_deployment(self):
        """Test creating a stack deployment."""
        recipe = StackRecipe(
            name="test",
            display_name="Test",
            description="Test",
            category="test",
            packages=(),
            services=(),
            config_templates={},
            guarantees=(),
            variables=(),
        )

        deployment = StackDeployment(
            deployment_id="deploy-001",
            recipe=recipe,
            variables={"key": "value"},
            phases=(),
            status="pending",
            requires_reboot=False,
            config_changes=(),
            services_affected=(),
        )

        assert deployment.deployment_id == "deploy-001"
        assert deployment.status == "pending"
        assert deployment.variables["key"] == "value"

    def test_deployment_with_status(self):
        """Test deployment status update."""
        recipe = StackRecipe(
            name="test",
            display_name="Test",
            description="Test",
            category="test",
            packages=(),
            services=(),
            config_templates={},
            guarantees=(),
            variables=(),
        )

        deployment = StackDeployment(
            deployment_id="deploy-001",
            recipe=recipe,
            status="pending",
        )

        updated = deployment.with_status("completed")
        assert updated.status == "completed"
        assert updated.completed_at is not None


class TestRecipeMatch:
    """Tests for RecipeMatch model."""

    def test_create_match(self):
        """Test creating a recipe match."""
        recipe = StackRecipe(
            name="postgres",
            display_name="PostgreSQL",
            description="PostgreSQL database",
            category="data",
            packages=("postgresql",),
            services=("postgresql",),
            config_templates={},
            guarantees=(),
            variables=(),
        )

        match = RecipeMatch(
            recipe=recipe,
            confidence=0.95,
            matched_keywords=("postgres", "database"),
        )

        assert match.confidence == 0.95
        assert len(match.matched_keywords) == 2


class TestPrerequisiteCheck:
    """Tests for PrerequisiteCheck model."""

    def test_passed_check(self):
        """Test a passed prerequisite check."""
        check = PrerequisiteCheck(
            passed=True,
            warnings=(),
            errors=(),
            ram_sufficient=True,
            disk_sufficient=True,
            ports_available=True,
        )

        assert check.passed is True
        assert len(check.warnings) == 0

    def test_failed_check(self):
        """Test a failed prerequisite check."""
        check = PrerequisiteCheck(
            passed=False,
            warnings=("Low RAM",),
            errors=("Port 80 in use",),
            ram_sufficient=False,
            disk_sufficient=True,
            ports_available=False,
        )

        assert check.passed is False
        assert len(check.errors) == 1


class TestStepResult:
    """Tests for StepResult model."""

    def test_successful_step(self):
        """Test successful step result."""
        step = StackStep(command="echo test")
        result = StepResult(
            step=step,
            success=True,
            exit_code=0,
            stdout="test\n",
            stderr="",
            duration_ms=50,
        )

        assert result.success is True
        assert result.exit_code == 0

    def test_failed_step(self):
        """Test failed step result."""
        step = StackStep(command="false")
        result = StepResult(
            step=step,
            success=False,
            exit_code=1,
            stderr="error",
        )

        assert result.success is False
        assert result.exit_code == 1


class TestPhaseResult:
    """Tests for PhaseResult model."""

    def test_phase_result(self):
        """Test phase result."""
        step = StackStep(command="echo test")
        phase = StackPhase(name="test", layer="verify", steps=(step,))
        step_result = StepResult(step=step, success=True)

        result = PhaseResult(
            phase=phase,
            success=True,
            step_results=(step_result,),
            duration_ms=100,
        )

        assert result.success is True
        assert len(result.step_results) == 1


class TestGuaranteeResult:
    """Tests for GuaranteeResult model."""

    def test_passed_guarantee(self):
        """Test passed guarantee."""
        guarantee = StackGuarantee(
            name="test",
            description="Test",
            check_command="echo OK",
            expected_pattern="OK",
        )

        result = GuaranteeResult(
            guarantee=guarantee,
            passed=True,
            actual_output="OK",
            match_found=True,
        )

        assert result.passed is True
        assert result.match_found is True


class TestRollbackResult:
    """Tests for RollbackResult model."""

    def test_successful_rollback(self):
        """Test successful rollback."""
        result = RollbackResult(
            success=True,
            phases_rolled_back=("install", "config"),
            errors=(),
        )

        assert result.success is True
        assert len(result.phases_rolled_back) == 2


class TestStackResult:
    """Tests for StackResult model."""

    def test_successful_result(self):
        """Test a successful stack result."""
        recipe = StackRecipe(
            name="test",
            display_name="Test",
            description="Test",
            category="test",
            packages=(),
            services=(),
            config_templates={},
            guarantees=(),
            variables=(),
        )

        deployment = StackDeployment(
            deployment_id="deploy-001",
            recipe=recipe,
            variables={},
            phases=(),
            status="completed",
        )

        result = StackResult(
            deployment=deployment,
            phase_results=(),
            guarantees_verified=(),
            success=True,
            rollback_performed=False,
            credentials_generated={"password": "secret123"},
        )

        assert result.success is True
        assert result.rollback_performed is False
        assert "password" in result.credentials_generated


class TestRecipes:
    """Tests for built-in recipes."""

    def test_get_all_recipes(self):
        """Test getting all recipes."""
        recipes = get_all_recipes()
        assert len(recipes) >= 6
        names = {r.name for r in recipes}
        assert "postgres" in names
        assert "redis" in names
        assert "lemp" in names

    def test_get_recipe_by_name(self):
        """Test getting recipe by name."""
        recipe = get_recipe_by_name("postgres")
        assert recipe is not None
        assert recipe.name == "postgres"

        not_found = get_recipe_by_name("nonexistent")
        assert not_found is None

    def test_postgres_recipe(self):
        """Test PostgreSQL recipe structure."""
        recipe = POSTGRES_RECIPE
        assert recipe.name == "postgres"
        assert "postgresql" in recipe.packages
        assert len(recipe.guarantees) >= 3
        assert any(g.name == "localhost_only" for g in recipe.guarantees)

    def test_redis_recipe(self):
        """Test Redis recipe structure."""
        recipe = REDIS_RECIPE
        assert recipe.name == "redis"
        assert "redis-server" in recipe.packages
        assert recipe.category == "data"
        assert any(g.name == "ping_works" for g in recipe.guarantees)

    def test_lemp_recipe(self):
        """Test LEMP recipe structure."""
        recipe = LEMP_RECIPE
        assert recipe.name == "lemp"
        assert "nginx" in recipe.packages
        assert "php-fpm" in recipe.packages
        assert "mariadb-server" in recipe.packages
        assert recipe.category == "web"

    def test_nodejs_recipe(self):
        """Test Node.js recipe structure."""
        recipe = NODEJS_RECIPE
        assert recipe.name == "nodejs"
        assert "nodejs" in recipe.packages
        assert "nginx" in recipe.packages
        assert any(v.name == "app_port" for v in recipe.variables)

    def test_prometheus_recipe(self):
        """Test Prometheus + Grafana recipe structure."""
        recipe = PROMETHEUS_GRAFANA_RECIPE
        assert "prometheus" in recipe.name
        assert "prometheus" in recipe.packages
        assert "grafana" in recipe.packages
        assert recipe.category == "observability"
        assert any(g.name == "grafana_accessible" for g in recipe.guarantees)

    def test_fail2ban_recipe(self):
        """Test Fail2ban recipe structure."""
        recipe = FAIL2BAN_RECIPE
        assert recipe.name == "fail2ban"
        assert "fail2ban" in recipe.packages
        assert recipe.category == "security"
        assert any(g.name == "sshd_jail_active" for g in recipe.guarantees)
        assert any(v.name == "bantime" for v in recipe.variables)

    def test_all_recipes_have_guarantees(self):
        """Test that all recipes have at least one guarantee."""
        for recipe in get_all_recipes():
            assert len(recipe.guarantees) >= 1, f"{recipe.name} has no guarantees"

    def test_all_recipes_have_valid_structure(self):
        """Test that all recipes have valid structure."""
        for recipe in get_all_recipes():
            assert recipe.name, "Recipe missing name"
            assert recipe.display_name, f"{recipe.name} missing display_name"
            assert recipe.description, f"{recipe.name} missing description"
            assert recipe.category, f"{recipe.name} missing category"
            assert recipe.version, f"{recipe.name} missing version"
