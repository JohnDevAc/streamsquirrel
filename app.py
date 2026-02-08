from fastapi import FastAPI, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import Dict, List

import re

from models import NDISource, SlotConfig, SystemConfig, Status
from ndi_backend import list_sources
from pipeline import SlotPipeline
from sap import build_sdp
from config import DEFAULT_SLOTS, SDP_SESSION_NAME_PREFIX

from system_utils import (
    get_network_state,
    apply_network_config,
    get_system_info,
)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

running = False
pipelines: Dict[int, SlotPipeline] = {}
last_error: str = ""

slots: List[SlotConfig] = []
for i, (ip, port) in enumerate(DEFAULT_SLOTS, start=1):
    slots.append(SlotConfig(
        slot_id=i,
        ndi_source_name=None,
        aes67_stream_name=f"{SDP_SESSION_NAME_PREFIX} Slot {i}",
        mcast_ip=ip,
        mcast_port=port
    ))

@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/system")
def system_page():
    """System control page (network + system info)."""
    return FileResponse("static/system.html")


@app.get("/api/system/network")
def api_system_network():
    """Return hostname + current/configured network state for eth0."""
    return get_network_state(iface="eth0")


@app.post("/api/system/network")
def api_set_system_network(body: dict):
    """Apply network settings (DHCP/Static) and optionally set hostname.

    Note: On a Pi this generally requires root privileges.
    """
    return apply_network_config(iface="eth0", payload=body)


@app.get("/api/system/info")
def api_system_info():
    return get_system_info()

@app.get("/api/sources", response_model=List[NDISource])
def api_sources():
    names = list_sources()
    return [NDISource(name=n) for n in names]

@app.get("/api/config", response_model=SystemConfig)
def api_get_config():
    return SystemConfig(slots=slots)

@app.post("/api/config/slot", response_model=SystemConfig)
def api_set_slot(cfg: SlotConfig):
    global running, slots
    if running:
        return SystemConfig(slots=slots)

    for idx, s in enumerate(slots):
        if s.slot_id == cfg.slot_id:
            slots[idx] = cfg
            break
    return SystemConfig(slots=slots)


@app.get("/api/slot/{slot_id}/sdp")
def api_slot_sdp(slot_id: int):
    """Download SDP for an active (running) slot. Returns 404 if inactive."""
    global running, pipelines, slots
    try:
        if not running or slot_id not in pipelines:
            return Response(status_code=404)

        slot = next((s for s in slots if s.slot_id == slot_id), None)
        if not slot or not slot.ndi_source_name:
            return Response(status_code=404)

        sdp = build_sdp(slot.aes67_stream_name, slot.mcast_ip, slot.mcast_port)

        # Use a conservative filename to avoid header issues on some browsers/servers
        headers = {
            "Content-Disposition": f'attachment; filename="slot{slot_id}.sdp"'
        }
        return Response(content=sdp, media_type="application/sdp", headers=headers)
    except Exception as e:
        # Print to server log for debugging, but avoid leaking details to client
        print(f"[SDP] slot {slot_id} error: {e}")
        return Response(status_code=500)


@app.get("/api/slot/{slot_id}/sdp_monitor")
def api_slot_sdp_monitor(slot_id: int):
    """Download VLC-friendly monitor SDP (L16) for an active slot."""
    global running, pipelines, slots
    if not running or slot_id not in pipelines:
        return Response(status_code=404)
    slot = next((s for s in slots if s.slot_id == slot_id), None)
    if not slot or not slot.ndi_source_name:
        return Response(status_code=404)

    # Monitor port is base port + 2 (see config.py)
    from config import MONITOR_PAYLOAD_TYPE, MONITOR_PORT_OFFSET
    mon_port = int(slot.mcast_port) + int(MONITOR_PORT_OFFSET)
    sdp = build_sdp(slot.aes67_stream_name + " (Monitor L16)", slot.mcast_ip, mon_port, payload_type=MONITOR_PAYLOAD_TYPE, codec="L16")
    headers = {"Content-Disposition": f'attachment; filename="slot{slot_id}_monitor.sdp"'}
    return Response(content=sdp, media_type="application/sdp", headers=headers)

@app.get("/api/active_slots")
def api_active_slots():
    """Return list of slot IDs currently outputting AES67."""
    return sorted([int(k) for k in pipelines.keys()]) if running else []

@app.get("/api/status", response_model=Status)
def api_status():
    return Status(running=running, message=("Live" if running else (last_error or "Offline")))

@app.post("/api/start", response_model=Status)
def api_start():
    global last_error
    global running, pipelines
    if running:
        return Status(running=True, message="Live")

    pipelines = {}
    # start pipelines only for configured slots
    for s in slots:
        if s.ndi_source_name:
            p = SlotPipeline(
                slot_id=s.slot_id,
                ndi_source_name=s.ndi_source_name,
                aes67_name=s.aes67_stream_name,
                mcast_ip=s.mcast_ip,
                mcast_port=s.mcast_port
            )
            pipelines[s.slot_id] = p

    try:
        for p in pipelines.values():
            p.start()
    except Exception as e:
        # rollback if any slot fails to start
        for p in pipelines.values():
            try: p.stop()
            except Exception: pass
        pipelines = {}
        running = False
        last_error = f"Start failed: {e}"
        return Status(running=False, message=last_error)

    last_error = ""
    running = True
    return Status(running=True, message="Live")

@app.post("/api/stop", response_model=Status)
def api_stop():
    global last_error
    global running, pipelines
    if not running:
        return Status(running=False, message="Offline")

    for p in pipelines.values():
        p.stop()

    pipelines = {}
    running = False
    last_error = ""
    return Status(running=False, message="Offline")


@app.get("/api/debug/slot/{slot_id}")
def api_debug_slot(slot_id: int):
    global pipelines, running
    if not running or slot_id not in pipelines:
        return {"running": running, "slot_id": slot_id, "active": False}
    return pipelines[slot_id].debug()
