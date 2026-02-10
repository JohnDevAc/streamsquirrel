import socket
import struct

from net_utils import pick_multicast_iface
from config import (
    AES67_PAYLOAD_TYPE,
    AES67_SAMPLE_RATE,
    AES67_CHANNELS,
    AES67_BIT_DEPTH,
)

class RTPAES67Sender:
    """RTP sender for AES67 L24/48k/2ch."""

    def __init__(self, mcast_ip: str, mcast_port: int, ssrc: int, ttl: int = 16):
        self.mcast_ip = mcast_ip
        self.mcast_port = int(mcast_port)
        self.ssrc = ssrc & 0xFFFFFFFF
        self.seq = 0
        self.timestamp = 0

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self.packets_sent = 0
        self.last_send_error = ""

        ifname, ip = pick_multicast_iface()
        self.mcast_iface = ifname
        if ip:
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            except Exception:
                pass

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    @staticmethod
    def _pack_l24_from_i32le(pcm_i32_le: bytes) -> bytes:
        """Convert interleaved stereo int32 LE PCM to packed signed 24-bit BE."""
        out = bytearray()
        for i in range(0, len(pcm_i32_le), 4):
            sample = struct.unpack_from("<i", pcm_i32_le, i)[0]
            s24 = sample >> 8  # keep top 24 bits
            out.extend(((s24 >> 16) & 0xFF, (s24 >> 8) & 0xFF, s24 & 0xFF))
        return bytes(out)

    def send_int32le_frame(self, pcm_i32_le: bytes, samples_per_channel: int):
        if AES67_BIT_DEPTH != 24 or AES67_CHANNELS != 2 or AES67_SAMPLE_RATE != 48000:
            raise ValueError("Configured for 48k/24-bit/2ch only.")

        # RTP header (12 bytes)
        v_p_x_cc = 0x80  # V=2
        m_pt = AES67_PAYLOAD_TYPE & 0x7F
        header = struct.pack(
            "!BBHII",
            v_p_x_cc,
            m_pt,
            self.seq & 0xFFFF,
            self.timestamp & 0xFFFFFFFF,
            self.ssrc,
        )
        payload = self._pack_l24_from_i32le(pcm_i32_le)

        try:
            self.sock.sendto(header + payload, (self.mcast_ip, self.mcast_port))
            self.packets_sent += 1
        except OSError as e:
            self.last_send_error = str(e)
            raise

        self.seq = (self.seq + 1) & 0xFFFF
        self.timestamp = (self.timestamp + int(samples_per_channel)) & 0xFFFFFFFF


