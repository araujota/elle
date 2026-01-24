"""Thermal event detection via eBPF.

Attaches to thermal:thermal_zone_trip tracepoint to detect
when thermal zones cross temperature thresholds.

Events produced:
- thermal_zone_trip: A thermal zone crossed a trip point
"""

import ctypes
import logging

from elle.daemon.telemetry.ebpf.bcc.base import BCCProgram, _bytes_to_string
from elle.daemon.telemetry.ebpf.models import EBPFEventType, EBPFRawEvent

logger = logging.getLogger(__name__)

# Thermal trip types
TRIP_TYPES = {
    0: "critical",
    1: "hot",
    2: "passive",
    3: "active",
}


# Event structure matching the BPF program
class ThermalEvent(ctypes.Structure):
    """C struct for thermal events from ring buffer."""

    _fields_ = [
        ("ts_ns", ctypes.c_uint64),       # Kernel timestamp
        ("id", ctypes.c_int32),            # Thermal zone ID
        ("trip", ctypes.c_int32),          # Trip point index
        ("trip_type", ctypes.c_int32),     # Trip type (critical, hot, etc.)
        ("temp", ctypes.c_int32),          # Current temperature (millidegrees C)
        ("trip_temp", ctypes.c_int32),     # Trip point temperature
        ("zone_type", ctypes.c_char * 16), # Zone type name
    ]


class ThermalProgram(BCCProgram):
    """eBPF program for thermal zone trip detection.

    Traces the thermal:thermal_zone_trip tracepoint which fires when
    a thermal zone crosses a configured temperature threshold.
    """

    name = "thermal"
    category = "thermal"
    hook_type = "tracepoint"
    hook_name = "thermal:thermal_zone_trip"

    def _get_ring_buffer_name(self) -> str:
        """Get ring buffer map name."""
        return "events"

    def _get_bpf_program(self) -> str:
        """Return the BPF C program for thermal detection.

        Returns:
            BPF C program source.
        """
        return """
#include <uapi/linux/ptrace.h>

// Ring buffer for events
BPF_RINGBUF_OUTPUT(events, 1 << 12);

// Event structure
struct thermal_event_t {
    u64 ts_ns;
    s32 id;
    s32 trip;
    s32 trip_type;
    s32 temp;
    s32 trip_temp;
    char zone_type[16];
};

// Tracepoint: thermal:thermal_zone_trip
// Fires when a thermal zone crosses a trip point
TRACEPOINT_PROBE(thermal, thermal_zone_trip) {
    struct thermal_event_t *e = events.ringbuf_reserve(sizeof(struct thermal_event_t));
    if (!e) {
        return 0;
    }

    e->ts_ns = bpf_ktime_get_ns();
    e->id = args->id;
    e->trip = args->trip;
    e->trip_type = args->trip_type;
    e->temp = args->temp;
    e->trip_temp = args->trip_temp;

    // Zone type is a string pointer, copy it
    bpf_probe_read_str(&e->zone_type, sizeof(e->zone_type), args->thermal_zone);

    events.ringbuf_submit(e, 0);
    return 0;
}
"""

    def _parse_event(
        self, cpu: int, data: ctypes.Structure, size: int
    ) -> EBPFRawEvent | None:
        """Parse thermal event from ring buffer.

        Args:
            cpu: CPU where event occurred.
            data: Raw event data.
            size: Data size.

        Returns:
            Parsed EBPFRawEvent.
        """
        try:
            event = ctypes.cast(data, ctypes.POINTER(ThermalEvent)).contents

            # Convert zone type to string
            zone_type = _bytes_to_string(event.zone_type)

            # Convert millidegrees to degrees
            temp_c = event.temp / 1000.0
            trip_temp_c = event.trip_temp / 1000.0

            # Get trip type name
            trip_type_name = TRIP_TYPES.get(event.trip_type, "unknown")

            return EBPFRawEvent(
                ts_ns=event.ts_ns,
                cpu=cpu,
                event_type=EBPFEventType.THERMAL_ZONE_TRIP,
                pid=None,  # Thermal events don't have process context
                tgid=None,
                uid=None,
                comm=None,
                data={
                    "zone": event.id,
                    "zone_type": zone_type,
                    "trip": event.trip,
                    "trip_type": trip_type_name,
                    "temp": temp_c,
                    "trip_temp": trip_temp_c,
                },
            )

        except Exception as e:
            logger.debug(f"Failed to parse thermal event: {e}")
            return None

    def attach(self) -> bool:
        """Attach to thermal:thermal_zone_trip tracepoint.

        Returns:
            True if attached successfully.
        """
        if not self._loaded:
            logger.error("Cannot attach thermal program: not loaded")
            return False

        if self._attached:
            return True

        try:
            # BCC's TRACEPOINT_PROBE macro auto-attaches
            self._attached = True
            logger.info(f"Attached thermal program to {self.hook_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to attach thermal program: {e}")
            self._errors_count += 1
            return False


def create_thermal_program() -> ThermalProgram:
    """Factory function to create a thermal program.

    Returns:
        Configured ThermalProgram instance.
    """
    return ThermalProgram()
