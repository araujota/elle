from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from elle.capabilities.autogen.models import (
    GeneratedCapabilitySpec,
    InputFieldSpec,
    OutputFieldSpec,
)
from elle.capabilities.autogen.factory import (
    normalize_type,
    sanitize_identifier,
    generate_input_model_code,
    generate_output_model_code,
    generate_capability_class_code,
    compile_capability_class,
    create_capability_from_spec,
    TYPE_MAP,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(
    name: str = "pkg.action",
    input_fields: tuple[InputFieldSpec, ...] = (),
    output_fields: tuple[OutputFieldSpec, ...] = (),
    side_effects: tuple[str, ...] = (),
    verify_command: str | None = None,
    command_template: str = "pkg action {arg}",
) -> GeneratedCapabilitySpec:
    return GeneratedCapabilitySpec(
        name=name,
        display_name="Pkg Action",
        description="Does an action",
        domain="file",
        risk_level="medium",
        side_effects=side_effects,
        input_fields=input_fields,
        output_fields=output_fields,
        command_template=command_template,
        verify_command=verify_command,
        source_command="pkg",
        source_man_section=1,
        confidence_score=0.7,
    )


# ---------------------------------------------------------------------------
# normalize_type
# ---------------------------------------------------------------------------

class TestNormalizeType:

    @pytest.mark.parametrize("raw,expected", [
        ("str", "str"),
        ("string", "str"),
        ("int", "int"),
        ("integer", "int"),
        ("float", "float"),
        ("bool", "bool"),
        ("boolean", "bool"),
        ("path", "Path"),
        ("Path", "Path"),
        ("list", "list"),
        ("list[str]", "list[str]"),
        ("list[int]", "list[int]"),
        ("Optional[str]", "str | None"),
        ("Optional[int]", "int | None"),
        ("unknown_type", "unknown_type"),
    ])
    def test_type_mapping(self, raw, expected):
        assert normalize_type(raw) == expected


# ---------------------------------------------------------------------------
# sanitize_identifier
# ---------------------------------------------------------------------------

class TestSanitizeIdentifier:

    def test_valid_identifier(self):
        assert sanitize_identifier("my_var") == "my_var"

    def test_invalid_chars_replaced(self):
        assert sanitize_identifier("my-var.thing") == "my_var_thing"

    def test_starts_with_digit(self):
        result = sanitize_identifier("1var")
        assert result.startswith("_")

    def test_python_keywords(self):
        for kw in ["class", "def", "return", "import", "from", "if", "else",
                    "for", "while", "try", "except", "with", "as", "pass",
                    "break", "continue", "and", "or", "not", "in", "is",
                    "None", "True", "False"]:
            result = sanitize_identifier(kw)
            assert result == kw + "_"

    def test_empty_string(self):
        assert sanitize_identifier("") == "value"


# ---------------------------------------------------------------------------
# generate_input_model_code
# ---------------------------------------------------------------------------

class TestGenerateInputModelCode:

    def test_no_fields(self):
        spec = _make_spec()
        code = generate_input_model_code(spec)
        assert "pass" in code
        assert "class" in code
        assert "BaseModel" in code

    def test_required_field(self):
        fields = (
            InputFieldSpec(
                name="my_arg",
                field_type="str",
                description="An argument",
                required=True,
            ),
        )
        spec = _make_spec(input_fields=fields)
        code = generate_input_model_code(spec)
        assert "my_arg" in code
        assert "str" in code
        assert "..." in code  # required sentinel

    def test_optional_field(self):
        fields = (
            InputFieldSpec(
                name="opt",
                field_type="int",
                required=False,
            ),
        )
        spec = _make_spec(input_fields=fields)
        code = generate_input_model_code(spec)
        assert "None" in code

    def test_default_value_string(self):
        fields = (
            InputFieldSpec(
                name="val",
                field_type="str",
                required=True,
                default="hello",
            ),
        )
        spec = _make_spec(input_fields=fields)
        code = generate_input_model_code(spec)
        assert 'default="hello"' in code

    def test_default_value_int(self):
        fields = (
            InputFieldSpec(
                name="val",
                field_type="int",
                required=True,
                default=42,
            ),
        )
        spec = _make_spec(input_fields=fields)
        code = generate_input_model_code(spec)
        assert "default=42" in code

    def test_constraints(self):
        fields = (
            InputFieldSpec(
                name="val",
                field_type="str",
                required=True,
                constraints={"min_length": 1, "pattern": "^[a-z]+$"},
            ),
        )
        spec = _make_spec(input_fields=fields)
        code = generate_input_model_code(spec)
        assert "min_length=1" in code
        assert 'pattern="^[a-z]+$"' in code

    def test_optional_field_with_none_type(self):
        fields = (
            InputFieldSpec(
                name="val",
                field_type="Optional[str]",
                required=False,
            ),
        )
        spec = _make_spec(input_fields=fields)
        code = generate_input_model_code(spec)
        # "None" is already in the type, so should not double-add
        assert "str | None" in code


# ---------------------------------------------------------------------------
# generate_output_model_code
# ---------------------------------------------------------------------------

class TestGenerateOutputModelCode:

    def test_default_fields(self):
        spec = _make_spec()
        code = generate_output_model_code(spec)
        assert "success" in code
        assert "output" in code
        assert "return_code" in code

    def test_additional_output_field(self):
        out_fields = (
            OutputFieldSpec(name="success", field_type="bool", description="ok"),
            OutputFieldSpec(name="extra", field_type="str", description="extra data"),
        )
        spec = _make_spec(output_fields=out_fields)
        code = generate_output_model_code(spec)
        assert "extra" in code
        # "success" should appear but not duplicated from built-in


# ---------------------------------------------------------------------------
# generate_capability_class_code
# ---------------------------------------------------------------------------

class TestGenerateCapabilityClassCode:

    def test_basic_class_generation(self):
        spec = _make_spec()
        code = generate_capability_class_code(spec)
        assert "class PkgActionCapability" in code
        assert "def dry_run" in code
        assert "def run" in code
        assert "def verify" in code

    def test_with_side_effects(self):
        spec = _make_spec(side_effects=("file_write", "service_restart"))
        code = generate_capability_class_code(spec)
        assert "FILE_WRITE" in code
        assert "SERVICE_RESTART" in code

    def test_with_verify_command(self):
        spec = _make_spec(verify_command="pkg check {arg}")
        code = generate_capability_class_code(spec)
        assert "pkg check" in code
        assert "subprocess.run" in code

    def test_without_verify_command(self):
        spec = _make_spec(verify_command=None)
        code = generate_capability_class_code(spec)
        assert "return run_result.get('success', False)" in code


# ---------------------------------------------------------------------------
# compile_capability_class
# ---------------------------------------------------------------------------

class TestCompileCapabilityClass:

    def test_compile_success(self):
        spec = _make_spec(name="echo.default", command_template="echo hello")
        input_code = generate_input_model_code(spec)
        output_code = generate_output_model_code(spec)
        cap_code = generate_capability_class_code(spec)

        cap_class = compile_capability_class(spec, input_code, output_code, cap_code)
        assert cap_class is not None

    def test_compile_bad_code(self):
        spec = _make_spec()
        cap_class = compile_capability_class(spec, "x = 1", "y = 1", "INVALID PYTHON ===")
        assert cap_class is None


# ---------------------------------------------------------------------------
# create_capability_from_spec
# ---------------------------------------------------------------------------

class TestCreateCapabilityFromSpec:

    def test_returns_three_code_strings(self):
        spec = _make_spec()
        input_code, output_code, cap_code = create_capability_from_spec(spec)
        assert isinstance(input_code, str)
        assert isinstance(output_code, str)
        assert isinstance(cap_code, str)
        assert "class" in input_code
        assert "class" in output_code
        assert "class" in cap_code
