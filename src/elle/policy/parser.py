"""YAML/TOML policy file parser.

Parses policy files and validates their structure.
"""

from __future__ import annotations

import logging
from datetime import time
from pathlib import Path
from typing import Any

from elle.policy.exceptions import PolicyParseError, PolicyValidationError
from elle.policy.models import (
    Condition,
    MatchType,
    PolicyDocument,
    PolicyEffect,
    PolicyRule,
    RiskLevel,
    TimeWindow,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Parser Implementation
# =============================================================================


class PolicyParser:
    """Parser for YAML policy files.

    Parses policy documents and validates their structure.
    """

    def parse_file(self, path: Path | str) -> PolicyDocument:
        """Parse a policy file.

        Args:
            path: Path to the policy file.

        Returns:
            Parsed PolicyDocument.

        Raises:
            PolicyParseError: If file cannot be read or parsed.
        """
        path = Path(path)

        if not path.exists():
            raise PolicyParseError(str(path), "File not found")

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            raise PolicyParseError(str(path), f"Cannot read file: {e}") from e

        return self.parse_string(content, str(path))

    def parse_string(self, content: str, source: str = "<string>") -> PolicyDocument:
        """Parse a policy from a string.

        Args:
            content: YAML content.
            source: Source identifier for error messages.

        Returns:
            Parsed PolicyDocument.

        Raises:
            PolicyParseError: If content cannot be parsed.
        """
        try:
            import yaml

            data = yaml.safe_load(content)
        except ImportError as e:
            raise PolicyParseError(source, "PyYAML is not installed") from e
        except yaml.YAMLError as e:
            line = getattr(e, "problem_mark", None)
            line_num = line.line + 1 if line else None
            raise PolicyParseError(source, f"Invalid YAML: {e}", line_num) from e

        if not isinstance(data, dict):
            raise PolicyParseError(source, "Policy must be a YAML mapping")

        return self._parse_document(data, source)

    def _parse_document(self, data: dict[str, Any], source: str) -> PolicyDocument:
        """Parse a policy document from a dictionary.

        Args:
            data: Parsed YAML data.
            source: Source identifier.

        Returns:
            PolicyDocument.

        Raises:
            PolicyParseError: If structure is invalid.
        """
        # Parse version
        version = str(data.get("version", "1.0"))

        # Parse name
        name = str(data.get("name", "Unnamed Policy"))

        # Parse extends
        extends_raw = data.get("extends", [])
        if isinstance(extends_raw, str):
            extends = (extends_raw,)
        elif isinstance(extends_raw, list):
            extends = tuple(str(e) for e in extends_raw)
        else:
            extends = ()

        # Parse default effect
        default_effect_str = data.get("default_effect", "allow")
        try:
            default_effect = PolicyEffect(default_effect_str)
        except ValueError as e:
            raise PolicyParseError(
                source,
                f"Invalid default_effect: {default_effect_str}. "
                f"Must be one of: {', '.join(eff.value for eff in PolicyEffect)}",
            ) from e

        # Parse rules
        rules_raw = data.get("rules", [])
        if not isinstance(rules_raw, list):
            raise PolicyParseError(source, "rules must be a list")

        rules = []
        for i, rule_data in enumerate(rules_raw):
            try:
                rule = self._parse_rule(rule_data, source, i)
                rules.append(rule)
            except PolicyValidationError as e:
                raise PolicyParseError(source, str(e)) from e

        return PolicyDocument(
            version=version,
            name=name,
            extends=extends,
            default_effect=default_effect,
            rules=tuple(rules),
        )

    def _parse_rule(
        self,
        data: dict[str, Any],
        source: str,
        index: int,
    ) -> PolicyRule:
        """Parse a single policy rule.

        Args:
            data: Rule data dictionary.
            source: Source identifier.
            index: Rule index for error messages.

        Returns:
            PolicyRule.

        Raises:
            PolicyValidationError: If rule is invalid.
        """
        if not isinstance(data, dict):
            raise PolicyValidationError(f"rule[{index}]", "Rule must be a mapping")

        # Required fields
        rule_id = data.get("id")
        if not rule_id:
            raise PolicyValidationError(f"rule[{index}]", "Missing required field: id")
        rule_id = str(rule_id)

        name = data.get("name", rule_id)

        # Parse effect
        effect_str = data.get("effect")
        if not effect_str:
            raise PolicyValidationError(rule_id, "Missing required field: effect")
        try:
            effect = PolicyEffect(effect_str)
        except ValueError as e:
            raise PolicyValidationError(
                rule_id,
                f"Invalid effect: {effect_str}. "
                f"Must be one of: {', '.join(eff.value for eff in PolicyEffect)}",
            ) from e

        # Parse conditions
        conditions_raw = data.get("conditions", [])
        if not isinstance(conditions_raw, list):
            raise PolicyValidationError(rule_id, "conditions must be a list")
        if not conditions_raw:
            raise PolicyValidationError(rule_id, "Rule must have at least one condition")

        conditions = []
        for j, cond_data in enumerate(conditions_raw):
            condition = self._parse_condition(cond_data, rule_id, j)
            conditions.append(condition)

        # Optional fields
        match_all = data.get("match_all", True)
        message = data.get("message")
        justification_prompt = data.get("justification_prompt")
        priority = int(data.get("priority", 0))
        enabled = bool(data.get("enabled", True))

        return PolicyRule(
            id=rule_id,
            name=str(name),
            conditions=tuple(conditions),
            match_all=bool(match_all),
            effect=effect,
            message=str(message) if message else None,
            justification_prompt=str(justification_prompt) if justification_prompt else None,
            priority=priority,
            enabled=enabled,
        )

    def _parse_condition(
        self,
        data: dict[str, Any],
        rule_id: str,
        index: int,
    ) -> Condition:
        """Parse a single condition.

        Args:
            data: Condition data dictionary.
            rule_id: Parent rule ID.
            index: Condition index.

        Returns:
            Condition.

        Raises:
            PolicyValidationError: If condition is invalid.
        """
        if not isinstance(data, dict):
            raise PolicyValidationError(
                rule_id,
                f"condition[{index}] must be a mapping",
            )

        # Pattern fields
        command = data.get("command")
        path = data.get("path")
        domain = data.get("domain")
        intent = data.get("intent")

        # At least one pattern must be specified
        has_pattern = any([command, path, domain, intent])
        has_attribute = any([
            data.get("risk_level"),
            data.get("requires_privilege") is not None,
            data.get("allowed_hours"),
        ])

        if not has_pattern and not has_attribute:
            raise PolicyValidationError(
                rule_id,
                f"condition[{index}] must specify at least one matching field",
            )

        # Parse match type
        match_type_str = data.get("match_type", "glob")
        try:
            match_type = MatchType(match_type_str)
        except ValueError as e:
            raise PolicyValidationError(
                rule_id,
                f"Invalid match_type: {match_type_str}. "
                f"Must be one of: {', '.join(mt.value for mt in MatchType)}",
            ) from e

        # Parse risk level
        risk_level = None
        if data.get("risk_level"):
            try:
                risk_level = RiskLevel(data["risk_level"])
            except ValueError as e:
                raise PolicyValidationError(
                    rule_id,
                    f"Invalid risk_level: {data['risk_level']}. "
                    f"Must be one of: {', '.join(rl.value for rl in RiskLevel)}",
                ) from e

        # Parse requires_privilege
        requires_privilege = data.get("requires_privilege")
        if requires_privilege is not None:
            requires_privilege = bool(requires_privilege)

        # Parse time windows
        allowed_hours = None
        if data.get("allowed_hours"):
            windows = []
            for window_data in data["allowed_hours"]:
                window = self._parse_time_window(window_data, rule_id)
                windows.append(window)
            allowed_hours = tuple(windows)

        # Parse negate
        negate = bool(data.get("negate", False))

        return Condition(
            command=str(command) if command else None,
            path=str(path) if path else None,
            domain=str(domain) if domain else None,
            intent=str(intent) if intent else None,
            risk_level=risk_level,
            requires_privilege=requires_privilege,
            match_type=match_type,
            allowed_hours=allowed_hours,
            negate=negate,
        )

    def _parse_time_window(
        self,
        data: dict[str, Any],
        rule_id: str,
    ) -> TimeWindow:
        """Parse a time window.

        Args:
            data: Time window data.
            rule_id: Parent rule ID.

        Returns:
            TimeWindow.

        Raises:
            PolicyValidationError: If time format is invalid.
        """
        start_str = data.get("start")
        end_str = data.get("end")

        if not start_str or not end_str:
            raise PolicyValidationError(
                rule_id,
                "Time window must have 'start' and 'end' fields",
            )

        try:
            start = self._parse_time(start_str)
            end = self._parse_time(end_str)
        except ValueError as e:
            raise PolicyValidationError(rule_id, f"Invalid time format: {e}") from e

        return TimeWindow(start=start, end=end)

    def _parse_time(self, time_str: str) -> time:
        """Parse a time string in HH:MM format.

        Args:
            time_str: Time string (e.g., "14:30").

        Returns:
            time object.

        Raises:
            ValueError: If format is invalid.
        """
        parts = time_str.split(":")
        if len(parts) != 2:
            raise ValueError(f"Expected HH:MM format, got: {time_str}")

        hour = int(parts[0])
        minute = int(parts[1])

        if not (0 <= hour <= 23):
            raise ValueError(f"Hour must be 0-23, got: {hour}")
        if not (0 <= minute <= 59):
            raise ValueError(f"Minute must be 0-59, got: {minute}")

        return time(hour=hour, minute=minute)


# =============================================================================
# Module-level Functions
# =============================================================================


_parser: PolicyParser | None = None


def get_parser() -> PolicyParser:
    """Get the shared parser instance.

    Returns:
        PolicyParser singleton.
    """
    global _parser
    if _parser is None:
        _parser = PolicyParser()
    return _parser


def parse_file(path: Path | str) -> PolicyDocument:
    """Parse a policy file (convenience function).

    Args:
        path: Path to policy file.

    Returns:
        PolicyDocument.
    """
    return get_parser().parse_file(path)


def parse_string(content: str, source: str = "<string>") -> PolicyDocument:
    """Parse a policy string (convenience function).

    Args:
        content: YAML content.
        source: Source identifier.

    Returns:
        PolicyDocument.
    """
    return get_parser().parse_string(content, source)
