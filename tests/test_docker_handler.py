"""Tests for Docker domain handler and compose converter."""

import pytest

from elle.rag.confgen.docker import DockerHandler
from elle.cli.docker.compose_converter import (
    docker_run_to_compose,
    parse_docker_run,
    DockerRunConfig,
)


class TestDockerHandler:
    """Tests for DockerHandler."""

    def test_domain_is_docker(self):
        handler = DockerHandler()
        assert handler.domain == "docker"

    def test_file_type_is_yaml(self):
        handler = DockerHandler()
        assert handler.file_type == "yaml"

    def test_matches_docker_compose_yml(self):
        handler = DockerHandler()
        assert handler.matches_path("docker-compose.yml")
        assert handler.matches_path("docker-compose.yaml")
        assert handler.matches_path("/path/to/docker-compose.yml")

    def test_matches_compose_yaml(self):
        handler = DockerHandler()
        assert handler.matches_path("compose.yml")
        assert handler.matches_path("compose.yaml")

    def test_matches_dockerfile(self):
        handler = DockerHandler()
        assert handler.matches_path("Dockerfile")
        assert handler.matches_path("Dockerfile.prod")
        assert handler.matches_path("/app/Dockerfile")

    def test_does_not_match_other_files(self):
        handler = DockerHandler()
        assert not handler.matches_path("config.yml")
        assert not handler.matches_path("settings.yaml")
        assert not handler.matches_path("package.json")

    def test_validate_valid_compose(self):
        handler = DockerHandler()
        content = """
services:
  web:
    image: nginx:alpine
    ports:
      - "8080:80"
"""
        result = handler.validate_content(content)
        assert result.valid

    def test_validate_compose_missing_services(self):
        handler = DockerHandler()
        content = """
version: "3"
networks:
  default:
"""
        result = handler.validate_content(content)
        assert not result.valid
        assert any("services" in i.message.lower() for i in result.issues)

    def test_validate_dockerfile_valid(self):
        handler = DockerHandler()
        content = """
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["python", "app.py"]
"""
        result = handler.validate_content(content)
        assert result.valid

    def test_validate_dockerfile_missing_from(self):
        handler = DockerHandler()
        content = """
RUN apt-get update
CMD ["bash"]
"""
        result = handler.validate_content(content)
        assert not result.valid
        assert any("FROM" in i.message for i in result.issues)

    def test_get_schema_hint_compose(self):
        handler = DockerHandler()
        schema = handler.get_schema_hint("docker-compose.yml")
        assert "services" in schema
        assert "volumes" in schema
        assert "networks" in schema

    def test_get_schema_hint_dockerfile(self):
        handler = DockerHandler()
        schema = handler.get_schema_hint("Dockerfile")
        assert "instructions" in schema
        assert "FROM <image>:<tag>" in schema["instructions"]

    def test_get_template(self):
        handler = DockerHandler()
        template = handler.get_template()
        assert "services:" in template
        assert "image:" in template


class TestParseDockerRun:
    """Tests for docker run command parsing."""

    def test_parse_simple_image(self):
        config = parse_docker_run("nginx")
        assert config.image == "nginx"

    def test_parse_image_with_tag(self):
        config = parse_docker_run("postgres:15")
        assert config.image == "postgres:15"

    def test_parse_detached_flag(self):
        config = parse_docker_run("-d nginx")
        assert config.detached
        assert config.image == "nginx"

    def test_parse_port_mapping(self):
        config = parse_docker_run("-p 8080:80 nginx")
        assert "8080:80" in config.ports
        assert config.image == "nginx"

    def test_parse_multiple_ports(self):
        config = parse_docker_run("-p 80:80 -p 443:443 nginx")
        assert len(config.ports) == 2
        assert "80:80" in config.ports
        assert "443:443" in config.ports

    def test_parse_volume_mount(self):
        config = parse_docker_run("-v /host:/container nginx")
        assert "/host:/container" in config.volumes

    def test_parse_environment_variable(self):
        config = parse_docker_run("-e POSTGRES_PASSWORD=secret postgres")
        assert ("POSTGRES_PASSWORD", "secret") in config.environment

    def test_parse_multiple_env_vars(self):
        config = parse_docker_run("-e FOO=bar -e BAZ=qux nginx")
        assert len(config.environment) == 2
        assert ("FOO", "bar") in config.environment
        assert ("BAZ", "qux") in config.environment

    def test_parse_container_name(self):
        config = parse_docker_run("--name mycontainer nginx")
        assert config.name == "mycontainer"

    def test_parse_restart_policy(self):
        config = parse_docker_run("--restart unless-stopped nginx")
        assert config.restart == "unless-stopped"

    def test_parse_network(self):
        config = parse_docker_run("--network mynet nginx")
        assert "mynet" in config.networks

    def test_parse_full_docker_run_prefix(self):
        config = parse_docker_run("docker run -d -p 80:80 nginx")
        assert config.detached
        assert "80:80" in config.ports
        assert config.image == "nginx"

    def test_parse_command_args(self):
        config = parse_docker_run("nginx nginx -g 'daemon off;'")
        assert config.image == "nginx"
        assert len(config.command) > 0

    def test_parse_memory_limit(self):
        config = parse_docker_run("--memory 512m nginx")
        assert config.memory == "512m"

    def test_parse_cpu_limit(self):
        config = parse_docker_run("--cpus 0.5 nginx")
        assert config.cpus == "0.5"

    def test_parse_privileged(self):
        config = parse_docker_run("--privileged nginx")
        assert config.privileged

    def test_parse_user(self):
        config = parse_docker_run("-u 1000:1000 nginx")
        assert config.user == "1000:1000"

    def test_parse_workdir(self):
        config = parse_docker_run("-w /app node")
        assert config.workdir == "/app"

    def test_raises_on_empty(self):
        with pytest.raises(ValueError):
            parse_docker_run("")

    def test_raises_on_no_image(self):
        with pytest.raises(ValueError):
            parse_docker_run("-d -p 80:80")


class TestDockerRunToCompose:
    """Tests for docker run to compose conversion."""

    def test_simple_conversion(self):
        result = docker_run_to_compose("docker run nginx")
        assert "services:" in result
        assert "nginx:" in result
        assert "image: nginx" in result

    def test_conversion_with_ports(self):
        result = docker_run_to_compose("docker run -p 8080:80 nginx")
        assert "ports:" in result
        assert "8080:80" in result

    def test_conversion_with_volumes(self):
        result = docker_run_to_compose("docker run -v /data:/data nginx")
        assert "volumes:" in result
        assert "/data:/data" in result

    def test_conversion_with_environment(self):
        result = docker_run_to_compose(
            "docker run -e POSTGRES_PASSWORD=secret postgres:15"
        )
        assert "environment:" in result
        assert "POSTGRES_PASSWORD: secret" in result

    def test_conversion_preserves_image_tag(self):
        result = docker_run_to_compose("docker run postgres:15-alpine")
        assert "image: postgres:15-alpine" in result

    def test_conversion_uses_container_name_as_service(self):
        result = docker_run_to_compose("docker run --name mydb postgres")
        assert "mydb:" in result or "container_name: mydb" in result

    def test_conversion_adds_restart_policy(self):
        result = docker_run_to_compose("docker run --restart always nginx")
        assert "restart: always" in result

    def test_conversion_detached_adds_unless_stopped(self):
        result = docker_run_to_compose("docker run -d nginx")
        assert "restart: unless-stopped" in result

    def test_conversion_with_custom_service_name(self):
        result = docker_run_to_compose(
            "docker run nginx",
            service_name="webserver"
        )
        assert "webserver:" in result

    def test_full_postgres_example(self):
        cmd = "docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=secret -v pgdata:/var/lib/postgresql/data postgres:15"
        result = docker_run_to_compose(cmd)

        assert "services:" in result
        assert "postgres:" in result
        assert "image: postgres:15" in result
        assert "5432:5432" in result
        assert "POSTGRES_PASSWORD" in result
        assert "pgdata:/var/lib/postgresql/data" in result
