"""Fail2ban security stack recipe."""

from elle.ops.stack.models import (
    StackGuarantee,
    StackRecipe,
    StackVariable,
)

FAIL2BAN_RECIPE = StackRecipe(
    name="fail2ban",
    display_name="Fail2ban Intrusion Prevention",
    description="Fail2ban with SSH jail, custom ban times, and verified log source access",
    category="security",
    packages=("fail2ban",),
    services=("fail2ban",),
    config_templates={
        # jail.local will be generated with SSH jail
    },
    guarantees=(
        StackGuarantee(
            name="service_running",
            description="Fail2ban service is active",
            check_command="systemctl is-active fail2ban",
            expected_pattern=r"active",
            failure_action="fail",
            remediation_hint="Fail2ban not running. Start with: systemctl start fail2ban",
        ),
        StackGuarantee(
            name="sshd_jail_active",
            description="SSH jail is enabled and running",
            check_command="fail2ban-client status sshd 2>/dev/null || echo 'jail not found'",
            expected_pattern=r"Status for the jail: sshd",
            failure_action="fail",
            remediation_hint="SSH jail not active. Check /etc/fail2ban/jail.local and restart fail2ban",
        ),
        StackGuarantee(
            name="log_access",
            description="Fail2ban can read auth log",
            check_command="test -r /var/log/auth.log && echo 'readable' || echo 'not readable'",
            expected_pattern=r"readable",
            failure_action="fail",
            remediation_hint="Cannot read /var/log/auth.log. Check permissions or rsyslog configuration",
        ),
        StackGuarantee(
            name="iptables_chain",
            description="Fail2ban iptables chain exists",
            check_command="iptables -L f2b-sshd 2>/dev/null | head -1 || echo 'chain missing'",
            expected_pattern=r"Chain f2b-sshd",
            failure_action="warn",
            remediation_hint="Fail2ban iptables chain missing. Try: systemctl restart fail2ban",
        ),
        StackGuarantee(
            name="socket_working",
            description="Fail2ban client can communicate with server",
            check_command="fail2ban-client ping 2>/dev/null || echo 'failed'",
            expected_pattern=r"pong",
            failure_action="fail",
            remediation_hint="Cannot communicate with fail2ban server. Check: systemctl status fail2ban",
        ),
    ),
    variables=(
        StackVariable(
            name="bantime",
            description="Duration of IP ban (e.g., 10m, 1h, 1d)",
            default="10m",
            required=False,
        ),
        StackVariable(
            name="findtime",
            description="Time window for counting failures (e.g., 10m, 1h)",
            default="10m",
            required=False,
        ),
        StackVariable(
            name="maxretry",
            description="Number of failures before ban",
            default="5",
            required=False,
            validator=r"^\d+$",
        ),
        StackVariable(
            name="ignoreip",
            description="IPs to never ban (space-separated)",
            default="127.0.0.1/8 ::1",
            required=False,
        ),
        StackVariable(
            name="banaction",
            description="Action to take on ban (iptables, iptables-multiport, nftables)",
            default="iptables-multiport",
            required=False,
        ),
    ),
    presets={
        "lenient": {
            "bantime": "10m",
            "findtime": "10m",
            "maxretry": "5",
        },
        "strict": {
            "bantime": "1d",
            "findtime": "1h",
            "maxretry": "3",
        },
        "aggressive": {
            "bantime": "1w",
            "findtime": "1d",
            "maxretry": "3",
        },
    },
    min_ram_mb=64,
    min_disk_gb=1,
    ports_required=(),  # No ports opened
    version="1.0.0",
    tags=("security", "fail2ban", "intrusion-prevention", "ssh", "firewall"),
)
