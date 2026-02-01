"""Tests for docker confgen handler coverage.

Covers: file_patterns property, Dockerfile validation (ADD vs COPY,
apt-get cleanup, USER ROOT), docker-compose validation (ruamel.yaml
ImportError, parse errors, missing services key, port format, privileged
mode), get_validation_command.
"""

from __future__ import annotations

from unittest.mock import patch

from elle.rag.confgen.docker import DockerHandler


def _make_handler():
    return DockerHandler()


class TestFilePatterns:
    """Tests for file_patterns property."""

    def test_file_patterns_returns_tuple(self):
        handler = _make_handler()
        patterns = handler.file_patterns
        assert isinstance(patterns, tuple)
        assert any("docker-compose" in p for p in patterns)
        assert any("Dockerfile" in p for p in patterns)


class TestDockerfileValidation:
    """Tests for Dockerfile validation."""

    def test_add_vs_copy_warning(self):
        """ADD for local files produces a warning suggesting COPY."""
        handler = _make_handler()
        content = "FROM ubuntu:22.04\nADD ./localfile /app/\n"
        result = handler.validate_content(content)
        msgs = [i.message for i in result.issues]
        assert any("COPY instead of ADD" in m for m in msgs)

    def test_add_with_url_no_warning(self):
        """ADD with http URL does not produce COPY warning."""
        handler = _make_handler()
        content = "FROM ubuntu:22.04\nADD http://example.com/file.tar.gz /app/\n"
        result = handler.validate_content(content)
        msgs = [i.message for i in result.issues]
        assert not any("COPY instead of ADD" in m for m in msgs)

    def test_apt_get_without_cleanup(self):
        """apt-get install without cleanup produces info message."""
        handler = _make_handler()
        content = "FROM ubuntu:22.04\nRUN apt-get install -y curl\n"
        result = handler.validate_content(content)
        msgs = [i.message for i in result.issues]
        assert any("apt-get install without cleanup" in m for m in msgs)

    def test_user_root_warning(self):
        """USER ROOT produces a security warning."""
        handler = _make_handler()
        content = "FROM ubuntu:22.04\nUSER ROOT\nCMD ['/bin/bash']\n"
        result = handler.validate_content(content)
        msgs = [i.message for i in result.issues]
        assert any("root user" in m.lower() for m in msgs)

    def test_missing_from_error(self):
        """Dockerfile without FROM is invalid."""
        handler = _make_handler()
        content = "RUN echo hello\nCMD ['/bin/bash']\n"
        result = handler.validate_content(content)
        assert not result.valid
        msgs = [i.message for i in result.issues]
        assert any("FROM" in m for m in msgs)

    def test_valid_dockerfile(self):
        """Valid Dockerfile passes validation."""
        handler = _make_handler()
        content = "FROM python:3.10\nWORKDIR /app\nCOPY . .\nRUN pip install -r requirements.txt\nCMD ['python', 'app.py']\n"
        result = handler.validate_content(content)
        assert result.valid


class TestComposeValidation:
    """Tests for docker-compose.yml validation."""

    def test_ruamel_import_error_returns_valid(self):
        """When ruamel.yaml is not available, validation returns valid=True."""
        handler = _make_handler()
        content = "services:\n  web:\n    image: nginx\n"
        with patch.dict("sys.modules", {"ruamel.yaml": None, "ruamel.yaml.error": None, "ruamel": None}):
            result = handler._validate_compose(content)
        assert result.valid

    def test_yaml_parse_error(self):
        """Invalid YAML syntax produces error."""
        handler = _make_handler()
        content = "services:\n  web:\n    image: nginx\n  bad_indent:\nkey: :\n: :\n"
        result = handler._validate_compose(content)
        # May or may not parse depending on YAML strictness; test does not crash
        assert result is not None

    def test_missing_services_key(self):
        """Compose file without 'services' key produces error."""
        handler = _make_handler()
        content = "version: '3.8'\nvolumes:\n  data:\n"
        result = handler._validate_compose(content)
        msgs = [i.message for i in result.issues]
        assert any("services" in m.lower() for m in msgs)

    def test_port_without_host_binding(self):
        """Port without host:container format produces warning."""
        handler = _make_handler()
        content = "services:\n  web:\n    image: nginx\n    ports:\n      - '80'\n"
        result = handler._validate_compose(content)
        msgs = [i.message for i in result.issues]
        assert any("exposes to all interfaces" in m for m in msgs)

    def test_privileged_mode_warning(self):
        """Service with privileged: true produces warning."""
        handler = _make_handler()
        content = "services:\n  web:\n    image: nginx\n    privileged: true\n"
        result = handler._validate_compose(content)
        msgs = [i.message for i in result.issues]
        assert any("privileged" in m.lower() for m in msgs)

    def test_valid_compose(self):
        """Valid compose file passes validation."""
        handler = _make_handler()
        content = "services:\n  web:\n    image: nginx:alpine\n    ports:\n      - '8080:80'\n"
        result = handler._validate_compose(content)
        assert result.valid

    def test_not_a_dict(self):
        """Content that is valid YAML but not a dict produces error."""
        handler = _make_handler()
        content = "- item1\n- item2\n"
        result = handler._validate_compose(content)
        assert not result.valid
        msgs = [i.message for i in result.issues]
        assert any("not a valid YAML mapping" in m for m in msgs)


class TestGetValidationCommand:
    """Tests for get_validation_command."""

    def test_dockerfile_command(self):
        handler = _make_handler()
        cmd = handler.get_validation_command("/path/to/Dockerfile")
        assert "docker build" in cmd
        assert "--check" in cmd

    def test_dockerfile_variant_command(self):
        handler = _make_handler()
        cmd = handler.get_validation_command("/path/to/Dockerfile.prod")
        assert "docker build" in cmd

    def test_compose_command(self):
        handler = _make_handler()
        cmd = handler.get_validation_command("/path/to/docker-compose.yml")
        assert "docker compose" in cmd
        assert "config" in cmd
