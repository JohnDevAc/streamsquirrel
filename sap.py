import os
import socket
import struct
import zlib
import time
from typing import Optional, Tuple

from net_utils import pick_multicast_iface

from config import (
    SAP_GROUP, SAP_PORT, SAP_INTERVAL_SEC,
    AES67_SAMPLE_RATE, AES67_CHANNELS, AES67_PAYLOAD_TYPE,
    SDP_ORIGIN_USER,
    SAP_SRC_IP_ENV,
    PTP_GMID_ENV,
    PTP_DOMAIN_ENV,
    AES67_SAMPLES_PER_PACKET,
)

# SAP payload type for embedded SDP (RFC 2974)
_SAP_PAYLOAD_TYPE = b"application/sdp\x00"


def _ptime_ms() -> int:
    # Rounded integer ptime in ms derived from samples-per-packet.
    # For 48kHz: ptime_ms = samples * 1000 / 48000
    return int(round((AES67_SAMPLES_PER_PACKET * 1000.0) / float(AES67_SAMPLE_RATE)))


def _stable_u32(s: str) -> int:
    return zlib.crc32(s.encode("utf-8")) & 0xFFFFFFFF


def build_sdp(
    stream_name: str,
    mcast_ip: str,
    mcast_port: int,
    payload_type: int = AES67_PAYLOAD_TYPE,
    codec: str = "L24",
    origin_ip: str = "0.0.0.0",
) -> Tuple[str, str]:
    """Build SDP and return (sdp, identity_key).

    identity_key is a stable string used for SAP msg-id-hash and SDP origin sess-id,
    to prevent duplicate 'devices' appearing after reboot.
    """
    # Stable identity across restarts
    identity_key = f"{stream_name}|{mcast_ip}|{int(mcast_port)}|pt={int(payload_type)}|sr={AES67_SAMPLE_RATE}|ch={AES67_CHANNELS}|{codec}"
    sess_id = _stable_u32(identity_key)
    sess_ver = 1  # stable unless we change the SDP (Dante treats changes as new/updated flow)

    ptp_gmid = os.getenv(PTP_GMID_ENV, "").strip()
    ptp_domain = os.getenv(PTP_DOMAIN_ENV, "").strip()

    ts_refclk_line: Optional[str] = None
    if ptp_gmid:
        if ptp_domain.isdigit():
            ts_refclk_line = f"a=ts-refclk:ptp=IEEE1588-2008:{ptp_gmid}:{ptp_domain}"
        else:
            ts_refclk_line = f"a=ts-refclk:ptp=IEEE1588-2008:{ptp_gmid}"

    # Keep the ordering conservative for Dante SDP parser:
    # v/o/s/t then m, c (media-level), rtpmap, ptime, recvonly, clocking
    lines = [
        "v=0",
        f"o={SDP_ORIGIN_USER} {sess_id} {sess_ver} IN IP4 {origin_ip}",
        f"s={stream_name}",
        "t=0 0",
        f"m=audio {int(mcast_port)} RTP/AVP {int(payload_type)}",
        f"c=IN IP4 {mcast_ip}/32",
        f"a=rtcp:{int(mcast_port) + 1}",
        f"a=rtpmap:{int(payload_type)} {codec}/{AES67_SAMPLE_RATE}/{AES67_CHANNELS}",
        f"a=ptime:{_ptime_ms()}",
        "a=recvonly",
        "a=mediaclk:direct=0",
    ]
    if ts_refclk_line:
        lines.append(ts_refclk_line)

    # CRLF and a trailing blank line
    return ("\r\n".join(lines + [""]), identity_key)


class SAPAnnouncer:
    """Sends SAP packets periodically with embedded SDP."""

    def __init__(self, sdp: str, identity_key: str, ttl: int = 16):
        self._sdp_text = sdp
        self._sdp_bytes = sdp.encode("utf-8")
        self._identity_key = identity_key

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        self.packets_sent = 0
        self.last_send_error = ""

        ifname, ip = pick_multicast_iface()
        self.mcast_iface = ifname

        forced_ip = os.getenv(SAP_SRC_IP_ENV, "").strip() or None
        src_ip = forced_ip or ip or "0.0.0.0"

        if ip:
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            except Exception:
                pass

        # SAP v1 header (RFC 2974):
        #  1 byte: version/flags (v=1 => 0x20)
        #  1 byte: auth len (0)
        #  2 bytes: message id hash (stable per flow)
        #  4 bytes: originating source IP
        msg_id_hash = _stable_u32(self._identity_key) & 0xFFFF
        try:
            src_ip_packed = socket.inet_aton(src_ip)
        except OSError:
            src_ip_packed = socket.inet_aton("0.0.0.0")

        self._header_base = struct.pack("!BBH4s", 0x20, 0x00, msg_id_hash, src_ip_packed)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def _header(self, is_delete: bool) -> bytes:
        if not is_delete:
            return self._header_base
        # Set the 'T' (deletion) bit. Version/flags byte: 0x20 -> 0x24
        b0 = 0x24
        return bytes([b0]) + self._header_base[1:]

    def send_once(self, delete: bool = False):
        try:
            pkt = self._header(delete) + _SAP_PAYLOAD_TYPE + self._sdp_bytes
            self.sock.sendto(pkt, (SAP_GROUP, SAP_PORT))
            self.packets_sent += 1
        except OSError as e:
            self.last_send_error = str(e)
            raise

    def send_delete_burst(self, count: int = 3, spacing_s: float = 0.05):
        """Best-effort withdrawal to help receivers remove the cached flow quickly."""
        for _ in range(max(1, int(count))):
            try:
                self.send_once(delete=True)
            except Exception:
                pass
            time.sleep(max(0.0, float(spacing_s)))
