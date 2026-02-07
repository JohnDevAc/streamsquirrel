import os
import socket
import struct
import zlib
from net_utils import pick_multicast_iface
import time
import random

from config import (
    SAP_GROUP, SAP_PORT, SAP_INTERVAL_SEC,
    AES67_SAMPLE_RATE, AES67_CHANNELS, AES67_PAYLOAD_TYPE,
    SDP_ORIGIN_USER,
)

def build_sdp(
    stream_name: str,
    mcast_ip: str,
    mcast_port: int,
    payload_type: int = AES67_PAYLOAD_TYPE,
    codec: str = "L24",
) -> str:
    """Build SDP. Optionally include PTP attributes when PTP_GMID env var is set.

    Environment variables:
      - PTP_GMID: e.g. 00-11-22-33-44-55-66-77
      - PTP_DOMAIN: optional integer domain
    """
    sess_id = random.randint(100000, 999999)
    sess_ver = int(time.time())

    ptp_gmid = os.getenv("PTP_GMID", "").strip()
    ptp_domain = os.getenv("PTP_DOMAIN", "").strip()

    ts_refclk_line = None
    if ptp_gmid:
        if ptp_domain.isdigit():
            ts_refclk_line = f"a=ts-refclk:ptp=IEEE1588-2008:{ptp_gmid}:{ptp_domain}"
        else:
            ts_refclk_line = f"a=ts-refclk:ptp=IEEE1588-2008:{ptp_gmid}"

    lines = [
        "v=0",
        f"o={SDP_ORIGIN_USER} {sess_id} {sess_ver} IN IP4 0.0.0.0",
        f"s={stream_name}",
        "c=IN IP4 0.0.0.0",
        "t=0 0",
        f"m=audio {int(mcast_port)} RTP/AVP {int(payload_type)}",
        f"c=IN IP4 {mcast_ip}/32",
        f"a=rtpmap:{int(payload_type)} {codec}/{AES67_SAMPLE_RATE}/{AES67_CHANNELS}",
        "a=ptime:1",
        "a=recvonly",
    ]

    # Helpful for many AES67 receivers
    lines.insert(-2, "a=mediaclk:direct=0")
    if ts_refclk_line:
        lines.insert(-2, ts_refclk_line)

    return "\r\n".join(lines + [""])

class SAPAnnouncer:
    """Sends SAP packets periodically with embedded SDP."""
    def __init__(self, sdp: str, ttl: int = 16):
        self.sdp = sdp.encode("utf-8")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        self.packets_sent = 0
        self.last_send_error = ""
        ifname, ip = pick_multicast_iface()
        self.mcast_iface = ifname
        if ip:
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            except Exception:
                pass
        # SAP v1 header (RFC 2974):
        #  1 byte: version/flags (v=1 => 0x20)
        #  1 byte: auth len (0)
        #  2 bytes: message id hash (unique per SDP)
        #  4 bytes: originating source IP
        src_ip = ip or "0.0.0.0"
        msg_id_hash = zlib.crc32(self.sdp) & 0xFFFF
        try:
            src_ip_packed = socket.inet_aton(src_ip)
        except OSError:
            src_ip_packed = socket.inet_aton("0.0.0.0")
        self.sap_header = struct.pack("!BBH4s", 0x20, 0x00, msg_id_hash, src_ip_packed)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def send_once(self):
        try:
            self.sock.sendto(self.sap_header + self.sdp, (SAP_GROUP, SAP_PORT))
            self.packets_sent += 1
        except OSError as e:
            self.last_send_error = str(e)
            raise
