from __future__ import annotations

"""Comprehensive tests for the docker compose_converter module.

Tests cover:
- DockerRunConfig model creation and frozen behavior
- parse_docker_run: all flag types, short flags, long flags, equals syntax,
  edge cases, error paths
- docker_run_to_compose: YAML generation with all optional fields,
  service name resolution, resource limits, networks section
- docker_run_with_env_prompt: mocked env detection, prompting, cancellation,
  skip, no specs, merge logic, profile-based required detection
- needs_env_prompting: mocked detector, parse error handling
- get_missing_env_vars: mocked detector, parse error handling
"""

from unittest.mock import MagicMock, patch

import pytest

from elle.cli.docker.compose_converter import (
    DockerRunConfig,
    docker_run_to_compose,
    docker_run_with_env_prompt,
    get_missing_env_vars,
    needs_env_prompting,
    parse_docker_run,
)

# ---------------------------------------------------------------------------
# DockerRunConfig model
# ---------------------------------------------------------------------------


class TestDockerRunConfig:
    """Tests for the DockerRunConfig Pydantic model."""

    def test_create_minimal_config(self):
        config = DockerRunConfig(image="nginx")
        assert config.image == "nginx"
        assert config.name is None
        assert config.ports == ()
        assert config.volumes == ()
        assert config.environment == ()
        assert config.env_files == ()
        assert config.networks == ()
        assert config.restart is None
        assert config.detached is False
        assert config.privileged is False
        assert config.user is None
        assert config.workdir is None
        assert config.entrypoint is None
        assert config.command == ()
        assert config.labels == ()
        assert config.memory is None
        assert config.cpus is None
        assert config.hostname is None
        assert config.depends_on == ()
        assert config.healthcheck is None
        assert config.cap_add == ()
        assert config.cap_drop == ()

    def test_create_full_config(self):
        config = DockerRunConfig(
            image="postgres:15",
            name="mydb",
            ports=("5432:5432",),
            volumes=("/data:/var/lib/postgresql/data",),
            environment=(("POSTGRES_PASSWORD", "secret"),),
            env_files=(".env",),
            networks=("backend",),
            restart="always",
            detached=True,
            privileged=False,
            user="1000:1000",
            workdir="/app",
            entrypoint="/docker-entrypoint.sh",
            command=("postgres", "-c", "max_connections=100"),
            labels=(("tier", "db"),),
            memory="512m",
            cpus="0.5",
            hostname="dbhost",
            depends_on=("redis",),
            healthcheck={"test": ["CMD-SHELL", "pg_isready"], "interval": "10s"},
            cap_add=("SYS_ADMIN",),
            cap_drop=("NET_RAW",),
        )
        assert config.image == "postgres:15"
        assert config.name == "mydb"
        assert len(config.ports) == 1
        assert len(config.volumes) == 1
        assert len(config.environment) == 1
        assert config.restart == "always"
        assert config.detached is True
        assert config.user == "1000:1000"
        assert config.workdir == "/app"
        assert config.entrypoint == "/docker-entrypoint.sh"
        assert len(config.command) == 3
        assert config.memory == "512m"
        assert config.cpus == "0.5"
        assert config.hostname == "dbhost"
        assert config.healthcheck is not None
        assert config.cap_add == ("SYS_ADMIN",)
        assert config.cap_drop == ("NET_RAW",)

    def test_config_is_frozen(self):
        config = DockerRunConfig(image="nginx")
        with pytest.raises(Exception):
            config.image = "alpine"


# ---------------------------------------------------------------------------
# parse_docker_run - error paths
# ---------------------------------------------------------------------------


class TestParseDockerRunErrors:
    """Tests for parse_docker_run error handling."""

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError, match="Empty docker run command"):
            parse_docker_run("")

    def test_raises_on_whitespace_only(self):
        with pytest.raises(ValueError, match="Empty docker run command"):
            parse_docker_run("   ")

    def test_raises_on_non_run_docker_command(self):
        with pytest.raises(ValueError, match="must be 'docker run'"):
            parse_docker_run("docker ps")

    def test_raises_on_docker_build(self):
        with pytest.raises(ValueError, match="must be 'docker run'"):
            parse_docker_run("docker build .")

    def test_raises_on_no_image(self):
        with pytest.raises(ValueError, match="No image specified"):
            parse_docker_run("-d -p 80:80")

    def test_raises_on_flags_only(self):
        with pytest.raises(ValueError, match="No image specified"):
            parse_docker_run("--detach --privileged")

    def test_raises_on_unterminated_quote(self):
        with pytest.raises(ValueError, match="Failed to parse command"):
            parse_docker_run("-e 'FOO=bar nginx")

    def test_docker_run_prefix_stripped(self):
        config = parse_docker_run("docker run nginx")
        assert config.image == "nginx"

    def test_docker_run_prefix_with_spaces(self):
        config = parse_docker_run("docker run   nginx")
        assert config.image == "nginx"


# ---------------------------------------------------------------------------
# parse_docker_run - short flags
# ---------------------------------------------------------------------------


class TestParseDockerRunShortFlags:
    """Tests for short flag parsing (-d, -p, -e, -v, etc.)."""

    def test_detached_short_flag(self):
        config = parse_docker_run("-d nginx")
        assert config.detached is True

    def test_port_short_flag(self):
        config = parse_docker_run("-p 8080:80 nginx")
        assert "8080:80" in config.ports

    def test_volume_short_flag(self):
        config = parse_docker_run("-v /host:/container nginx")
        assert "/host:/container" in config.volumes

    def test_env_short_flag_with_value(self):
        config = parse_docker_run("-e FOO=bar nginx")
        assert ("FOO", "bar") in config.environment

    def test_env_short_flag_without_value(self):
        config = parse_docker_run("-e JUST_NAME nginx")
        assert ("JUST_NAME", "") in config.environment

    def test_user_short_flag(self):
        config = parse_docker_run("-u 1000 nginx")
        assert config.user == "1000"

    def test_workdir_short_flag(self):
        config = parse_docker_run("-w /app node")
        assert config.workdir == "/app"

    def test_memory_short_flag(self):
        config = parse_docker_run("-m 256m nginx")
        assert config.memory == "256m"

    def test_hostname_short_flag(self):
        config = parse_docker_run("-h myhost nginx")
        assert config.hostname == "myhost"

    def test_label_short_flag_with_value(self):
        config = parse_docker_run("-l tier=web nginx")
        assert ("tier", "web") in config.labels

    def test_multiple_short_flags(self):
        config = parse_docker_run("-d -p 80:80 -e FOO=bar nginx")
        assert config.detached is True
        assert "80:80" in config.ports
        assert ("FOO", "bar") in config.environment

    def test_multiple_ports(self):
        config = parse_docker_run("-p 80:80 -p 443:443 nginx")
        assert len(config.ports) == 2
        assert "80:80" in config.ports
        assert "443:443" in config.ports

    def test_multiple_volumes(self):
        config = parse_docker_run("-v /a:/a -v /b:/b nginx")
        assert len(config.volumes) == 2

    def test_multiple_env_vars(self):
        config = parse_docker_run("-e A=1 -e B=2 -e C=3 nginx")
        assert len(config.environment) == 3


# ---------------------------------------------------------------------------
# parse_docker_run - long flags (space-separated)
# ---------------------------------------------------------------------------


class TestParseDockerRunLongFlags:
    """Tests for long flag parsing (--name, --restart, etc.)."""

    def test_name_flag(self):
        config = parse_docker_run("--name mycontainer nginx")
        assert config.name == "mycontainer"

    def test_publish_flag(self):
        config = parse_docker_run("--publish 8080:80 nginx")
        assert "8080:80" in config.ports

    def test_volume_long_flag(self):
        config = parse_docker_run("--volume /host:/container nginx")
        assert "/host:/container" in config.volumes

    def test_env_long_flag(self):
        config = parse_docker_run("--env DB_HOST=localhost nginx")
        assert ("DB_HOST", "localhost") in config.environment

    def test_env_long_flag_no_value(self):
        config = parse_docker_run("--env MYVAR nginx")
        assert ("MYVAR", "") in config.environment

    def test_env_file_flag(self):
        config = parse_docker_run("--env-file .env nginx")
        assert ".env" in config.env_files

    def test_network_flag(self):
        config = parse_docker_run("--network mynet nginx")
        assert "mynet" in config.networks

    def test_net_alias_flag(self):
        config = parse_docker_run("--net mynet nginx")
        assert "mynet" in config.networks

    def test_restart_flag(self):
        config = parse_docker_run("--restart unless-stopped nginx")
        assert config.restart == "unless-stopped"

    def test_detach_long_flag(self):
        config = parse_docker_run("--detach nginx")
        assert config.detached is True

    def test_privileged_long_flag(self):
        config = parse_docker_run("--privileged nginx")
        assert config.privileged is True

    def test_user_long_flag(self):
        config = parse_docker_run("--user 1000:1000 nginx")
        assert config.user == "1000:1000"

    def test_workdir_long_flag(self):
        config = parse_docker_run("--workdir /app nginx")
        assert config.workdir == "/app"

    def test_entrypoint_long_flag(self):
        config = parse_docker_run("--entrypoint /bin/sh nginx")
        assert config.entrypoint == "/bin/sh"

    def test_memory_long_flag(self):
        config = parse_docker_run("--memory 1g nginx")
        assert config.memory == "1g"

    def test_cpus_long_flag(self):
        config = parse_docker_run("--cpus 2.0 nginx")
        assert config.cpus == "2.0"

    def test_hostname_long_flag(self):
        config = parse_docker_run("--hostname webhost nginx")
        assert config.hostname == "webhost"

    def test_label_long_flag(self):
        config = parse_docker_run("--label env=prod nginx")
        assert ("env", "prod") in config.labels

    def test_cap_add_flag(self):
        config = parse_docker_run("--cap-add SYS_ADMIN nginx")
        assert "SYS_ADMIN" in config.cap_add

    def test_cap_drop_flag(self):
        config = parse_docker_run("--cap-drop NET_RAW nginx")
        assert "NET_RAW" in config.cap_drop


# ---------------------------------------------------------------------------
# parse_docker_run - long flags with = syntax
# ---------------------------------------------------------------------------


class TestParseDockerRunEqualsFlags:
    """Tests for long flags using the --flag=value syntax."""

    def test_name_with_equals(self):
        config = parse_docker_run("--name=mycontainer nginx")
        assert config.name == "mycontainer"

    def test_publish_with_equals(self):
        config = parse_docker_run("--publish=8080:80 nginx")
        assert "8080:80" in config.ports

    def test_volume_with_equals(self):
        config = parse_docker_run("--volume=/host:/container nginx")
        assert "/host:/container" in config.volumes

    def test_env_with_equals(self):
        config = parse_docker_run("--env=DB_HOST=localhost nginx")
        assert ("DB_HOST", "localhost") in config.environment

    def test_env_file_with_equals(self):
        config = parse_docker_run("--env-file=.env nginx")
        assert ".env" in config.env_files

    def test_network_with_equals(self):
        config = parse_docker_run("--network=mynet nginx")
        assert "mynet" in config.networks

    def test_restart_with_equals(self):
        config = parse_docker_run("--restart=always nginx")
        assert config.restart == "always"

    def test_user_with_equals(self):
        config = parse_docker_run("--user=1000 nginx")
        assert config.user == "1000"

    def test_workdir_with_equals(self):
        config = parse_docker_run("--workdir=/app nginx")
        assert config.workdir == "/app"

    def test_entrypoint_with_equals(self):
        config = parse_docker_run("--entrypoint=/bin/sh nginx")
        assert config.entrypoint == "/bin/sh"

    def test_memory_with_equals(self):
        config = parse_docker_run("--memory=512m nginx")
        assert config.memory == "512m"

    def test_cpus_with_equals(self):
        config = parse_docker_run("--cpus=1.5 nginx")
        assert config.cpus == "1.5"

    def test_hostname_with_equals(self):
        config = parse_docker_run("--hostname=myhost nginx")
        assert config.hostname == "myhost"

    def test_label_with_equals(self):
        config = parse_docker_run("--label=tier=web nginx")
        assert ("tier", "web") in config.labels

    def test_cap_add_with_equals(self):
        config = parse_docker_run("--cap-add=SYS_PTRACE nginx")
        assert "SYS_PTRACE" in config.cap_add

    def test_cap_drop_with_equals(self):
        config = parse_docker_run("--cap-drop=ALL nginx")
        assert "ALL" in config.cap_drop


# ---------------------------------------------------------------------------
# parse_docker_run - health check flags
# ---------------------------------------------------------------------------


class TestParseDockerRunHealthcheck:
    """Tests for health check flag parsing."""

    def test_health_cmd(self):
        config = parse_docker_run("--health-cmd 'curl -f http://localhost/' nginx")
        assert config.healthcheck is not None
        assert config.healthcheck["test"] == [
            "CMD-SHELL",
            "curl -f http://localhost/",
        ]

    def test_health_interval(self):
        config = parse_docker_run("--health-interval 30s nginx")
        assert config.healthcheck is not None
        assert config.healthcheck["interval"] == "30s"

    def test_health_timeout(self):
        config = parse_docker_run("--health-timeout 10s nginx")
        assert config.healthcheck is not None
        assert config.healthcheck["timeout"] == "10s"

    def test_health_retries(self):
        config = parse_docker_run("--health-retries 5 nginx")
        assert config.healthcheck is not None
        assert config.healthcheck["retries"] == 5

    def test_health_retries_with_equals(self):
        config = parse_docker_run("--health-retries=3 nginx")
        assert config.healthcheck is not None
        assert config.healthcheck["retries"] == 3

    def test_combined_health_flags(self):
        config = parse_docker_run(
            "--health-cmd 'pg_isready' --health-interval 10s --health-timeout 5s --health-retries 3 postgres"
        )
        assert config.healthcheck is not None
        assert config.healthcheck["test"] == ["CMD-SHELL", "pg_isready"]
        assert config.healthcheck["interval"] == "10s"
        assert config.healthcheck["timeout"] == "5s"
        assert config.healthcheck["retries"] == 3

    def test_health_cmd_with_equals(self):
        config = parse_docker_run("--health-cmd='curl localhost' nginx")
        assert config.healthcheck is not None
        assert config.healthcheck["test"] == ["CMD-SHELL", "curl localhost"]

    def test_health_interval_with_equals(self):
        config = parse_docker_run("--health-interval=15s nginx")
        assert config.healthcheck["interval"] == "15s"

    def test_health_timeout_with_equals(self):
        config = parse_docker_run("--health-timeout=5s nginx")
        assert config.healthcheck["timeout"] == "5s"


# ---------------------------------------------------------------------------
# parse_docker_run - command arguments and image variants
# ---------------------------------------------------------------------------


class TestParseDockerRunImageAndCommand:
    """Tests for image name and command argument parsing."""

    def test_simple_image(self):
        config = parse_docker_run("nginx")
        assert config.image == "nginx"
        assert config.command == ()

    def test_image_with_tag(self):
        config = parse_docker_run("postgres:15")
        assert config.image == "postgres:15"

    def test_image_with_registry(self):
        config = parse_docker_run("ghcr.io/owner/repo:latest")
        assert config.image == "ghcr.io/owner/repo:latest"

    def test_command_after_image(self):
        config = parse_docker_run("nginx nginx -g 'daemon off;'")
        assert config.image == "nginx"
        assert len(config.command) >= 1

    def test_single_command_arg(self):
        config = parse_docker_run("python app.py")
        assert config.image == "python"
        assert config.command == ("app.py",)

    def test_multiple_command_args(self):
        config2 = parse_docker_run("python:3.11 python -u app.py")
        assert config2.image == "python:3.11"
        assert "python" in config2.command


# ---------------------------------------------------------------------------
# parse_docker_run - complex realistic commands
# ---------------------------------------------------------------------------


class TestParseDockerRunComplex:
    """Tests for complex, real-world-like docker run commands."""

    def test_full_postgres_command(self):
        cmd = (
            "docker run -d --name mydb -p 5432:5432 "
            "-e POSTGRES_PASSWORD=secret -e POSTGRES_USER=admin "
            "-v pgdata:/var/lib/postgresql/data "
            "--restart unless-stopped postgres:15"
        )
        config = parse_docker_run(cmd)
        assert config.image == "postgres:15"
        assert config.name == "mydb"
        assert config.detached is True
        assert "5432:5432" in config.ports
        assert ("POSTGRES_PASSWORD", "secret") in config.environment
        assert ("POSTGRES_USER", "admin") in config.environment
        assert "pgdata:/var/lib/postgresql/data" in config.volumes
        assert config.restart == "unless-stopped"

    def test_nginx_with_network_and_labels(self):
        cmd = (
            "--name web -p 80:80 -p 443:443 "
            "--network frontend --label env=prod --label tier=web "
            "--restart always nginx:alpine"
        )
        config = parse_docker_run(cmd)
        assert config.image == "nginx:alpine"
        assert config.name == "web"
        assert len(config.ports) == 2
        assert "frontend" in config.networks
        assert ("env", "prod") in config.labels
        assert ("tier", "web") in config.labels
        assert config.restart == "always"

    def test_privileged_with_capabilities(self):
        cmd = "--privileged --cap-add SYS_ADMIN --cap-drop NET_RAW alpine"
        config = parse_docker_run(cmd)
        assert config.privileged is True
        assert "SYS_ADMIN" in config.cap_add
        assert "NET_RAW" in config.cap_drop

    def test_resource_limited_container(self):
        cmd = "--memory 256m --cpus 0.5 --name worker python:3.11"
        config = parse_docker_run(cmd)
        assert config.memory == "256m"
        assert config.cpus == "0.5"
        assert config.name == "worker"

    def test_env_file_with_env_vars(self):
        cmd = "--env-file .env --env-file secrets.env -e EXTRA=val nginx"
        config = parse_docker_run(cmd)
        assert ".env" in config.env_files
        assert "secrets.env" in config.env_files
        assert ("EXTRA", "val") in config.environment


# ---------------------------------------------------------------------------
# docker_run_to_compose - basic YAML generation
# ---------------------------------------------------------------------------


class TestDockerRunToCompose:
    """Tests for docker_run_to_compose YAML generation."""

    def test_simple_image(self):
        result = docker_run_to_compose("docker run nginx")
        assert "services:" in result
        assert "nginx:" in result
        assert "image: nginx" in result

    def test_image_with_tag_service_name(self):
        result = docker_run_to_compose("docker run postgres:15")
        assert "postgres:" in result
        assert "image: postgres:15" in result

    def test_registry_image_service_name_extraction(self):
        result = docker_run_to_compose("docker run ghcr.io/owner/myapp:v1")
        assert "myapp:" in result
        assert "image: ghcr.io/owner/myapp:v1" in result

    def test_custom_service_name(self):
        result = docker_run_to_compose("docker run nginx", service_name="webserver")
        assert "webserver:" in result

    def test_container_name_used_as_service_name(self):
        result = docker_run_to_compose("docker run --name mydb postgres")
        assert "mydb:" in result
        assert "container_name: mydb" in result

    def test_custom_service_name_overrides_container_name(self):
        result = docker_run_to_compose(
            "docker run --name mydb postgres",
            service_name="database",
        )
        assert "database:" in result
        assert "container_name: mydb" in result

    def test_ports_in_yaml(self):
        result = docker_run_to_compose("docker run -p 8080:80 -p 443:443 nginx")
        assert "ports:" in result
        assert "8080:80" in result
        assert "443:443" in result

    def test_volumes_in_yaml(self):
        result = docker_run_to_compose("docker run -v /data:/data -v logs:/logs nginx")
        assert "volumes:" in result
        assert "/data:/data" in result
        assert "logs:/logs" in result

    def test_environment_in_yaml(self):
        result = docker_run_to_compose("docker run -e POSTGRES_PASSWORD=secret -e POSTGRES_USER=admin postgres")
        assert "environment:" in result
        assert "POSTGRES_PASSWORD: secret" in result
        assert "POSTGRES_USER: admin" in result

    def test_env_file_in_yaml(self):
        result = docker_run_to_compose("docker run --env-file .env nginx")
        assert "env_file:" in result
        assert ".env" in result

    def test_networks_in_yaml(self):
        result = docker_run_to_compose("docker run --network backend nginx")
        assert "networks:" in result
        assert "backend" in result

    def test_networks_external_section(self):
        result = docker_run_to_compose("docker run --network mynet nginx")
        assert "external: true" in result

    def test_restart_policy_explicit(self):
        result = docker_run_to_compose("docker run --restart always nginx")
        assert "restart: always" in result

    def test_restart_policy_from_detached(self):
        result = docker_run_to_compose("docker run -d nginx")
        assert "restart: unless-stopped" in result

    def test_explicit_restart_overrides_detached(self):
        result = docker_run_to_compose("docker run -d --restart on-failure nginx")
        assert "restart: on-failure" in result
        assert "unless-stopped" not in result

    def test_privileged_in_yaml(self):
        result = docker_run_to_compose("docker run --privileged nginx")
        assert "privileged: true" in result

    def test_user_in_yaml(self):
        result = docker_run_to_compose("docker run -u 1000:1000 nginx")
        assert "user:" in result
        assert "1000:1000" in result

    def test_working_dir_in_yaml(self):
        result = docker_run_to_compose("docker run --workdir /app node")
        assert "working_dir: /app" in result

    def test_entrypoint_in_yaml(self):
        result = docker_run_to_compose("docker run --entrypoint /bin/sh nginx")
        assert "entrypoint: /bin/sh" in result

    def test_single_command_in_yaml(self):
        result = docker_run_to_compose("docker run nginx echo hello")
        assert "command:" in result

    def test_multi_command_in_yaml(self):
        result = docker_run_to_compose("docker run nginx sh -c 'echo hello'")
        assert "command:" in result

    def test_labels_in_yaml(self):
        result = docker_run_to_compose("docker run --label env=prod nginx")
        assert "labels:" in result
        assert "env: prod" in result

    def test_hostname_in_yaml(self):
        result = docker_run_to_compose("docker run --hostname webhost nginx")
        assert "hostname: webhost" in result

    def test_healthcheck_in_yaml(self):
        result = docker_run_to_compose("docker run --health-cmd 'curl localhost' --health-interval 10s nginx")
        assert "healthcheck:" in result
        assert "curl localhost" in result

    def test_cap_add_in_yaml(self):
        result = docker_run_to_compose("docker run --cap-add SYS_ADMIN nginx")
        assert "cap_add:" in result
        assert "SYS_ADMIN" in result

    def test_cap_drop_in_yaml(self):
        result = docker_run_to_compose("docker run --cap-drop NET_RAW nginx")
        assert "cap_drop:" in result
        assert "NET_RAW" in result

    def test_memory_limit_in_yaml(self):
        result = docker_run_to_compose("docker run --memory 512m nginx")
        assert "deploy:" in result
        assert "resources:" in result
        assert "limits:" in result
        assert "memory: 512m" in result

    def test_cpu_limit_in_yaml(self):
        result = docker_run_to_compose("docker run --cpus 2.0 nginx")
        assert "deploy:" in result
        assert "cpus:" in result

    def test_both_resource_limits(self):
        result = docker_run_to_compose("docker run --memory 1g --cpus 0.5 nginx")
        assert "memory: 1g" in result
        assert "cpus:" in result

    def test_no_resource_limits_when_absent(self):
        result = docker_run_to_compose("docker run nginx")
        assert "deploy:" not in result


# ---------------------------------------------------------------------------
# docker_run_to_compose - full realistic YAML output
# ---------------------------------------------------------------------------


class TestDockerRunToComposeRealistic:
    """Tests for complete realistic compose output."""

    def test_full_postgres_compose(self):
        cmd = "docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=secret -v pgdata:/var/lib/postgresql/data postgres:15"
        result = docker_run_to_compose(cmd)
        assert "services:" in result
        assert "postgres:" in result
        assert "image: postgres:15" in result
        assert "5432:5432" in result
        assert "POSTGRES_PASSWORD" in result
        assert "pgdata:/var/lib/postgresql/data" in result
        assert "restart: unless-stopped" in result

    def test_no_ports_no_ports_section(self):
        result = docker_run_to_compose("docker run nginx")
        assert "ports:" not in result

    def test_no_volumes_no_volumes_section(self):
        result = docker_run_to_compose("docker run nginx")
        assert "volumes:" not in result

    def test_no_env_no_environment_section(self):
        result = docker_run_to_compose("docker run nginx")
        assert "environment:" not in result

    def test_multiple_networks_all_external(self):
        result = docker_run_to_compose("docker run --network net1 --network net2 nginx")
        assert "networks:" in result
        assert "net1" in result
        assert "net2" in result

    def test_valid_yaml_output(self):
        """Verify the output is valid YAML."""
        import io

        from ruamel.yaml import YAML

        result = docker_run_to_compose("docker run -d -p 80:80 -e FOO=bar --name web nginx:alpine")
        yml = YAML()
        parsed = yml.load(io.StringIO(result))
        assert "services" in parsed
        assert "web" in parsed["services"]
        assert parsed["services"]["web"]["image"] == "nginx:alpine"


# ---------------------------------------------------------------------------
# docker_run_with_env_prompt - mocked env detection and prompting
#
# The imports of DockerEnvDetector, extract_image_family, get_profile, and
# prompt_env_vars are LOCAL (inside the function body), so they must be
# patched at their source modules, not on compose_converter.
# ---------------------------------------------------------------------------


_PATCH_DETECTOR = "elle.cli.docker.env_detector.DockerEnvDetector"
_PATCH_EXTRACT = "elle.cli.docker.env_profiles.extract_image_family"
_PATCH_PROFILE = "elle.cli.docker.env_profiles.get_profile"
_PATCH_PROMPT = "elle.cli.docker.env_prompt.prompt_env_vars"


class TestDockerRunWithEnvPrompt:
    """Tests for docker_run_with_env_prompt with all deps mocked."""

    @patch(_PATCH_PROMPT)
    @patch(_PATCH_PROFILE)
    @patch(_PATCH_EXTRACT)
    @patch(_PATCH_DETECTOR)
    def test_skip_prompt_returns_original_config(self, mock_detector_cls, mock_extract, mock_profile, mock_prompt):
        result = docker_run_with_env_prompt("docker run -e FOO=bar nginx", skip_prompt=True)
        assert result.image == "nginx"
        assert ("FOO", "bar") in result.environment
        mock_detector_cls.assert_not_called()
        mock_prompt.assert_not_called()

    @patch(_PATCH_PROMPT)
    @patch(_PATCH_PROFILE)
    @patch(_PATCH_EXTRACT)
    @patch(_PATCH_DETECTOR)
    def test_no_specs_returns_original_config(self, mock_detector_cls, mock_extract, mock_profile, mock_prompt):
        mock_detector = MagicMock()
        mock_detector.detect_from_image.return_value = ()
        mock_detector_cls.return_value = mock_detector

        result = docker_run_with_env_prompt("docker run postgres:15")
        assert result.image == "postgres:15"
        mock_prompt.assert_not_called()

    @patch(_PATCH_PROMPT)
    @patch(_PATCH_PROFILE)
    @patch(_PATCH_EXTRACT)
    @patch(_PATCH_DETECTOR)
    def test_no_required_and_no_profile_returns_original(
        self, mock_detector_cls, mock_extract, mock_profile, mock_prompt
    ):
        """If no required vars and no profile, skip prompting."""
        mock_spec = MagicMock()
        mock_spec.required = False

        mock_detector = MagicMock()
        mock_detector.detect_from_image.return_value = (mock_spec,)
        mock_detector_cls.return_value = mock_detector

        mock_extract.return_value = "unknownimg"
        mock_profile.return_value = None

        result = docker_run_with_env_prompt("docker run unknownimg:latest")
        assert result.image == "unknownimg:latest"
        mock_prompt.assert_not_called()

    @patch(_PATCH_PROMPT)
    @patch(_PATCH_PROFILE)
    @patch(_PATCH_EXTRACT)
    @patch(_PATCH_DETECTOR)
    def test_required_vars_triggers_prompt(self, mock_detector_cls, mock_extract, mock_profile, mock_prompt):
        mock_spec = MagicMock()
        mock_spec.required = True

        mock_detector = MagicMock()
        mock_detector.detect_from_image.return_value = (mock_spec,)
        mock_detector_cls.return_value = mock_detector

        mock_extract.return_value = "postgres"
        mock_profile_obj = MagicMock()
        mock_profile_obj.has_required_vars.return_value = True
        mock_profile.return_value = mock_profile_obj

        from elle.cli.docker.env_models import EnvVarPromptResult

        mock_prompt.return_value = EnvVarPromptResult(cancelled=True, image="postgres:15")

        result = docker_run_with_env_prompt("docker run postgres:15")
        assert result.image == "postgres:15"
        mock_prompt.assert_called_once()

    @patch(_PATCH_PROMPT)
    @patch(_PATCH_PROFILE)
    @patch(_PATCH_EXTRACT)
    @patch(_PATCH_DETECTOR)
    def test_cancelled_prompt_returns_original(self, mock_detector_cls, mock_extract, mock_profile, mock_prompt):
        mock_spec = MagicMock()
        mock_spec.required = True

        mock_detector = MagicMock()
        mock_detector.detect_from_image.return_value = (mock_spec,)
        mock_detector_cls.return_value = mock_detector

        mock_extract.return_value = "postgres"
        mock_profile.return_value = None

        from elle.cli.docker.env_models import EnvVarPromptResult

        mock_prompt.return_value = EnvVarPromptResult(cancelled=True, image="postgres:15")

        result = docker_run_with_env_prompt("docker run -e EXISTING=val postgres:15")
        assert result.image == "postgres:15"
        assert ("EXISTING", "val") in result.environment

    @patch(_PATCH_PROMPT)
    @patch(_PATCH_PROFILE)
    @patch(_PATCH_EXTRACT)
    @patch(_PATCH_DETECTOR)
    def test_empty_prompt_values_returns_original(self, mock_detector_cls, mock_extract, mock_profile, mock_prompt):
        mock_spec = MagicMock()
        mock_spec.required = True

        mock_detector = MagicMock()
        mock_detector.detect_from_image.return_value = (mock_spec,)
        mock_detector_cls.return_value = mock_detector

        mock_extract.return_value = "postgres"
        mock_profile.return_value = None

        from elle.cli.docker.env_models import EnvVarPromptResult

        mock_prompt.return_value = EnvVarPromptResult(cancelled=False, values=(), image="postgres:15")

        result = docker_run_with_env_prompt("docker run postgres:15")
        assert result.image == "postgres:15"

    @patch(_PATCH_PROMPT)
    @patch(_PATCH_PROFILE)
    @patch(_PATCH_EXTRACT)
    @patch(_PATCH_DETECTOR)
    def test_prompt_merges_new_env_with_existing(self, mock_detector_cls, mock_extract, mock_profile, mock_prompt):
        mock_spec = MagicMock()
        mock_spec.required = True

        mock_detector = MagicMock()
        mock_detector.detect_from_image.return_value = (mock_spec,)
        mock_detector_cls.return_value = mock_detector

        mock_extract.return_value = "postgres"
        mock_profile.return_value = None

        from elle.cli.docker.env_models import EnvVarPromptResult, EnvVarValue

        mock_prompt.return_value = EnvVarPromptResult(
            cancelled=False,
            values=(EnvVarValue(name="POSTGRES_PASSWORD", value="newsecret"),),
            image="postgres:15",
        )

        result = docker_run_with_env_prompt("docker run -e EXISTING=val postgres:15")
        assert result.image == "postgres:15"
        env_dict = dict(result.environment)
        assert env_dict["EXISTING"] == "val"
        assert env_dict["POSTGRES_PASSWORD"] == "newsecret"

    @patch(_PATCH_PROMPT)
    @patch(_PATCH_PROFILE)
    @patch(_PATCH_EXTRACT)
    @patch(_PATCH_DETECTOR)
    def test_prompt_overwrites_existing_env_value(self, mock_detector_cls, mock_extract, mock_profile, mock_prompt):
        """If user re-enters a value that already exists, it overwrites."""
        mock_spec = MagicMock()
        mock_spec.required = True

        mock_detector = MagicMock()
        mock_detector.detect_from_image.return_value = (mock_spec,)
        mock_detector_cls.return_value = mock_detector

        mock_extract.return_value = "postgres"
        mock_profile.return_value = None

        from elle.cli.docker.env_models import EnvVarPromptResult, EnvVarValue

        mock_prompt.return_value = EnvVarPromptResult(
            cancelled=False,
            values=(EnvVarValue(name="FOO", value="newval"),),
            image="nginx",
        )

        result = docker_run_with_env_prompt("docker run -e FOO=oldval nginx")
        env_dict = dict(result.environment)
        assert env_dict["FOO"] == "newval"

    @patch(_PATCH_PROMPT)
    @patch(_PATCH_PROFILE)
    @patch(_PATCH_EXTRACT)
    @patch(_PATCH_DETECTOR)
    def test_profile_with_required_vars_triggers_prompt(
        self, mock_detector_cls, mock_extract, mock_profile, mock_prompt
    ):
        """Profile with required vars triggers prompting even if spec not required."""
        mock_spec = MagicMock()
        mock_spec.required = False

        mock_detector = MagicMock()
        mock_detector.detect_from_image.return_value = (mock_spec,)
        mock_detector_cls.return_value = mock_detector

        mock_extract.return_value = "postgres"
        mock_profile_obj = MagicMock()
        mock_profile_obj.has_required_vars.return_value = True
        mock_profile.return_value = mock_profile_obj

        from elle.cli.docker.env_models import EnvVarPromptResult

        mock_prompt.return_value = EnvVarPromptResult(cancelled=False, values=(), image="postgres:15")

        docker_run_with_env_prompt("docker run postgres:15")
        mock_prompt.assert_called_once()

    @patch(_PATCH_PROMPT)
    @patch(_PATCH_PROFILE)
    @patch(_PATCH_EXTRACT)
    @patch(_PATCH_DETECTOR)
    def test_profile_without_required_but_exists_triggers_prompt(
        self, mock_detector_cls, mock_extract, mock_profile, mock_prompt
    ):
        """If no required vars but profile exists, still prompt."""
        mock_spec = MagicMock()
        mock_spec.required = False

        mock_detector = MagicMock()
        mock_detector.detect_from_image.return_value = (mock_spec,)
        mock_detector_cls.return_value = mock_detector

        mock_extract.return_value = "redis"
        mock_profile_obj = MagicMock()
        mock_profile_obj.has_required_vars.return_value = False
        mock_profile.return_value = mock_profile_obj

        from elle.cli.docker.env_models import EnvVarPromptResult

        mock_prompt.return_value = EnvVarPromptResult(cancelled=False, values=(), image="redis:7")

        docker_run_with_env_prompt("docker run redis:7")
        mock_prompt.assert_called_once()

    @patch(_PATCH_PROMPT)
    @patch(_PATCH_PROFILE)
    @patch(_PATCH_EXTRACT)
    @patch(_PATCH_DETECTOR)
    def test_returned_config_preserves_all_fields(self, mock_detector_cls, mock_extract, mock_profile, mock_prompt):
        """Verify the new DockerRunConfig preserves all original fields."""
        mock_spec = MagicMock()
        mock_spec.required = True

        mock_detector = MagicMock()
        mock_detector.detect_from_image.return_value = (mock_spec,)
        mock_detector_cls.return_value = mock_detector

        mock_extract.return_value = "nginx"
        mock_profile.return_value = None

        from elle.cli.docker.env_models import EnvVarPromptResult, EnvVarValue

        mock_prompt.return_value = EnvVarPromptResult(
            cancelled=False,
            values=(EnvVarValue(name="NEW_VAR", value="val"),),
            image="nginx",
        )

        result = docker_run_with_env_prompt(
            "docker run -d -p 80:80 --name web --restart always "
            "--privileged -u 1000 -w /app --entrypoint /bin/sh "
            "--memory 256m --cpus 0.5 --hostname webhost "
            "--cap-add SYS_ADMIN --cap-drop NET_RAW "
            "-v /data:/data --env-file .env --network mynet "
            "--label tier=web nginx"
        )
        assert result.image == "nginx"
        assert result.name == "web"
        assert result.detached is True
        assert "80:80" in result.ports
        assert result.restart == "always"
        assert result.privileged is True
        assert result.user == "1000"
        assert result.workdir == "/app"
        assert result.entrypoint == "/bin/sh"
        assert result.memory == "256m"
        assert result.cpus == "0.5"
        assert result.hostname == "webhost"
        assert "SYS_ADMIN" in result.cap_add
        assert "NET_RAW" in result.cap_drop
        assert "/data:/data" in result.volumes
        assert ".env" in result.env_files
        assert "mynet" in result.networks
        assert ("tier", "web") in result.labels
        env_dict = dict(result.environment)
        assert "NEW_VAR" in env_dict

    @patch(_PATCH_PROMPT)
    @patch(_PATCH_PROFILE)
    @patch(_PATCH_EXTRACT)
    @patch(_PATCH_DETECTOR)
    def test_console_parameter_passed_to_prompt(self, mock_detector_cls, mock_extract, mock_profile, mock_prompt):
        mock_spec = MagicMock()
        mock_spec.required = True

        mock_detector = MagicMock()
        mock_detector.detect_from_image.return_value = (mock_spec,)
        mock_detector_cls.return_value = mock_detector

        mock_extract.return_value = "postgres"
        mock_profile.return_value = None

        from elle.cli.docker.env_models import EnvVarPromptResult

        mock_prompt.return_value = EnvVarPromptResult(cancelled=True, image="postgres:15")

        mock_console = MagicMock()
        docker_run_with_env_prompt(
            "docker run postgres:15",
            console=mock_console,
            use_saved=False,
            save_values=False,
        )
        _, kwargs = mock_prompt.call_args
        assert kwargs["console"] is mock_console
        assert kwargs["use_saved"] is False
        assert kwargs["save_values"] is False


# ---------------------------------------------------------------------------
# needs_env_prompting
# ---------------------------------------------------------------------------


class TestNeedsEnvPrompting:
    """Tests for needs_env_prompting with mocked detector."""

    @patch(_PATCH_DETECTOR)
    def test_returns_true_when_prompting_needed(self, mock_detector_cls):
        mock_detector = MagicMock()
        mock_detector.needs_prompting.return_value = True
        mock_detector_cls.return_value = mock_detector

        assert needs_env_prompting("docker run postgres:15") is True
        mock_detector.needs_prompting.assert_called_once_with("postgres:15", ())

    @patch(_PATCH_DETECTOR)
    def test_returns_false_when_no_prompting_needed(self, mock_detector_cls):
        mock_detector = MagicMock()
        mock_detector.needs_prompting.return_value = False
        mock_detector_cls.return_value = mock_detector

        assert needs_env_prompting("docker run nginx") is False

    def test_returns_false_on_parse_error(self):
        assert needs_env_prompting("docker build .") is False

    def test_returns_false_on_empty_command(self):
        assert needs_env_prompting("") is False

    @patch(_PATCH_DETECTOR)
    def test_passes_existing_env_to_detector(self, mock_detector_cls):
        mock_detector = MagicMock()
        mock_detector.needs_prompting.return_value = False
        mock_detector_cls.return_value = mock_detector

        needs_env_prompting("docker run -e FOO=bar postgres:15")
        mock_detector.needs_prompting.assert_called_once_with("postgres:15", (("FOO", "bar"),))


# ---------------------------------------------------------------------------
# get_missing_env_vars
# ---------------------------------------------------------------------------


class TestGetMissingEnvVars:
    """Tests for get_missing_env_vars with mocked detector."""

    @patch(_PATCH_DETECTOR)
    def test_returns_missing_var_names(self, mock_detector_cls):
        mock_spec1 = MagicMock()
        mock_spec1.name = "POSTGRES_PASSWORD"
        mock_spec2 = MagicMock()
        mock_spec2.name = "POSTGRES_USER"

        mock_detector = MagicMock()
        mock_detector.get_missing_required.return_value = [
            mock_spec1,
            mock_spec2,
        ]
        mock_detector_cls.return_value = mock_detector

        result = get_missing_env_vars("docker run postgres:15")
        assert result == ["POSTGRES_PASSWORD", "POSTGRES_USER"]
        mock_detector.get_missing_required.assert_called_once_with("postgres:15", ())

    @patch(_PATCH_DETECTOR)
    def test_returns_empty_list_when_none_missing(self, mock_detector_cls):
        mock_detector = MagicMock()
        mock_detector.get_missing_required.return_value = []
        mock_detector_cls.return_value = mock_detector

        result = get_missing_env_vars("docker run -e FOO=bar nginx")
        assert result == []

    def test_returns_empty_list_on_parse_error(self):
        result = get_missing_env_vars("docker build .")
        assert result == []

    def test_returns_empty_list_on_empty_command(self):
        result = get_missing_env_vars("")
        assert result == []

    @patch(_PATCH_DETECTOR)
    def test_passes_existing_env_to_detector(self, mock_detector_cls):
        mock_detector = MagicMock()
        mock_detector.get_missing_required.return_value = []
        mock_detector_cls.return_value = mock_detector

        get_missing_env_vars("docker run -e FOO=bar -e BAZ=qux nginx")
        mock_detector.get_missing_required.assert_called_once_with("nginx", (("FOO", "bar"), ("BAZ", "qux")))
