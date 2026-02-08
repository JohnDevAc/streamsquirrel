import os
import re
import socket
import subprocess
import time
from typing import Dict, List, Optional, Tuple


_DHCPCD_CONF = os.getenv("DHCPCD_CONF", "/etc/dhcpcd.conf")
_BLOCK_BEGIN = "# StreamSquirrel network config BEGIN"
_BLOCK_END = "# StreamSquirrel network config END"


def _run(cmd: List[str]) -> Tuple[int, str, str]:
    """Run a command and return (rc, stdout, stderr)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(timeout=5)
    return p.returncode, (out or ""), (err or "")


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def _write_file(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _hostname() -> str:
    return socket.gethostname()


def _eth_link_speed_mbps(iface: str) -> Optional[int]:
    # Try ethtool first
    rc, out, _ = _run(["ethtool", iface])
    if rc == 0 and out:
        m = re.search(r"^\s*Speed:\s*(\d+)Mb/s", out, flags=re.MULTILINE)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass

    # Fallback: sysfs (often present on Pi)
    try:
        p = f"/sys/class/net/{iface}/speed"
        s = _read_file(p).strip()
        if s and s.isdigit():
            v = int(s)
            return v if v > 0 else None
    except Exception:
        pass
    return None


def _ip_addr_v4(iface: str) -> Tuple[Optional[str], Optional[int]]:
    rc, out, _ = _run(["ip", "-4", "addr", "show", "dev", iface])
    if rc != 0:
        return None, None
    m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", out)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def _default_gateway(iface: str) -> Optional[str]:
    # Prefer iface-specific default route first
    rc, out, _ = _run(["ip", "route", "show", "default", "dev", iface])
    if rc == 0 and out.strip():
        m = re.search(r"default\s+via\s+(\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)

    rc, out, _ = _run(["ip", "route", "show", "default"])
    if rc == 0 and out.strip():
        m = re.search(r"default\s+via\s+(\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    return None


def _dns_servers() -> List[str]:
    # Parse /etc/resolv.conf
    res = []
    txt = _read_file("/etc/resolv.conf")
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("nameserver"):
            parts = line.split()
            if len(parts) >= 2:
                res.append(parts[1])
    # De-dupe preserving order
    out = []
    for s in res:
        if s not in out:
            out.append(s)
    return out


def _parse_dhcpcd_block(conf_text: str, iface: str) -> Dict[str, Optional[str]]:
    """Parse our marked block, returning mode + fields. Missing keys => None."""
    out: Dict[str, Optional[str]] = {
        "mode": "dhcp",
        "ip": None,
        "prefix": None,
        "gateway": None,
        "dns": None,
    }

    if _BLOCK_BEGIN in conf_text and _BLOCK_END in conf_text:
        block = conf_text.split(_BLOCK_BEGIN, 1)[1].split(_BLOCK_END, 1)[0]
        # Require interface match to be safe
        if re.search(rf"^\s*interface\s+{re.escape(iface)}\s*$", block, flags=re.MULTILINE):
            ipm = re.search(r"^\s*static\s+ip_address\s*=\s*([0-9.]+)/(\d+)\s*$", block, flags=re.MULTILINE)
            if ipm:
                out["mode"] = "static"
                out["ip"] = ipm.group(1)
                out["prefix"] = ipm.group(2)
            gwm = re.search(r"^\s*static\s+routers\s*=\s*([0-9.]+)\s*$", block, flags=re.MULTILINE)
            if gwm:
                out["gateway"] = gwm.group(1)
            dnm = re.search(r"^\s*static\s+domain_name_servers\s*=\s*(.+?)\s*$", block, flags=re.MULTILINE)
            if dnm:
                dns = [x for x in re.split(r"\s+", dnm.group(1).strip()) if x]
                out["dns"] = ",".join(dns)

    return out


def get_network_state(iface: str = "eth0") -> Dict:
    conf_text = _read_file(_DHCPCD_CONF)
    cfg = _parse_dhcpcd_block(conf_text, iface)

    ip, prefix = _ip_addr_v4(iface)
    gw = _default_gateway(iface)
    dns = _dns_servers()
    speed = _eth_link_speed_mbps(iface)

    configured = {
        "mode": cfg.get("mode") or "dhcp",
        "ip": cfg.get("ip"),
        "prefix": int(cfg["prefix"]) if cfg.get("prefix") and str(cfg.get("prefix")).isdigit() else None,
        "gateway": cfg.get("gateway"),
        "dns": [d for d in (cfg.get("dns") or "").split(",") if d] if cfg.get("dns") else [],
    }

    current = {
        "ip": ip,
        "prefix": prefix,
        "gateway": gw,
        "dns": dns,
    }

    return {
        "hostname": _hostname(),
        "iface": iface,
        "link_speed_mbps": speed,
        "current": current,
        "configured": configured,
    }


def _sanitize_hostname(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"[^a-zA-Z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name[:63]


def _replace_block(conf_text: str, new_block: str) -> str:
    if _BLOCK_BEGIN in conf_text and _BLOCK_END in conf_text:
        before = conf_text.split(_BLOCK_BEGIN, 1)[0]
        after = conf_text.split(_BLOCK_END, 1)[1]
        return before.rstrip() + "\n\n" + new_block.rstrip() + "\n\n" + after.lstrip()
    # Append
    return conf_text.rstrip() + "\n\n" + new_block.rstrip() + "\n"


def apply_network_config(iface: str = "eth0", payload: Optional[dict] = None) -> Dict:
    payload = payload or {}
    mode = (payload.get("mode") or "dhcp").strip().lower()
    hostname = payload.get("hostname")
    ip = (payload.get("ip") or "").strip()
    prefix = payload.get("prefix")
    gateway = (payload.get("gateway") or "").strip()
    dns = payload.get("dns") or []

    # Normalise dns list
    if isinstance(dns, str):
        dns = [x for x in re.split(r"[\s,]+", dns.strip()) if x]
    if not isinstance(dns, list):
        dns = []
    dns = [str(x).strip() for x in dns if str(x).strip()]

    errors = []
    if mode not in ("dhcp", "static"):
        errors.append("mode must be 'dhcp' or 'static'")

    if mode == "static":
        if not ip or not re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
            errors.append("ip is required for static mode")
        if prefix is None:
            errors.append("prefix is required for static mode")
        try:
            prefix_int = int(prefix)
            if prefix_int < 0 or prefix_int > 32:
                errors.append("prefix must be 0-32")
        except Exception:
            errors.append("prefix must be an integer")

        if gateway and not re.match(r"^\d+\.\d+\.\d+\.\d+$", gateway):
            errors.append("gateway must be an IPv4 address")
        for d in dns:
            if not re.match(r"^\d+\.\d+\.\d+\.\d+$", d):
                errors.append(f"dns contains invalid IPv4: {d}")

    if hostname is not None and hostname.strip():
        hostname = _sanitize_hostname(hostname)
        if not hostname:
            errors.append("hostname is invalid")

    if errors:
        return {"ok": False, "errors": errors, "state": get_network_state(iface=iface)}

    # Update dhcpcd.conf (best-effort). For DHCP, remove our block.
    conf = _read_file(_DHCPCD_CONF)
    if mode == "dhcp":
        # Remove block entirely if present
        if _BLOCK_BEGIN in conf and _BLOCK_END in conf:
            before = conf.split(_BLOCK_BEGIN, 1)[0]
            after = conf.split(_BLOCK_END, 1)[1]
            conf = (before.rstrip() + "\n\n" + after.lstrip()).strip() + "\n"
    else:
        prefix_int = int(prefix)
        lines = [
            _BLOCK_BEGIN,
            f"interface {iface}",
            f"static ip_address={ip}/{prefix_int}",
        ]
        if gateway:
            lines.append(f"static routers={gateway}")
        if dns:
            lines.append("static domain_name_servers=" + " ".join(dns))
        lines.append(_BLOCK_END)
        conf = _replace_block(conf, "\n".join(lines) + "\n")

    write_error = None
    try:
        _write_file(_DHCPCD_CONF, conf)
    except Exception as e:
        write_error = f"Failed to write {_DHCPCD_CONF}: {e}"

    cmd_results = []
    if hostname:
        rc, out, err = _run(["hostnamectl", "set-hostname", hostname])
        cmd_results.append({"cmd": "hostnamectl set-hostname", "rc": rc, "out": out.strip(), "err": err.strip()})

    # Restart dhcpcd (common on Raspberry Pi OS); ignore failures.
    rc, out, err = _run(["systemctl", "restart", "dhcpcd"])
    cmd_results.append({"cmd": "systemctl restart dhcpcd", "rc": rc, "out": out.strip(), "err": err.strip()})

    return {
        "ok": write_error is None,
        "warning": write_error,
        "commands": cmd_results,
        "state": get_network_state(iface=iface),
    }


def _cpu_usage_percent(sample_s: float = 0.15) -> Optional[float]:
    """Best-effort CPU usage, sampled from /proc/stat."""
    def read() -> Optional[Tuple[int, int]]:
        txt = _read_file("/proc/stat")
        for line in txt.splitlines():
            if line.startswith("cpu "):
                parts = line.split()
                if len(parts) < 8:
                    return None
                vals = [int(x) for x in parts[1:8]]
                user, nice, system, idle, iowait, irq, softirq = vals
                idle_all = idle + iowait
                non_idle = user + nice + system + irq + softirq
                total = idle_all + non_idle
                return total, idle_all
        return None

    a = read()
    if not a:
        return None
    time.sleep(max(0.05, sample_s))
    b = read()
    if not b:
        return None

    totald = b[0] - a[0]
    idled = b[1] - a[1]
    if totald <= 0:
        return None
    usage = (totald - idled) / totald * 100.0
    return round(usage, 1)


def _cpu_temp_c() -> Optional[float]:
    for p in (
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/devices/virtual/thermal/thermal_zone0/temp",
    ):
        s = _read_file(p).strip()
        if s and s.isdigit():
            try:
                return round(int(s) / 1000.0, 1)
            except Exception:
                pass
    return None


def _uptime_s() -> Optional[int]:
    s = _read_file("/proc/uptime").strip().split()
    if not s:
        return None
    try:
        return int(float(s[0]))
    except Exception:
        return None


def get_system_info() -> Dict:
    return {
        "hostname": _hostname(),
        "cpu_usage_percent": _cpu_usage_percent(),
        "cpu_temp_c": _cpu_temp_c(),
        "uptime_s": _uptime_s(),
        "load_avg": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
    }
