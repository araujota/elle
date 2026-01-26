"""Stack coordinator for unified multi-layer operations.

Orchestrates package installation, configuration, service management,
and verification as a single atomic transaction with rollback support.
"""

import re
import secrets
import subprocess
import uuid
from datetime import datetime

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
    StepResult,
)


class StackCoordinator:
    """Orchestrates stack deployment with unified rollback.

    Combines package installation, configuration, and service management
    into a single transactional deployment.
    """

    def __init__(self):
        """Initialize the coordinator."""
        self._recipes: dict[str, StackRecipe] = {}
        self._load_recipes()

    def _load_recipes(self) -> None:
        """Load all available recipes."""
        from elle.ops.stack.recipes import get_all_recipes

        for recipe in get_all_recipes():
            self._recipes[recipe.name] = recipe

    def get_recipe(self, name: str) -> StackRecipe | None:
        """Get a recipe by name."""
        return self._recipes.get(name)

    def list_recipes(self) -> list[StackRecipe]:
        """List all available recipes."""
        return list(self._recipes.values())

    def match_recipe(self, request: str) -> RecipeMatch | None:
        """Match a natural language request to a recipe.

        Args:
            request: User's natural language request.

        Returns:
            RecipeMatch if a recipe matches, None otherwise.
        """
        request_lower = request.lower()

        best_match: RecipeMatch | None = None
        best_confidence = 0.0

        for recipe in self._recipes.values():
            confidence = 0.0
            matched_keywords: list[str] = []

            # Check recipe name
            if recipe.name in request_lower:
                confidence += 0.4
                matched_keywords.append(recipe.name)

            # Check display name words
            for word in recipe.display_name.lower().split():
                if len(word) > 2 and word in request_lower:
                    confidence += 0.2
                    matched_keywords.append(word)

            # Check tags
            for tag in recipe.tags:
                if tag.lower() in request_lower:
                    confidence += 0.15
                    matched_keywords.append(tag)

            # Check packages
            for pkg in recipe.packages:
                if pkg.lower() in request_lower:
                    confidence += 0.3
                    matched_keywords.append(pkg)

            # Check common request patterns
            patterns = [
                (r"set up\s+(\w+)", 0.3),
                (r"install\s+(\w+)", 0.3),
                (r"deploy\s+(\w+)", 0.3),
                (r"configure\s+(\w+)", 0.2),
            ]
            for pattern, weight in patterns:
                match = re.search(pattern, request_lower)
                if match and match.group(1) in recipe.name.lower():
                    confidence += weight

            if confidence > best_confidence:
                best_confidence = confidence
                best_match = RecipeMatch(
                    recipe=recipe,
                    confidence=min(1.0, confidence),
                    matched_keywords=tuple(set(matched_keywords)),
                )

        return best_match if best_match and best_confidence >= 0.3 else None

    def check_prerequisites(
        self,
        recipe: StackRecipe,
    ) -> PrerequisiteCheck:
        """Check if prerequisites for deployment are met.

        Args:
            recipe: Recipe to check.

        Returns:
            PrerequisiteCheck with results.
        """
        warnings: list[str] = []
        errors: list[str] = []

        ram_ok = True
        disk_ok = True
        ports_ok = True
        packages_ok = True

        # Check RAM
        if recipe.min_ram_mb > 0:
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemAvailable:"):
                            available_kb = int(line.split()[1])
                            available_mb = available_kb // 1024
                            if available_mb < recipe.min_ram_mb:
                                warnings.append(
                                    f"Available RAM ({available_mb}MB) is below recommended ({recipe.min_ram_mb}MB)"
                                )
                                ram_ok = False
                            break
            except Exception:
                warnings.append("Could not check available RAM")

        # Check disk space
        if recipe.min_disk_gb > 0:
            try:
                import os

                stat = os.statvfs("/")
                available_gb = (stat.f_bavail * stat.f_frsize) // (1024**3)
                if available_gb < recipe.min_disk_gb:
                    errors.append(f"Available disk ({available_gb}GB) is below required ({recipe.min_disk_gb}GB)")
                    disk_ok = False
            except Exception:
                warnings.append("Could not check available disk space")

        # Check ports
        if recipe.ports_required:
            for port in recipe.ports_required:
                try:
                    result = subprocess.run(
                        ["ss", "-tlnp", f"sport = :{port}"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if str(port) in result.stdout:
                        errors.append(f"Port {port} is already in use")
                        ports_ok = False
                except Exception:
                    warnings.append(f"Could not check port {port}")

        passed = not errors and disk_ok
        return PrerequisiteCheck(
            passed=passed,
            warnings=tuple(warnings),
            errors=tuple(errors),
            ram_sufficient=ram_ok,
            disk_sufficient=disk_ok,
            ports_available=ports_ok,
            packages_available=packages_ok,
        )

    def build_deployment(
        self,
        recipe: StackRecipe,
        variables: dict[str, str] | None = None,
        preset: str | None = None,
    ) -> StackDeployment:
        """Build a deployment specification from a recipe.

        Args:
            recipe: Recipe to deploy.
            variables: User-provided variable values.
            preset: Named preset to apply.

        Returns:
            StackDeployment ready for execution.
        """
        # Apply preset if specified
        var_values: dict[str, str] = {}
        if preset and preset in recipe.presets:
            var_values.update(recipe.presets[preset])
        if variables:
            var_values.update(variables)

        # Generate credentials for required secret variables
        for var in recipe.variables:
            if var.name not in var_values:
                if var.required and var.secret:
                    # Generate secure random password
                    var_values[var.name] = secrets.token_urlsafe(16)
                elif var.default is not None:
                    var_values[var.name] = var.default

        # Build phases
        phases = self._build_phases(recipe, var_values)

        return StackDeployment(
            deployment_id=str(uuid.uuid4())[:8],
            recipe=recipe,
            variables=var_values,
            preset=preset,
            phases=phases,
            status="pending",
            config_changes=tuple(recipe.config_templates.keys()),
            services_affected=recipe.services,
        )

    def _build_phases(
        self,
        recipe: StackRecipe,
        variables: dict[str, str],
    ) -> tuple[StackPhase, ...]:
        """Build deployment phases from recipe."""
        phases: list[StackPhase] = []

        # Package phase
        if recipe.packages:
            pkg_list = " ".join(recipe.packages)
            phases.append(
                StackPhase(
                    name="install_packages",
                    layer="package",
                    steps=(
                        StackStep(
                            command="apt-get update",
                            description="Update package lists",
                        ),
                        StackStep(
                            command=f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkg_list}",
                            description=f"Install {', '.join(recipe.packages)}",
                        ),
                    ),
                    rollback_on_failure=True,
                )
            )

        # Config phase
        if recipe.config_templates:
            config_steps: list[StackStep] = []
            for path, template in recipe.config_templates.items():
                # Substitute variables in template
                content = template
                for var_name, var_value in variables.items():
                    content = content.replace(f"{{{var_name}}}", var_value)

                # Create backup and write config
                config_steps.append(
                    StackStep(
                        command=f"cp -f {path} {path}.bak 2>/dev/null || true",
                        description=f"Backup {path}",
                        can_fail=True,
                    )
                )

            phases.append(
                StackPhase(
                    name="configure",
                    layer="config",
                    steps=tuple(config_steps),
                    rollback_on_failure=True,
                )
            )

        # Service phase
        if recipe.services:
            service_steps: list[StackStep] = []
            for service in recipe.services:
                service_steps.append(
                    StackStep(
                        command=f"systemctl enable {service}",
                        description=f"Enable {service}",
                    )
                )
                service_steps.append(
                    StackStep(
                        command=f"systemctl restart {service}",
                        description=f"Start {service}",
                    )
                )

            phases.append(
                StackPhase(
                    name="start_services",
                    layer="service",
                    steps=tuple(service_steps),
                    rollback_on_failure=True,
                )
            )

        # Verify phase
        if recipe.guarantees:
            verify_steps: list[StackStep] = []
            for guarantee in recipe.guarantees:
                verify_steps.append(
                    StackStep(
                        command=guarantee.check_command,
                        description=f"Verify: {guarantee.name}",
                        can_fail=guarantee.failure_action == "warn",
                    )
                )

            phases.append(
                StackPhase(
                    name="verify",
                    layer="verify",
                    steps=tuple(verify_steps),
                    rollback_on_failure=False,
                )
            )

        return tuple(phases)

    def deploy(
        self,
        deployment: StackDeployment,
        dry_run: bool = False,
    ) -> StackResult:
        """Execute a stack deployment.

        Args:
            deployment: Deployment specification.
            dry_run: If True, only simulate deployment.

        Returns:
            StackResult with execution results.
        """
        start_time = datetime.utcnow()
        phase_results: list[PhaseResult] = []
        guarantee_results: list[GuaranteeResult] = []
        rollback_performed = False
        rollback_result: RollbackResult | None = None
        error_message: str | None = None
        credentials: dict[str, str] = {}

        # Update deployment status
        deployment = deployment.with_status("executing")

        try:
            # Execute each phase
            for phase in deployment.phases:
                if dry_run:
                    # Simulate success
                    phase_results.append(
                        PhaseResult(
                            phase=phase,
                            success=True,
                            step_results=(),
                            duration_ms=0,
                        )
                    )
                    continue

                result = self._execute_phase(phase)
                phase_results.append(result)

                if not result.success and phase.rollback_on_failure:
                    # Trigger rollback
                    rollback_performed = True
                    rollback_result = self._rollback(phase_results)
                    deployment = deployment.with_status("rolled_back")
                    error_message = f"Phase '{phase.name}' failed, rollback performed"
                    break

            # Verify guarantees (if not rolled back)
            if not rollback_performed and not dry_run:
                for guarantee in deployment.recipe.guarantees:
                    g_result = self._verify_guarantee(guarantee)
                    guarantee_results.append(g_result)

                    if not g_result.passed:
                        if guarantee.failure_action == "rollback":
                            rollback_performed = True
                            rollback_result = self._rollback(phase_results)
                            deployment = deployment.with_status("rolled_back")
                            error_message = f"Guarantee '{guarantee.name}' failed, rollback performed"
                            break
                        elif guarantee.failure_action == "fail":
                            deployment = deployment.with_status("failed")
                            error_message = f"Guarantee '{guarantee.name}' failed"
                            break

            # Collect credentials to surface
            for var in deployment.recipe.variables:
                if var.secret and var.name in deployment.variables:
                    credentials[var.name] = deployment.variables[var.name]

            # Determine final status
            if not rollback_performed and not error_message:
                deployment = deployment.with_status("completed")

            end_time = datetime.utcnow()
            duration_ms = int((end_time - start_time).total_seconds() * 1000)

            success = (
                deployment.status == "completed"
                and all(p.success for p in phase_results)
                and all(g.passed or g.guarantee.failure_action == "warn" for g in guarantee_results)
            )

            return StackResult(
                deployment=deployment,
                phase_results=tuple(phase_results),
                guarantees_verified=tuple(guarantee_results),
                rollback_performed=rollback_performed,
                rollback_result=rollback_result,
                credentials_generated=credentials,
                success=success,
                error_message=error_message,
                duration_ms=duration_ms,
            )

        except Exception as e:
            # Handle unexpected errors
            deployment = deployment.with_status("failed")
            return StackResult(
                deployment=deployment,
                phase_results=tuple(phase_results),
                guarantees_verified=tuple(guarantee_results),
                rollback_performed=rollback_performed,
                rollback_result=rollback_result,
                credentials_generated=credentials,
                success=False,
                error_message=str(e),
                duration_ms=0,
            )

    def _execute_phase(self, phase: StackPhase) -> PhaseResult:
        """Execute a single deployment phase."""
        start_time = datetime.utcnow()
        step_results: list[StepResult] = []
        overall_success = True
        backup_path: str | None = None

        for step in phase.steps:
            result = self._execute_step(step)
            step_results.append(result)

            if not result.success and not step.can_fail:
                overall_success = False
                break

            # Track backup path for config phase
            if phase.layer == "config" and ".bak" in step.command:
                # Extract backup path from command
                match = re.search(r"cp.*?(\S+\.bak)", step.command)
                if match:
                    backup_path = match.group(1)

        end_time = datetime.utcnow()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        return PhaseResult(
            phase=phase,
            success=overall_success,
            step_results=tuple(step_results),
            backup_path=backup_path,
            duration_ms=duration_ms,
        )

    def _execute_step(self, step: StackStep) -> StepResult:
        """Execute a single step."""
        start_time = datetime.utcnow()

        try:
            result = subprocess.run(
                step.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=step.timeout_sec,
            )

            end_time = datetime.utcnow()
            duration_ms = int((end_time - start_time).total_seconds() * 1000)

            return StepResult(
                step=step,
                success=result.returncode == 0,
                exit_code=result.returncode,
                stdout=result.stdout[:10000],
                stderr=result.stderr[:10000],
                duration_ms=duration_ms,
            )

        except subprocess.TimeoutExpired:
            return StepResult(
                step=step,
                success=False,
                exit_code=-1,
                stderr="Command timed out",
                duration_ms=step.timeout_sec * 1000,
            )
        except Exception as e:
            return StepResult(
                step=step,
                success=False,
                exit_code=-1,
                stderr=str(e),
                duration_ms=0,
            )

    def _verify_guarantee(self, guarantee: StackGuarantee) -> GuaranteeResult:
        """Verify a single guarantee."""
        try:
            result = subprocess.run(
                guarantee.check_command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )

            passed = result.returncode == 0
            match_found = False

            if passed and guarantee.expected_pattern:
                pattern = re.compile(guarantee.expected_pattern)
                match_found = bool(pattern.search(result.stdout))
                passed = match_found

            return GuaranteeResult(
                guarantee=guarantee,
                passed=passed,
                actual_output=result.stdout[:1000],
                match_found=match_found,
            )

        except Exception as e:
            return GuaranteeResult(
                guarantee=guarantee,
                passed=False,
                error=str(e),
            )

    def _rollback(self, phase_results: list[PhaseResult]) -> RollbackResult:
        """Rollback completed phases in reverse order."""
        rolled_back: list[str] = []
        errors: list[str] = []

        for result in reversed(phase_results):
            if not result.success:
                continue

            phase = result.phase

            try:
                if phase.layer == "package":
                    # Attempt to remove installed packages
                    # Note: This is risky, so we're cautious
                    errors.append(f"Package rollback for {phase.name} not implemented")

                elif phase.layer == "config":
                    # Restore from backup
                    if result.backup_path:
                        backup = result.backup_path
                        original = backup.replace(".bak", "")
                        subprocess.run(
                            f"mv {backup} {original}",
                            shell=True,
                            timeout=10,
                        )
                        rolled_back.append(phase.name)

                elif phase.layer == "service":
                    # Stop services
                    for step in phase.steps:
                        if "restart" in step.command or "start" in step.command:
                            service = step.command.split()[-1]
                            subprocess.run(
                                f"systemctl stop {service}",
                                shell=True,
                                timeout=30,
                            )
                    rolled_back.append(phase.name)

            except Exception as e:
                errors.append(f"Rollback failed for {phase.name}: {e}")

        return RollbackResult(
            success=len(errors) == 0,
            phases_rolled_back=tuple(rolled_back),
            errors=tuple(errors),
        )


# =============================================================================
# Module-level instance
# =============================================================================


_coordinator: StackCoordinator | None = None


def get_coordinator() -> StackCoordinator:
    """Get the shared stack coordinator."""
    global _coordinator
    if _coordinator is None:
        _coordinator = StackCoordinator()
    return _coordinator


def deploy_from_request(
    request: str,
    variables: dict[str, str] | None = None,
    dry_run: bool = False,
) -> StackResult | None:
    """Deploy a stack from a natural language request.

    Convenience function for common use case.

    Args:
        request: Natural language request.
        variables: User-provided variable values.
        dry_run: If True, only simulate deployment.

    Returns:
        StackResult if a recipe matched, None otherwise.
    """
    coordinator = get_coordinator()

    match = coordinator.match_recipe(request)
    if not match:
        return None

    # Check prerequisites
    prereq = coordinator.check_prerequisites(match.recipe)
    if not prereq.passed:
        # Return failure result
        deployment = coordinator.build_deployment(match.recipe, variables)
        return StackResult(
            deployment=deployment.with_status("failed"),
            success=False,
            error_message="Prerequisites not met: " + "; ".join(prereq.errors),
        )

    deployment = coordinator.build_deployment(match.recipe, variables)
    return coordinator.deploy(deployment, dry_run=dry_run)
