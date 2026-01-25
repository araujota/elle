"""LLM prompt templates for capability generation.

Provides system and user prompts for generating capability specifications
from man page documentation.
"""

from __future__ import annotations

from elle.capabilities.autogen.models import ParsedManPage

# =============================================================================
# System Prompt
# =============================================================================

CAPABILITY_GEN_SYSTEM_PROMPT = """\
You are a capability specification generator for ELLE, a system administration AI.
Your job is to generate safe, well-typed capability specifications from Unix man pages.

## RISK LEVEL GUIDELINES

Assign risk based on potential for damage:

- **none**: Read-only queries with no side effects
  Examples: systemctl status, docker ps, df -h, cat, ls

- **low**: Creates files in temp dirs, minor state changes
  Examples: touch, mkdir in /tmp, docker logs

- **medium**: Modifies user files, restarts services, affects running processes
  Examples: systemctl restart, docker stop, kill, chmod on user files

- **high**: Deletes files, modifies system config, affects multiple services
  Examples: rm, docker rm, systemctl disable, config file edits

- **critical**: Affects boot, auth, can brick system, irreversible changes
  Examples: passwd, visudo, grub-update, dd, mkfs, fdisk

## DOMAIN GUIDELINES

Choose the most specific domain:

- **service**: systemd/init control (systemctl, service)
- **file**: File operations (cp, mv, rm, touch, chmod, chown)
- **config**: Configuration file editing (specific config tools)
- **network**: Network tools (ip, ss, netplan, ufw, iptables, wg)
- **package**: Package management (apt, dpkg, snap)
- **docker**: Container operations (docker, podman, crictl)
- **auth**: User/group/permission management (useradd, passwd, chmod)

## INPUT FIELD GUIDELINES

- Only include fields that are necessary for the capability's primary purpose
- Use Python type annotations (str, int, bool, Path, list[str])
- Mark optional fields with required=False and provide sensible defaults
- Use descriptive field names in snake_case
- Add constraints where appropriate (min_length, pattern, etc.)

## COMMAND TEMPLATE GUIDELINES

- Use {field_name} placeholders for input fields
- Include proper quoting for paths: '"{path}"'
- Include common safety flags where appropriate (--no-preserve-root for rm)
- For dangerous operations, include dry-run or preview variants

## OUTPUT GUIDELINES

Respond with a JSON object matching this schema:
{
    "name": "domain.action",
    "display_name": "Human Readable Name",
    "description": "What this capability does",
    "domain": "service|file|config|network|package|docker|auth",
    "risk_level": "none|low|medium|high|critical",
    "side_effects": ["file_write", "service_change", etc.],
    "input_fields": [
        {
            "name": "field_name",
            "field_type": "str",
            "description": "What this field does",
            "required": true,
            "default": null,
            "constraints": {}
        }
    ],
    "output_fields": [
        {
            "name": "success",
            "field_type": "bool",
            "description": "Whether the operation succeeded"
        }
    ],
    "command_template": "command {arg1} {arg2}",
    "verify_command": "command --status {arg1}",
    "rollback_command": null,
    "source_command": "original_command",
    "source_man_section": 1,
    "confidence_score": 0.85
}

## SIDE EFFECT KINDS

Available side effects:
- file_write: Creates or modifies files
- file_delete: Removes files
- service_change: Starts/stops/restarts services
- network_change: Modifies network configuration
- privilege_escalation: Changes permissions or capabilities
- package_change: Installs/removes packages
- container_change: Affects containers
- notification_sent: Sends notifications
- incident_created: Creates incidents
"""


# =============================================================================
# User Prompt Template
# =============================================================================


def build_generation_prompt(man_page: ParsedManPage) -> str:
    """Build a user prompt for capability generation.

    Args:
        man_page: Parsed man page to generate from.

    Returns:
        User prompt string.
    """
    prompt_parts = [
        f"Generate a capability specification for the `{man_page.name}` command.",
        "",
        f"## Man Page: {man_page.name}({man_page.section})",
        "",
    ]

    # Add synopsis
    if man_page.synopsis:
        prompt_parts.extend([
            "### SYNOPSIS",
            "```",
            man_page.synopsis.raw,
            "```",
            "",
        ])

    # Add description
    if man_page.description:
        prompt_parts.extend([
            "### DESCRIPTION",
            man_page.description[:500],
            "",
        ])

    # Add flags (limit to most important)
    if man_page.flags:
        prompt_parts.extend([
            "### OPTIONS (partial list)",
        ])
        for flag in man_page.flags[:20]:  # Limit to 20 flags
            if flag.long:
                flag_str = f"{flag.short}, {flag.long}" if flag.short else flag.long
            else:
                flag_str = flag.short or f"--{flag.name}"

            if flag.metavar:
                flag_str += f"={flag.metavar}"

            prompt_parts.append(f"- `{flag_str}`: {flag.description[:100]}")
        prompt_parts.append("")

    # Add examples
    if man_page.examples:
        prompt_parts.extend([
            "### EXAMPLES",
        ])
        for ex in man_page.examples[:5]:  # Limit to 5 examples
            prompt_parts.append("```")
            prompt_parts.append(ex.command)
            prompt_parts.append("```")
            if ex.description:
                prompt_parts.append(f"_{ex.description}_")
        prompt_parts.append("")

    # Add generation instructions
    prompt_parts.extend([
        "## Instructions",
        "",
        "1. Analyze the command's purpose and typical use cases",
        "2. Identify the most common and useful operation(s)",
        "3. Generate a capability spec for the PRIMARY use case",
        "4. Assign appropriate risk level based on potential for damage",
        "5. Include only necessary input fields",
        "6. Set confidence_score based on how well-defined the command is",
        "",
        "Respond with ONLY the JSON specification, no explanation.",
    ])

    return "\n".join(prompt_parts)


# =============================================================================
# Subcommand Analysis Prompt
# =============================================================================


def build_subcommand_prompt(
    man_page: ParsedManPage,
    subcommand: str,
) -> str:
    """Build a prompt for analyzing a specific subcommand.

    Args:
        man_page: Parsed man page.
        subcommand: Specific subcommand to analyze.

    Returns:
        User prompt string.
    """
    prompt_parts = [
        f"Generate a capability specification for `{man_page.name} {subcommand}`.",
        "",
        f"## Command: {man_page.name} {subcommand}",
        "",
        "This is a SUBCOMMAND of the main command. Focus only on this specific operation.",
        "",
    ]

    # Add synopsis
    if man_page.synopsis:
        prompt_parts.extend([
            "### SYNOPSIS",
            "```",
            man_page.synopsis.raw,
            "```",
            "",
        ])

    # Add description
    if man_page.description:
        prompt_parts.extend([
            "### DESCRIPTION",
            man_page.description[:300],
            "",
        ])

    # Add relevant flags
    if man_page.flags:
        prompt_parts.extend([
            "### OPTIONS",
        ])
        for flag in man_page.flags[:15]:
            if flag.long:
                flag_str = f"{flag.short}, {flag.long}" if flag.short else flag.long
            else:
                flag_str = flag.short or f"--{flag.name}"
            prompt_parts.append(f"- `{flag_str}`: {flag.description[:80]}")
        prompt_parts.append("")

    prompt_parts.extend([
        "## Instructions",
        "",
        f"Generate a capability spec specifically for `{man_page.name} {subcommand}`.",
        f"The capability name should be `{man_page.name}.{subcommand.replace('-', '_')}`.",
        "",
        "Respond with ONLY the JSON specification.",
    ])

    return "\n".join(prompt_parts)


# =============================================================================
# Validation Prompt
# =============================================================================


def build_validation_prompt(
    spec_json: str,
    man_page: ParsedManPage,
) -> str:
    """Build a prompt for validating a generated spec.

    Args:
        spec_json: JSON string of generated spec.
        man_page: Original man page for reference.

    Returns:
        Validation prompt string.
    """
    return f"""\
Review this capability specification for correctness and safety.

## Generated Specification
```json
{spec_json}
```

## Original Man Page: {man_page.name}({man_page.section})

### Available Flags
{chr(10).join(f"- {f.long or f.short}: {f.description[:50]}" for f in man_page.flags[:30])}

## Validation Tasks

1. **Flag Verification**: Are all referenced flags valid for this command?
2. **Risk Assessment**: Is the risk level appropriate?
3. **Side Effects**: Are all side effects listed?
4. **Safety**: Are there any dangerous patterns in the command template?

Respond with JSON:
{{
    "valid": true/false,
    "issues": ["list of issues found"],
    "suggestions": ["list of improvements"],
    "adjusted_risk": "same|none|low|medium|high|critical"
}}
"""
