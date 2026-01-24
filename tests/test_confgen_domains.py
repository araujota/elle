"""Tests for confgen domain handlers."""

from __future__ import annotations

from pathlib import Path

import pytest

from elle.rag.confgen.domains import (
    CronHandler,
    DomainHandler,
    FstabHandler,
    GeneralHandler,
    HostsHandler,
    NetplanHandler,
    SshdHandler,
    SystemdHandler,
    UfwHandler,
    detect_domain,
    detect_file_type,
    get_handler,
)
from elle.rag.confgen.models import ConfigOp


class TestNetplanHandler:
    """Tests for NetplanHandler."""

    def test_domain(self) -> None:
        """Test domain property."""
        handler = NetplanHandler()
        assert handler.domain == "network"

    def test_file_type(self) -> None:
        """Test file_type property."""
        handler = NetplanHandler()
        assert handler.file_type == "yaml"

    def test_matches_netplan_paths(self) -> None:
        """Test matching netplan file paths."""
        handler = NetplanHandler()

        assert handler.matches_path("/etc/netplan/01-netcfg.yaml") is True
        assert handler.matches_path("/etc/netplan/50-cloud-init.yml") is True
        assert handler.matches_path("/etc/ssh/sshd_config") is False

    def test_schema_hint(self) -> None:
        """Test schema hint."""
        handler = NetplanHandler()
        schema = handler.get_schema_hint("/etc/netplan/01-netcfg.yaml")

        assert schema is not None
        assert "network" in schema
        assert "version" in schema["network"]

    def test_validation_command(self) -> None:
        """Test validation command."""
        handler = NetplanHandler()
        cmd = handler.get_validation_command("/etc/netplan/01-netcfg.yaml")

        assert cmd == "netplan try"

    def test_template(self) -> None:
        """Test template."""
        handler = NetplanHandler()
        template = handler.get_template()

        assert template is not None
        assert "network:" in template
        assert "version: 2" in template

    def test_validate_content_valid_yaml(self) -> None:
        """Test validating valid YAML content."""
        handler = NetplanHandler()
        content = """network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
"""
        validation = handler.validate_content(content)
        assert validation.valid is True

    def test_validate_content_invalid_yaml(self) -> None:
        """Test validating invalid YAML content."""
        handler = NetplanHandler()
        content = "network: {invalid yaml"

        validation = handler.validate_content(content)
        assert validation.valid is False

    def test_validate_content_missing_network_key(self) -> None:
        """Test validating YAML without network key."""
        handler = NetplanHandler()
        content = """version: 2
ethernets:
  eth0:
    dhcp4: true
"""
        validation = handler.validate_content(content)
        assert validation.valid is False


class TestFstabHandler:
    """Tests for FstabHandler."""

    def test_domain(self) -> None:
        """Test domain property."""
        handler = FstabHandler()
        assert handler.domain == "filesystem"

    def test_matches_fstab(self) -> None:
        """Test matching fstab path."""
        handler = FstabHandler()

        assert handler.matches_path("/etc/fstab") is True
        assert handler.matches_path("/etc/fstab.bak") is False

    def test_validation_command(self) -> None:
        """Test validation command."""
        handler = FstabHandler()
        cmd = handler.get_validation_command("/etc/fstab")

        assert cmd == "mount -a --fake"

    def test_validate_operations_critical_mount_removal(self) -> None:
        """Test warning when removing critical mount."""
        handler = FstabHandler()
        ops = (ConfigOp(kind="rm", path="/files/etc/fstab/entry[file='/']"),)

        validation = handler.validate_operations(ops)

        # Should have a warning about critical mount
        assert any("critical" in i.message.lower() for i in validation.issues)


class TestSshdHandler:
    """Tests for SshdHandler."""

    def test_domain(self) -> None:
        """Test domain property."""
        handler = SshdHandler()
        assert handler.domain == "ssh"

    def test_matches_sshd_config(self) -> None:
        """Test matching sshd_config path."""
        handler = SshdHandler()

        assert handler.matches_path("/etc/ssh/sshd_config") is True
        assert handler.matches_path("/etc/ssh/sshd_config.d/custom.conf") is True
        assert handler.matches_path("/etc/ssh/ssh_config") is False

    def test_validation_command(self) -> None:
        """Test validation command."""
        handler = SshdHandler()
        cmd = handler.get_validation_command("/etc/ssh/sshd_config")

        assert cmd == "sshd -t"

    def test_validate_security_setting_warning(self) -> None:
        """Test warning for insecure setting."""
        handler = SshdHandler()
        ops = (
            ConfigOp(
                kind="set",
                path="/files/etc/ssh/sshd_config/PermitRootLogin",
                value="yes",
            ),
        )

        validation = handler.validate_operations(ops)

        # Should have a security warning
        assert any("security" in i.message.lower() for i in validation.issues)

    def test_validate_invalid_port(self) -> None:
        """Test error for invalid port."""
        handler = SshdHandler()
        ops = (
            ConfigOp(
                kind="set",
                path="/files/etc/ssh/sshd_config/Port",
                value="99999",
            ),
        )

        validation = handler.validate_operations(ops)

        assert validation.valid is False
        assert any("port" in i.message.lower() for i in validation.issues)

    def test_validate_privileged_port_info(self) -> None:
        """Test info for privileged port."""
        handler = SshdHandler()
        ops = (
            ConfigOp(
                kind="set",
                path="/files/etc/ssh/sshd_config/Port",
                value="22",
            ),
        )

        validation = handler.validate_operations(ops)

        # Valid port, but should have info about privileges
        assert any("privileged" in i.message.lower() for i in validation.issues)


class TestHostsHandler:
    """Tests for HostsHandler."""

    def test_domain(self) -> None:
        """Test domain property."""
        handler = HostsHandler()
        assert handler.domain == "hosts"

    def test_matches_hosts(self) -> None:
        """Test matching hosts path."""
        handler = HostsHandler()

        assert handler.matches_path("/etc/hosts") is True


class TestSystemdHandler:
    """Tests for SystemdHandler."""

    def test_domain(self) -> None:
        """Test domain property."""
        handler = SystemdHandler()
        assert handler.domain == "service"

    def test_file_type(self) -> None:
        """Test file_type property."""
        handler = SystemdHandler()
        assert handler.file_type == "systemd"

    def test_matches_service_files(self) -> None:
        """Test matching service files."""
        handler = SystemdHandler()

        assert handler.matches_path("/etc/systemd/system/myapp.service") is True
        assert handler.matches_path("/etc/systemd/system/backup.timer") is True
        assert handler.matches_path("/lib/systemd/system/nginx.service") is True

    def test_schema_hint(self) -> None:
        """Test schema hint."""
        handler = SystemdHandler()
        schema = handler.get_schema_hint("/etc/systemd/system/myapp.service")

        assert schema is not None
        assert "[Unit]" in schema
        assert "[Service]" in schema
        assert "[Install]" in schema

    def test_template(self) -> None:
        """Test template."""
        handler = SystemdHandler()
        template = handler.get_template()

        assert template is not None
        assert "[Unit]" in template
        assert "[Service]" in template
        assert "ExecStart" in template


class TestCronHandler:
    """Tests for CronHandler."""

    def test_domain(self) -> None:
        """Test domain property."""
        handler = CronHandler()
        assert handler.domain == "cron"

    def test_matches_crontab(self) -> None:
        """Test matching crontab paths."""
        handler = CronHandler()

        assert handler.matches_path("/etc/crontab") is True
        assert handler.matches_path("/etc/cron.d/myjob") is True

    def test_validate_content_valid(self) -> None:
        """Test validating valid cron content."""
        handler = CronHandler()
        content = """# Cron job
0 * * * * /usr/bin/script.sh
30 2 * * * /usr/bin/backup.sh
"""
        validation = handler.validate_content(content)
        assert validation.valid is True

    def test_validate_content_with_comments(self) -> None:
        """Test validating cron with comments and variables."""
        handler = CronHandler()
        content = """# My cron
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin
0 5 * * * /usr/bin/script.sh
"""
        validation = handler.validate_content(content)
        assert validation.valid is True


class TestUfwHandler:
    """Tests for UfwHandler."""

    def test_domain(self) -> None:
        """Test domain property."""
        handler = UfwHandler()
        assert handler.domain == "firewall"

    def test_matches_ufw_config(self) -> None:
        """Test matching UFW config paths."""
        handler = UfwHandler()

        assert handler.matches_path("/etc/ufw/ufw.conf") is True
        assert handler.matches_path("/etc/ufw/user.rules") is True

    def test_validation_command(self) -> None:
        """Test validation command."""
        handler = UfwHandler()
        cmd = handler.get_validation_command("/etc/ufw/ufw.conf")

        assert cmd == "ufw status verbose"


class TestGeneralHandler:
    """Tests for GeneralHandler."""

    def test_domain(self) -> None:
        """Test domain property."""
        handler = GeneralHandler()
        assert handler.domain == "general"

    def test_file_type(self) -> None:
        """Test file_type property."""
        handler = GeneralHandler()
        assert handler.file_type == "text"

    def test_matches_everything(self) -> None:
        """Test matching any path."""
        handler = GeneralHandler()

        assert handler.matches_path("/any/path") is True
        assert handler.matches_path("/etc/unknown.conf") is True


class TestDomainDetection:
    """Tests for domain detection functions."""

    def test_detect_network_domain(self) -> None:
        """Test detecting network domain."""
        assert detect_domain("/etc/netplan/01-netcfg.yaml") == "network"
        assert detect_domain("/etc/netplan/custom.yml") == "network"

    def test_detect_filesystem_domain(self) -> None:
        """Test detecting filesystem domain."""
        assert detect_domain("/etc/fstab") == "filesystem"

    def test_detect_ssh_domain(self) -> None:
        """Test detecting SSH domain."""
        assert detect_domain("/etc/ssh/sshd_config") == "ssh"
        assert detect_domain("/etc/ssh/sshd_config.d/custom.conf") == "ssh"

    def test_detect_service_domain(self) -> None:
        """Test detecting service domain."""
        assert detect_domain("/etc/systemd/system/myapp.service") == "service"

    def test_detect_cron_domain(self) -> None:
        """Test detecting cron domain."""
        assert detect_domain("/etc/crontab") == "cron"
        assert detect_domain("/etc/cron.d/myjob") == "cron"

    def test_detect_firewall_domain(self) -> None:
        """Test detecting firewall domain."""
        assert detect_domain("/etc/ufw/ufw.conf") == "firewall"

    def test_detect_hosts_domain(self) -> None:
        """Test detecting hosts domain."""
        assert detect_domain("/etc/hosts") == "hosts"

    def test_detect_general_domain(self) -> None:
        """Test detecting general/unknown domain."""
        assert detect_domain("/etc/unknown.conf") == "general"
        assert detect_domain("/tmp/config.txt") == "general"


class TestFileTypeDetection:
    """Tests for file type detection functions."""

    def test_detect_yaml_type(self) -> None:
        """Test detecting YAML file type."""
        assert detect_file_type("/etc/netplan/config.yaml") == "yaml"
        assert detect_file_type("/etc/netplan/config.yml") == "yaml"

    def test_detect_json_type(self) -> None:
        """Test detecting JSON file type."""
        assert detect_file_type("/etc/app/config.json") == "json"

    def test_detect_toml_type(self) -> None:
        """Test detecting TOML file type."""
        assert detect_file_type("/etc/app/config.toml") == "toml"

    def test_detect_markdown_type(self) -> None:
        """Test detecting markdown file type."""
        assert detect_file_type("/docs/README.md") == "markdown"
        assert detect_file_type("/docs/guide.markdown") == "markdown"

    def test_detect_systemd_type(self) -> None:
        """Test detecting systemd file type."""
        assert detect_file_type("/etc/systemd/system/app.service") == "systemd"
        assert detect_file_type("/etc/systemd/system/backup.timer") == "systemd"

    def test_detect_text_type(self) -> None:
        """Test detecting text file type (fallback)."""
        assert detect_file_type("/etc/unknown.conf") == "text"
        assert detect_file_type("/tmp/file") == "text"


class TestGetHandler:
    """Tests for get_handler function."""

    def test_get_network_handler(self) -> None:
        """Test getting network handler."""
        handler = get_handler("network")
        assert isinstance(handler, NetplanHandler)

    def test_get_filesystem_handler(self) -> None:
        """Test getting filesystem handler."""
        handler = get_handler("filesystem")
        assert isinstance(handler, FstabHandler)

    def test_get_ssh_handler(self) -> None:
        """Test getting SSH handler."""
        handler = get_handler("ssh")
        assert isinstance(handler, SshdHandler)

    def test_get_service_handler(self) -> None:
        """Test getting service handler."""
        handler = get_handler("service")
        assert isinstance(handler, SystemdHandler)

    def test_get_general_handler(self) -> None:
        """Test getting general handler."""
        handler = get_handler("general")
        assert isinstance(handler, GeneralHandler)

    def test_get_unknown_returns_general(self) -> None:
        """Test getting unknown domain returns general handler."""
        handler = get_handler("unknown")  # type: ignore
        assert isinstance(handler, GeneralHandler)


class TestShellInjectionDetection:
    """Tests for shell injection detection in domain handlers."""

    def test_detect_command_substitution(self) -> None:
        """Test detecting $() command substitution."""
        handler = GeneralHandler()
        ops = (ConfigOp(kind="set", path="/test", value="$(rm -rf /)"),)

        validation = handler.validate_operations(ops)

        assert validation.valid is False
        assert any("injection" in i.message.lower() for i in validation.issues)

    def test_detect_backtick_substitution(self) -> None:
        """Test detecting backtick command substitution."""
        handler = GeneralHandler()
        ops = (ConfigOp(kind="set", path="/test", value="`whoami`"),)

        validation = handler.validate_operations(ops)

        assert validation.valid is False

    def test_detect_command_chaining(self) -> None:
        """Test detecting command chaining."""
        handler = GeneralHandler()
        ops = (ConfigOp(kind="set", path="/test", value="value; rm -rf /"),)

        validation = handler.validate_operations(ops)

        assert validation.valid is False

    def test_detect_pipe_to_command(self) -> None:
        """Test detecting pipe to command."""
        handler = GeneralHandler()
        ops = (ConfigOp(kind="set", path="/test", value="data | bash"),)

        validation = handler.validate_operations(ops)

        assert validation.valid is False

    def test_safe_value_passes(self) -> None:
        """Test that safe values pass validation."""
        handler = GeneralHandler()
        ops = (ConfigOp(kind="set", path="/test", value="normal value"),)

        validation = handler.validate_operations(ops)

        assert validation.valid is True
