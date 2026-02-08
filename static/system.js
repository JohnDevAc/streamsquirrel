async function apiGet(path){
  const r = await fetch(path);
  return r.json();
}

async function apiPost(path, body){
  const r = await fetch(path, {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(body || {})
  });
  return r.json();
}

function $(id){ return document.getElementById(id); }

let _editing = false;
let _suspendFormSyncUntil = 0;  // epoch ms; while user edits, don't overwrite form values


function setMsg(text, ok=true){
  const pill = $("sysMsg");
  if (!pill) return;
  pill.textContent = text;
  pill.classList.toggle("live", !!ok);
  pill.classList.toggle("off", !ok);
}

function fmtList(xs){
  if (!xs || !xs.length) return "—";
  return xs.join(", ");
}

function fmtUptime(sec){
  if (sec === null || sec === undefined) return "—";
  sec = Math.max(0, Number(sec));
  const d = Math.floor(sec / 86400); sec -= d*86400;
  const h = Math.floor(sec / 3600); sec -= h*3600;
  const m = Math.floor(sec / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

let netState = null;

function renderNetwork(state){
  netState = state;
  $("curHostname").textContent = state.hostname || "—";
  $("curIp").textContent = state.current?.ip ? `${state.current.ip}/${state.current.prefix}` : "—";
  $("curGw").textContent = state.current?.gateway || "—";
  $("curDns").textContent = fmtList(state.current?.dns || []);

  const sp = (state.link_speed_mbps ? `${state.link_speed_mbps} Mb/s` : "—");
  $("linkSpeed").textContent = `Link: ${sp}`;

  // configured summary
  const cfg = state.configured || {};
  const parts = [];
  parts.push((cfg.mode || "dhcp").toUpperCase());
  if (cfg.mode === "static" && cfg.ip && cfg.prefix !== null && cfg.prefix !== undefined){
    parts.push(`${cfg.ip}/${cfg.prefix}`);
  }
  $("cfgSummary").textContent = parts.join(" • ") || "—";

  // form defaults (don't overwrite while user is editing)
  const now = Date.now();
  const canSyncForm = (!_editing) && (now > _suspendFormSyncUntil);
  if (canSyncForm){
    $("hostname").value = state.hostname || "";
    $("mode").value = (cfg.mode || "dhcp");
    const showStatic = (cfg.mode === "static");
    $("staticFields").style.display = showStatic ? "block" : "none";
    $("ip").value = cfg.ip || (state.current?.ip || "");
    $("prefix").value = (cfg.prefix ?? state.current?.prefix ?? 24);
    $("gateway").value = cfg.gateway || (state.current?.gateway || "");
    $("dns").value = (cfg.dns && cfg.dns.length) ? cfg.dns.join(", ") : (state.current?.dns || []).join(", ");
  }
}

function readNetworkForm(){
  const mode = $("mode").value;
  const hostname = $("hostname").value.trim();
  const payload = { mode };
  if (hostname) payload.hostname = hostname;
  if (mode === "static"){
    payload.ip = $("ip").value.trim();
    payload.prefix = Number($("prefix").value);
    payload.gateway = $("gateway").value.trim();
    payload.dns = $("dns").value;
  }
  return payload;
}

async function refreshNetwork(){
  const st = await apiGet("/api/system/network");
  renderNetwork(st);
}

async function applyNetwork(){
  const btn = $("applyBtn");
  btn.disabled = true;
  setMsg("Applying…", true);
  try{
    const res = await apiPost("/api/system/network", readNetworkForm());
    if (!res.ok){
      setMsg((res.errors || ["Failed"])[0], false);
    }else if (res.warning){
      setMsg(res.warning, false);
    }else{
      setMsg("Applied", true);
    }
    if (res.ok){
      _editing = false;
      _suspendFormSyncUntil = 0;
    }
    if (res.state) renderNetwork(res.state);
  }catch(e){
    setMsg("Apply failed", false);
  }finally{
    btn.disabled = false;
  }
}

async function refreshSystemInfo(){
  const info = await apiGet("/api/system/info");
  $("cpuUsage").textContent = (info.cpu_usage_percent !== null && info.cpu_usage_percent !== undefined) ? `${info.cpu_usage_percent}%` : "—";
  $("cpuTemp").textContent = (info.cpu_temp_c !== null && info.cpu_temp_c !== undefined) ? `${info.cpu_temp_c}°C` : "—";
  $("uptime").textContent = fmtUptime(info.uptime_s);
  $("loadAvg").textContent = (info.load_avg && info.load_avg.length) ? info.load_avg.map(x => Number(x).toFixed(2)).join(" ") : "—";
}

async function refreshBridge(){
  try{
    const st = await apiGet("/api/status");
    const active = await apiGet("/api/active_slots");
    $("bridgeState").textContent = st.running ? "Live" : (st.message || "Offline");
    $("activeSlots").textContent = (Array.isArray(active) && active.length) ? active.join(", ") : "—";
  }catch(e){}
}

$("mode").addEventListener("change", (e) => {
  // User is editing; don't let background polling overwrite the form
  _editing = true;
  _suspendFormSyncUntil = Date.now() + 15000; // 15s grace window
  const show = $("mode").value === "static";
  $("staticFields").style.display = show ? "block" : "none";
});


$("applyBtn").addEventListener("click", applyNetwork);

async function restartProgram(){
  const btn = $("restartProgramBtn");
  if (!btn) return;
  const ok = confirm("Restart the Stream Squirrel program now?\n\nThe web UI may disconnect briefly.");
  if (!ok) return;
  btn.disabled = true;
  try{
    const res = await apiPost("/api/system/restart_program", {});
    if (res && res.ok){
      alert("Restarting program… The page may disconnect for a few seconds.");
    }else{
      alert((res && (res.error || res.message)) ? (res.error || res.message) : "Restart failed.");
    }
  }catch(e){
    alert("Restart failed.");
  }finally{
    // The process may restart before we can re-enable; best-effort.
    btn.disabled = false;
  }
}

async function rebootPi(){
  const btn = $("rebootPiBtn");
  if (!btn) return;
  const ok = confirm("Reboot the Raspberry Pi now?\n\nYou will lose connection for a short time.");
  if (!ok) return;
  btn.disabled = true;
  try{
    const res = await apiPost("/api/system/reboot", {});
    if (res && res.ok){
      alert("Rebooting… This page will disconnect until the Pi is back online.");
    }else{
      alert((res && (res.error || res.message)) ? (res.error || res.message) : "Reboot failed.");
    }
  }catch(e){
    alert("Reboot failed.");
  }finally{
    btn.disabled = false;
  }
}

const restartProgramBtn = $("restartProgramBtn");
if (restartProgramBtn){
  restartProgramBtn.addEventListener("click", restartProgram);
}
const rebootPiBtn = $("rebootPiBtn");
if (rebootPiBtn){
  rebootPiBtn.addEventListener("click", rebootPi);
}

// Mark as editing when any form field changes so polling doesn't reset selections
["hostname","ip","prefix","gateway","dns"].forEach((id) => {
  const el = $(id);
  if (!el) return;
  el.addEventListener("input", () => {
    _editing = true;
    _suspendFormSyncUntil = Date.now() + 15000;
  });
});

const refreshBtn = $("refreshBtn");
if (refreshBtn){
  refreshBtn.addEventListener("click", async () => {
    setMsg("Refreshing…", true);
    await refreshAll();
    setMsg("Ready", true);
  });
}


async function refreshLogs(){
  const box = $("logsBox");
  const meta = $("logsMeta");
  if (!box) return;
  try{
    const r = await apiGet("/api/system/logs");
    if (!r || r.ok === false){
      box.textContent = (r && r.error) ? r.error : "Unable to read logs.";
      if (meta) meta.textContent = "—";
      return;
    }
    const lines = r.lines || [];
    box.textContent = (lines.length ? lines.join("\n") : "—");
    if (meta){
      const src = r.source ? `Source: ${r.source}` : "Source: —";
      meta.textContent = `${src} • Updated ${new Date().toLocaleTimeString()}`;
    }
  }catch(e){
    box.textContent = "Unable to read logs.";
    if (meta) meta.textContent = "—";
  }
}

async function refreshAll(){
  await Promise.all([
    refreshNetwork(),
    refreshSystemInfo(),
    refreshBridge(),
  ]);
}

refreshAll();

// Logs: only fetch when expanded, and allow manual refresh
const logsDetails = $("logsDetails");
const logsRefreshBtn = $("logsRefreshBtn");
if (logsDetails){
  logsDetails.addEventListener("toggle", () => {
    if (logsDetails.open) refreshLogs();
  });
}
if (logsRefreshBtn){
  logsRefreshBtn.addEventListener("click", (e) => { e.preventDefault(); refreshLogs(); });
}


// Poll fast for system info, slower for network state
setInterval(refreshSystemInfo, 2000);
setInterval(refreshBridge, 2000);
setInterval(refreshNetwork, 6000);

// (Optional) Logo sizing is handled by CSS on this page.