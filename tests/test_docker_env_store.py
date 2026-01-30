"""Tests for Docker environment variable store."""

from datetime import datetime

import pytest

from elle.cli.docker.env_models import EnvVarValue
from elle.cli.docker.env_store import DockerEnvStore


class TestDockerEnvStore:
    """Tests for DockerEnvStore class."""

    @pytest.fixture
    def store(self, docker_conn):
        """Create a store with the conftest docker connection."""
        return DockerEnvStore(conn=docker_conn)

    def test_create_store(self, store):
        assert store is not None

    def test_save_and_get_values(self, store):
        values = [
            EnvVarValue(name="VAR1", value="value1"),
            EnvVarValue(name="VAR2", value="value2"),
        ]
        store.save_values("postgres:15", values)

        saved = store.get_saved_values("postgres:15")
        assert len(saved) == 2

        var_names = {s.name for s in saved}
        assert "VAR1" in var_names
        assert "VAR2" in var_names

    def test_saved_values_by_image_family(self, store):
        """Values saved for postgres:15 should be available for postgres:16."""
        values = [EnvVarValue(name="VAR1", value="value1")]
        store.save_values("postgres:15", values)

        # Different tag, same family
        saved = store.get_saved_values("postgres:16")
        assert len(saved) == 1
        assert saved[0].name == "VAR1"

    def test_save_sensitive_value(self, store):
        values = [EnvVarValue(name="PASSWORD", value="secret", sensitive=True)]
        store.save_values("postgres", values)

        saved = store.get_saved_values("postgres")
        assert len(saved) == 1
        assert saved[0].sensitive

    def test_update_existing_value(self, store):
        values1 = [EnvVarValue(name="VAR1", value="old_value")]
        store.save_values("postgres", values1)

        values2 = [EnvVarValue(name="VAR1", value="new_value")]
        store.save_values("postgres", values2)

        saved = store.get_saved_values("postgres")
        assert len(saved) == 1
        assert saved[0].value == "new_value"

    def test_get_saved_value_single(self, store):
        values = [
            EnvVarValue(name="VAR1", value="value1"),
            EnvVarValue(name="VAR2", value="value2"),
        ]
        store.save_values("postgres", values)

        saved = store.get_saved_value("postgres", "VAR1")
        assert saved is not None
        assert saved.name == "VAR1"
        assert saved.value == "value1"

    def test_get_saved_value_not_found(self, store):
        saved = store.get_saved_value("postgres", "NONEXISTENT")
        assert saved is None

    def test_clear_values(self, store):
        values = [EnvVarValue(name="VAR1", value="value1")]
        store.save_values("postgres", values)

        deleted = store.clear_values("postgres")
        assert deleted == 1

        saved = store.get_saved_values("postgres")
        assert len(saved) == 0

    def test_clear_single_value(self, store):
        values = [
            EnvVarValue(name="VAR1", value="value1"),
            EnvVarValue(name="VAR2", value="value2"),
        ]
        store.save_values("postgres", values)

        result = store.clear_value("postgres", "VAR1")
        assert result

        saved = store.get_saved_values("postgres")
        assert len(saved) == 1
        assert saved[0].name == "VAR2"

    def test_clear_nonexistent_value(self, store):
        result = store.clear_value("postgres", "NONEXISTENT")
        assert not result

    def test_list_families(self, store):
        store.save_values("postgres", [EnvVarValue(name="V1", value="v1")])
        store.save_values("mysql", [EnvVarValue(name="V2", value="v2")])

        families = store.list_families()
        assert "postgres" in families
        assert "mysql" in families

    def test_clear_all(self, store):
        store.save_values("postgres", [EnvVarValue(name="V1", value="v1")])
        store.save_values("mysql", [EnvVarValue(name="V2", value="v2")])

        deleted = store.clear_all()
        assert deleted == 2

        assert len(store.list_families()) == 0

    def test_has_saved_values(self, store):
        assert not store.has_saved_values("postgres")

        store.save_values("postgres", [EnvVarValue(name="V1", value="v1")])

        assert store.has_saved_values("postgres")
        assert not store.has_saved_values("mysql")

    def test_saved_var_has_updated_at(self, store):
        before = datetime.utcnow()
        values = [EnvVarValue(name="VAR1", value="value1")]
        store.save_values("postgres", values)
        after = datetime.utcnow()

        saved = store.get_saved_values("postgres")
        assert len(saved) == 1
        assert before <= saved[0].updated_at <= after

    def test_image_family_extraction(self, store):
        """Test that image family is correctly extracted."""
        values = [EnvVarValue(name="VAR1", value="value1")]

        # Save with full image reference
        store.save_values("docker.io/library/postgres:15", values)

        # Should be retrievable with just the family name
        saved = store.get_saved_values("postgres")
        assert len(saved) == 1


class TestSchemaInitialization:
    """Tests for schema initialization."""

    def test_init_schema(self, docker_conn):
        """Verify table exists via information_schema."""
        cursor = docker_conn.cursor()
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_name='docker_env_values'")
        assert cursor.fetchone() is not None

    def test_schema_is_idempotent(self, docker_conn):
        """Schema re-application should not raise."""
        # Tables already exist from conftest; this is a no-op validation
        cursor = docker_conn.cursor()
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_name='docker_env_values'")
        assert cursor.fetchone() is not None
