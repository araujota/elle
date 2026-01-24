"""elled - Background daemon for telemetry and privileged operations.

The elled daemon provides:
- Real-time journal and kernel event monitoring
- Periodic system health probes (memory, disk, network, thermal, SMART)
- Event normalization and deduplication
- Incident correlation with existing Incident Vault
- REST API for CLI queries

Usage:
    elled                    # Start daemon with default config
    elled --no-api           # Start without REST API
    elled -c /path/to.toml   # Use custom config file
"""

from elle.daemon.config import Config, get_config, load_config
from elle.daemon.main import ElledDaemon, main, run_daemon

__all__ = [
    "Config",
    "get_config",
    "load_config",
    "ElledDaemon",
    "run_daemon",
    "main",
]
