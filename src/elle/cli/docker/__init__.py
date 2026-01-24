"""Docker CLI utilities for ELLE.

Provides natural language Docker operations including:
- Container diagnostics and resource analysis
- docker run to compose conversion
- Container health monitoring

Usage:
    from elle.cli.docker import (
        docker_run_to_compose,
        diagnose_container_restart,
        explain_resource_usage,
    )

    # Convert docker run to compose
    compose_yaml = docker_run_to_compose(
        "docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=secret postgres:15"
    )

    # Diagnose container restart issues
    diagnosis = diagnose_container_restart("my-container")

    # Explain resource usage
    explanation = explain_resource_usage()
"""

from elle.cli.docker.compose_converter import (
    docker_run_to_compose,
    parse_docker_run,
    DockerRunConfig,
)
from elle.cli.docker.diagnostics import (
    diagnose_container_restart,
    explain_resource_usage,
    ContainerDiagnosis,
    ResourceExplanation,
)

__all__ = [
    # Compose converter
    "docker_run_to_compose",
    "parse_docker_run",
    "DockerRunConfig",
    # Diagnostics
    "diagnose_container_restart",
    "explain_resource_usage",
    "ContainerDiagnosis",
    "ResourceExplanation",
]
