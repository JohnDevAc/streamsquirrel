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

  // form defaults
  $("hostname").value = state.hostname || "";
  $("mode").value = (cfg.mode || "dhcp");
  const showStatic = (cfg.mode === "static");
  $("staticFields").style.display = showStatic ? "block" : "none";
  $("ip").value = cfg.ip || (state.current?.ip || "");
  $("prefix").value = (cfg.prefix ?? state.current?.prefix ?? 24);
  $("gateway").value = cfg.gateway || (state.current?.gateway || "");
  $("dns").value = (cfg.dns && cfg.dns.length) ? cfg.dns.join(", ") : (state.current?.dns || []).join(", ");
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

$("mode").addEventListener("change", () => {
  const show = $("mode").value === "static";
  $("staticFields").style.display = show ? "block" : "none";
});

$("applyBtn").addEventListener("click", applyNetwork);

const refreshBtn = $("refreshBtn");
if (refreshBtn){
  refreshBtn.addEventListener("click", async () => {
    setMsg("Refreshing…", true);
    await refreshAll();
    setMsg("Ready", true);
  });
}


async function refreshAll(){
  await Promise.all([
    refreshNetwork(),
    refreshSystemInfo(),
    refreshBridge(),
  ]);
}

refreshAll();

// Poll fast for system info, slower for network state
setInterval(refreshSystemInfo, 2000);
setInterval(refreshBridge, 2000);
setInterval(refreshNetwork, 6000);

// (Optional) Logo sizing is handled by CSS on this page.
