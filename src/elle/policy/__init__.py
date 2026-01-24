"""ELLE Policy Engine.

A user-configurable policy engine that replaces ad-hoc safety logic
with declarative rules. Transforms ELLE from "safe by convention"
to "safe by declaration."

Usage:
    from elle.policy import evaluate_command, PolicyEvaluationRequest

    # Simple command evaluation
    result = evaluate_command("rm -rf /tmp/test")
    if not result.allowed:
        print(f"Blocked: {result.message}")

    # Full request evaluation
    request = PolicyEvaluationRequest(
        operation_type="command",
        command="sudo apt update",
        requires_privilege=True,
    )
    result = evaluate(request)
    if result.requires_confirmation:
        # Prompt user for confirmation
        ...

Policy file locations (in order of precedence):
    1. ~/.config/elle/policy.yaml (user override)
    2. /etc/elle/policy.yaml (system-wide)
    3. Built-in defaults (always active unless explicitly disabled)

Policy file format (YAML):
    version: "1.0"
    name: "My Custom Policy"
    extends: ["system:defaults"]
    default_effect: allow

    rules:
      - id: "require-confirm-reboot"
        name: "Require confirmation for reboot"
        effect: require_confirmation
        conditions:
          - command: "reboot"
            match_type: exact
        message: "This will reboot the system. Continue?"
"""

from elle.policy.audit import (
    PolicyAuditLogger,
    get_audit_logger,
    log_evaluation,
    read_audit_entries,
)
from elle.policy.defaults import (
    ALL_DEFAULT_RULES,
    DEFAULT_POLICY,
    get_default_policy,
    get_default_rules,
)
from elle.policy.engine import (
    PolicyEngine,
    evaluate,
    evaluate_command,
    evaluate_path,
    get_policy_engine,
    reload_policy,
)
from elle.policy.exceptions import (
    PolicyCircularExtendError,
    PolicyError,
    PolicyEvaluationError,
    PolicyLoadError,
    PolicyParseError,
    PolicyValidationError,
)
from elle.policy.loader import (
    PolicyLoader,
    get_loader,
    load_policy,
    load_policy_file,
)
from elle.policy.matchers import (
    match_condition,
    match_domain,
    match_exact,
    match_glob,
    match_pattern,
    match_prefix,
    match_regex,
    match_time_window,
)
from elle.policy.models import (
    Condition,
    MatchType,
    OperationType,
    PolicyAuditEntry,
    PolicyDocument,
    PolicyEffect,
    PolicyEvaluationRequest,
    PolicyEvaluationResult,
    PolicyRule,
    RiskLevel,
    TimeWindow,
)
from elle.policy.parser import (
    PolicyParser,
    get_parser,
    parse_file,
    parse_string,
)

__all__ = [
    # Main API
    "evaluate",
    "evaluate_command",
    "evaluate_path",
    "reload_policy",
    "get_policy_engine",
    "PolicyEngine",
    # Models
    "PolicyEffect",
    "MatchType",
    "RiskLevel",
    "TimeWindow",
    "Condition",
    "PolicyRule",
    "PolicyDocument",
    "PolicyEvaluationRequest",
    "PolicyEvaluationResult",
    "PolicyAuditEntry",
    "OperationType",
    # Parsing
    "parse_file",
    "parse_string",
    "PolicyParser",
    "get_parser",
    # Loading
    "load_policy",
    "load_policy_file",
    "PolicyLoader",
    "get_loader",
    # Defaults
    "get_default_policy",
    "get_default_rules",
    "DEFAULT_POLICY",
    "ALL_DEFAULT_RULES",
    # Audit
    "log_evaluation",
    "read_audit_entries",
    "PolicyAuditLogger",
    "get_audit_logger",
    # Matchers
    "match_pattern",
    "match_glob",
    "match_regex",
    "match_exact",
    "match_prefix",
    "match_domain",
    "match_time_window",
    "match_condition",
    # Exceptions
    "PolicyError",
    "PolicyLoadError",
    "PolicyParseError",
    "PolicyValidationError",
    "PolicyEvaluationError",
    "PolicyCircularExtendError",
]
