import os
import socket
from typing import Optional, Tuple

def pick_multicast_iface() -> Tuple[Optional[str], Optional[str]]:
    """Return (iface_name, iface_ipv4) for multicast. Uses env MCAST_IFACE if set."""
    preferred = os.getenv("MCAST_IFACE", "").strip()
    if preferred:
        ip = _iface_ipv4(preferred)
        return (preferred, ip)

    # Auto-pick: first non-loopback interface with IPv4
    for ifname in _list_ifaces():
        if ifname == "lo":
            continue
        ip = _iface_ipv4(ifname)
        if ip:
            return (ifname, ip)
    return (None, None)

def _list_ifaces():
    try:
        return [name for _, name in socket.if_nameindex()]
    except Exception:
        return ["eth0"]

def _iface_ipv4(ifname: str) -> Optional[str]:
    try:
        import fcntl, struct
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ifreq = struct.pack("256s", ifname.encode("utf-8")[:15])
        res = fcntl.ioctl(s.fileno(), 0x8915, ifreq)  # SIOCGIFADDR
        ip = socket.inet_ntoa(res[20:24])
        return ip
    except Exception:
        return None
