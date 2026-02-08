import os
import re
import socket
import subprocess
import time
from typing import Dict, List, Optional, Tuple


_DHCPCD_CONF = os.getenv("DHCPCD_CONF", "/etc/dhcpcd.conf")
_HOSTNAME_FILE = os.getenv("HOSTNAME_FILE", "/etc/hostname")
_HOSTS_FILE = os.getenv("HOSTS_FILE", "/etc/hosts")
_BLOCK_BEGIN = "# StreamSquirrel network config BEGIN"
_BLOCK_END = "# StreamSquirrel network config END"


def _run(cmd: List[str], *, sudo: bool = False, timeout_s: float = 5.0) -> Tuple[int, str, str]:
    """Run a command and return (rc, stdout, stderr).

    If sudo=True and we're not root, the command is executed via:
      sudo -n <cmd>
    (non-interactive; fails fast if not permitted).
    """
    run_cmd = list(cmd)
    if sudo and os.geteuid() != 0:
        run_cmd = ["sudo", "-n"] + run_cmd
    p = subprocess.Popen(run_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(timeout=timeout_s)
    return p.returncode, (out or ""), (err or "")


def _nm_is_active() -> bool:
    rc, out, _ = _run(["systemctl", "is-active", "NetworkManager"], sudo=False, timeout_s=1.5)
    return rc == 0 and out.strip() == "active"


def _nm_active_connection_for_device(device: str) -> Optional[str]:
    """Return the active NetworkManager connection name for a device (e.g. eth0)."""
    rc, out, _ = _run(["nmcli", "-t", "-f", "NAME,DEVICE", "con", "show", "--active"], sudo=True, timeout_s=2.5)
    if rc != 0 or not out.strip():
        return None
    for line in out.splitlines():
        # format: NAME:DEVICE
        parts = line.split(":", 1)
        if len(parts) == 2 and parts[1].strip() == device:
            name = parts[0].strip()
            return name or None
    return None


def _nm_set_dhcp_hostname(conn_name: str, hostname: str) -> Tuple[int, str, str]:
    return _run(["nmcli", "con", "modify", conn_name, "ipv4.dhcp-hostname", hostname], sudo=True, timeout_s=3.5)


def _nm_set_dhcp_hostname_for_iface(iface: str, hostname: str) -> Tuple[bool, List[Dict[str, str]]]:
    """Best-effort: set NetworkManager DHCP hostname for the active connection on iface."""
    res: List[Dict[str, str]] = []
    if not _nm_is_active():
        return True, res
    conn = _nm_active_connection_for_device(iface)
    if not conn:
        return False, [{"cmd": f"nmcli active conn for {iface}", "rc": "1", "out": "", "err": "No active NetworkManager connection found"}]

    rc, out, err = _nm_set_dhcp_hostname(conn, hostname)
    res.append({"cmd": f"nmcli con modify {conn} ipv4.dhcp-hostname <hostname>", "rc": str(rc), "out": out.strip(), "err": err.strip()})

    # Bounce the connection so the DHCP server learns the new name.
    res.extend(_nm_bounce_connection(conn))
    ok = (rc == 0)
    return ok, res


def _nm_bounce_connection(conn_name: str) -> List[Dict[str, str]]:
    """Down/up a NetworkManager connection. Returns cmd result dicts."""
    res: List[Dict[str, str]] = []
    rc, out, err = _run(["nmcli", "con", "down", conn_name], sudo=True, timeout_s=10.0)
    res.append({"cmd": f"nmcli con down {conn_name}", "rc": str(rc), "out": out.strip(), "err": err.strip()})
    rc, out, err = _run(["nmcli", "con", "up", conn_name], sudo=True, timeout_s=15.0)
    res.append({"cmd": f"nmcli con up {conn_name}", "rc": str(rc), "out": out.strip(), "err": err.strip()})
    return res


def disable_wlan0_on_startup() -> List[Dict[str, str]]:
    """Best-effort disable of wlan0 at service startup.

    This is intended to run at boot (the streamsquirrel service starts at boot).
    It turns Wi‑Fi radio off via NetworkManager and disconnects wlan0.
    """
    res: List[Dict[str, str]] = []
    if not _nm_is_active():
        return res
    rc, out, err = _run(["nmcli", "radio", "wifi", "off"], sudo=True, timeout_s=3.0)
    res.append({"cmd": "nmcli radio wifi off", "rc": str(rc), "out": out.strip(), "err": err.strip()})
    rc, out, err = _run(["nmcli", "dev", "disconnect", "wlan0"], sudo=True, timeout_s=4.0)
    res.append({"cmd": "nmcli dev disconnect wlan0", "rc": str(rc), "out": out.strip(), "err": err.strip()})
    return res


def _read_file_priv(path: str) -> str:
    """Read a file, falling back to `sudo -n cat` if needed."""
    txt = _read_file(path)
    if txt != "":
        return txt
    # If we couldn't read (permission or missing), try sudo cat.
    rc, out, _ = _run(["cat", path], sudo=True, timeout_s=2.0)
    return out if rc == 0 else ""


def _write_file_priv(path: str, text: str) -> Optional[str]:
    """Write a file. Returns error string or None on success.

    Tries normal write first, then falls back to `sudo -n tee <path>`.
    """
    try:
        _write_file(path, text)
        return None
    except Exception as e:
        # Fallback: sudo tee
        try:
            p = subprocess.run(
                ["sudo", "-n", "tee", path],
                input=text,
                capture_output=True,
                text=True,
                timeout=2.5,
            )
            if p.returncode == 0:
                return None
            return (p.stderr or "").strip() or str(e)
        except Exception as e2:
            return str(e2) or str(e)


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def _write_file(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _set_hostname_persistent(new_hostname: str) -> Tuple[bool, List[Dict[str, str]]]:
    """Persist hostname across reboots.

    We try multiple mechanisms because Raspberry Pi OS images vary:
    - Write /etc/hostname
    - Update 127.0.1.1 entry in /etc/hosts (common Debian/RPi convention)
    - Call hostnamectl (systemd)
    - Call hostname (sets runtime kernel hostname)

    Returns (ok, command_results).
    """
    results: List[Dict[str, str]] = []
    ok = True

    # 1) /etc/hostname
    err = _write_file_priv(_HOSTNAME_FILE, new_hostname.strip() + "\n")
    if err is None:
        results.append({"cmd": f"write {_HOSTNAME_FILE}", "rc": "0", "out": "", "err": ""})
    else:
        ok = False
        results.append({"cmd": f"write {_HOSTNAME_FILE}", "rc": "1", "out": "", "err": err})

    # 2) /etc/hosts – replace 127.0.1.1 line if present, else append
    try:
        hosts_txt = _read_file_priv(_HOSTS_FILE)
        lines = hosts_txt.splitlines()
        replaced = False
        new_lines: List[str] = []
        for line in lines:
            if re.match(r"^\s*127\.0\.1\.1\s+", line):
                new_lines.append(f"127.0.1.1\t{new_hostname}")
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            # Keep a blank line for readability if file already has content
            if new_lines and new_lines[-1].strip() != "":
                new_lines.append("")
            new_lines.append(f"127.0.1.1\t{new_hostname}")
        err = _write_file_priv(_HOSTS_FILE, "\n".join(new_lines).rstrip() + "\n")
        if err is None:
            results.append({"cmd": f"update {_HOSTS_FILE}", "rc": "0", "out": "", "err": ""})
        else:
            ok = False
            results.append({"cmd": f"update {_HOSTS_FILE}", "rc": "1", "out": "", "err": err})
    except Exception as e:
        ok = False
        results.append({"cmd": f"update {_HOSTS_FILE}", "rc": "1", "out": "", "err": str(e)})

    # 3) hostnamectl (systemd) – optional
    rc, out, err = _run(["hostnamectl", "set-hostname", new_hostname], sudo=True, timeout_s=3.0)
    results.append({"cmd": "hostnamectl set-hostname", "rc": str(rc), "out": out.strip(), "err": err.strip()})
    if rc != 0:
        # Don't fail overall just because hostnamectl isn't available / permitted
        pass

    # 4) runtime hostname (kernel)
    rc, out, err = _run(["hostname", new_hostname], sudo=True, timeout_s=2.0)
    results.append({"cmd": "hostname <name>", "rc": str(rc), "out": out.strip(), "err": err.strip()})
    if rc != 0:
        # Also best-effort; not all systems allow this without privileges
        pass

    return ok, results


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

    write_error = _write_file_priv(_DHCPCD_CONF, conf)
    if write_error is not None:
        write_error = f"Failed to write {_DHCPCD_CONF}: {write_error}"

    cmd_results: List[Dict[str, str]] = []
    hostname_ok = True
    if hostname:
        hostname_ok, host_cmds = _set_hostname_persistent(hostname)
        cmd_results.extend(host_cmds)

        # If NetworkManager is active, also set the DHCP "client hostname" on the
        # active connection for this interface. Many routers/"network scan" tools
        # show the DHCP hostname (not /etc/hostname), so without this the network
        # may continue to display the old name until lease renewal.
        nm_ok, nm_cmds = _nm_set_dhcp_hostname_for_iface(iface=iface, hostname=hostname)
        cmd_results.extend(nm_cmds)
        if not nm_ok:
            # Best-effort; don't fail the whole request.
            pass

    # Restart network service (varies by distro/image).
    restart_candidates = ["dhcpcd", "NetworkManager", "systemd-networkd", "networking"]
    restarted = False
    last_err = ""
    for svc in restart_candidates:
        rc, out, err = _run(["systemctl", "restart", svc], sudo=True, timeout_s=3.0)
        cmd_results.append({"cmd": f"systemctl restart {svc}", "rc": str(rc), "out": out.strip(), "err": err.strip()})
        if rc == 0:
            restarted = True
            break
        if err.strip():
            last_err = err.strip()

    net_warning: Optional[str] = None
    if not restarted:
        net_warning = "Network config written; could not restart network service automatically. A reboot may be required."
        if last_err:
            net_warning += f" (last error: {last_err})"

    warning_out = write_error
    if warning_out is None:
        warning_out = net_warning
    elif net_warning:
        warning_out = warning_out + " | " + net_warning

    return {
        "ok": (write_error is None) and hostname_ok,
        "warning": warning_out,
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


def get_last_logs(n: int = 20) -> Dict:
    """Return last *n* log lines for the program.

    Tries (in order):
    1) systemd journal for unit $STREAMSQUIRREL_SERVICE (default: streamsquirrel)
       - Uses non-interactive sudo (``sudo -n``) automatically when not running as root.
       - Also tries with/without the ``.service`` suffix.
    2) tail common log files (env STREAMSQUIRREL_LOG or a few fallbacks)

    Always returns a dict:
      {"ok": bool, "lines": [...], "source": str, "error": str}
    """
    n = int(n or 20)
    n = max(1, min(200, n))

    service = os.getenv("STREAMSQUIRREL_SERVICE", "streamsquirrel").strip() or "streamsquirrel"
    # Support unit name provided with/without .service
    units = [service]
    if service.endswith(".service"):
        units.append(service[:-8])
    else:
        units.append(service + ".service")
    # de-dupe, preserve order
    seen = set()
    units = [u for u in units if not (u in seen or seen.add(u))]

    # 1) journalctl
    last_err = ""
    for u in units:
        rc, out, err = _run(["journalctl", "-u", u, "-n", str(n), "--no-pager", "-o", "short"], sudo=True, timeout_s=3.5)
        if rc == 0 and out.strip():
            lines = [ln.rstrip("\n") for ln in out.splitlines()[-n:]]
            return {"ok": True, "lines": lines, "source": f"journalctl -u {u}", "error": ""}
        last_err = (err or "").strip() or last_err

        # Fallback without sudo (works if user is in systemd-journal group)
        rc2, out2, err2 = _run(["journalctl", "-u", u, "-n", str(n), "--no-pager", "-o", "short"], sudo=False, timeout_s=3.5)
        if rc2 == 0 and out2.strip():
            lines = [ln.rstrip("\n") for ln in out2.splitlines()[-n:]]
            return {"ok": True, "lines": lines, "source": f"journalctl -u {u}", "error": ""}
        last_err = (err2 or "").strip() or last_err

    # 2) log files
    candidates: List[str] = []
    env_log = os.getenv("STREAMSQUIRREL_LOG")
    if env_log:
        candidates.append(env_log)
    candidates += [
        "/var/log/streamsquirrel.log",
        "/var/log/streamsquirrel/streamsquirrel.log",
        "/opt/streamsquirrel/streamsquirrel.log",
        "/opt/streamsquirrel/logs/streamsquirrel.log",
        "/opt/streamsquirrel/logs/app.log",
    ]

    for path in candidates:
        if not path:
            continue
        # First try direct read
        try:
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()[-n:]
            if lines:
                return {"ok": True, "lines": lines, "source": f"file: {path}", "error": ""}
        except Exception as e:
            last_err = str(e) or last_err

        # Then try sudo tail (non-interactive)
        rc, out, err = _run(["tail", "-n", str(n), path], sudo=True, timeout_s=2.5)
        if rc == 0 and out.strip():
            lines = [ln.rstrip("\n") for ln in out.splitlines()[-n:]]
            return {"ok": True, "lines": lines, "source": f"sudo tail: {path}", "error": ""}
        last_err = (err or "").strip() or last_err

    return {
        "ok": False,
        "lines": [],
        "source": "",
        "error": "No logs available (journal/log file not found or insufficient permissions)." + (
            f" ({last_err})" if last_err else ""
        ),
    }


def restart_program() -> Dict:
    """Restart the Stream Squirrel program via systemd.

    Uses unit name from $STREAMSQUIRREL_SERVICE (default: streamsquirrel).
    Returns {"ok": bool, "error": str, "results": [...]}.
    """
    service = os.getenv("STREAMSQUIRREL_SERVICE", "streamsquirrel").strip() or "streamsquirrel"
    units = [service]
    if service.endswith(".service"):
        units.append(service[:-8])
    else:
        units.append(service + ".service")
    # de-dupe
    seen = set()
    units = [u for u in units if not (u in seen or seen.add(u))]

    results: List[Dict[str, str]] = []
    last_err = ""
    for u in units:
        rc, out, err = _run(["systemctl", "restart", u], sudo=True, timeout_s=4.0)
        results.append({"cmd": f"systemctl restart {u}", "rc": str(rc), "out": out.strip(), "err": err.strip()})
        if rc == 0:
            return {"ok": True, "error": "", "results": results}
        last_err = (err or "").strip() or last_err

    return {
        "ok": False,
        "error": last_err or "Unable to restart program (insufficient permissions or unit not found).",
        "results": results,
    }


def reboot_pi() -> Dict:
    """Reboot the device (best-effort).

    Attempts `systemctl reboot` first, then `reboot`.
    Returns immediately with {"ok": bool, "error": str, "results": [...]}.
    """
    results: List[Dict[str, str]] = []

    for cmd in (["systemctl", "reboot"], ["reboot"]):
        try:
            rc, out, err = _run(list(cmd), sudo=True, timeout_s=2.0)
            results.append({"cmd": " ".join(cmd), "rc": str(rc), "out": out.strip(), "err": err.strip()})
            if rc == 0:
                return {"ok": True, "error": "", "results": results}
        except Exception as e:
            results.append({"cmd": " ".join(cmd), "rc": "1", "out": "", "err": str(e)})

    last_err = ""
    for r in reversed(results):
        if r.get("err"):
            last_err = r["err"]
            break
    return {
        "ok": False,
        "error": last_err or "Unable to reboot (insufficient permissions).",
        "results": results,
    }
