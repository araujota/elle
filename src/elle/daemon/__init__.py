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

from __future__ import annotations

from elle.daemon.config import Config, get_config, load_config

__all__ = [
    "Config",
    "get_config",
    "load_config",
    "ElledDaemon",
    "run_daemon",
    "main",
]


def __getattr__(name: str) -> object:
    """Lazy-import heavy modules to avoid pulling in psycopg at import time."""
    if name in ("ElledDaemon", "main", "run_daemon"):
        from elle.daemon.main import ElledDaemon, main, run_daemon

        globals().update(
            ElledDaemon=ElledDaemon,
            main=main,
            run_daemon=run_daemon,
        )
        return globals()[name]

    # Allow normal subpackage access (e.g. elle.daemon.reboot)
    import importlib

    try:
        return importlib.import_module(f"{__name__}.{name}")
    except ImportError:
        pass

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
