from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elle.ops.augeas.yaml_handler import (
    NetplanHandler,
    YAMLChange,
    YAMLEditResult,
    YAMLHandler,
    get_yaml_value,
    is_available,
    load_yaml,
    save_yaml,
    set_yaml_value,
)


# ---------------------------------------------------------------------------
# Module availability
# ---------------------------------------------------------------------------

class TestAvailability:
    def test_is_available_true(self):
        assert is_available() is True

    def test_is_available_false(self):
        with patch("elle.ops.augeas.yaml_handler._get_ruamel", side_effect=ImportError("no")):
            assert is_available() is False


# ---------------------------------------------------------------------------
# YAMLChange / YAMLEditResult
# ---------------------------------------------------------------------------

class TestModels:
    def test_yaml_change(self):
        c = YAMLChange(path="a.b", old_value="1", new_value="2", operation="set")
        assert c.path == "a.b"
        assert c.operation == "set"

    def test_yaml_edit_result(self):
        r = YAMLEditResult(success=True, file_path="/etc/test.yaml")
        assert r.success is True


# ---------------------------------------------------------------------------
# YAMLHandler
# ---------------------------------------------------------------------------

class TestYAMLHandler:
    def test_init(self):
        h = YAMLHandler()
        assert h._yaml is not None
        assert len(h._changes) == 0

    def test_load(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("key: value\nlist:\n  - one\n  - two\n")
        h = YAMLHandler()
        data = h.load(f)
        assert data["key"] == "value"
        assert len(data["list"]) == 2

    def test_load_not_found(self, tmp_path):
        h = YAMLHandler()
        with pytest.raises(FileNotFoundError):
            h.load(tmp_path / "missing.yaml")

    def test_load_empty_file(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("")
        h = YAMLHandler()
        data = h.load(f)
        assert data == {}

    def test_save(self, tmp_path):
        f = tmp_path / "out.yaml"
        h = YAMLHandler()
        h.save(f, {"key": "value"})
        content = f.read_text()
        assert "key" in content
        assert "value" in content

    def test_dumps(self):
        h = YAMLHandler()
        result = h.dumps({"foo": "bar"})
        assert "foo" in result
        assert "bar" in result

    # -- get_path --

    def test_get_path_simple(self):
        h = YAMLHandler()
        data = {"a": {"b": {"c": "val"}}}
        assert h.get_path(data, "a.b.c") == "val"

    def test_get_path_missing(self):
        h = YAMLHandler()
        data = {"a": {"b": "val"}}
        assert h.get_path(data, "a.x.y") is None

    def test_get_path_list_index(self):
        h = YAMLHandler()
        data = {"items": ["a", "b", "c"]}
        assert h.get_path(data, "items.1") == "b"

    def test_get_path_list_out_of_range(self):
        h = YAMLHandler()
        data = {"items": ["a"]}
        assert h.get_path(data, "items.5") is None

    def test_get_path_non_dict(self):
        h = YAMLHandler()
        data = {"a": "string"}
        assert h.get_path(data, "a.b") is None

    def test_get_path_none_intermediate(self):
        h = YAMLHandler()
        data = {"a": None}
        assert h.get_path(data, "a.b") is None

    def test_get_path_digit_on_non_list(self):
        h = YAMLHandler()
        data = {"a": "string"}
        assert h.get_path(data, "a.0") is None

    # -- set_path --

    def test_set_path_simple(self):
        h = YAMLHandler()
        data = {"a": {"b": "old"}}
        h.set_path(data, "a.b", "new")
        assert data["a"]["b"] == "new"
        assert len(h._changes) == 1

    def test_set_path_create_parents(self):
        h = YAMLHandler()
        data: dict[str, Any] = {}
        h.set_path(data, "a.b.c", "val")
        assert data["a"]["b"]["c"] == "val"

    def test_set_path_no_create(self):
        h = YAMLHandler()
        data: dict[str, Any] = {}
        with pytest.raises(KeyError):
            h.set_path(data, "a.b", "val", create_parents=False)

    def test_set_path_list_index(self):
        h = YAMLHandler()
        data = {"items": ["a", "b", "c"]}
        h.set_path(data, "items.1", "x")
        assert data["items"][1] == "x"

    def test_set_path_list_append(self):
        h = YAMLHandler()
        data = {"items": ["a"]}
        h.set_path(data, "items.5", "new")
        assert data["items"][-1] == "new"

    def test_set_path_list_index_on_non_list_in_nav(self):
        h = YAMLHandler()
        data = {"a": "string"}
        with pytest.raises(KeyError):
            h.set_path(data, "a.0.b", "val")

    def test_set_path_digit_key_on_non_list(self):
        h = YAMLHandler()
        data = {"items": "notalist"}
        with pytest.raises(KeyError):
            h.set_path(data, "items.0", "val")

    def test_set_path_navigate_non_dict(self):
        h = YAMLHandler()
        data = {"a": 42}
        with pytest.raises(KeyError):
            h.set_path(data, "a.b.c", "val")

    def test_set_path_index_out_of_range_nav(self):
        h = YAMLHandler()
        data = {"items": ["a"]}
        with pytest.raises(KeyError):
            h.set_path(data, "items.99.sub", "val")

    # -- delete_path --

    def test_delete_path_dict(self):
        h = YAMLHandler()
        data = {"a": {"b": "val", "c": "keep"}}
        result = h.delete_path(data, "a.b")
        assert result is True
        assert "b" not in data["a"]
        assert len(h._changes) == 1

    def test_delete_path_missing(self):
        h = YAMLHandler()
        data = {"a": {"b": "val"}}
        result = h.delete_path(data, "a.x")
        assert result is False

    def test_delete_path_list_index(self):
        h = YAMLHandler()
        data = {"items": ["a", "b", "c"]}
        result = h.delete_path(data, "items.1")
        assert result is True
        assert data["items"] == ["a", "c"]

    def test_delete_path_list_out_of_range(self):
        h = YAMLHandler()
        data = {"items": ["a"]}
        result = h.delete_path(data, "items.5")
        assert result is False

    def test_delete_path_nav_missing(self):
        h = YAMLHandler()
        data = {"a": {"b": "val"}}
        result = h.delete_path(data, "x.y")
        assert result is False

    def test_delete_path_nav_list(self):
        h = YAMLHandler()
        data = {"items": [{"key": "val"}]}
        result = h.delete_path(data, "items.0.key")
        assert result is True
        assert data["items"][0] == {}

    def test_delete_path_nav_list_out_of_range(self):
        h = YAMLHandler()
        data = {"items": []}
        result = h.delete_path(data, "items.0.key")
        assert result is False

    # -- merge --

    def test_merge(self):
        h = YAMLHandler()
        data: dict[str, Any] = {"a": 1, "b": {"c": 2}}
        h.merge(data, {"a": 10, "b": {"c": 20, "d": 30}})
        assert data["a"] == 10
        assert data["b"]["c"] == 20
        assert data["b"]["d"] == 30
        assert len(h._changes) >= 3

    def test_merge_with_path(self):
        h = YAMLHandler()
        data: dict[str, Any] = {"x": 1}
        h.merge(data, {"x": 2}, path="root")
        assert len(h._changes) == 1
        assert h._changes[0].path == "root.x"

    # -- change tracking --

    def test_get_changes(self):
        h = YAMLHandler()
        data = {"a": "old"}
        h.set_path(data, "a", "new")
        changes = h.get_changes()
        assert len(changes) == 1

    def test_clear_changes(self):
        h = YAMLHandler()
        data = {"a": "old"}
        h.set_path(data, "a", "new")
        h.clear_changes()
        assert len(h.get_changes()) == 0

    # -- validate_path --

    def test_validate_path_valid(self):
        h = YAMLHandler()
        assert h.validate_path("a.b.c") is True
        assert h.validate_path("key-name") is True
        assert h.validate_path("key_name") is True
        assert h.validate_path("0") is True

    def test_validate_path_invalid(self):
        h = YAMLHandler()
        assert h.validate_path("") is False
        assert h.validate_path("a..b") is False
        assert h.validate_path(".a") is False
        assert h.validate_path("a.") is False
        assert h.validate_path("a b") is False


# ---------------------------------------------------------------------------
# NetplanHandler
# ---------------------------------------------------------------------------

class TestNetplanHandler:
    def test_init(self):
        h = NetplanHandler()
        assert h._yaml is not None

    def test_get_interface(self):
        h = NetplanHandler()
        data = {"network": {"ethernets": {"eth0": {"dhcp4": True}}}}
        result = h.get_interface(data, "eth0")
        assert result is not None
        assert result["dhcp4"] is True

    def test_get_interface_missing(self):
        h = NetplanHandler()
        data = {"network": {"ethernets": {}}}
        assert h.get_interface(data, "eth0") is None

    def test_get_interface_not_dict(self):
        h = NetplanHandler()
        data = {"network": {"ethernets": {"eth0": "invalid"}}}
        assert h.get_interface(data, "eth0") is None

    def test_set_interface(self):
        h = NetplanHandler()
        data: dict[str, Any] = {"network": {"ethernets": {}}}
        h.set_interface(data, "eth0", {"dhcp4": True})
        assert data["network"]["ethernets"]["eth0"]["dhcp4"] is True

    def test_set_dhcp(self):
        h = NetplanHandler()
        data: dict[str, Any] = {"network": {"ethernets": {"eth0": {}}}}
        h.set_dhcp(data, "eth0", dhcp4=True, dhcp6=False)
        assert data["network"]["ethernets"]["eth0"]["dhcp4"] is True
        assert data["network"]["ethernets"]["eth0"]["dhcp6"] is False

    def test_set_dhcp_partial(self):
        h = NetplanHandler()
        data: dict[str, Any] = {"network": {"ethernets": {"eth0": {}}}}
        h.set_dhcp(data, "eth0", dhcp4=True)
        assert data["network"]["ethernets"]["eth0"]["dhcp4"] is True
        assert "dhcp6" not in data["network"]["ethernets"]["eth0"]

    def test_set_static_ip(self):
        h = NetplanHandler()
        data: dict[str, Any] = {"network": {"ethernets": {"eth0": {}}}}
        h.set_static_ip(
            data,
            "eth0",
            addresses=["192.168.1.10/24"],
            gateway4="192.168.1.1",
            nameservers=["8.8.8.8"],
        )
        assert data["network"]["ethernets"]["eth0"]["dhcp4"] is False
        assert data["network"]["ethernets"]["eth0"]["addresses"] == ["192.168.1.10/24"]

    def test_set_static_ip_no_gateway(self):
        h = NetplanHandler()
        data: dict[str, Any] = {"network": {"ethernets": {"eth0": {}}}}
        h.set_static_ip(data, "eth0", addresses=["10.0.0.1/8"])
        assert data["network"]["ethernets"]["eth0"]["addresses"] == ["10.0.0.1/8"]

    def test_ensure_structure(self):
        h = NetplanHandler()
        data: dict[str, Any] = {}
        h.ensure_structure(data)
        assert "network" in data
        assert data["network"]["version"] == 2

    def test_ensure_structure_existing(self):
        h = NetplanHandler()
        data: dict[str, Any] = {"network": {"version": 2, "ethernets": {}}}
        h.ensure_structure(data)
        assert data["network"]["version"] == 2

    def test_get_renderer(self):
        h = NetplanHandler()
        data = {"network": {"renderer": "networkd"}}
        assert h.get_renderer(data) == "networkd"

    def test_get_renderer_none(self):
        h = NetplanHandler()
        data: dict[str, Any] = {"network": {}}
        assert h.get_renderer(data) is None

    def test_set_renderer(self):
        h = NetplanHandler()
        data: dict[str, Any] = {"network": {}}
        h.set_renderer(data, "NetworkManager")
        assert data["network"]["renderer"] == "NetworkManager"


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

class TestConvenienceFunctions:
    def test_load_yaml(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("key: value\n")
        data = load_yaml(f)
        assert data["key"] == "value"

    def test_save_yaml(self, tmp_path):
        f = tmp_path / "out.yaml"
        save_yaml(f, {"key": "value"})
        content = f.read_text()
        assert "key" in content

    def test_get_yaml_value(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("a:\n  b: 42\n")
        assert get_yaml_value(f, "a.b") == 42

    def test_set_yaml_value(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("a:\n  b: old\n")
        set_yaml_value(f, "a.b", "new")
        data = load_yaml(f)
        assert data["a"]["b"] == "new"
