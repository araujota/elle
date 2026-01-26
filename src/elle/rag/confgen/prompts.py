"""Prompt templates for LLM-driven configuration generation.

Contains system prompts, templates, and JSON schemas for
structured output generation.
"""

from __future__ import annotations

from typing import Any

# =============================================================================
# System Prompts
# =============================================================================

BASE_SYSTEM_PROMPT = """You are ELLE, a local Linux system configuration assistant for Ubuntu 24.04.

Your role is to generate precise, minimal configuration changes based on natural language requests.

CRITICAL RULES:
1. Generate ONLY valid JSON matching the specified schema
2. Follow documented syntax exactly (from provided man pages/documentation)
3. Prefer reversible, additive changes over destructive ones
4. Never generate sudo commands - privileged ops are handled separately
5. Be conservative - only change what is explicitly requested
6. Include validation commands when possible
7. Warn about risks but do not refuse reasonable requests

SAFETY GUIDELINES:
- Never modify /etc/passwd, /etc/shadow, or /etc/group
- Never touch /boot, /dev, or /proc directories
- Always suggest backup commands for critical configs
- Flag any changes that could affect system boot or network access
"""

CLASSIFICATION_SYSTEM_PROMPT = """You are a configuration domain classifier.

Given a configuration request, identify:
1. The domain (network, filesystem, ssh, firewall, service, cron, package, hosts, general)
2. The file type (augeas, yaml, json, toml, text, markdown, systemd)
3. The target file path (if identifiable)
4. Key entities (IPs, hostnames, interface names, service names)

Respond with valid JSON only.
"""


# =============================================================================
# Edit Prompt Template
# =============================================================================

EDIT_PROMPT_TEMPLATE = """
TASK: Generate configuration operations to modify an existing file.

REQUEST: {request}
TARGET FILE: {target_path}
FILE TYPE: {file_type}
DOMAIN: {domain}

CURRENT CONTENT:
```
{current_content}
```

{man_context}

{prior_context}

Generate minimal operations to achieve the request. Each operation should:
- Use the correct path syntax for the file type
- Include a clear explanation
- Change only what is necessary

Respond with valid JSON matching this schema:
```json
{{
  "title": "Short title for the change",
  "explanation": "Detailed explanation of what this does",
  "operations": [
    {{
      "kind": "set|rm|ins|mv|append|replace",
      "path": "path.to.setting or /augeas/path",
      "value": "new value (for set/ins/append)",
      "explanation": "why this operation"
    }}
  ],
  "validation_command": "command to validate (or null)",
  "risks": ["potential risk 1", "risk 2"],
  "rollback_hint": "how to undo this change",
  "requires_privilege": true/false
}}
```
"""

CREATE_PROMPT_TEMPLATE = """
TASK: Generate new configuration file content.

REQUEST: {request}
TARGET FILE: {target_path}
FILE TYPE: {file_type}
DOMAIN: {domain}

{schema_hint}

{man_context}

{prior_context}

Generate complete, valid configuration content for a new file.
Follow the standard format for {file_type} files.

Respond with valid JSON matching this schema:
```json
{{
  "title": "Short title for the configuration",
  "explanation": "What this configuration does",
  "generated_content": "the complete file content",
  "validation_command": "command to validate (or null)",
  "risks": ["potential risk 1"],
  "rollback_hint": "delete the file",
  "requires_privilege": true/false
}}
```
"""

PATCH_PROMPT_TEMPLATE = """
TASK: Generate a patch for a configuration file.

REQUEST: {request}
TARGET FILE: {target_path}
FILE TYPE: {file_type}
DOMAIN: {domain}

CURRENT CONTENT:
```
{current_content}
```

{man_context}

{prior_context}

Generate operations to patch specific sections while preserving the rest.
Focus on minimal, targeted changes.

Respond with valid JSON matching the edit schema.
"""


# =============================================================================
# JSON Schemas
# =============================================================================

EDIT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "explanation", "operations"],
    "properties": {
        "title": {
            "type": "string",
            "description": "Short title for the change",
        },
        "explanation": {
            "type": "string",
            "description": "Detailed explanation",
        },
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind", "path"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["set", "rm", "ins", "mv", "append", "replace"],
                    },
                    "path": {"type": "string"},
                    "value": {"type": "string"},
                    "position": {"type": "string"},
                    "explanation": {"type": "string"},
                },
            },
        },
        "validation_command": {"type": ["string", "null"]},
        "risks": {
            "type": "array",
            "items": {"type": "string"},
        },
        "rollback_hint": {"type": ["string", "null"]},
        "requires_privilege": {"type": "boolean"},
    },
}

CREATE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "explanation", "generated_content"],
    "properties": {
        "title": {"type": "string"},
        "explanation": {"type": "string"},
        "generated_content": {"type": "string"},
        "validation_command": {"type": ["string", "null"]},
        "risks": {
            "type": "array",
            "items": {"type": "string"},
        },
        "rollback_hint": {"type": ["string", "null"]},
        "requires_privilege": {"type": "boolean"},
    },
}

CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["domain", "file_type"],
    "properties": {
        "domain": {
            "type": "string",
            "enum": [
                "network",
                "filesystem",
                "ssh",
                "firewall",
                "service",
                "cron",
                "package",
                "hosts",
                "general",
            ],
        },
        "file_type": {
            "type": "string",
            "enum": ["augeas", "yaml", "json", "toml", "text", "markdown", "systemd"],
        },
        "target_path": {"type": ["string", "null"]},
        "entities": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}


# =============================================================================
# Context Templates
# =============================================================================

MAN_CONTEXT_TEMPLATE = """
RELEVANT DOCUMENTATION:
{snippets}

Use the above documentation to ensure correct syntax and options.
"""

PRIOR_ART_TEMPLATE = """
SIMILAR PAST INCIDENTS:
{incidents}

Consider the outcomes and approaches from similar past changes.
"""

SCHEMA_HINT_TEMPLATE = """
FILE SCHEMA:
```{format}
{schema}
```

The generated content must follow this structure.
"""


# =============================================================================
# Helper Functions
# =============================================================================


def build_edit_prompt(
    request: str,
    target_path: str,
    file_type: str,
    domain: str,
    current_content: str,
    man_docs: tuple[str, ...] = (),
    prior_art: tuple[dict[str, Any], ...] = (),
) -> str:
    """Build the edit prompt with context."""
    man_context = ""
    if man_docs:
        snippets = "\n\n".join(f"---\n{doc}" for doc in man_docs)
        man_context = MAN_CONTEXT_TEMPLATE.format(snippets=snippets)

    prior_context = ""
    if prior_art:
        incidents = "\n\n".join(
            f"- {p.get('title', 'Unknown')}: {p.get('outcome', 'unknown')} - {p.get('root_cause', '')}"
            for p in prior_art
        )
        prior_context = PRIOR_ART_TEMPLATE.format(incidents=incidents)

    return EDIT_PROMPT_TEMPLATE.format(
        request=request,
        target_path=target_path,
        file_type=file_type,
        domain=domain,
        current_content=current_content[:4000] if current_content else "(empty file)",
        man_context=man_context,
        prior_context=prior_context,
    )


def build_create_prompt(
    request: str,
    target_path: str,
    file_type: str,
    domain: str,
    schema_hint: dict[str, Any] | None = None,
    man_docs: tuple[str, ...] = (),
    prior_art: tuple[dict[str, Any], ...] = (),
) -> str:
    """Build the create prompt with context."""
    man_context = ""
    if man_docs:
        snippets = "\n\n".join(f"---\n{doc}" for doc in man_docs)
        man_context = MAN_CONTEXT_TEMPLATE.format(snippets=snippets)

    prior_context = ""
    if prior_art:
        incidents = "\n\n".join(f"- {p.get('title', 'Unknown')}: {p.get('outcome', 'unknown')}" for p in prior_art)
        prior_context = PRIOR_ART_TEMPLATE.format(incidents=incidents)

    schema_context = ""
    if schema_hint:
        import json

        schema_context = SCHEMA_HINT_TEMPLATE.format(
            format=file_type,
            schema=json.dumps(schema_hint, indent=2),
        )

    return CREATE_PROMPT_TEMPLATE.format(
        request=request,
        target_path=target_path,
        file_type=file_type,
        domain=domain,
        schema_hint=schema_context,
        man_context=man_context,
        prior_context=prior_context,
    )


def build_patch_prompt(
    request: str,
    target_path: str,
    file_type: str,
    domain: str,
    current_content: str,
    man_docs: tuple[str, ...] = (),
    prior_art: tuple[dict[str, Any], ...] = (),
) -> str:
    """Build the patch prompt with context."""
    man_context = ""
    if man_docs:
        snippets = "\n\n".join(f"---\n{doc}" for doc in man_docs)
        man_context = MAN_CONTEXT_TEMPLATE.format(snippets=snippets)

    prior_context = ""
    if prior_art:
        incidents = "\n\n".join(f"- {p.get('title', 'Unknown')}: {p.get('outcome', 'unknown')}" for p in prior_art)
        prior_context = PRIOR_ART_TEMPLATE.format(incidents=incidents)

    return PATCH_PROMPT_TEMPLATE.format(
        request=request,
        target_path=target_path,
        file_type=file_type,
        domain=domain,
        current_content=current_content[:4000] if current_content else "(empty file)",
        man_context=man_context,
        prior_context=prior_context,
    )
