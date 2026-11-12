const VITALS = [
  {k:"hr",  label:"Heart rate",  unit:"bpm",  lo:51,  hi:90,  min:30, max:160, dec:0},
  {k:"spo2",label:"SpO₂",        unit:"%",    lo:96,  hi:100, min:82, max:100, dec:0},
  {k:"rr",  label:"Resp. rate",  unit:"/min", lo:12,  hi:20,  min:4,  max:36,  dec:0},
  {k:"temp",label:"Temperature", unit:"°C",   lo:36.1,hi:38,  min:34, max:41,  dec:1},
  {k:"sbp", label:"Systolic BP", unit:"mmHg", lo:111, hi:180, min:70, max:200, dec:0}];
const LEVELS = ["Normal","Watch","Warning","Critical"];
const S = {patients:{}, series:{}, sel:null, alerts:[], stats:{}};
const $ = id => document.getElementById(id);
const clock = ts => new Date(ts*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"});
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const api = (p, o) => fetch("/api"+p, o).then(r => r.json());

async function load(){
  const [pts, al, st] = await Promise.all([api("/patients"), api("/alerts"), api("/stats")]);
  pts.forEach(p => S.patients[p.id] = p);
  S.alerts = al; S.stats = st;
  S.sel = S.sel || (pts[0] && pts[0].id);
  if (S.sel) S.series[S.sel] = await api(`/patients/${S.sel}/vitals?limit=120`);
  renderAll(); stream();
}
async function select(id){
  S.sel = id; S.series[id] = await api(`/patients/${id}/vitals?limit=120`); renderAll();
}
function stream(){
  const es = new EventSource("/api/stream");
  es.onopen = () => { $("conn").textContent = "Live"; $("conn").className = "conn on"; };
  es.onerror = () => { $("conn").textContent = "Disconnected, retrying…"; $("conn").className = "conn off"; };
  es.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.type === "vitals"){
      const p = S.patients[m.patient_id]; if (!p) return;
      p.latest = m; S.stats[m.patient_id] = m.edge;
      (S.series[m.patient_id] = S.series[m.patient_id] || []).push(m);
      if (S.series[m.patient_id].length > 120) S.series[m.patient_id].shift();
    } else if (m.type === "alert"){ S.alerts.unshift(m.alert); S.alerts.length = Math.min(S.alerts.length, 50); }
    else if (m.type === "ack"){ const a = S.alerts.find(x => x.id === m.id); if (a) a.acked = 1; }
    renderAll();
  };
}
function renderAll(){ renderPatients(); renderDetail(); renderEdge(); renderAlerts(); }

function renderPatients(){
  $("patients").innerHTML = Object.values(S.patients).map(p => {
    const l = p.latest;
    return `<button class="pt lv${l?l.level:0}" data-id="${p.id}" aria-current="${p.id===S.sel}">
      <b>${esc(p.name)}</b><span class="score">${l?l.news2:"–"}</span>
      <small>${esc(p.bed)} · ${p.age} y · ${l?LEVELS[l.level]:"No data"}</small></button>`;
  }).join("");
  document.querySelectorAll(".pt").forEach(b => b.onclick = () => select(b.dataset.id));
}

function renderDetail(){
  const p = S.patients[S.sel]; if (!p) return;
  const l = p.latest;
  if (!l){ $("detail").innerHTML = `<h2>${esc(p.name)}</h2><p class="empty">No readings yet. Start this patient's edge node.</p>`; return; }
  const rows = S.series[p.id] || [];
  $("detail").innerHTML = `
    <div class="top lv${l.level}">
      <div><h2>${esc(p.name)}</h2><p>${esc(p.bed)} · ${p.age} years · updated ${clock(l.ts)}</p></div>
      <div class="badge"><div class="n">${l.news2}</div><div class="t">${LEVELS[l.level]}</div>
        <div class="risk" title="Deterioration risk"><i style="width:${Math.round(l.risk*100)}%"></i></div>
        <small>Deterioration risk ${Math.round(l.risk*100)}%</small></div>
    </div>
    <p class="why">${l.reasons.length ? "Why: " + esc(l.reasons.join(", ")) : "All vitals within the expected range."}</p>
    <div class="tiles">${VITALS.map(v => {
      const x = l[v.k], out = x < v.lo || x > v.hi;
      return `<div class="tile"><div class="l"><span>${v.label}</span></div>
        <div class="v ${out?"out":""}">${x.toFixed(v.dec)} <small>${v.unit}</small></div>
        <canvas data-k="${v.k}" role="img" aria-label="${v.label} trend"></canvas></div>`;}).join("")}</div>
    <p class="legend"><i></i>Shaded band = normal range. Values come from the edge node's cleaned, averaged readings.</p>`;
  document.querySelectorAll("canvas").forEach(cv => chart(cv, VITALS.find(v => v.k === cv.dataset.k), rows));
  const t = $("detail").querySelector(".top"); // colour hook for badge/risk
  t.style.setProperty("--c", getComputedStyle(document.documentElement).getPropertyValue("--l"+l.level));
}

function chart(cv, v, rows){
  const dpr = devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
  cv.width = w*dpr; cv.height = h*dpr;
  const c = cv.getContext("2d"); c.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement), N = 120;
  const y = x => h - 4 - (Math.min(Math.max(x, v.min), v.max) - v.min) / (v.max - v.min) * (h - 8);
  c.fillStyle = css.getPropertyValue("--band"); c.fillRect(0, y(v.hi), w, y(v.lo) - y(v.hi));
  if (!rows.length) return;
  const X = i => w - (rows.length - 1 - i) * (w / (N - 1));
  c.strokeStyle = css.getPropertyValue("--ink"); c.lineWidth = 1.8; c.beginPath();
  rows.forEach((r, i) => i ? c.lineTo(X(i), y(r[v.k])) : c.moveTo(X(i), y(r[v.k]))); c.stroke();
  const last = rows[rows.length-1], out = last[v.k] < v.lo || last[v.k] > v.hi;
  c.fillStyle = css.getPropertyValue(out ? "--l2" : "--teal");
  c.beginPath(); c.arc(X(rows.length-1) - 3, y(last[v.k]), 3.5, 0, 7); c.fill();
}

function renderEdge(){
  const all = Object.values(S.stats), raw = all.reduce((a, s) => a + (s.raw_bytes||0), 0), sent = all.reduce((a, s) => a + (s.sent_bytes||0), 0);
  const buf = all.reduce((a, s) => a + (s.buffered||0), 0), saved = raw ? Math.max(0, Math.round((1 - sent/raw) * 100)) : 0;
  $("edge").innerHTML = `<h2>Edge processing</h2><div class="big">${saved}%</div>
    <p class="empty" style="margin:0 0 8px">less data sent than streaming raw readings</p>
    <div class="kv"><span>Raw sensor data</span><span>${(raw/1024).toFixed(1)} KB</span></div>
    <div class="kv"><span>Actually uplinked</span><span>${(sent/1024).toFixed(1)} KB</span></div>
    <div class="kv"><span>Waiting in offline buffer</span><span>${buf}</span></div>`;
}

function renderAlerts(){
  $("alerts").innerHTML = S.alerts.length ? S.alerts.map(a => {
    const p = S.patients[a.patient_id];
    return `<li class="lv${a.level} ${a.acked?"acked":""}"><b>${esc(p?p.name:a.patient_id)}</b> · ${clock(a.ts)}<br>${esc(a.message)}
      ${a.acked ? "<br><small>Acknowledged</small>" : `<br><button data-a="${a.id}">Acknowledge</button>`}</li>`;
  }).join("") : `<li class="empty">No alerts. Alerts appear when a patient's risk level rises.</li>`;
  document.querySelectorAll("[data-a]").forEach(b => b.onclick = () => api(`/alerts/${b.dataset.a}/ack`, {method:"POST"}));
}

window.addEventListener("resize", renderDetail);
load();
