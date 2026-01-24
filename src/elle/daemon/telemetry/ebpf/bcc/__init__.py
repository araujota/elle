"""BCC backend for eBPF programs.

BCC (BPF Compiler Collection) provides a Python interface
for loading and interacting with eBPF programs.

This module contains:
- BCCProgram: Abstract base class for all BCC programs
- OOMProgram: OOM kill detection
- BlockIOProgram: Disk I/O error detection
- NetDropsProgram: Network packet drops
- ProcessProgram: Process crash detection
- ThermalProgram: Thermal events
"""

from elle.daemon.telemetry.ebpf.bcc.base import BCCProgram
from elle.daemon.telemetry.ebpf.bcc.block_io import BlockIOProgram, create_block_io_program
from elle.daemon.telemetry.ebpf.bcc.net_drops import NetDropsProgram, create_net_drops_program
from elle.daemon.telemetry.ebpf.bcc.oom import OOMProgram, create_oom_program
from elle.daemon.telemetry.ebpf.bcc.process import ProcessProgram, create_process_program
from elle.daemon.telemetry.ebpf.bcc.thermal import ThermalProgram, create_thermal_program

__all__ = [
    "BCCProgram",
    # OOM
    "OOMProgram",
    "create_oom_program",
    # Block I/O
    "BlockIOProgram",
    "create_block_io_program",
    # Network drops
    "NetDropsProgram",
    "create_net_drops_program",
    # Process
    "ProcessProgram",
    "create_process_program",
    # Thermal
    "ThermalProgram",
    "create_thermal_program",
]
