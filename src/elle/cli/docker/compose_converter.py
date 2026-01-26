"""Convert docker run commands to docker-compose.yml format.

Parses docker run command flags and generates equivalent
docker-compose.yml service definitions.

Example:
    Input: docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=secret postgres:15
    Output:
        services:
          postgres:
            image: postgres:15
            ports:
              - "5432:5432"
            environment:
              POSTGRES_PASSWORD: secret
"""

from __future__ import annotations

import io
import shlex
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from rich.console import Console


class DockerRunConfig(BaseModel):
    """Parsed docker run configuration.

    Captures all relevant flags from a docker run command
    for conversion to docker-compose format.
    """

    model_config = ConfigDict(frozen=True)

    image: str = Field(description="Docker image name with optional tag")
    name: str | None = Field(default=None, description="Container name")
    ports: tuple[str, ...] = Field(default_factory=tuple, description="Port mappings")
    volumes: tuple[str, ...] = Field(default_factory=tuple, description="Volume mounts")
    environment: tuple[tuple[str, str], ...] = Field(
        default_factory=tuple,
        description="Environment variables as (key, value) pairs",
    )
    env_files: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Environment files",
    )
    networks: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Network names",
    )
    restart: str | None = Field(default=None, description="Restart policy")
    detached: bool = Field(default=False, description="Run in background")
    privileged: bool = Field(default=False, description="Privileged mode")
    user: str | None = Field(default=None, description="User to run as")
    workdir: str | None = Field(default=None, description="Working directory")
    entrypoint: str | None = Field(default=None, description="Override entrypoint")
    command: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Command to run",
    )
    labels: tuple[tuple[str, str], ...] = Field(
        default_factory=tuple,
        description="Container labels",
    )
    memory: str | None = Field(default=None, description="Memory limit")
    cpus: str | None = Field(default=None, description="CPU limit")
    hostname: str | None = Field(default=None, description="Container hostname")
    depends_on: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Service dependencies",
    )
    healthcheck: dict[str, Any] | None = Field(
        default=None,
        description="Health check configuration",
    )
    cap_add: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Added capabilities",
    )
    cap_drop: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Dropped capabilities",
    )


def parse_docker_run(command: str) -> DockerRunConfig:
    """Parse a docker run command into structured configuration.

    Args:
        command: The docker run command string.

    Returns:
        DockerRunConfig with parsed values.

    Raises:
        ValueError: If the command is not a valid docker run command.
    """
    # Normalize command - handle both 'docker run' and just the args
    command = command.strip()
    if command.startswith("docker run"):
        command = command[10:].strip()
    elif command.startswith("docker "):
        raise ValueError("Command must be 'docker run', not other docker commands")

    # Parse into tokens
    try:
        tokens = shlex.split(command)
    except ValueError as e:
        raise ValueError(f"Failed to parse command: {e}") from e

    if not tokens:
        raise ValueError("Empty docker run command")

    # Parse flags and image
    ports: list[str] = []
    volumes: list[str] = []
    environment: list[tuple[str, str]] = []
    env_files: list[str] = []
    networks: list[str] = []
    labels: list[tuple[str, str]] = []
    cap_add: list[str] = []
    cap_drop: list[str] = []

    name: str | None = None
    restart: str | None = None
    detached = False
    privileged = False
    user: str | None = None
    workdir: str | None = None
    entrypoint: str | None = None
    memory: str | None = None
    cpus: str | None = None
    hostname: str | None = None
    healthcheck: dict[str, Any] | None = None

    image: str | None = None
    cmd_args: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]

        # Short flags that can be combined (like -d, -it)
        if token.startswith("-") and not token.startswith("--"):
            if "d" in token:
                detached = True
            if "p" in token and len(token) == 2 and i + 1 < len(tokens) or token == "-p" and i + 1 < len(tokens):
                i += 1
                ports.append(tokens[i])
            elif "v" in token and len(token) == 2 and i + 1 < len(tokens) or token == "-v" and i + 1 < len(tokens):
                i += 1
                volumes.append(tokens[i])
            elif "e" in token and len(token) == 2 and i + 1 < len(tokens) or token == "-e" and i + 1 < len(tokens):
                i += 1
                env_var = tokens[i]
                if "=" in env_var:
                    key, val = env_var.split("=", 1)
                    environment.append((key, val))
                else:
                    environment.append((env_var, ""))
            elif "u" in token and len(token) == 2 and i + 1 < len(tokens):
                i += 1
                user = tokens[i]
            elif "w" in token and len(token) == 2 and i + 1 < len(tokens):
                i += 1
                workdir = tokens[i]
            elif "m" in token and len(token) == 2 and i + 1 < len(tokens):
                i += 1
                memory = tokens[i]
            elif "h" in token and len(token) == 2 and i + 1 < len(tokens):
                i += 1
                hostname = tokens[i]
            elif "l" in token and len(token) == 2 and i + 1 < len(tokens):
                i += 1
                label = tokens[i]
                if "=" in label:
                    key, val = label.split("=", 1)
                    labels.append((key, val))
            i += 1
            continue

        # Long flags
        if token.startswith("--"):
            flag = token[2:]

            if "=" in flag:
                flag_name, flag_value = flag.split("=", 1)
            else:
                flag_name = flag
                flag_value = tokens[i + 1] if i + 1 < len(tokens) else None

            if flag_name == "name":
                name = flag_value
                if "=" not in flag:
                    i += 1
            elif flag_name == "publish" or flag_name == "p":
                ports.append(flag_value or "")
                if "=" not in flag:
                    i += 1
            elif flag_name == "volume" or flag_name == "v":
                volumes.append(flag_value or "")
                if "=" not in flag:
                    i += 1
            elif flag_name == "env":
                if flag_value and "=" in flag_value:
                    key, val = flag_value.split("=", 1)
                    environment.append((key, val))
                elif flag_value:
                    environment.append((flag_value, ""))
                if "=" not in flag:
                    i += 1
            elif flag_name == "env-file":
                env_files.append(flag_value or "")
                if "=" not in flag:
                    i += 1
            elif flag_name == "network" or flag_name == "net":
                networks.append(flag_value or "")
                if "=" not in flag:
                    i += 1
            elif flag_name == "restart":
                restart = flag_value
                if "=" not in flag:
                    i += 1
            elif flag_name == "detach":
                detached = True
            elif flag_name == "privileged":
                privileged = True
            elif flag_name == "user":
                user = flag_value
                if "=" not in flag:
                    i += 1
            elif flag_name == "workdir":
                workdir = flag_value
                if "=" not in flag:
                    i += 1
            elif flag_name == "entrypoint":
                entrypoint = flag_value
                if "=" not in flag:
                    i += 1
            elif flag_name == "memory":
                memory = flag_value
                if "=" not in flag:
                    i += 1
            elif flag_name == "cpus":
                cpus = flag_value
                if "=" not in flag:
                    i += 1
            elif flag_name == "hostname":
                hostname = flag_value
                if "=" not in flag:
                    i += 1
            elif flag_name == "label":
                if flag_value and "=" in flag_value:
                    key, val = flag_value.split("=", 1)
                    labels.append((key, val))
                if "=" not in flag:
                    i += 1
            elif flag_name == "cap-add":
                cap_add.append(flag_value or "")
                if "=" not in flag:
                    i += 1
            elif flag_name == "cap-drop":
                cap_drop.append(flag_value or "")
                if "=" not in flag:
                    i += 1
            elif flag_name == "health-cmd":
                healthcheck = healthcheck or {}
                healthcheck["test"] = ["CMD-SHELL", flag_value]
                if "=" not in flag:
                    i += 1
            elif flag_name == "health-interval":
                healthcheck = healthcheck or {}
                healthcheck["interval"] = flag_value
                if "=" not in flag:
                    i += 1
            elif flag_name == "health-timeout":
                healthcheck = healthcheck or {}
                healthcheck["timeout"] = flag_value
                if "=" not in flag:
                    i += 1
            elif flag_name == "health-retries":
                healthcheck = healthcheck or {}
                healthcheck["retries"] = int(flag_value) if flag_value else 3
                if "=" not in flag:
                    i += 1

            i += 1
            continue

        # Not a flag - this is the image or command
        if image is None:
            image = token
        else:
            cmd_args.append(token)

        i += 1

    if not image:
        raise ValueError("No image specified in docker run command")

    return DockerRunConfig(
        image=image,
        name=name,
        ports=tuple(ports),
        volumes=tuple(volumes),
        environment=tuple(environment),
        env_files=tuple(env_files),
        networks=tuple(networks),
        restart=restart,
        detached=detached,
        privileged=privileged,
        user=user,
        workdir=workdir,
        entrypoint=entrypoint,
        command=tuple(cmd_args),
        labels=tuple(labels),
        memory=memory,
        cpus=cpus,
        hostname=hostname,
        healthcheck=healthcheck,
        cap_add=tuple(cap_add),
        cap_drop=tuple(cap_drop),
    )


def docker_run_to_compose(
    command: str,
    service_name: str | None = None,
) -> str:
    """Convert a docker run command to docker-compose.yml format.

    Args:
        command: The docker run command string.
        service_name: Optional service name. Defaults to image name without tag.

    Returns:
        YAML string for docker-compose.yml.

    Example:
        >>> docker_run_to_compose(
        ...     "docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=secret postgres:15"
        ... )
        services:
          postgres:
            image: postgres:15
            ports:
              - "5432:5432"
            environment:
              POSTGRES_PASSWORD: secret
    """
    config = parse_docker_run(command)

    # Determine service name
    if service_name:
        svc_name = service_name
    elif config.name:
        svc_name = config.name
    else:
        # Extract name from image (e.g., "postgres:15" -> "postgres")
        svc_name = config.image.split(":")[0].split("/")[-1]

    # Build service definition
    service: dict[str, Any] = {
        "image": config.image,
    }

    # Add container name if specified
    if config.name:
        service["container_name"] = config.name

    # Add ports
    if config.ports:
        service["ports"] = [f'"{p}"' if ":" in p else p for p in config.ports]
        # Convert to plain list for YAML
        service["ports"] = list(config.ports)

    # Add volumes
    if config.volumes:
        service["volumes"] = list(config.volumes)

    # Add environment
    if config.environment:
        service["environment"] = {}
        for key, val in config.environment:
            service["environment"][key] = val

    # Add env_file
    if config.env_files:
        service["env_file"] = list(config.env_files)

    # Add networks
    if config.networks:
        service["networks"] = list(config.networks)

    # Add restart policy
    if config.restart:
        service["restart"] = config.restart
    elif config.detached:
        service["restart"] = "unless-stopped"

    # Add privileged mode
    if config.privileged:
        service["privileged"] = True

    # Add user
    if config.user:
        service["user"] = config.user

    # Add working directory
    if config.workdir:
        service["working_dir"] = config.workdir

    # Add entrypoint
    if config.entrypoint:
        service["entrypoint"] = config.entrypoint

    # Add command
    if config.command:
        if len(config.command) == 1:
            service["command"] = config.command[0]
        else:
            service["command"] = list(config.command)

    # Add labels
    if config.labels:
        service["labels"] = {}
        for key, val in config.labels:
            service["labels"][key] = val

    # Add hostname
    if config.hostname:
        service["hostname"] = config.hostname

    # Add healthcheck
    if config.healthcheck:
        service["healthcheck"] = config.healthcheck

    # Add capabilities
    if config.cap_add:
        service["cap_add"] = list(config.cap_add)
    if config.cap_drop:
        service["cap_drop"] = list(config.cap_drop)

    # Add resource limits
    if config.memory or config.cpus:
        service["deploy"] = {"resources": {"limits": {}}}
        if config.memory:
            service["deploy"]["resources"]["limits"]["memory"] = config.memory
        if config.cpus:
            service["deploy"]["resources"]["limits"]["cpus"] = config.cpus

    # Build compose structure
    compose: dict[str, Any] = {
        "services": {
            svc_name: service,
        }
    }

    # Add networks section if custom networks used
    if config.networks:
        compose["networks"] = {}
        for net in config.networks:
            compose["networks"][net] = {"external": True}

    # Generate YAML
    yml = YAML()
    yml.default_flow_style = False
    stream = io.StringIO()
    yml.dump(compose, stream)
    return stream.getvalue()


def docker_run_with_env_prompt(
    command: str,
    *,
    console: Console | None = None,
    skip_prompt: bool = False,
    use_saved: bool = True,
    save_values: bool = True,
) -> DockerRunConfig:
    """Parse a docker run command and prompt for missing environment variables.

    Detects required environment variables from the image and prompts the
    user to enter values for any that aren't already specified in the command.

    Args:
        command: The docker run command string.
        console: Rich console for output (creates new if None).
        skip_prompt: If True, skip prompting and return as-is.
        use_saved: Whether to offer previously saved values.
        save_values: Whether to save entered values for future use.

    Returns:
        DockerRunConfig with environment variables populated.

    Raises:
        ValueError: If the command is not a valid docker run command.

    Example:
        >>> config = docker_run_with_env_prompt("docker run postgres:15")
        # User is prompted for POSTGRES_PASSWORD
        >>> print(config.environment)
        (('POSTGRES_PASSWORD', 'secret'),)
    """
    from elle.cli.docker.env_detector import DockerEnvDetector
    from elle.cli.docker.env_profiles import extract_image_family, get_profile
    from elle.cli.docker.env_prompt import prompt_env_vars

    # Parse the command first
    config = parse_docker_run(command)

    if skip_prompt:
        return config

    # Detect environment variables
    detector = DockerEnvDetector()
    specs = detector.detect_from_image(config.image, existing_env=config.environment)

    if not specs:
        # No env vars to prompt for
        return config

    # Check if we have any required vars missing
    has_missing_required = any(spec.required for spec in specs)

    # Also check if the image has known required vars
    family = extract_image_family(config.image)
    profile = get_profile(family)
    if profile and profile.has_required_vars():
        has_missing_required = True

    # Only prompt if there are required vars or this is a well-known image
    if not has_missing_required and not profile:
        return config

    # Run the prompting session
    result = prompt_env_vars(
        specs=specs,
        image=config.image,
        console=console,
        use_saved=use_saved,
        save_values=save_values,
    )

    if result.cancelled:
        # User cancelled - return original config
        return config

    if not result.values:
        # No values entered
        return config

    # Merge the new values with existing environment
    existing_env = dict(config.environment)
    for env_value in result.values:
        existing_env[env_value.name] = env_value.value

    # Create new config with merged environment
    return DockerRunConfig(
        image=config.image,
        name=config.name,
        ports=config.ports,
        volumes=config.volumes,
        environment=tuple(existing_env.items()),
        env_files=config.env_files,
        networks=config.networks,
        restart=config.restart,
        detached=config.detached,
        privileged=config.privileged,
        user=config.user,
        workdir=config.workdir,
        entrypoint=config.entrypoint,
        command=config.command,
        labels=config.labels,
        memory=config.memory,
        cpus=config.cpus,
        hostname=config.hostname,
        depends_on=config.depends_on,
        healthcheck=config.healthcheck,
        cap_add=config.cap_add,
        cap_drop=config.cap_drop,
    )


def needs_env_prompting(command: str) -> bool:
    """Check if a docker run command needs environment variable prompting.

    Args:
        command: The docker run command string.

    Returns:
        True if prompting would be useful, False otherwise.
    """
    from elle.cli.docker.env_detector import DockerEnvDetector

    try:
        config = parse_docker_run(command)
    except ValueError:
        return False

    detector = DockerEnvDetector()
    return detector.needs_prompting(config.image, config.environment)


def get_missing_env_vars(command: str) -> list[str]:
    """Get a list of missing required environment variable names.

    Args:
        command: The docker run command string.

    Returns:
        List of variable names that are required but not set.
    """
    from elle.cli.docker.env_detector import DockerEnvDetector

    try:
        config = parse_docker_run(command)
    except ValueError:
        return []

    detector = DockerEnvDetector()
    specs = detector.get_missing_required(config.image, config.environment)
    return [spec.name for spec in specs]
