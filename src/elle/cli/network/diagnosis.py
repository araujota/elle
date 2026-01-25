"""Network connectivity diagnosis for ELLE.

Provides systematic diagnosis of network connectivity issues,
checking DNS, routing, firewall, and connectivity in order.

Example:
    diagnosis = diagnose_connectivity("api.example.com", port=443)
    print(diagnosis.summary)
    # "Cannot reach api.example.com:443 because:
    #  - DNS resolves to 192.168.1.50
    #  - Route goes through wg0 interface
    #  - UFW is blocking outbound port 443
    #  Suggestion: ufw allow out 443/tcp"
"""

from __future__ import annotations

import logging
import re
import socket
import subprocess
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class ConnectivityCheck(BaseModel):
    """Result of a single connectivity check."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Check name")
    passed: bool = Field(description="Whether check passed")
    result: str = Field(description="Check result description")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional details",
    )


class ConnectivityDiagnosis(BaseModel):
    """Complete connectivity diagnosis result."""

    model_config = ConfigDict(frozen=True)

    target: str = Field(description="Target host/IP")
    port: int | None = Field(default=None, description="Target port")
    reachable: bool = Field(description="Whether target is reachable")
    summary: str = Field(description="Natural language summary")
    checks: tuple[ConnectivityCheck, ...] = Field(
        default_factory=tuple,
        description="Individual check results",
    )
    suggestions: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Suggested fixes",
    )
    likely_cause: str | None = Field(
        default=None,
        description="Most likely cause of the issue",
    )


def _run_command(
    cmd: list[str],
    timeout: float = 10.0,
) -> tuple[int, str, str]:
    """Run a command and return result.

    Args:
        cmd: Command and arguments.
        timeout: Timeout in seconds.

    Returns:
        Tuple of (return_code, stdout, stderr).
    """
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


def _check_dns(target: str) -> ConnectivityCheck:
    """Check DNS resolution for the target.

    Args:
        target: Hostname to resolve.

    Returns:
        ConnectivityCheck with DNS result.
    """
    # Skip if already an IP
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target):
        return ConnectivityCheck(
            name="DNS Resolution",
            passed=True,
            result=f"Target is already an IP address: {target}",
            details={"ip": target, "is_ip": True},
        )

    try:
        # Resolve hostname
        infos = socket.getaddrinfo(target, None, socket.AF_UNSPEC)
        ips = list(set(info[4][0] for info in infos))

        if ips:
            return ConnectivityCheck(
                name="DNS Resolution",
                passed=True,
                result=f"DNS resolves to: {', '.join(ips)}",
                details={"ips": ips, "hostname": target},
            )
        else:
            return ConnectivityCheck(
                name="DNS Resolution",
                passed=False,
                result=f"DNS resolution returned no results for {target}",
                details={"hostname": target},
            )

    except socket.gaierror as e:
        return ConnectivityCheck(
            name="DNS Resolution",
            passed=False,
            result=f"DNS resolution failed: {e}",
            details={"error": str(e), "hostname": target},
        )


def _check_route(target: str) -> ConnectivityCheck:
    """Check routing to the target.

    Args:
        target: Target IP or hostname.

    Returns:
        ConnectivityCheck with routing result.
    """
    # Get the IP to route to
    target_ip = target
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target):
        try:
            target_ip = socket.gethostbyname(target)
        except socket.gaierror:
            return ConnectivityCheck(
                name="Route Check",
                passed=False,
                result="Cannot determine route: DNS resolution failed",
                details={"target": target},
            )

    # Use ip route get
    code, stdout, stderr = _run_command(["ip", "route", "get", target_ip])

    if code != 0:
        return ConnectivityCheck(
            name="Route Check",
            passed=False,
            result=f"No route to {target_ip}",
            details={"error": stderr},
        )

    # Parse output: "192.168.1.1 via 10.0.0.1 dev wg0 src 10.0.0.2"
    details: dict[str, Any] = {"target": target_ip}

    # Extract interface
    match = re.search(r"dev\s+(\S+)", stdout)
    if match:
        details["interface"] = match.group(1)

    # Extract gateway
    match = re.search(r"via\s+(\S+)", stdout)
    if match:
        details["gateway"] = match.group(1)

    # Extract source IP
    match = re.search(r"src\s+(\S+)", stdout)
    if match:
        details["source"] = match.group(1)

    interface = details.get("interface", "unknown")
    gateway = details.get("gateway")

    if gateway:
        result = f"Route to {target_ip} via {gateway} on {interface}"
    else:
        result = f"Route to {target_ip} via {interface} (direct)"

    return ConnectivityCheck(
        name="Route Check",
        passed=True,
        result=result,
        details=details,
    )


def _check_firewall(target: str, port: int | None) -> ConnectivityCheck:
    """Check if firewall might be blocking the connection.

    Args:
        target: Target IP or hostname.
        port: Target port (optional).

    Returns:
        ConnectivityCheck with firewall result.
    """
    # Check UFW status
    code, stdout, stderr = _run_command(["ufw", "status", "verbose"])

    if code != 0:
        # Try iptables
        code, stdout, stderr = _run_command(["iptables", "-L", "-n"])
        if code != 0:
            return ConnectivityCheck(
                name="Firewall Check",
                passed=True,
                result="Could not check firewall (ufw/iptables not available)",
                details={"error": "No firewall tool available"},
            )

        # Basic iptables analysis
        details: dict[str, Any] = {"type": "iptables"}

        if "DROP" in stdout or "REJECT" in stdout:
            return ConnectivityCheck(
                name="Firewall Check",
                passed=True,  # Can't determine if blocking our specific connection
                result="iptables has DROP/REJECT rules (manual review needed)",
                details=details,
            )

        return ConnectivityCheck(
            name="Firewall Check",
            passed=True,
            result="iptables rules appear permissive",
            details=details,
        )

    # Parse UFW status
    details = {"type": "ufw"}

    if "Status: inactive" in stdout:
        return ConnectivityCheck(
            name="Firewall Check",
            passed=True,
            result="UFW is inactive (not blocking)",
            details=details,
        )

    details["active"] = True

    # Check default policies
    outgoing_policy = "allow"
    if "Default: deny (outgoing)" in stdout:
        outgoing_policy = "deny"
        details["default_outgoing"] = "deny"
    elif "Default: reject (outgoing)" in stdout:
        outgoing_policy = "reject"
        details["default_outgoing"] = "reject"

    incoming_policy = "deny"
    if "Default: allow (incoming)" in stdout:
        incoming_policy = "allow"
    details["default_incoming"] = incoming_policy

    # If checking a specific port
    if port:
        # Look for rules affecting this port
        port_str = str(port)
        port_allowed_out = outgoing_policy == "allow"
        port_blocked_out = False

        for line in stdout.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Look for outgoing rules for this port
            if port_str in line:
                if "ALLOW OUT" in line:
                    port_allowed_out = True
                elif "DENY OUT" in line or "REJECT OUT" in line:
                    port_blocked_out = True

        if port_blocked_out:
            return ConnectivityCheck(
                name="Firewall Check",
                passed=False,
                result=f"UFW is explicitly blocking outbound port {port}",
                details=details,
            )
        elif outgoing_policy != "allow" and not port_allowed_out:
            return ConnectivityCheck(
                name="Firewall Check",
                passed=False,
                result=f"UFW default denies outgoing and port {port} is not allowed",
                details=details,
            )

    # General assessment
    if outgoing_policy != "allow":
        return ConnectivityCheck(
            name="Firewall Check",
            passed=True,
            result=f"UFW outgoing policy is '{outgoing_policy}' (may block some traffic)",
            details=details,
        )

    return ConnectivityCheck(
        name="Firewall Check",
        passed=True,
        result="UFW is active but outgoing traffic is allowed by default",
        details=details,
    )


def _check_listening(port: int) -> ConnectivityCheck:
    """Check if anything is listening on a port locally.

    This is useful when diagnosing "connection refused" for localhost.

    Args:
        port: Port to check.

    Returns:
        ConnectivityCheck with listening result.
    """
    code, stdout, stderr = _run_command(["ss", "-tlnp", f"sport = :{port}"])

    if code != 0:
        return ConnectivityCheck(
            name="Local Port Check",
            passed=True,  # Skip if can't check
            result="Could not check if port is listening",
            details={"error": stderr},
        )

    # Parse output
    lines = [l for l in stdout.split("\n") if l and not l.startswith("State")]
    if lines:
        # Something is listening
        details: dict[str, Any] = {"listening": True, "port": port}

        # Try to extract process name
        match = re.search(r'users:\(\("([^"]+)"', stdout)
        if match:
            details["process"] = match.group(1)

        return ConnectivityCheck(
            name="Local Port Check",
            passed=True,
            result=f"Port {port} has a listener",
            details=details,
        )

    return ConnectivityCheck(
        name="Local Port Check",
        passed=False,
        result=f"Nothing is listening on port {port}",
        details={"listening": False, "port": port},
    )


def _check_tcp_connection(target: str, port: int, timeout: float = 5.0) -> ConnectivityCheck:
    """Attempt a TCP connection to the target.

    Args:
        target: Target host.
        port: Target port.
        timeout: Connection timeout.

    Returns:
        ConnectivityCheck with connection result.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        result = sock.connect_ex((target, port))
        sock.close()

        if result == 0:
            return ConnectivityCheck(
                name="TCP Connection",
                passed=True,
                result=f"Successfully connected to {target}:{port}",
                details={"target": target, "port": port},
            )
        else:
            error_map = {
                111: "Connection refused",
                113: "No route to host",
                110: "Connection timed out",
                101: "Network unreachable",
            }
            error = error_map.get(result, f"Error code {result}")

            return ConnectivityCheck(
                name="TCP Connection",
                passed=False,
                result=f"Connection to {target}:{port} failed: {error}",
                details={"target": target, "port": port, "error_code": result},
            )

    except TimeoutError:
        return ConnectivityCheck(
            name="TCP Connection",
            passed=False,
            result=f"Connection to {target}:{port} timed out",
            details={"target": target, "port": port, "timeout": timeout},
        )
    except socket.gaierror as e:
        return ConnectivityCheck(
            name="TCP Connection",
            passed=False,
            result=f"Could not resolve {target}: {e}",
            details={"target": target, "port": port, "error": str(e)},
        )
    except Exception as e:
        return ConnectivityCheck(
            name="TCP Connection",
            passed=False,
            result=f"Connection failed: {e}",
            details={"target": target, "port": port, "error": str(e)},
        )


def _check_ping(target: str) -> ConnectivityCheck:
    """Check ICMP reachability.

    Args:
        target: Target to ping.

    Returns:
        ConnectivityCheck with ping result.
    """
    code, stdout, stderr = _run_command(["ping", "-c", "1", "-W", "3", target])

    if code == 0:
        # Extract RTT
        match = re.search(r"time=(\d+\.?\d*)", stdout)
        rtt = match.group(1) if match else "unknown"

        return ConnectivityCheck(
            name="ICMP Ping",
            passed=True,
            result=f"Ping successful (RTT: {rtt}ms)",
            details={"target": target, "rtt_ms": rtt},
        )

    return ConnectivityCheck(
        name="ICMP Ping",
        passed=False,
        result=f"Ping failed: {stderr.strip() or 'no response'}",
        details={"target": target},
    )


def diagnose_connectivity(
    target: str,
    port: int | None = None,
) -> ConnectivityDiagnosis:
    """Diagnose why a service/host is unreachable.

    Performs checks in order:
    1. DNS resolution
    2. Route to target
    3. Firewall rules
    4. Port listening (if localhost)
    5. TCP connection attempt (if port specified)
    6. ICMP ping

    Args:
        target: Target hostname or IP address.
        port: Target port (optional but recommended).

    Returns:
        ConnectivityDiagnosis with natural language explanation.

    Example:
        >>> diagnosis = diagnose_connectivity("api.example.com", port=443)
        >>> print(diagnosis.summary)
        "Cannot reach api.example.com:443 because:
         - DNS resolves to 192.168.1.50
         - Route goes through wg0 interface
         - UFW is blocking outbound port 443
         Suggestion: ufw allow out 443/tcp"
    """
    checks: list[ConnectivityCheck] = []
    suggestions: list[str] = []

    # 1. DNS Check
    dns_check = _check_dns(target)
    checks.append(dns_check)

    if not dns_check.passed:
        suggestions.append("Check DNS configuration in /etc/resolv.conf")
        suggestions.append(f"Try: dig {target}")

        return ConnectivityDiagnosis(
            target=target,
            port=port,
            reachable=False,
            summary=f"Cannot reach {target}: DNS resolution failed",
            checks=tuple(checks),
            suggestions=tuple(suggestions),
            likely_cause="DNS resolution failure",
        )

    # Get resolved IP for further checks
    target_ip = dns_check.details.get("ips", [target])[0] if dns_check.details.get("ips") else target

    # 2. Route Check
    route_check = _check_route(target_ip)
    checks.append(route_check)

    if not route_check.passed:
        suggestions.append("Check routing table: ip route")
        suggestions.append("Verify network interface is up: ip link show")

        return ConnectivityDiagnosis(
            target=target,
            port=port,
            reachable=False,
            summary=f"Cannot reach {target}: No route to host",
            checks=tuple(checks),
            suggestions=tuple(suggestions),
            likely_cause="No route to host",
        )

    # 3. Firewall Check
    firewall_check = _check_firewall(target_ip, port)
    checks.append(firewall_check)

    if not firewall_check.passed:
        if port:
            suggestions.append(f"Allow outbound traffic: ufw allow out {port}/tcp")
        else:
            suggestions.append("Review firewall rules: ufw status verbose")

    # 4. Local port check (for localhost targets)
    is_localhost = target_ip in ("127.0.0.1", "localhost", "::1")
    if is_localhost and port:
        listen_check = _check_listening(port)
        checks.append(listen_check)

        if not listen_check.passed:
            suggestions.append(f"Verify service is running on port {port}")
            suggestions.append(f"Check with: ss -tlnp | grep {port}")

    # 5. TCP Connection (if port specified)
    if port:
        tcp_check = _check_tcp_connection(target_ip, port)
        checks.append(tcp_check)

        if tcp_check.passed:
            return ConnectivityDiagnosis(
                target=target,
                port=port,
                reachable=True,
                summary=f"Successfully connected to {target}:{port}",
                checks=tuple(checks),
                suggestions=tuple(suggestions),
            )

        # Determine likely cause from error
        error_code = tcp_check.details.get("error_code")
        if error_code == 111:  # Connection refused
            if is_localhost:
                likely_cause = "No service listening on port"
            else:
                likely_cause = "Service not accepting connections (refused)"
        elif error_code == 110:  # Timeout
            likely_cause = "Connection timed out (firewall or network issue)"
        elif error_code == 113:  # No route
            likely_cause = "No route to host"
        elif error_code == 101:  # Network unreachable
            likely_cause = "Network unreachable"
        else:
            likely_cause = "Connection failed"

    else:
        # 6. ICMP Ping (if no port specified)
        ping_check = _check_ping(target_ip)
        checks.append(ping_check)

        if ping_check.passed:
            return ConnectivityDiagnosis(
                target=target,
                port=port,
                reachable=True,
                summary=f"Host {target} is reachable (ping successful)",
                checks=tuple(checks),
                suggestions=tuple(suggestions),
            )

        likely_cause = "Host not responding to ping"

    # Build failure summary
    _failed_checks = [c for c in checks if not c.passed]  # Available for detailed logging
    summary_parts = [f"Cannot reach {target}"]
    if port:
        summary_parts[0] += f":{port}"
    summary_parts.append("because:")

    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        summary_parts.append(f"  [{status}] {check.name}: {check.result}")

    if suggestions:
        summary_parts.append("\nSuggestions:")
        for suggestion in suggestions:
            summary_parts.append(f"  - {suggestion}")

    return ConnectivityDiagnosis(
        target=target,
        port=port,
        reachable=False,
        summary="\n".join(summary_parts),
        checks=tuple(checks),
        suggestions=tuple(suggestions),
        likely_cause=likely_cause,
    )
