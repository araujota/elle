"""Firewall explanation and management for ELLE.

Provides natural language explanation of firewall rules
and generates commands for service lockdown.

Example:
    explanation = explain_firewall_rules()
    print(explanation.summary)
    # "Your firewall (UFW) is ACTIVE with these rules:
    #  - SSH (port 22): Allowed from anywhere
    #  - HTTP/HTTPS (80, 443): Allowed from anywhere
    #  - PostgreSQL (5432): Allowed only from 10.0.0.0/8
    #  - All other incoming: DENIED
    #  - All outgoing: ALLOWED"

    commands = suggest_lockdown("postgres", "localhost only")
    # ["ufw delete allow 5432", "ufw allow from 127.0.0.1 to any port 5432"]
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# Common service to port mappings
SERVICE_PORTS = {
    "ssh": 22,
    "http": 80,
    "https": 443,
    "ftp": 21,
    "smtp": 25,
    "dns": 53,
    "pop3": 110,
    "imap": 143,
    "mysql": 3306,
    "postgres": 5432,
    "postgresql": 5432,
    "redis": 6379,
    "mongodb": 27017,
    "elasticsearch": 9200,
    "rabbitmq": 5672,
    "nginx": 80,
    "apache": 80,
}


class FirewallRule(BaseModel):
    """Representation of a firewall rule."""

    model_config = ConfigDict(frozen=True)

    action: Literal["allow", "deny", "reject", "limit"] = Field(
        description="Rule action"
    )
    direction: Literal["in", "out", "both"] = Field(
        description="Traffic direction"
    )
    port: int | None = Field(default=None, description="Port number")
    protocol: str | None = Field(default=None, description="Protocol (tcp/udp)")
    from_addr: str | None = Field(default=None, description="Source address")
    to_addr: str | None = Field(default=None, description="Destination address")
    interface: str | None = Field(default=None, description="Interface")
    comment: str | None = Field(default=None, description="Rule comment")
    raw: str = Field(description="Raw rule string")


class FirewallExplanation(BaseModel):
    """Natural language explanation of firewall rules."""

    model_config = ConfigDict(frozen=True)

    summary: str = Field(description="Plain English summary")
    active: bool = Field(description="Whether firewall is active")
    firewall_type: str = Field(default="ufw", description="Firewall type")
    default_incoming: str = Field(default="deny", description="Default incoming policy")
    default_outgoing: str = Field(default="allow", description="Default outgoing policy")
    rules: tuple[FirewallRule, ...] = Field(
        default_factory=tuple,
        description="Parsed rules",
    )
    rule_explanations: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Human-readable rule explanations",
    )
    warnings: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Security warnings",
    )


def _run_command(cmd: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    """Run a command and return result."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except Exception as e:
        return -1, "", str(e)


def _parse_ufw_rule(line: str) -> FirewallRule | None:
    """Parse a UFW rule line.

    Args:
        line: Raw rule line from 'ufw status verbose'.

    Returns:
        FirewallRule or None if unparseable.
    """
    line = line.strip()
    if not line:
        return None

    # Skip header lines
    if line.startswith("To") or line.startswith("--"):
        return None

    # Parse format: "22/tcp                     ALLOW IN    Anywhere"
    # or: "5432                       ALLOW IN    10.0.0.0/8"

    # Split by multiple spaces
    parts = re.split(r"\s{2,}", line)
    if len(parts) < 3:
        return None

    port_spec = parts[0]
    action_dir = parts[1].lower()
    from_addr = parts[2] if len(parts) > 2 else "Anywhere"

    # Parse action and direction
    action = "allow"
    direction: Literal["in", "out", "both"] = "in"

    if "allow" in action_dir:
        action = "allow"
    elif "deny" in action_dir:
        action = "deny"
    elif "reject" in action_dir:
        action = "reject"
    elif "limit" in action_dir:
        action = "limit"

    if "in" in action_dir:
        direction = "in"
    elif "out" in action_dir:
        direction = "out"
    elif "fwd" in action_dir:
        direction = "both"

    # Parse port and protocol
    port = None
    protocol = None

    if "/" in port_spec:
        port_str, protocol = port_spec.split("/", 1)
        try:
            port = int(port_str)
        except ValueError:
            pass
    else:
        try:
            port = int(port_spec)
        except ValueError:
            # Might be a service name
            pass

    # Clean from_addr
    if from_addr.lower() == "anywhere":
        from_addr = None

    return FirewallRule(
        action=action,  # type: ignore
        direction=direction,
        port=port,
        protocol=protocol,
        from_addr=from_addr,
        raw=line,
    )


def _explain_rule(rule: FirewallRule) -> str:
    """Generate human-readable explanation of a rule.

    Args:
        rule: Parsed firewall rule.

    Returns:
        Plain English explanation.
    """
    # Identify service
    service = None
    if rule.port:
        for name, port in SERVICE_PORTS.items():
            if port == rule.port:
                service = name.upper()
                break

    # Build explanation
    parts = []

    # Port/service
    if service:
        parts.append(f"{service} (port {rule.port})")
    elif rule.port:
        parts.append(f"Port {rule.port}")
    else:
        parts.append("All ports")

    # Protocol
    if rule.protocol:
        parts[-1] += f"/{rule.protocol}"

    # Action
    if rule.action == "allow":
        parts.append("allowed")
    elif rule.action == "deny":
        parts.append("denied")
    elif rule.action == "reject":
        parts.append("rejected")
    elif rule.action == "limit":
        parts.append("rate-limited")

    # Direction
    if rule.direction == "in":
        parts.append("incoming")
    elif rule.direction == "out":
        parts.append("outgoing")
    else:
        parts.append("forwarded")

    # Source
    if rule.from_addr:
        parts.append(f"from {rule.from_addr}")
    else:
        parts.append("from anywhere")

    return " ".join(parts)


def explain_firewall_rules() -> FirewallExplanation:
    """Explain current firewall rules in plain English.

    Returns:
        FirewallExplanation with human-readable summary.

    Example:
        >>> explanation = explain_firewall_rules()
        >>> print(explanation.summary)
        "Your firewall (UFW) is ACTIVE with these rules:
         - SSH (port 22): Allowed from anywhere
         - HTTP/HTTPS (80, 443): Allowed from anywhere
         - PostgreSQL (5432): Allowed only from 10.0.0.0/8
         - All other incoming: DENIED
         - All outgoing: ALLOWED"
    """
    rules: list[FirewallRule] = []
    explanations: list[str] = []
    warnings: list[str] = []

    # Try UFW first
    code, stdout, stderr = _run_command(["ufw", "status", "verbose"])

    if code == 0:
        # Parse UFW output
        active = "Status: active" in stdout
        default_incoming = "deny"
        default_outgoing = "allow"

        # Parse default policies
        if "Default: deny (incoming)" in stdout:
            default_incoming = "deny"
        elif "Default: allow (incoming)" in stdout:
            default_incoming = "allow"
            warnings.append("Default incoming policy is ALLOW - all ports are open!")
        elif "Default: reject (incoming)" in stdout:
            default_incoming = "reject"

        if "Default: deny (outgoing)" in stdout:
            default_outgoing = "deny"
        elif "Default: allow (outgoing)" in stdout:
            default_outgoing = "allow"

        # Parse rules
        in_rules = False
        for line in stdout.split("\n"):
            if line.startswith("--"):
                in_rules = True
                continue

            if in_rules and line.strip():
                rule = _parse_ufw_rule(line)
                if rule:
                    rules.append(rule)
                    explanations.append(_explain_rule(rule))

        # Check for security issues
        for rule in rules:
            if rule.port == 22 and not rule.from_addr and rule.action == "allow":
                pass  # SSH from anywhere is common
            elif rule.port in (3306, 5432, 6379, 27017) and not rule.from_addr:
                service = next(
                    (n for n, p in SERVICE_PORTS.items() if p == rule.port),
                    str(rule.port)
                )
                warnings.append(
                    f"{service.upper()} (port {rule.port}) is open to the world - "
                    "consider restricting to specific IPs"
                )

        # Build summary
        if not active:
            summary = "Your firewall (UFW) is INACTIVE - all ports are accessible!"
        else:
            summary_parts = ["Your firewall (UFW) is ACTIVE with these rules:"]

            if explanations:
                for exp in explanations:
                    summary_parts.append(f"  - {exp}")
            else:
                summary_parts.append("  - No custom rules defined")

            summary_parts.append(f"  - All other incoming traffic: {default_incoming.upper()}ED")
            summary_parts.append(f"  - All outgoing traffic: {default_outgoing.upper()}ED")

            summary = "\n".join(summary_parts)

        return FirewallExplanation(
            summary=summary,
            active=active,
            firewall_type="ufw",
            default_incoming=default_incoming,
            default_outgoing=default_outgoing,
            rules=tuple(rules),
            rule_explanations=tuple(explanations),
            warnings=tuple(warnings),
        )

    # Fallback to iptables
    code, stdout, stderr = _run_command(["iptables", "-L", "-n", "-v"])

    if code == 0:
        # Basic iptables parsing
        has_drop = "DROP" in stdout or "REJECT" in stdout

        if has_drop:
            summary = (
                "Using iptables with DROP/REJECT rules.\n"
                "Run 'iptables -L -n -v' for detailed rules.\n"
                "(UFW provides easier management: apt install ufw)"
            )
        else:
            summary = (
                "Using iptables with permissive rules.\n"
                "Consider using UFW for easier firewall management."
            )
            warnings.append("iptables appears permissive - verify rules manually")

        return FirewallExplanation(
            summary=summary,
            active=True,
            firewall_type="iptables",
            warnings=tuple(warnings),
        )

    # No firewall found
    return FirewallExplanation(
        summary="No firewall detected. Consider installing UFW: apt install ufw",
        active=False,
        firewall_type="none",
        warnings=("No firewall active - all ports may be accessible",),
    )


def parse_restriction(restriction: str) -> dict:
    """Parse a natural language restriction into structured data.

    Args:
        restriction: Natural language restriction like "localhost only",
                    "my office", "10.0.0.0/8", "local network".

    Returns:
        Dict with parsed restriction info.
    """
    restriction = restriction.lower().strip()

    # Localhost
    if restriction in ("localhost", "localhost only", "local only", "127.0.0.1"):
        return {"type": "ip", "value": "127.0.0.1", "description": "localhost only"}

    # CIDR notation
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}$", restriction):
        return {"type": "cidr", "value": restriction, "description": f"network {restriction}"}

    # Single IP
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", restriction):
        return {"type": "ip", "value": restriction, "description": f"IP {restriction}"}

    # Common phrases
    if "local network" in restriction or "lan" in restriction:
        # Assuming common private ranges
        return {
            "type": "cidr",
            "value": "192.168.0.0/16",
            "description": "local network (192.168.x.x)",
            "alternatives": ["10.0.0.0/8", "172.16.0.0/12"],
        }

    if "internal" in restriction:
        return {
            "type": "cidr",
            "value": "10.0.0.0/8",
            "description": "internal network (10.x.x.x)",
        }

    # "my office" or other custom phrases - will need user input
    return {
        "type": "unknown",
        "original": restriction,
        "description": "Custom restriction (IP address needed)",
        "needs_input": True,
    }


def suggest_lockdown(
    service: str,
    restriction: str,
) -> list[str]:
    """Suggest commands to restrict a service.

    Args:
        service: Service name (e.g., "postgres", "mysql", "redis")
                or port number.
        restriction: Natural language restriction (e.g., "localhost only",
                    "10.0.0.0/8").

    Returns:
        List of UFW commands to implement the restriction.

    Example:
        >>> suggest_lockdown("postgres", "localhost only")
        ["ufw delete allow 5432", "ufw allow from 127.0.0.1 to any port 5432"]
    """
    commands: list[str] = []

    # Determine port
    port = None
    if service.isdigit():
        port = int(service)
    else:
        port = SERVICE_PORTS.get(service.lower())

    if not port:
        return [f"# Unknown service: {service}"]

    # Parse restriction
    restriction_info = parse_restriction(restriction)

    if restriction_info.get("needs_input"):
        return [
            f"# Please provide an IP address for: {restriction}",
            f"# Then use: ufw allow from <IP> to any port {port}",
        ]

    # Generate commands
    # First, remove any existing allow rules for this port
    commands.append(f"# Restrict {service} (port {port}) to {restriction_info['description']}")
    commands.append(f"ufw delete allow {port} 2>/dev/null || true")
    commands.append(f"ufw delete allow {port}/tcp 2>/dev/null || true")

    # Add restricted rule
    source = restriction_info.get("value")
    if source:
        commands.append(f"ufw allow from {source} to any port {port}")

    # Add alternatives if specified
    if "alternatives" in restriction_info:
        commands.append(f"# Alternative network ranges you might also need:")
        for alt in restriction_info["alternatives"]:
            commands.append(f"# ufw allow from {alt} to any port {port}")

    return commands


def generate_lockdown_plan(
    allow_services: list[str],
    deny_all_else: bool = True,
) -> list[str]:
    """Generate a complete firewall lockdown plan.

    Args:
        allow_services: List of services to allow (e.g., ["ssh", "http", "https"]).
        deny_all_else: Whether to deny all other incoming traffic.

    Returns:
        List of UFW commands for the lockdown.
    """
    commands = [
        "# Firewall lockdown plan",
        "# WARNING: Review these commands carefully before running!",
        "",
        "# Reset UFW to defaults",
        "ufw --force reset",
        "",
        "# Set default policies",
        "ufw default deny incoming",
        "ufw default allow outgoing",
        "",
    ]

    # Add rules for allowed services
    commands.append("# Allow specified services")
    for service in allow_services:
        port = SERVICE_PORTS.get(service.lower())
        if port:
            commands.append(f"ufw allow {port}/tcp  # {service}")
        else:
            commands.append(f"# Unknown service: {service}")

    commands.extend([
        "",
        "# Enable firewall",
        "ufw --force enable",
        "",
        "# Show status",
        "ufw status verbose",
    ])

    return commands
