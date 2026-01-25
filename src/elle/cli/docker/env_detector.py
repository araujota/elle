"""Docker Environment Variable Detector.

Detects required and optional environment variables for Docker images
from multiple sources: known profiles, image metadata, and compose files.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from elle.cli.docker.env_models import EnvVarSpec
from elle.cli.docker.env_profiles import (
    extract_image_family,
    get_profile,
)

if TYPE_CHECKING:
    pass


class DockerEnvDetector:
    """Detect environment variables required by Docker images.

    Sources (in priority order):
    1. Known profiles for common images (postgres, mysql, etc.)
    2. Image metadata via `docker inspect`
    3. Compose file environment declarations

    Example:
        detector = DockerEnvDetector()

        # From image name
        specs = detector.detect_from_image("postgres:15")

        # From docker run command
        specs = detector.detect_from_command("docker run -d postgres:15")

        # From compose file
        service_specs = detector.detect_from_compose(Path("docker-compose.yml"))
    """

    def __init__(
        self,
        *,
        use_profiles: bool = True,
        use_inspect: bool = True,
        docker_timeout: float = 10.0,
    ) -> None:
        """Initialize the detector.

        Args:
            use_profiles: Whether to use known image profiles.
            use_inspect: Whether to query docker inspect for image metadata.
            docker_timeout: Timeout in seconds for docker commands.
        """
        self.use_profiles = use_profiles
        self.use_inspect = use_inspect
        self.docker_timeout = docker_timeout

    def detect_from_image(
        self,
        image: str,
        *,
        existing_env: tuple[tuple[str, str], ...] = (),
    ) -> tuple[EnvVarSpec, ...]:
        """Detect environment variables for a Docker image.

        Args:
            image: Docker image name with optional tag (e.g., "postgres:15").
            existing_env: Environment variables already set (to exclude).

        Returns:
            Tuple of EnvVarSpec for required/optional variables.
        """
        specs: list[EnvVarSpec] = []
        existing_names = {name for name, _ in existing_env}

        # 1. Check known profiles first
        if self.use_profiles:
            family = extract_image_family(image)
            profile = get_profile(family)
            if profile:
                for spec in profile.env_vars:
                    if spec.name not in existing_names:
                        specs.append(spec)
                # If we found a profile, return early (profiles are authoritative)
                return tuple(specs)

        # 2. Fall back to docker inspect
        if self.use_inspect:
            inspect_specs = self._detect_from_inspect(image)
            for spec in inspect_specs:
                if spec.name not in existing_names:
                    specs.append(spec)

        return tuple(specs)

    def detect_from_command(
        self,
        command: str,
    ) -> tuple[EnvVarSpec, ...]:
        """Detect environment variables from a docker run command.

        Parses the command to extract the image name and any existing
        environment variables, then detects additional required vars.

        Args:
            command: Docker run command string.

        Returns:
            Tuple of EnvVarSpec for variables not already in the command.
        """
        # Import here to avoid circular import
        from elle.cli.docker.compose_converter import parse_docker_run

        try:
            config = parse_docker_run(command)
        except ValueError:
            return ()

        return self.detect_from_image(
            config.image,
            existing_env=config.environment,
        )

    def detect_from_compose(
        self,
        path: Path,
    ) -> dict[str, tuple[EnvVarSpec, ...]]:
        """Detect environment variables from a docker-compose file.

        Args:
            path: Path to docker-compose.yml file.

        Returns:
            Dict mapping service names to their required EnvVarSpecs.
        """
        result: dict[str, tuple[EnvVarSpec, ...]] = {}

        try:
            with path.open() as f:
                compose = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            return result

        if not compose or "services" not in compose:
            return result

        for service_name, service_config in compose.get("services", {}).items():
            if not isinstance(service_config, dict):
                continue

            image = service_config.get("image")
            if not image:
                continue

            # Get existing environment from compose
            existing_env: list[tuple[str, str]] = []
            env = service_config.get("environment")
            if isinstance(env, dict):
                for key, value in env.items():
                    existing_env.append((key, str(value) if value else ""))
            elif isinstance(env, list):
                for item in env:
                    if "=" in item:
                        key, value = item.split("=", 1)
                        existing_env.append((key, value))
                    else:
                        existing_env.append((item, ""))

            specs = self.detect_from_image(
                image,
                existing_env=tuple(existing_env),
            )
            if specs:
                result[service_name] = specs

        return result

    def _detect_from_inspect(self, image: str) -> tuple[EnvVarSpec, ...]:
        """Detect environment variables from docker inspect.

        Args:
            image: Docker image name.

        Returns:
            Tuple of EnvVarSpec from image metadata.
        """
        specs: list[EnvVarSpec] = []

        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{json .Config.Env}}", image],
                capture_output=True,
                text=True,
                timeout=self.docker_timeout,
            )

            if result.returncode != 0:
                # Image may not be pulled locally - try pulling
                pull_result = subprocess.run(
                    ["docker", "pull", image],
                    capture_output=True,
                    text=True,
                    timeout=120.0,  # Longer timeout for pull
                )
                if pull_result.returncode != 0:
                    return ()

                # Retry inspect
                result = subprocess.run(
                    ["docker", "inspect", "--format", "{{json .Config.Env}}", image],
                    capture_output=True,
                    text=True,
                    timeout=self.docker_timeout,
                )
                if result.returncode != 0:
                    return ()

            # Parse the JSON output
            env_list = json.loads(result.stdout.strip())
            if not isinstance(env_list, list):
                return ()

            # Extract variable names that look like placeholders
            for env_str in env_list:
                if not isinstance(env_str, str):
                    continue

                if "=" in env_str:
                    name, value = env_str.split("=", 1)

                    # Skip if it has a real value (not a placeholder)
                    if value and not value.startswith("$"):
                        continue

                    # Detect if it looks like a required variable
                    is_sensitive = any(
                        kw in name.upper()
                        for kw in ("PASSWORD", "SECRET", "KEY", "TOKEN", "CREDENTIAL")
                    )

                    specs.append(
                        EnvVarSpec(
                            name=name,
                            required=False,  # Can't determine required from inspect
                            sensitive=is_sensitive,
                            description="",
                            source="image",
                        )
                    )

        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            pass

        return tuple(specs)

    def get_missing_required(
        self,
        image: str,
        existing_env: tuple[tuple[str, str], ...],
    ) -> tuple[EnvVarSpec, ...]:
        """Get only the required variables that are missing.

        Args:
            image: Docker image name.
            existing_env: Currently set environment variables.

        Returns:
            Tuple of required EnvVarSpecs that are not in existing_env.
        """
        all_specs = self.detect_from_image(image, existing_env=existing_env)
        return tuple(spec for spec in all_specs if spec.required)

    def needs_prompting(
        self,
        image: str,
        existing_env: tuple[tuple[str, str], ...],
    ) -> bool:
        """Check if an image needs environment variable prompting.

        Returns True if there are required variables missing or if there
        are known optional variables that haven't been set.

        Args:
            image: Docker image name.
            existing_env: Currently set environment variables.

        Returns:
            True if prompting would be useful, False otherwise.
        """
        specs = self.detect_from_image(image, existing_env=existing_env)

        # Need prompting if any required vars are missing
        if any(spec.required for spec in specs):
            return True

        # Also prompt if we have known optional vars for common images
        if self.use_profiles:
            family = extract_image_family(image)
            profile = get_profile(family)
            if profile and profile.has_required_vars():
                return True

        return False


# Module-level convenience functions


def detect_env_vars(
    image: str,
    *,
    existing_env: tuple[tuple[str, str], ...] = (),
) -> tuple[EnvVarSpec, ...]:
    """Detect environment variables for a Docker image.

    Convenience function using default detector settings.

    Args:
        image: Docker image name with optional tag.
        existing_env: Environment variables already set.

    Returns:
        Tuple of EnvVarSpec for required/optional variables.
    """
    detector = DockerEnvDetector()
    return detector.detect_from_image(image, existing_env=existing_env)


def get_missing_required_vars(
    image: str,
    existing_env: tuple[tuple[str, str], ...] = (),
) -> tuple[EnvVarSpec, ...]:
    """Get required environment variables that are missing.

    Args:
        image: Docker image name.
        existing_env: Currently set environment variables.

    Returns:
        Tuple of required EnvVarSpecs not in existing_env.
    """
    detector = DockerEnvDetector()
    return detector.get_missing_required(image, existing_env)
