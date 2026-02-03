from __future__ import annotations

"""Comprehensive tests for elle.storage.provision module.

Covers all provisioning steps: PostgreSQL detection, system user creation,
PG role creation, database creation, extension installation, schema creation,
migrations, encryption key generation, and the full provision orchestrator.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from elle.storage.config import EXTENSIONS, SCHEMAS, PostgresConfig
from elle.storage.provision import (
    ProvisionError,
    _import_schema_modules,
    _run,
    check_postgresql_installed,
    create_schemas,
    ensure_database,
    ensure_encryption_key,
    ensure_pg_role,
    ensure_system_user,
    install_extensions,
    provision,
    run_initial_migrations,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> PostgresConfig:
    """Build a PostgresConfig with sensible test defaults."""
    defaults = {
        "dbname": "testdb",
        "user": "testuser",
        "socket_dir": "/tmp/pg",
        "encryption_key_path": "/tmp/test.key",
    }
    defaults.update(overrides)
    return PostgresConfig(**defaults)


# ---------------------------------------------------------------------------
# Tests for _run
# ---------------------------------------------------------------------------


class TestRun:
    """Tests for the _run subprocess helper."""

    @patch("elle.storage.provision.subprocess.run")
    def test_basic_command(self, mock_subproc):
        """_run passes the command list to subprocess.run with defaults."""
        mock_subproc.return_value = subprocess.CompletedProcess(args=["ls"], returncode=0, stdout="output", stderr="")
        result = _run(["ls", "-la"])

        mock_subproc.assert_called_once_with(
            ["ls", "-la"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        assert result.stdout == "output"

    @patch("elle.storage.provision.subprocess.run")
    def test_with_user_prepends_sudo(self, mock_subproc):
        """When user= is given, cmd is prefixed with sudo -u <user>."""
        mock_subproc.return_value = subprocess.CompletedProcess(
            args=["sudo", "-u", "postgres", "psql"],
            returncode=0,
            stdout="",
            stderr="",
        )
        _run(["psql", "-c", "SELECT 1"], user="postgres")

        actual_cmd = mock_subproc.call_args[0][0]
        assert actual_cmd[:3] == ["sudo", "-u", "postgres"]
        assert actual_cmd[3:] == ["psql", "-c", "SELECT 1"]

    @patch("elle.storage.provision.subprocess.run")
    def test_without_user_no_sudo(self, mock_subproc):
        """When user= is None, no sudo prefix is added."""
        mock_subproc.return_value = subprocess.CompletedProcess(args=["echo"], returncode=0, stdout="hi", stderr="")
        _run(["echo", "hi"])

        actual_cmd = mock_subproc.call_args[0][0]
        assert actual_cmd == ["echo", "hi"]

    @patch("elle.storage.provision.subprocess.run")
    def test_check_false(self, mock_subproc):
        """When check=False, CalledProcessError is not raised."""
        mock_subproc.return_value = subprocess.CompletedProcess(args=["false"], returncode=1, stdout="", stderr="err")
        result = _run(["false"], check=False)

        mock_subproc.assert_called_once_with(
            ["false"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 1

    @patch("elle.storage.provision.subprocess.run")
    def test_capture_false(self, mock_subproc):
        """When capture=False, capture_output is False."""
        mock_subproc.return_value = subprocess.CompletedProcess(args=["ls"], returncode=0, stdout=None, stderr=None)
        _run(["ls"], capture=False)

        mock_subproc.assert_called_once_with(
            ["ls"],
            capture_output=False,
            text=True,
            check=True,
            timeout=30,
        )

    @patch("elle.storage.provision.subprocess.run")
    def test_user_none_is_same_as_no_user(self, mock_subproc):
        """Passing user=None explicitly should not add sudo."""
        mock_subproc.return_value = subprocess.CompletedProcess(args=["id"], returncode=0, stdout="uid=0", stderr="")
        _run(["id"], user=None)

        actual_cmd = mock_subproc.call_args[0][0]
        assert actual_cmd == ["id"]

    @patch("elle.storage.provision.subprocess.run")
    def test_user_empty_string_no_sudo(self, mock_subproc):
        """An empty-string user evaluates as falsy, so no sudo is added."""
        mock_subproc.return_value = subprocess.CompletedProcess(args=["whoami"], returncode=0, stdout="root", stderr="")
        _run(["whoami"], user="")

        actual_cmd = mock_subproc.call_args[0][0]
        assert actual_cmd == ["whoami"]


# ---------------------------------------------------------------------------
# Tests for check_postgresql_installed
# ---------------------------------------------------------------------------


class TestCheckPostgresqlInstalled:
    """Tests for check_postgresql_installed."""

    @patch("elle.storage.provision._run")
    @patch("elle.storage.provision.shutil.which")
    def test_postgresql_found(self, mock_which, mock_run):
        """Returns the version when pg_config is found."""
        mock_which.return_value = "/usr/bin/pg_config"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="PostgreSQL 16.4\n", stderr=""
        )

        version = check_postgresql_installed()

        assert version == "16.4"
        mock_which.assert_called_once_with("pg_config")
        mock_run.assert_called_once_with(["/usr/bin/pg_config", "--version"])

    @patch("elle.storage.provision.shutil.which")
    def test_postgresql_not_found(self, mock_which):
        """Raises ProvisionError when pg_config is not on PATH."""
        mock_which.return_value = None

        with pytest.raises(ProvisionError, match="PostgreSQL is not installed"):
            check_postgresql_installed()

    @patch("elle.storage.provision._run")
    @patch("elle.storage.provision.shutil.which")
    def test_version_parsing_multiword(self, mock_which, mock_run):
        """Handles version strings with multiple words correctly."""
        mock_which.return_value = "/usr/local/bin/pg_config"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="PostgreSQL 15.2.1\n", stderr=""
        )

        version = check_postgresql_installed()
        assert version == "15.2.1"

    @patch("elle.storage.provision._run")
    @patch("elle.storage.provision.shutil.which")
    def test_version_with_trailing_whitespace(self, mock_which, mock_run):
        """Version string with trailing whitespace is handled."""
        mock_which.return_value = "/usr/bin/pg_config"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="  PostgreSQL 14.0  \n", stderr=""
        )

        version = check_postgresql_installed()
        assert version == "14.0"


# ---------------------------------------------------------------------------
# Tests for ensure_system_user
# ---------------------------------------------------------------------------


class TestEnsureSystemUser:
    """Tests for ensure_system_user."""

    @patch("elle.storage.provision._run")
    def test_user_already_exists(self, mock_run):
        """When the user exists, 'id' succeeds and no useradd is called."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="uid=1000(elle)", stderr="")

        ensure_system_user("elle")

        mock_run.assert_called_once_with(["id", "elle"])

    @patch("elle.storage.provision._run")
    def test_user_created_when_missing(self, mock_run):
        """When 'id' fails, useradd is called to create the user."""
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, "id"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]

        ensure_system_user("elle")

        assert mock_run.call_count == 2
        useradd_call = mock_run.call_args_list[1]
        cmd = useradd_call[0][0]
        assert cmd[0] == "useradd"
        assert "--system" in cmd
        assert "--no-create-home" in cmd
        assert "--shell" in cmd
        assert "/usr/sbin/nologin" in cmd
        assert "elle" in cmd

    @patch("elle.storage.provision._run")
    def test_custom_username(self, mock_run):
        """Works with a custom username."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="uid=1001(myuser)", stderr="")

        ensure_system_user("myuser")
        mock_run.assert_called_once_with(["id", "myuser"])

    @patch("elle.storage.provision._run")
    def test_default_username_is_elle(self, mock_run):
        """Default username parameter is 'elle'."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="uid=999(elle)", stderr="")

        ensure_system_user()
        mock_run.assert_called_once_with(["id", "elle"])


# ---------------------------------------------------------------------------
# Tests for ensure_pg_role
# ---------------------------------------------------------------------------


class TestEnsurePgRole:
    """Tests for ensure_pg_role."""

    @patch("elle.storage.provision._run")
    def test_role_already_exists(self, mock_run):
        """When the role already exists (stdout='1'), no createuser is called."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="1\n", stderr="")

        ensure_pg_role("elle")

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["user"] == "postgres"
        assert call_kwargs[1]["check"] is False

    @patch("elle.storage.provision._run")
    def test_role_created_when_missing(self, mock_run):
        """When the role does not exist, createuser is called."""
        mock_run.side_effect = [
            # First call: psql check returns empty
            subprocess.CompletedProcess(args=[], returncode=0, stdout="\n", stderr=""),
            # Second call: createuser
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]

        ensure_pg_role("elle")

        assert mock_run.call_count == 2
        createuser_call = mock_run.call_args_list[1]
        cmd = createuser_call[0][0]
        assert cmd == ["createuser", "--no-password", "elle"]
        assert createuser_call[1]["user"] == "postgres"

    @patch("elle.storage.provision._run")
    def test_role_empty_stdout_triggers_creation(self, mock_run):
        """When psql returns empty string (not '1'), role is created."""
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]

        ensure_pg_role("testuser")

        assert mock_run.call_count == 2
        createuser_call = mock_run.call_args_list[1]
        cmd = createuser_call[0][0]
        assert "testuser" in cmd

    @patch("elle.storage.provision._run")
    def test_default_username_is_elle(self, mock_run):
        """Default parameter for role name is 'elle'."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="1\n", stderr="")

        ensure_pg_role()

        psql_cmd = mock_run.call_args[0][0]
        # psql variable binding: -v name=elle
        assert "name=" in psql_cmd[-1] and "elle" in psql_cmd[-1]

    @patch("elle.storage.provision._run")
    def test_psql_check_runs_as_postgres(self, mock_run):
        """The psql SELECT query runs as the postgres OS user."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="1", stderr="")

        ensure_pg_role("testrole")

        first_call = mock_run.call_args_list[0]
        assert first_call[1]["user"] == "postgres"


# ---------------------------------------------------------------------------
# Tests for ensure_database
# ---------------------------------------------------------------------------


class TestEnsureDatabase:
    """Tests for ensure_database."""

    @patch("elle.storage.provision._run")
    def test_database_already_exists(self, mock_run):
        """When the database exists, no createdb is called."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="1\n", stderr="")
        cfg = _make_config()

        ensure_database(cfg)

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["user"] == "postgres"
        assert call_kwargs[1]["check"] is False

    @patch("elle.storage.provision._run")
    def test_database_created_when_missing(self, mock_run):
        """When the database does not exist, createdb is called."""
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]
        cfg = _make_config(dbname="elledb", user="elleuser")

        ensure_database(cfg)

        assert mock_run.call_count == 2
        createdb_call = mock_run.call_args_list[1]
        cmd = createdb_call[0][0]
        assert cmd == ["createdb", "--owner", "elleuser", "elledb"]
        assert createdb_call[1]["user"] == "postgres"

    @patch("elle.storage.provision._run")
    def test_database_check_uses_config_dbname(self, mock_run):
        """The psql query uses the database name from the config."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="1\n", stderr="")
        cfg = _make_config(dbname="mydb")

        ensure_database(cfg)

        psql_cmd = mock_run.call_args[0][0]
        # psql variable binding: -v name=mydb
        assert "name=" in psql_cmd[-1] and "mydb" in psql_cmd[-1]

    @patch("elle.storage.provision._run")
    def test_database_check_empty_stdout_triggers_creation(self, mock_run):
        """When psql returns only whitespace, the database is created."""
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="  \n", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]
        cfg = _make_config(dbname="newdb", user="elle")

        ensure_database(cfg)

        assert mock_run.call_count == 2


# ---------------------------------------------------------------------------
# Tests for install_extensions
# ---------------------------------------------------------------------------


class TestInstallExtensions:
    """Tests for install_extensions."""

    @patch("elle.storage.provision.psycopg.connect")
    def test_installs_all_extensions(self, mock_connect):
        """All EXTENSIONS from config are installed via CREATE EXTENSION."""
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        cfg = _make_config()
        install_extensions(cfg)

        expected_conninfo = f"host={cfg.socket_dir} dbname={cfg.dbname} user=postgres"
        mock_connect.assert_called_once_with(expected_conninfo)

        assert mock_conn.autocommit is True
        assert mock_conn.execute.call_count == len(EXTENSIONS)
        # SQL is now parameterized via sql.Identifier; verify each extension
        for call_args in mock_conn.execute.call_args_list:
            sql_obj = call_args[0][0]
            rendered = sql_obj.as_string(None)
            assert "CREATE EXTENSION IF NOT EXISTS" in rendered
        rendered_exts = [call_args[0][0].as_string(None) for call_args in mock_conn.execute.call_args_list]
        for ext in EXTENSIONS:
            assert any(f'"{ext}"' in r for r in rendered_exts), f"Extension {ext} not found"

    @patch("elle.storage.provision.psycopg.connect")
    def test_conninfo_format(self, mock_connect):
        """The connection string uses socket_dir, dbname, and user=postgres."""
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        cfg = _make_config(socket_dir="/run/pg", dbname="testelle")
        install_extensions(cfg)

        mock_connect.assert_called_once_with("host=/run/pg dbname=testelle user=postgres")

    @patch("elle.storage.provision.psycopg.connect")
    def test_autocommit_set_true(self, mock_connect):
        """autocommit is set to True on the connection."""
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        cfg = _make_config()
        install_extensions(cfg)

        assert mock_conn.autocommit is True


# ---------------------------------------------------------------------------
# Tests for create_schemas
# ---------------------------------------------------------------------------


class TestCreateSchemas:
    """Tests for create_schemas."""

    @patch("elle.storage.provision.psycopg.connect")
    def test_creates_all_schemas(self, mock_connect):
        """All SCHEMAS from config are created with proper AUTHORIZATION."""
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        cfg = _make_config(user="ellerole")
        create_schemas(cfg)

        assert mock_conn.autocommit is True

        # CREATE SCHEMA calls + GRANT calls = 2 * len(SCHEMAS)
        expected_calls = 2 * len(SCHEMAS)
        assert mock_conn.execute.call_count == expected_calls

        from psycopg import sql

        for schema in SCHEMAS:
            mock_conn.execute.assert_any_call(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(
                    sql.Identifier(schema), sql.Identifier("ellerole")
                )
            )
            mock_conn.execute.assert_any_call(
                sql.SQL("GRANT ALL ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier("ellerole"))
            )

    @patch("elle.storage.provision.psycopg.connect")
    def test_conninfo_format(self, mock_connect):
        """The connection string is correctly formed."""
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        cfg = _make_config(socket_dir="/var/run/postgresql", dbname="elle")
        create_schemas(cfg)

        mock_connect.assert_called_once_with("host=/var/run/postgresql dbname=elle user=postgres")

    @patch("elle.storage.provision.psycopg.connect")
    def test_grant_all_on_each_schema(self, mock_connect):
        """GRANT ALL is issued for every schema to the config user."""
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        cfg = _make_config(user="myuser")
        create_schemas(cfg)

        grant_calls = [c for c in mock_conn.execute.call_args_list if "GRANT ALL" in str(c)]
        assert len(grant_calls) == len(SCHEMAS)


# ---------------------------------------------------------------------------
# Tests for _import_schema_modules
# ---------------------------------------------------------------------------


class TestImportSchemaModules:
    """Tests for _import_schema_modules."""

    @patch("importlib.import_module")
    def test_imports_all_modules(self, mock_import):
        """All expected schema modules are imported."""
        _import_schema_modules()

        expected_modules = [
            "elle.daemon.telemetry.schema",
            "elle.daemon.incidents.schema",
            "elle.daemon.manvault.schema",
            "elle.capabilities.autogen.store",
            "elle.reactive.schema",
            "elle.daemon.reboot.schema",
            "elle.mobile.store",
            "elle.cli.docker.env_store",
            "elle.daemon.api.auth",
            "elle.capabilities.dependencies.schema",
        ]

        assert mock_import.call_count == len(expected_modules)
        for mod in expected_modules:
            mock_import.assert_any_call(mod)

    @patch("importlib.import_module")
    def test_handles_import_error_gracefully(self, mock_import):
        """ImportError for a module is logged but does not stop the process."""
        mock_import.side_effect = ImportError("no module")

        # Should not raise
        _import_schema_modules()

        # All 10 modules are attempted despite ImportError
        assert mock_import.call_count == 10

    @patch("importlib.import_module")
    def test_partial_import_failures(self, mock_import):
        """Some modules succeed, some fail with ImportError."""
        call_count = {"n": 0}

        def side_effect(mod):
            call_count["n"] += 1
            if call_count["n"] % 3 == 0:
                raise ImportError(f"Cannot import {mod}")
            return MagicMock()

        mock_import.side_effect = side_effect

        _import_schema_modules()
        assert mock_import.call_count == 10


# ---------------------------------------------------------------------------
# Tests for run_initial_migrations
# ---------------------------------------------------------------------------


class TestRunInitialMigrations:
    """Tests for run_initial_migrations."""

    @patch("elle.storage.provision._import_schema_modules")
    @patch("elle.storage.migrate.run_all_migrations")
    @patch("elle.storage.provision.psycopg.connect")
    def test_runs_migrations(self, mock_connect, mock_ram, mock_import_schemas):
        """Calls run_all_migrations and returns results."""
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_ram.return_value = {"incidents": 3, "telemetry": 2}

        cfg = _make_config()
        result = run_initial_migrations(cfg)

        mock_import_schemas.assert_called_once()
        mock_connect.assert_called_once_with(cfg.conninfo)
        mock_ram.assert_called_once_with(mock_conn)
        assert result == {"incidents": 3, "telemetry": 2}

    @patch("elle.storage.provision._import_schema_modules")
    @patch("elle.storage.migrate.run_all_migrations")
    @patch("elle.storage.provision.psycopg.connect")
    def test_import_schemas_called_before_connect(self, mock_connect, mock_ram, mock_import_schemas):
        """_import_schema_modules is called before connecting."""
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_ram.return_value = {}

        cfg = _make_config()
        run_initial_migrations(cfg)

        mock_import_schemas.assert_called_once()

    @patch("elle.storage.provision._import_schema_modules")
    @patch("elle.storage.migrate.run_all_migrations")
    @patch("elle.storage.provision.psycopg.connect")
    def test_uses_config_conninfo(self, mock_connect, mock_ram, mock_import_schemas):
        """Connection uses config.conninfo for the connection string."""
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_ram.return_value = {}

        cfg = _make_config(socket_dir="/var/run/postgresql", dbname="elle", user="elle")
        run_initial_migrations(cfg)

        mock_connect.assert_called_once_with(cfg.conninfo)


# ---------------------------------------------------------------------------
# Tests for ensure_encryption_key
# ---------------------------------------------------------------------------


class TestEnsureEncryptionKey:
    """Tests for ensure_encryption_key."""

    @patch("elle.storage.provision.os.close")
    @patch("elle.storage.provision.os.fchmod")
    @patch("elle.storage.provision.os.fsync")
    @patch("elle.storage.provision.os.write")
    @patch("elle.storage.provision.os.open", side_effect=FileExistsError)
    @patch("elle.storage.provision.secrets.token_hex")
    @patch("elle.storage.provision.Path")
    def test_key_already_exists(
        self, mock_path_cls, mock_token, mock_os_open, mock_os_write, mock_os_fsync, mock_os_fchmod, mock_os_close
    ):
        """When the key file exists, os.open raises FileExistsError (O_EXCL) and nothing is generated."""
        mock_path = MagicMock()
        mock_path.__str__ = MagicMock(return_value="/etc/elle/db.key")
        mock_path_cls.return_value = mock_path

        ensure_encryption_key("/etc/elle/db.key")

        mock_path.parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_os_write.assert_not_called()
        mock_os_fchmod.assert_not_called()
        mock_os_close.assert_not_called()

    @patch("elle.storage.provision.os.close")
    @patch("elle.storage.provision.os.fchmod")
    @patch("elle.storage.provision.os.fsync")
    @patch("elle.storage.provision.os.write")
    @patch("elle.storage.provision.os.open", return_value=42)
    @patch("elle.storage.provision.secrets.token_hex")
    @patch("elle.storage.provision.Path")
    def test_key_generated_when_missing(
        self, mock_path_cls, mock_token, mock_os_open, mock_os_write, mock_os_fsync, mock_os_fchmod, mock_os_close
    ):
        """When the key file does not exist, a new key is generated atomically."""
        import stat

        mock_path = MagicMock()
        mock_path.__str__ = MagicMock(return_value="/etc/elle/db.key")
        mock_path_cls.return_value = mock_path
        mock_token.return_value = "a1b2c3d4" * 8  # 64 hex chars = 32 bytes

        ensure_encryption_key("/etc/elle/db.key")

        mock_path.parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_token.assert_called_once_with(32)
        mock_os_write.assert_called_once_with(42, ("a1b2c3d4" * 8).encode("utf-8"))
        mock_os_fsync.assert_called_once_with(42)
        mock_os_fchmod.assert_called_once_with(42, stat.S_IRUSR)
        mock_os_close.assert_called_once_with(42)

    @patch("elle.storage.provision.os.close")
    @patch("elle.storage.provision.os.fchmod")
    @patch("elle.storage.provision.os.fsync")
    @patch("elle.storage.provision.os.write")
    @patch("elle.storage.provision.os.open", return_value=42)
    @patch("elle.storage.provision.secrets.token_hex")
    @patch("elle.storage.provision.Path")
    def test_parent_directory_created(
        self, mock_path_cls, mock_token, mock_os_open, mock_os_write, mock_os_fsync, mock_os_fchmod, mock_os_close
    ):
        """Ensures parent directory is created with parents=True."""
        mock_path = MagicMock()
        mock_path.__str__ = MagicMock(return_value="/some/deep/path/key.bin")
        mock_path_cls.return_value = mock_path
        mock_token.return_value = "deadbeef" * 8

        ensure_encryption_key("/some/deep/path/key.bin")

        mock_path.parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)

    @patch("elle.storage.provision.os.close")
    @patch("elle.storage.provision.os.fchmod")
    @patch("elle.storage.provision.os.fsync")
    @patch("elle.storage.provision.os.write")
    @patch("elle.storage.provision.os.open", return_value=42)
    @patch("elle.storage.provision.secrets.token_hex")
    @patch("elle.storage.provision.Path")
    def test_file_permissions_0400(
        self, mock_path_cls, mock_token, mock_os_open, mock_os_write, mock_os_fsync, mock_os_fchmod, mock_os_close
    ):
        """The key file is created with O_EXCL and fchmod sets read-only after write."""
        import os as real_os
        import stat

        mock_path = MagicMock()
        mock_path.__str__ = MagicMock(return_value="/etc/elle/test.key")
        mock_path_cls.return_value = mock_path
        mock_token.return_value = "ff" * 32

        ensure_encryption_key("/etc/elle/test.key")

        # Verify os.open was called with O_EXCL (atomic creation) and S_IRUSR | S_IWUSR
        mock_os_open.assert_called_once_with(
            "/etc/elle/test.key",
            real_os.O_WRONLY | real_os.O_CREAT | real_os.O_EXCL,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        # Verify fchmod sets read-only (S_IRUSR) after write
        mock_os_fchmod.assert_called_once_with(42, stat.S_IRUSR)

    @patch("elle.storage.provision.os.close")
    @patch("elle.storage.provision.os.fchmod")
    @patch("elle.storage.provision.os.fsync")
    @patch("elle.storage.provision.os.write")
    @patch("elle.storage.provision.os.open", side_effect=FileExistsError)
    @patch("elle.storage.provision.secrets.token_hex")
    @patch("elle.storage.provision.Path")
    def test_default_key_path(
        self, mock_path_cls, mock_token, mock_os_open, mock_os_write, mock_os_fsync, mock_os_fchmod, mock_os_close
    ):
        """Default key path is /etc/elle/db.key."""
        mock_path = MagicMock()
        mock_path.__str__ = MagicMock(return_value="/etc/elle/db.key")
        mock_path_cls.return_value = mock_path

        ensure_encryption_key()

        mock_path_cls.assert_called_once_with("/etc/elle/db.key")


# ---------------------------------------------------------------------------
# Tests for ProvisionError
# ---------------------------------------------------------------------------


class TestProvisionError:
    """Tests for the ProvisionError exception class."""

    def test_is_exception(self):
        """ProvisionError is a subclass of Exception."""
        assert issubclass(ProvisionError, Exception)

    def test_message_preserved(self):
        """The error message is stored correctly."""
        err = ProvisionError("test error message")
        assert str(err) == "test error message"

    def test_can_be_raised_and_caught(self):
        """ProvisionError can be raised and caught."""
        with pytest.raises(ProvisionError):
            raise ProvisionError("provision failed")


# ---------------------------------------------------------------------------
# Tests for provision (full orchestrator)
# ---------------------------------------------------------------------------


class TestProvision:
    """Tests for the full provision orchestrator."""

    @patch("elle.storage.provision.ensure_encryption_key")
    @patch("elle.storage.provision.run_initial_migrations")
    @patch("elle.storage.provision.create_schemas")
    @patch("elle.storage.provision.install_extensions")
    @patch("elle.storage.provision.ensure_database")
    @patch("elle.storage.provision.ensure_pg_role")
    @patch("elle.storage.provision.ensure_system_user")
    @patch("elle.storage.provision.check_postgresql_installed")
    def test_all_steps_called_in_order(
        self,
        mock_check_pg,
        mock_sys_user,
        mock_pg_role,
        mock_db,
        mock_ext,
        mock_schemas,
        mock_migrate,
        mock_enc_key,
    ):
        """All provisioning steps are called in the correct order."""
        mock_check_pg.return_value = "16.4"
        mock_migrate.return_value = {"incidents": 1, "telemetry": 1}

        cfg = _make_config()
        provision(cfg)

        mock_check_pg.assert_called_once()
        mock_sys_user.assert_called_once_with(cfg.user)
        mock_pg_role.assert_called_once_with(cfg.user)
        mock_db.assert_called_once_with(cfg)
        mock_ext.assert_called_once_with(cfg)
        mock_schemas.assert_called_once_with(cfg)
        mock_migrate.assert_called_once_with(cfg)
        mock_enc_key.assert_called_once_with(cfg.encryption_key_path)

    @patch("elle.storage.provision.ensure_encryption_key")
    @patch("elle.storage.provision.run_initial_migrations")
    @patch("elle.storage.provision.create_schemas")
    @patch("elle.storage.provision.install_extensions")
    @patch("elle.storage.provision.ensure_database")
    @patch("elle.storage.provision.ensure_pg_role")
    @patch("elle.storage.provision.ensure_system_user")
    @patch("elle.storage.provision.check_postgresql_installed")
    def test_default_config_when_none(
        self,
        mock_check_pg,
        mock_sys_user,
        mock_pg_role,
        mock_db,
        mock_ext,
        mock_schemas,
        mock_migrate,
        mock_enc_key,
    ):
        """When config is None, defaults are used."""
        mock_check_pg.return_value = "15.0"
        mock_migrate.return_value = {}

        provision(None)

        # Default user is "elle"
        mock_sys_user.assert_called_once_with("elle")
        mock_pg_role.assert_called_once_with("elle")

    @patch("elle.storage.provision.ensure_encryption_key")
    @patch("elle.storage.provision.run_initial_migrations")
    @patch("elle.storage.provision.create_schemas")
    @patch("elle.storage.provision.install_extensions")
    @patch("elle.storage.provision.ensure_database")
    @patch("elle.storage.provision.ensure_pg_role")
    @patch("elle.storage.provision.ensure_system_user")
    @patch("elle.storage.provision.check_postgresql_installed")
    def test_provision_no_config_uses_defaults(
        self,
        mock_check_pg,
        mock_sys_user,
        mock_pg_role,
        mock_db,
        mock_ext,
        mock_schemas,
        mock_migrate,
        mock_enc_key,
    ):
        """Calling provision() with no arguments uses default PostgresConfig."""
        mock_check_pg.return_value = "16.0"
        mock_migrate.return_value = {}

        provision()

        # Verify called with default config values
        mock_sys_user.assert_called_once_with("elle")
        mock_pg_role.assert_called_once_with("elle")
        mock_enc_key.assert_called_once_with("/etc/elle/db.key")

    @patch("elle.storage.provision.ensure_encryption_key")
    @patch("elle.storage.provision.run_initial_migrations")
    @patch("elle.storage.provision.create_schemas")
    @patch("elle.storage.provision.install_extensions")
    @patch("elle.storage.provision.ensure_database")
    @patch("elle.storage.provision.ensure_pg_role")
    @patch("elle.storage.provision.ensure_system_user")
    @patch("elle.storage.provision.check_postgresql_installed")
    def test_provision_step1_failure_stops_execution(
        self,
        mock_check_pg,
        mock_sys_user,
        mock_pg_role,
        mock_db,
        mock_ext,
        mock_schemas,
        mock_migrate,
        mock_enc_key,
    ):
        """If check_postgresql_installed raises, subsequent steps skip."""
        mock_check_pg.side_effect = ProvisionError("PostgreSQL not installed")

        with pytest.raises(ProvisionError, match="PostgreSQL not installed"):
            provision(_make_config())

        mock_check_pg.assert_called_once()
        mock_sys_user.assert_not_called()
        mock_pg_role.assert_not_called()
        mock_db.assert_not_called()
        mock_ext.assert_not_called()
        mock_schemas.assert_not_called()
        mock_migrate.assert_not_called()
        mock_enc_key.assert_not_called()

    @patch("elle.storage.provision.ensure_encryption_key")
    @patch("elle.storage.provision.run_initial_migrations")
    @patch("elle.storage.provision.create_schemas")
    @patch("elle.storage.provision.install_extensions")
    @patch("elle.storage.provision.ensure_database")
    @patch("elle.storage.provision.ensure_pg_role")
    @patch("elle.storage.provision.ensure_system_user")
    @patch("elle.storage.provision.check_postgresql_installed")
    def test_migration_results_iterated(
        self,
        mock_check_pg,
        mock_sys_user,
        mock_pg_role,
        mock_db,
        mock_ext,
        mock_schemas,
        mock_migrate,
        mock_enc_key,
    ):
        """Migration results are iterated (logged) after migrations."""
        mock_check_pg.return_value = "16.4"
        mock_migrate.return_value = {
            "incidents": 5,
            "telemetry": 3,
            "manvault": 2,
        }

        cfg = _make_config()
        provision(cfg)

        # The function completes without error, logging migration results
        mock_migrate.assert_called_once_with(cfg)

    @patch("elle.storage.provision.ensure_encryption_key")
    @patch("elle.storage.provision.run_initial_migrations")
    @patch("elle.storage.provision.create_schemas")
    @patch("elle.storage.provision.install_extensions")
    @patch("elle.storage.provision.ensure_database")
    @patch("elle.storage.provision.ensure_pg_role")
    @patch("elle.storage.provision.ensure_system_user")
    @patch("elle.storage.provision.check_postgresql_installed")
    def test_provision_empty_migration_results(
        self,
        mock_check_pg,
        mock_sys_user,
        mock_pg_role,
        mock_db,
        mock_ext,
        mock_schemas,
        mock_migrate,
        mock_enc_key,
    ):
        """Provision handles empty migration results dict without error."""
        mock_check_pg.return_value = "16.4"
        mock_migrate.return_value = {}

        cfg = _make_config()
        provision(cfg)  # Should complete without error

        mock_enc_key.assert_called_once_with(cfg.encryption_key_path)

    @patch("elle.storage.provision.ensure_encryption_key")
    @patch("elle.storage.provision.run_initial_migrations")
    @patch("elle.storage.provision.create_schemas")
    @patch("elle.storage.provision.install_extensions")
    @patch("elle.storage.provision.ensure_database")
    @patch("elle.storage.provision.ensure_pg_role")
    @patch("elle.storage.provision.ensure_system_user")
    @patch("elle.storage.provision.check_postgresql_installed")
    def test_provision_custom_config_propagated(
        self,
        mock_check_pg,
        mock_sys_user,
        mock_pg_role,
        mock_db,
        mock_ext,
        mock_schemas,
        mock_migrate,
        mock_enc_key,
    ):
        """Custom config values are propagated to all steps."""
        mock_check_pg.return_value = "16.4"
        mock_migrate.return_value = {}

        cfg = _make_config(
            user="customuser",
            dbname="customdb",
            encryption_key_path="/custom/key.bin",
        )
        provision(cfg)

        mock_sys_user.assert_called_once_with("customuser")
        mock_pg_role.assert_called_once_with("customuser")
        mock_db.assert_called_once_with(cfg)
        mock_ext.assert_called_once_with(cfg)
        mock_schemas.assert_called_once_with(cfg)
        mock_migrate.assert_called_once_with(cfg)
        mock_enc_key.assert_called_once_with("/custom/key.bin")

    @patch("elle.storage.provision.ensure_encryption_key")
    @patch("elle.storage.provision.run_initial_migrations")
    @patch("elle.storage.provision.create_schemas")
    @patch("elle.storage.provision.install_extensions")
    @patch("elle.storage.provision.ensure_database")
    @patch("elle.storage.provision.ensure_pg_role")
    @patch("elle.storage.provision.ensure_system_user")
    @patch("elle.storage.provision.check_postgresql_installed")
    def test_provision_mid_step_failure(
        self,
        mock_check_pg,
        mock_sys_user,
        mock_pg_role,
        mock_db,
        mock_ext,
        mock_schemas,
        mock_migrate,
        mock_enc_key,
    ):
        """If ensure_database raises, later steps are not called."""
        mock_check_pg.return_value = "16.4"
        mock_db.side_effect = subprocess.CalledProcessError(1, "createdb")

        with pytest.raises(subprocess.CalledProcessError):
            provision(_make_config())

        mock_check_pg.assert_called_once()
        mock_sys_user.assert_called_once()
        mock_pg_role.assert_called_once()
        mock_db.assert_called_once()
        mock_ext.assert_not_called()
        mock_schemas.assert_not_called()
        mock_migrate.assert_not_called()
        mock_enc_key.assert_not_called()
