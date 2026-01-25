"""Docker CLI utilities for ELLE.

Provides natural language Docker operations including:
- Container diagnostics and resource analysis
- docker run to compose conversion
- Container health monitoring
- Environment variable prompting and management

Usage:
    from elle.cli.docker import (
        docker_run_to_compose,
        diagnose_container_restart,
        explain_resource_usage,
        docker_run_with_env_prompt,
        prompt_for_image,
    )

    # Convert docker run to compose
    compose_yaml = docker_run_to_compose(
        "docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=secret postgres:15"
    )

    # Diagnose container restart issues
    diagnosis = diagnose_container_restart("my-container")

    # Explain resource usage
    explanation = explain_resource_usage()

    # Run with env prompting
    config = docker_run_with_env_prompt("docker run postgres:15")
"""

# Compose converter
from elle.cli.docker.compose_converter import (
    DockerRunConfig,
    docker_run_to_compose,
    docker_run_with_env_prompt,
    get_missing_env_vars,
    needs_env_prompting,
    parse_docker_run,
)

# Diagnostics
from elle.cli.docker.diagnostics import (
    ContainerDiagnosis,
    ResourceExplanation,
    diagnose_container_restart,
    explain_resource_usage,
)

# Environment variable detection
from elle.cli.docker.env_detector import (
    DockerEnvDetector,
    detect_env_vars,
    get_missing_required_vars,
)

# Environment variable explainer
from elle.cli.docker.env_explainer import (
    EnvVarExplainer,
    EnvVarExplanation,
    explain_env_var,
    get_explainer,
)

# Environment variable models
from elle.cli.docker.env_models import (
    EnvVarPromptResult,
    EnvVarSpec,
    EnvVarValue,
    ImageEnvProfile,
    SavedEnvVar,
)

# Environment variable profiles
from elle.cli.docker.env_profiles import (
    IMAGE_PROFILES,
    extract_image_family,
    get_profile,
    list_known_families,
)

# Environment variable prompting
from elle.cli.docker.env_prompt import (
    EnvVarPromptSession,
    prompt_env_vars,
    prompt_for_image,
)

# Environment variable storage
from elle.cli.docker.env_store import (
    DockerEnvStore,
    clear_saved_env_values,
    get_saved_env_values,
    save_env_values,
)

__all__ = [
    # Compose converter
    "docker_run_to_compose",
    "docker_run_with_env_prompt",
    "get_missing_env_vars",
    "needs_env_prompting",
    "parse_docker_run",
    "DockerRunConfig",
    # Diagnostics
    "diagnose_container_restart",
    "explain_resource_usage",
    "ContainerDiagnosis",
    "ResourceExplanation",
    # Environment variable models
    "EnvVarSpec",
    "EnvVarValue",
    "EnvVarPromptResult",
    "SavedEnvVar",
    "ImageEnvProfile",
    # Environment variable profiles
    "get_profile",
    "extract_image_family",
    "list_known_families",
    "IMAGE_PROFILES",
    # Environment variable detection
    "DockerEnvDetector",
    "detect_env_vars",
    "get_missing_required_vars",
    # Environment variable prompting
    "EnvVarPromptSession",
    "prompt_env_vars",
    "prompt_for_image",
    # Environment variable storage
    "DockerEnvStore",
    "save_env_values",
    "get_saved_env_values",
    "clear_saved_env_values",
    # Environment variable explainer
    "EnvVarExplainer",
    "EnvVarExplanation",
    "explain_env_var",
    "get_explainer",
]
