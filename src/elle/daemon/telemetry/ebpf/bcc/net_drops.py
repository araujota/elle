"""Network packet drop detection via eBPF.

Attaches to the skb:kfree_skb tracepoint to detect when network
packets are dropped by the kernel.

Events produced:
- net_drop: A network packet was dropped
"""

import ctypes
import logging

from elle.daemon.telemetry.ebpf.bcc.base import BCCProgram, _bytes_to_string
from elle.daemon.telemetry.ebpf.models import EBPFEventType, EBPFRawEvent

logger = logging.getLogger(__name__)

# Common drop reasons (from include/linux/skbuff.h)
# These are simplified - the actual enum is more extensive
DROP_REASONS = {
    0: "NOT_SPECIFIED",
    1: "NO_SOCKET",
    2: "PKT_TOO_SMALL",
    3: "TCP_CSUM",
    4: "SOCKET_FILTER",
    5: "UDP_CSUM",
    6: "NETFILTER_DROP",
    7: "OTHERHOST",
    8: "IP_CSUM",
    9: "IP_INHDR",
    10: "IP_RPFILTER",
    11: "UNICAST_IN_L2_MULTICAST",
    12: "XFRM_POLICY",
    13: "IP_NOPROTO",
    14: "SOCKET_RCVBUFF",
    15: "PROTO_MEM",
    16: "TCP_MD5NOTFOUND",
    17: "TCP_MD5UNEXPECTED",
    18: "TCP_MD5FAILURE",
    19: "SOCKET_BACKLOG",
    20: "TCP_FLAGS",
    21: "TCP_ZEROWINDOW",
    22: "TCP_OLD_DATA",
    23: "TCP_OVERWINDOW",
    24: "TCP_OFOMERGE",
    25: "TCP_OFO_DROP",
    26: "TCP_RFC7323_PAWS",
    27: "TCP_INVALID_SEQUENCE",
    28: "TCP_RESET",
    29: "TCP_INVALID_SYN",
    30: "TCP_CLOSE",
    31: "TCP_FASTOPEN",
    32: "TCP_OLD_ACK",
    33: "TCP_TOO_OLD_ACK",
    34: "TCP_ACK_UNSENT_DATA",
    35: "TCP_OFO_QUEUE_PRUNE",
}


# Event structure matching the BPF program
class NetDropEvent(ctypes.Structure):
    """C struct for network drop events from ring buffer."""

    _fields_ = [
        ("ts_ns", ctypes.c_uint64),       # Kernel timestamp
        ("protocol", ctypes.c_uint16),     # Network protocol
        ("reason", ctypes.c_uint32),       # Drop reason
        ("ifindex", ctypes.c_uint32),      # Interface index
        ("len", ctypes.c_uint32),          # Packet length
        ("pid", ctypes.c_uint32),          # Process ID (if available)
        ("comm", ctypes.c_char * 16),      # Process name
    ]


class NetDropsProgram(BCCProgram):
    """eBPF program for network packet drop detection.

    Traces the skb:kfree_skb tracepoint which fires when the kernel
    frees a socket buffer, optionally with a drop reason.
    """

    name = "net_drops"
    category = "net"
    hook_type = "tracepoint"
    hook_name = "skb:kfree_skb"

    def _get_ring_buffer_name(self) -> str:
        """Get ring buffer map name."""
        return "events"

    def _get_bpf_program(self) -> str:
        """Return the BPF C program for network drop detection.

        Returns:
            BPF C program source.
        """
        return """
#include <uapi/linux/ptrace.h>
#include <linux/skbuff.h>

// Ring buffer for events
BPF_RINGBUF_OUTPUT(events, 1 << 12);

// Event structure
struct net_drop_event_t {
    u64 ts_ns;
    u16 protocol;
    u32 reason;
    u32 ifindex;
    u32 len;
    u32 pid;
    char comm[16];
};

// Tracepoint: skb:kfree_skb
// Fires when a socket buffer is freed (potentially dropped)
TRACEPOINT_PROBE(skb, kfree_skb) {
    // Filter out non-drop events (reason == 0 means not a drop)
    // Note: On older kernels, reason field may not exist
    u32 reason = 0;
    #ifdef SKB_DROP_REASON_NOT_SPECIFIED
    reason = args->reason;
    if (reason == SKB_DROP_REASON_NOT_SPECIFIED) {
        return 0;
    }
    #endif

    struct net_drop_event_t *e = events.ringbuf_reserve(sizeof(struct net_drop_event_t));
    if (!e) {
        return 0;
    }

    e->ts_ns = bpf_ktime_get_ns();

    // Get info from skb pointer
    struct sk_buff *skb = (struct sk_buff *)args->skbaddr;
    if (skb) {
        bpf_probe_read(&e->protocol, sizeof(e->protocol), &skb->protocol);
        bpf_probe_read(&e->len, sizeof(e->len), &skb->len);

        // Try to get interface index
        struct net_device *dev;
        bpf_probe_read(&dev, sizeof(dev), &skb->dev);
        if (dev) {
            bpf_probe_read(&e->ifindex, sizeof(e->ifindex), &dev->ifindex);
        }
    }

    e->reason = reason;

    // Get process context
    u64 id = bpf_get_current_pid_tgid();
    e->pid = id & 0xFFFFFFFF;
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    events.ringbuf_submit(e, 0);
    return 0;
}
"""

    def _parse_event(
        self, cpu: int, data: ctypes.Structure, size: int
    ) -> EBPFRawEvent | None:
        """Parse network drop event from ring buffer.

        Args:
            cpu: CPU where event occurred.
            data: Raw event data.
            size: Data size.

        Returns:
            Parsed EBPFRawEvent.
        """
        try:
            event = ctypes.cast(data, ctypes.POINTER(NetDropEvent)).contents

            # Convert comm to string
            comm = _bytes_to_string(event.comm) if event.pid else None

            # Get reason name
            reason_name = DROP_REASONS.get(event.reason, f"REASON_{event.reason}")

            # Protocol number to name (simplified)
            protocol_names = {
                0x0800: "IPv4",
                0x0806: "ARP",
                0x86DD: "IPv6",
            }
            protocol_name = protocol_names.get(event.protocol, f"0x{event.protocol:04x}")

            return EBPFRawEvent(
                ts_ns=event.ts_ns,
                cpu=cpu,
                event_type=EBPFEventType.NET_DROP,
                pid=event.pid if event.pid else None,
                tgid=None,
                uid=None,
                comm=comm,
                data={
                    "protocol": event.protocol,
                    "protocol_name": protocol_name,
                    "reason": event.reason,
                    "reason_name": reason_name,
                    "ifindex": event.ifindex,
                    "len": event.len,
                },
            )

        except Exception as e:
            logger.debug(f"Failed to parse network drop event: {e}")
            return None

    def attach(self) -> bool:
        """Attach to skb:kfree_skb tracepoint.

        Returns:
            True if attached successfully.
        """
        if not self._loaded:
            logger.error("Cannot attach net_drops program: not loaded")
            return False

        if self._attached:
            return True

        try:
            # BCC's TRACEPOINT_PROBE macro auto-attaches
            self._attached = True
            logger.info(f"Attached net_drops program to {self.hook_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to attach net_drops program: {e}")
            self._errors_count += 1
            return False


def create_net_drops_program() -> NetDropsProgram:
    """Factory function to create a network drops program.

    Returns:
        Configured NetDropsProgram instance.
    """
    return NetDropsProgram()
