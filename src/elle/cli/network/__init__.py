"""Network CLI utilities for ELLE.

Provides natural language network operations including:
- Connectivity diagnosis
- Firewall rule explanation
- Service isolation suggestions

Usage:
    from elle.cli.network import (
        diagnose_connectivity,
        explain_firewall_rules,
        suggest_lockdown,
    )

    # Diagnose connectivity issues
    diagnosis = diagnose_connectivity("api.example.com", port=443)
    print(diagnosis.summary)

    # Explain firewall rules
    explanation = explain_firewall_rules()
    print(explanation.summary)

    # Get lockdown suggestions
    suggestions = suggest_lockdown("postgres", "localhost only")
    for cmd in suggestions:
        print(cmd)
"""

from elle.cli.network.diagnosis import (
    diagnose_connectivity,
    ConnectivityDiagnosis,
    ConnectivityCheck,
)
from elle.cli.network.firewall import (
    explain_firewall_rules,
    suggest_lockdown,
    parse_restriction,
    FirewallExplanation,
    FirewallRule,
)

__all__ = [
    # Diagnosis
    "diagnose_connectivity",
    "ConnectivityDiagnosis",
    "ConnectivityCheck",
    # Firewall
    "explain_firewall_rules",
    "suggest_lockdown",
    "parse_restriction",
    "FirewallExplanation",
    "FirewallRule",
]
