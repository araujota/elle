"""Capability factory for generating Pydantic models and classes.

Dynamically creates:
1. Input Pydantic model from input_fields
2. Output Pydantic model from output_fields
3. Capability class with templated methods
"""

from __future__ import annotations

import logging
import re
from typing import Any

from elle.capabilities.autogen.models import (
    GeneratedCapabilitySpec,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Type Mapping
# =============================================================================

TYPE_MAP = {
    "str": "str",
    "string": "str",
    "int": "int",
    "integer": "int",
    "float": "float",
    "bool": "bool",
    "boolean": "bool",
    "path": "Path",
    "Path": "Path",
    "list": "list",
    "list[str]": "list[str]",
    "list[int]": "list[int]",
    "Optional[str]": "str | None",
    "Optional[int]": "int | None",
}


def normalize_type(type_str: str) -> str:
    """Normalize a type string to valid Python type annotation.

    Args:
        type_str: Raw type string.

    Returns:
        Normalized type annotation.
    """
    return TYPE_MAP.get(type_str, type_str)


def sanitize_identifier(name: str) -> str:
    """Sanitize a string to be a valid Python identifier.

    Args:
        name: Raw name.

    Returns:
        Valid Python identifier.
    """
    # Replace invalid characters
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    # Ensure doesn't start with digit
    if name and name[0].isdigit():
        name = "_" + name
    # Avoid Python keywords
    if name in (
        "class",
        "def",
        "return",
        "import",
        "from",
        "if",
        "else",
        "for",
        "while",
        "try",
        "except",
        "with",
        "as",
        "pass",
        "break",
        "continue",
        "and",
        "or",
        "not",
        "in",
        "is",
        "None",
        "True",
        "False",
    ):
        name = name + "_"
    return name or "value"


# =============================================================================
# Code Generation
# =============================================================================


def generate_input_model_code(spec: GeneratedCapabilitySpec) -> str:
    """Generate Python code for the input model.

    Args:
        spec: Capability specification.

    Returns:
        Python source code string.
    """
    class_name = spec.name.replace(".", "_").title().replace("_", "") + "Input"

    lines = [
        "from __future__ import annotations",
        "",
        "from pathlib import Path",
        "from pydantic import BaseModel, Field",
        "",
        "",
        f"class {class_name}(BaseModel, frozen=True):",
        f'    """Input for {spec.display_name}."""',
        "",
    ]

    if not spec.input_fields:
        lines.append("    pass")
    else:
        for field in spec.input_fields:
            field_name = sanitize_identifier(field.name)
            field_type = normalize_type(field.field_type)

            # Handle optional fields
            if not field.required and "None" not in field_type:
                field_type = f"{field_type} | None"

            # Build Field() call
            field_args = []
            if field.default is not None:
                if isinstance(field.default, str):
                    field_args.append(f'default="{field.default}"')
                else:
                    field_args.append(f"default={field.default!r}")
            elif not field.required:
                field_args.append("default=None")
            else:
                field_args.append("...")

            if field.description:
                desc = field.description.replace('"', '\\"')[:100]
                field_args.append(f'description="{desc}"')

            # Add constraints
            for key, value in field.constraints.items():
                if isinstance(value, str):
                    field_args.append(f'{key}="{value}"')
                else:
                    field_args.append(f"{key}={value!r}")

            field_def = f"Field({', '.join(field_args)})"
            lines.append(f"    {field_name}: {field_type} = {field_def}")

    return "\n".join(lines)


def generate_output_model_code(spec: GeneratedCapabilitySpec) -> str:
    """Generate Python code for the output model.

    Args:
        spec: Capability specification.

    Returns:
        Python source code string.
    """
    class_name = spec.name.replace(".", "_").title().replace("_", "") + "Output"

    lines = [
        "from __future__ import annotations",
        "",
        "from pydantic import BaseModel, Field",
        "",
        "",
        f"class {class_name}(BaseModel, frozen=True):",
        f'    """Output for {spec.display_name}."""',
        "",
    ]

    # Always include success and output fields
    lines.extend(
        [
            '    success: bool = Field(..., description="Whether the operation succeeded")',
            '    output: str = Field(default="", description="Command output")',
            '    return_code: int = Field(default=0, description="Command return code")',
        ]
    )

    # Add any additional output fields
    for field in spec.output_fields:
        if field.name in ("success", "output", "return_code"):
            continue

        field_name = sanitize_identifier(field.name)
        field_type = normalize_type(field.field_type)

        lines.append(f'    {field_name}: {field_type} = Field(default=None, description="{field.description[:80]}")')

    return "\n".join(lines)


def generate_capability_class_code(spec: GeneratedCapabilitySpec) -> str:
    """Generate Python code for the capability class.

    Args:
        spec: Capability specification.

    Returns:
        Python source code string.
    """
    base_name = spec.name.replace(".", "_").title().replace("_", "")
    class_name = base_name + "Capability"
    input_class = base_name + "Input"
    output_class = base_name + "Output"

    # Escape command template for string
    cmd_template = spec.command_template.replace("\\", "\\\\").replace('"', '\\"')
    verify_template = (spec.verify_command or "").replace("\\", "\\\\").replace('"', '\\"')

    lines = [
        "from __future__ import annotations",
        "",
        "import subprocess",
        "from typing import Any",
        "",
        "from elle.capabilities.models import (",
        "    CapabilitySpec,",
        "    DryRunResult,",
        "    RiskLevel,",
        "    SideEffect,",
        "    SideEffectKind,",
        ")",
        "from elle.capabilities.protocol import Capability",
        "",
        "# Import generated models",
        f"# from .{spec.name.replace('.', '_')}_input import {input_class}",
        f"# from .{spec.name.replace('.', '_')}_output import {output_class}",
        "",
        "",
        f"class {class_name}(Capability):",
        f'    """Auto-generated capability for {spec.display_name}."""',
        "",
        "    @property",
        "    def spec(self) -> CapabilitySpec:",
        '        """Return the capability specification."""',
        "        return CapabilitySpec(",
        f'            name="{spec.name}",',
        f'            description="{spec.description[:100]}",',
        f'            domain="{spec.domain}",',
        f"            risk_level=RiskLevel.{spec.risk_level.upper()},",
        "            side_effects=(",
    ]

    # Add side effects
    for effect in spec.side_effects:
        lines.append("                SideEffect(")
        lines.append(f"                    kind=SideEffectKind.{effect.upper()},")
        lines.append(f'                    description="{effect} by {spec.name}",')
        lines.append("                    reversible=False,")
        lines.append("                ),")

    lines.extend(
        [
            "            ),",
            "            input_schema={},  # TODO: Generate from input fields",
            "            output_schema={},",
            "        )",
            "",
            "    def _build_command(self, input_data: Any) -> str:",
            '        """Build command string from input."""',
            f'        template = "{cmd_template}"',
            "        # Substitute input fields",
            "        if hasattr(input_data, 'model_dump'):",
            "            values = input_data.model_dump()",
            "        elif isinstance(input_data, dict):",
            "            values = input_data",
            "        else:",
            "            values = {}",
            "        for key, value in values.items():",
            "            if value is not None:",
            '                template = template.replace("{" + key + "}", str(value))',
            "        return template",
            "",
            "    def dry_run(self, input_data: Any) -> DryRunResult:",
            '        """Preview what the capability would do."""',
            "        command = self._build_command(input_data)",
            "        return DryRunResult(",
            "            would_execute=(command,),",
            '            preview_text=f"Would run: {command}",',
            "            estimated_duration_sec=5.0,",
            "        )",
            "",
            "    def run(self, input_data: Any) -> Any:",
            '        """Execute the capability."""',
            "        command = self._build_command(input_data)",
            "        try:",
            "            result = subprocess.run(",
            "                command,",
            "                shell=True,  # nosec B602",
            "                capture_output=True,",
            "                text=True,",
            "                timeout=60,",
            "            )",
            "            return {",
            "                'success': result.returncode == 0,",
            "                'output': result.stdout + result.stderr,",
            "                'return_code': result.returncode,",
            "            }",
            "        except subprocess.TimeoutExpired:",
            "            return {",
            "                'success': False,",
            "                'output': 'Command timed out',",
            "                'return_code': -1,",
            "            }",
            "        except Exception as e:",
            "            return {",
            "                'success': False,",
            "                'output': f\"Error: {e}\",",
            "                'return_code': -1,",
            "            }",
        ]
    )

    # Add verify method if verify_command is provided
    if spec.verify_command:
        lines.extend(
            [
                "",
                "    def verify(self, input_data: Any, run_result: Any) -> bool:",
                '        """Verify the operation completed successfully."""',
                f'        template = "{verify_template}"',
                "        if hasattr(input_data, 'model_dump'):",
                "            values = input_data.model_dump()",
                "        elif isinstance(input_data, dict):",
                "            values = input_data",
                "        else:",
                "            values = {}",
                "        for key, value in values.items():",
                "            if value is not None:",
                '                template = template.replace("{" + key + "}", str(value))',
                "        try:",
                "            result = subprocess.run(",
                "                template,",
                "                shell=True,  # nosec B602",
                "                capture_output=True,",
                "                timeout=30,",
                "            )",
                "            return result.returncode == 0",
                "        except Exception:",
                "            return False",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "    def verify(self, input_data: Any, run_result: Any) -> bool:",
                '        """Verify the operation completed successfully."""',
                "        return run_result.get('success', False)",
            ]
        )

    return "\n".join(lines)


# =============================================================================
# Dynamic Class Creation
# =============================================================================


def compile_capability_class(
    spec: GeneratedCapabilitySpec,
    input_model_code: str,
    output_model_code: str,
    capability_code: str,
) -> type | None:
    """Compile and return a capability class from generated code.

    Args:
        spec: Capability specification.
        input_model_code: Generated input model code.
        output_model_code: Generated output model code.
        capability_code: Generated capability class code.

    Returns:
        Compiled capability class or None on error.
    """
    try:
        # Compile input model
        input_namespace: dict[str, Any] = {}
        exec(input_model_code, input_namespace)  # nosec B102
        input_class_name = spec.name.replace(".", "_").title().replace("_", "") + "Input"
        input_class = input_namespace.get(input_class_name)

        # Compile output model
        output_namespace: dict[str, Any] = {}
        exec(output_model_code, output_namespace)  # nosec B102
        output_class_name = spec.name.replace(".", "_").title().replace("_", "") + "Output"
        output_class = output_namespace.get(output_class_name)

        # Compile capability class
        cap_namespace: dict[str, Any] = {
            "input_class": input_class,
            "output_class": output_class,
        }
        exec(capability_code, cap_namespace)  # nosec B102
        cap_class_name = spec.name.replace(".", "_").title().replace("_", "") + "Capability"
        cap_class = cap_namespace.get(cap_class_name)

        return cap_class

    except Exception as e:
        logger.error(f"Failed to compile capability class: {e}")
        return None


# =============================================================================
# Factory Functions
# =============================================================================


def create_capability_from_spec(
    spec: GeneratedCapabilitySpec,
) -> tuple[str, str, str]:
    """Create all code artifacts for a capability spec.

    Args:
        spec: Capability specification.

    Returns:
        Tuple of (input_model_code, output_model_code, capability_code).
    """
    input_code = generate_input_model_code(spec)
    output_code = generate_output_model_code(spec)
    cap_code = generate_capability_class_code(spec)

    return input_code, output_code, cap_code
