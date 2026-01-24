"""Docker domain handler for configuration generation.

Handles docker-compose.yml, compose.yaml, and Dockerfile generation
using natural language requests.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from elle.rag.confgen.models import (
    ConfigDomain,
    ConfigFileType,
    ConfigGenValidation,
    ConfigOp,
    ValidationIssue,
)
from elle.rag.confgen.domains import DomainHandler


class DockerHandler(DomainHandler):
    """Handler for Docker/container configuration.

    Supports generation of:
    - docker-compose.yml / compose.yaml files
    - Dockerfiles
    - Container configuration

    Usage:
        handler = DockerHandler()
        if handler.matches_path("/path/to/docker-compose.yml"):
            validation = handler.validate_content(content)
    """

    @property
    def domain(self) -> ConfigDomain:
        return "docker"

    @property
    def file_patterns(self) -> tuple[str, ...]:
        return (
            "**/docker-compose.yml",
            "**/docker-compose.yaml",
            "**/compose.yml",
            "**/compose.yaml",
            "**/Dockerfile",
            "**/Dockerfile.*",
        )

    @property
    def file_type(self) -> ConfigFileType:
        return "yaml"

    def matches_path(self, path: Path | str) -> bool:
        """Check if a path matches Docker configuration patterns."""
        path = Path(path)
        name = path.name.lower()

        # Check for docker-compose files
        if name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            return True

        # Check for Dockerfiles
        if name == "dockerfile" or name.startswith("dockerfile."):
            return True

        return False

    def get_schema_hint(self, file_path: Path | str) -> dict[str, Any] | None:
        """Get schema hints for Docker configuration files."""
        path = Path(file_path)
        name = path.name.lower()

        # Dockerfile schema
        if name == "dockerfile" or name.startswith("dockerfile."):
            return {
                "instructions": [
                    "FROM <image>:<tag>",
                    "WORKDIR <path>",
                    "COPY <src> <dest>",
                    "RUN <command>",
                    "ENV <key>=<value>",
                    "EXPOSE <port>",
                    "CMD [<args>]",
                    "ENTRYPOINT [<command>]",
                    "USER <user>",
                    "VOLUME [<path>]",
                    "ARG <name>[=<default>]",
                    "HEALTHCHECK CMD <command>",
                ]
            }

        # docker-compose.yml schema
        return {
            "version": "3.8",
            "services": {
                "<service_name>": {
                    "image": "<image>:<tag>",
                    "build": {
                        "context": ".",
                        "dockerfile": "Dockerfile",
                    },
                    "container_name": "<name>",
                    "ports": ["<host>:<container>"],
                    "volumes": ["<host>:<container>"],
                    "environment": {
                        "<KEY>": "<value>",
                    },
                    "env_file": [".env"],
                    "depends_on": ["<service>"],
                    "networks": ["<network>"],
                    "restart": "unless-stopped|always|no|on-failure",
                    "healthcheck": {
                        "test": ["CMD", "<command>"],
                        "interval": "30s",
                        "timeout": "10s",
                        "retries": 3,
                    },
                    "deploy": {
                        "resources": {
                            "limits": {"cpus": "0.5", "memory": "512M"},
                            "reservations": {"cpus": "0.25", "memory": "256M"},
                        },
                    },
                },
            },
            "volumes": {
                "<volume_name>": {"driver": "local"},
            },
            "networks": {
                "<network_name>": {"driver": "bridge"},
            },
        }

    def validate_content(self, content: str) -> ConfigGenValidation:
        """Validate Docker configuration content."""
        issues: list[ValidationIssue] = []

        # Check if it looks like a Dockerfile
        # Dockerfile indicators: FROM, RUN, CMD, ENTRYPOINT at line starts
        dockerfile_patterns = ["FROM ", "RUN ", "CMD ", "ENTRYPOINT ", "COPY ", "WORKDIR "]
        content_stripped = content.strip()

        is_dockerfile = any(
            content_stripped.startswith(p) or f"\n{p}" in content
            for p in dockerfile_patterns
        )

        if is_dockerfile:
            return self._validate_dockerfile(content)

        # Otherwise validate as docker-compose.yml
        return self._validate_compose(content)

    def _validate_dockerfile(self, content: str) -> ConfigGenValidation:
        """Validate Dockerfile content."""
        issues: list[ValidationIssue] = []
        lines = content.split("\n")

        has_from = False
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Check for FROM instruction
            if line.upper().startswith("FROM "):
                has_from = True

            # Check for ADD vs COPY (prefer COPY)
            if line.upper().startswith("ADD ") and "http" not in line.lower():
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        message=f"Line {i}: Consider using COPY instead of ADD for local files",
                        suggestion="COPY is preferred unless you need ADD's special features",
                    )
                )

            # Check for apt-get without cleanup
            if "apt-get install" in line and "rm -rf /var/lib/apt/lists" not in content:
                issues.append(
                    ValidationIssue(
                        severity="info",
                        message="apt-get install without cleanup",
                        suggestion="Add 'rm -rf /var/lib/apt/lists/*' to reduce image size",
                    )
                )

            # Check for running as root warning
            if line.upper().startswith("USER ROOT"):
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        message=f"Line {i}: Running as root user",
                        suggestion="Consider creating a non-root user for security",
                    )
                )

        if not has_from:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message="Dockerfile must start with a FROM instruction",
                )
            )

        return ConfigGenValidation(
            valid=not any(i.severity == "error" for i in issues),
            issues=tuple(issues),
        )

    def _validate_compose(self, content: str) -> ConfigGenValidation:
        """Validate docker-compose.yml content."""
        issues: list[ValidationIssue] = []

        try:
            import yaml
        except ImportError:
            return ConfigGenValidation(valid=True)

        try:
            data = yaml.safe_load(content)

            if not isinstance(data, dict):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message="Content is not a valid YAML mapping",
                    )
                )
                return ConfigGenValidation(valid=False, issues=tuple(issues))

            # Check for services key
            if "services" not in data:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message="Missing required 'services' key",
                        suggestion="Add 'services:' section to define containers",
                    )
                )

            # Validate services
            services = data.get("services", {})
            if isinstance(services, dict):
                for name, service in services.items():
                    if not isinstance(service, dict):
                        continue

                    # Check for image or build
                    if "image" not in service and "build" not in service:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                message=f"Service '{name}' must have 'image' or 'build'",
                            )
                        )

                    # Check port format
                    ports = service.get("ports", [])
                    for port in ports:
                        if isinstance(port, str) and ":" not in port:
                            issues.append(
                                ValidationIssue(
                                    severity="warning",
                                    message=f"Service '{name}': port '{port}' exposes to all interfaces",
                                    suggestion="Use 'host:container' format (e.g., '127.0.0.1:8080:80')",
                                )
                            )

                    # Check for privileged mode
                    if service.get("privileged"):
                        issues.append(
                            ValidationIssue(
                                severity="warning",
                                message=f"Service '{name}' runs in privileged mode",
                                suggestion="Consider using specific capabilities instead",
                            )
                        )

        except yaml.YAMLError as e:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=f"Invalid YAML syntax: {e}",
                )
            )

        return ConfigGenValidation(
            valid=not any(i.severity == "error" for i in issues),
            issues=tuple(issues),
            syntax_valid=not any(
                i.severity == "error" and "syntax" in i.message.lower()
                for i in issues
            ),
        )

    def get_template(self) -> str | None:
        """Get a template for docker-compose.yml."""
        return """services:
  app:
    image: nginx:alpine
    ports:
      - "8080:80"
    volumes:
      - ./html:/usr/share/nginx/html:ro
    restart: unless-stopped
"""

    def get_validation_command(self, file_path: Path | str) -> str | None:
        """Get command to validate Docker configuration."""
        path = Path(file_path)
        name = path.name.lower()

        if name == "dockerfile" or name.startswith("dockerfile."):
            return f"docker build --check -f {path} ."

        # docker-compose validation
        return f"docker compose -f {path} config --quiet"
