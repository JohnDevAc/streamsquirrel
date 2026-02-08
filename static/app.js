let sources = [];
let config = null;
let running = false;
let activeSlots = [];

async function apiGet(path){
  const r = await fetch(path);
  return r.json();
}
async function apiPost(path, body){
  const r = await fetch(path, {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: body ? JSON.stringify(body) : "{}"
  });
  return r.json();
}

function el(tag, cls, text){
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function setStatusUI(isRunning, msg){
  running = isRunning;
  const pill = document.getElementById("statusPill");
  pill.textContent = isRunning ? "Live" : (msg || "Offline");
  pill.title = msg || (isRunning ? "Live" : "Offline");
  pill.classList.toggle("live", isRunning);
  pill.classList.toggle("off", !isRunning);

  // NDI refresh button should not be usable while Live
  const ndiRefreshBtn = document.getElementById("ndiRefreshBtn");
  if (ndiRefreshBtn) ndiRefreshBtn.disabled = isRunning;

  // Start/Stop active brightness
  const startBtn = document.getElementById("startBtn");
  const stopBtn = document.getElementById("stopBtn");
  if (startBtn) startBtn.classList.toggle("active", isRunning);
  if (stopBtn) stopBtn.classList.toggle("active", isRunning);

  // lock controls when Live
  document.querySelectorAll("select,input").forEach(x => x.disabled = isRunning);
}

async function refreshNdiSources(){
  if (running) return;
  try{
    sources = await apiGet("/api/sources");
    renderSources();
    renderSlots();
    setStatusUI(running);
  }catch(e){}
}

function updateSdpButtons(){
  // Enable only for active slots while Live (no full re-render)
  document.querySelectorAll("button[data-slot-id]").forEach(btn => {
    const id = Number(btn.dataset.slotId);
    const isActive = running && Array.isArray(activeSlots) && activeSlots.includes(id);
    btn.disabled = !isActive;
    btn.title = isActive ? "Download SDP for this AES67 stream" : "Available when this slot is Live";
  });
}

function renderSources(){
  const box = document.getElementById("sourcesList");
  if (!box) return; // section removed from UI
  box.innerHTML = "";
  if (!sources.length){
    box.textContent = "No NDI sources detected.";
    return;
  }
  sources.forEach(s => box.appendChild(el("div","tag", s.name)));
}

function renderSlots(){
  const grid = document.getElementById("slotsGrid");
  grid.innerHTML = "";

  config.slots.forEach(slot => {
    const card = el("div","card");
    const head = el("div","cardhead");
    head.appendChild(el("div","slotnum", `Slot ${slot.slot_id}`));
    head.appendChild(el("div","addr", `${slot.mcast_ip}:${slot.mcast_port}`));
    card.appendChild(head);

    // NDI dropdown
    const f1 = el("div","field");
    f1.appendChild(el("label", null, "NDI Source"));
    const sel = document.createElement("select");
    const opt0 = document.createElement("option");
    opt0.value = "";
    opt0.textContent = "— Not assigned —";
    sel.appendChild(opt0);

    sources.forEach(src => {
      const o = document.createElement("option");
      o.value = src.name;
      o.textContent = src.name;
      sel.appendChild(o);
    });

    sel.value = slot.ndi_source_name || "";

    // lock while Live
    sel.disabled = running;

    sel.addEventListener("change", async () => {
      // default AES67 name to NDI name when selected, if user hasn't customized much
      let aesName = slot.aes67_stream_name;
      if (sel.value && (!aesName || aesName.startsWith("AES67 Slot"))) {
        aesName = sel.value;
      }
      const updated = {
        ...slot,
        ndi_source_name: sel.value || null,
        aes67_stream_name: aesName
      };
      config = await apiPost("/api/config/slot", updated);
      renderSlots();
      setStatusUI(running);
    });

    f1.appendChild(sel);
    card.appendChild(f1);

    // AES67 name edit
    const f2 = el("div","field");
    f2.appendChild(el("label", null, "AES67 Stream Name"));
    const inp = document.createElement("input");
    inp.type = "text";
    inp.value = slot.aes67_stream_name;

    // lock while Live
    inp.disabled = running;

    inp.addEventListener("change", async () => {
      const updated = { ...slot, aes67_stream_name: inp.value };
      config = await apiPost("/api/config/slot", updated);
    });

    f2.appendChild(inp);
    card.appendChild(f2);

    // Download SDP (enabled only when slot is active and running)
    const dlWrap = el("div","field");
    dlWrap.appendChild(el("label", null, "SDP"));
    const dlBtn = document.createElement("button");
    dlBtn.className = "btn small";
    dlBtn.textContent = "Download SDP (AES67)";
    dlBtn.dataset.slotId = String(slot.slot_id);
    dlBtn.addEventListener("click", () => {
      const id = Number(slot.slot_id);
      const isActiveNow = running && Array.isArray(activeSlots) && activeSlots.includes(id);
      if (!isActiveNow) return;
      // trigger browser download
      window.location.href = `/api/slot/${slot.slot_id}/sdp`;
    });
    dlWrap.appendChild(dlBtn);

    card.appendChild(dlWrap);

    grid.appendChild(card);
  });

  // After rebuilding, refresh SDP enable/disable and lock state
  updateSdpButtons();
}

function sizeBrandLogo(){
  const logo = document.getElementById("brandLogo");
  const start = document.getElementById("startBtn");
  if (!logo || !start) return;
  // Scale logo to be ~3x the Start/Stop button height (50% larger than previous 2x)
  const h = start.getBoundingClientRect().height;
  if (h && isFinite(h)) logo.style.height = `${Math.round(h * 3)}px`;
}

async function refreshAll(){
  sources = await apiGet("/api/sources");
  config = await apiGet("/api/config");
  const st = await apiGet("/api/status");
  activeSlots = (await apiGet("/api/active_slots") || []).map(x => Number(x));
  setStatusUI(!!st.running, st.message);
  renderSources();
  renderSlots();
  setStatusUI(!!st.running, st.message);
  updateSdpButtons();
}

const _refreshBtn = document.getElementById("refreshBtn");
if (_refreshBtn) _refreshBtn.addEventListener("click", refreshAll);

const _ndiRefreshBtn = document.getElementById("ndiRefreshBtn");
if (_ndiRefreshBtn) _ndiRefreshBtn.addEventListener("click", refreshNdiSources);


document.getElementById("startBtn").addEventListener("click", async () => {
  const st = await apiPost("/api/start");
  activeSlots = (await apiGet("/api/active_slots") || []).map(x => Number(x));
  setStatusUI(!!st.running, st.message);
  updateSdpButtons();
});

document.getElementById("stopBtn").addEventListener("click", async () => {
  const st = await apiPost("/api/stop");
  activeSlots = (await apiGet("/api/active_slots") || []).map(x => Number(x));
  setStatusUI(!!st.running, st.message);
  updateSdpButtons();
});

refreshAll();

// Size the logo once layout is stable
window.addEventListener("load", () => {
  sizeBrandLogo();
  // Re-run after first refresh in case fonts/styles shift sizes
  setTimeout(sizeBrandLogo, 200);
});

// Poll status while page is open to keep SDP buttons accurate
setInterval(async () => {
  try{
    const st = await apiGet("/api/status");
    activeSlots = (await apiGet("/api/active_slots") || []).map(x => Number(x));
    setStatusUI(!!st.running, st.message);
    // update only what needs to change (avoid rebuilding the whole UI)
    updateSdpButtons();
  }catch(e){}
}, 3000);

