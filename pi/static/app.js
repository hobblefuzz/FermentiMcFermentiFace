"use strict";

// Field order matches the profile table columns in index.html
const TEMP_FIELDS = [
  ["heat_lower", "Heat on"], ["heat_upper", "Heat off"], ["min_room", "Min room"],
  ["temp_upper", "Cool on"], ["temp_lower", "Cool off"],
  ["rh_lower", "RH low"], ["rh_upper", "RH high"],
];
const ENABLES = [
  ["heat_enabled", "Heizung"], ["temp_enabled", "Kühlung"],
  ["rh_enabled", "RH-Regelung"], ["idle_enabled", "Idle-Lüftung"],
];
const PWM_FIELDS = [
  ["temp_pwm_f1", "Cool PWM1"], ["temp_pwm_f2", "Cool PWM2"],
  ["rh_pwm_f1", "RH PWM1"], ["rh_pwm_f2", "RH PWM2"],
  ["idle_pwm_f1", "Idle PWM1"], ["idle_pwm_f2", "Idle PWM2"],
];

const $ = id => document.getElementById(id);
const live = $("live"), profState = $("profState"), profBody = $("profBody"),
      ranges = $("ranges"), canvas = $("chart");

const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const fmt = v => (v === null || v === undefined) ? "--" : v;

function fmtDur(s) {
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${("0" + m).slice(-2)}m` : `${m}m ${("0" + (s % 60)).slice(-2)}s`;
}
function flash(btn) {
  const t = btn.textContent; btn.textContent = "✓ gespeichert";
  setTimeout(() => btn.textContent = t, 1200);
}

// ─────────────────────────────────────── live status
async function pollStatus() {
  try {
    const d = await (await fetch("/api/status")).json();
    const t = d.telemetry || {}, p = d.profile || {};
    let warn = "";
    if (t.chamber_alarm) warn += ' <span class="warn">⚠ OVERTEMP</span>';
    if (t.core_fault) warn += ' <span class="warn">⚠ CORE SENSOR</span>';
    live.className = "live";
    live.innerHTML =
      `Kern <b>${fmt(t.core)}°C</b> · Kammer ${fmt(t.chamber)}°C · RH ${fmt(t.rh)}% · ` +
      `Heizung <b>${t.heater ? "AN" : "aus"}</b> · Lüfter ${fmt(t.fan)} · ` +
      `Modus ${t.ext ? "extern" : "lokal"}${warn}`;
    profState.textContent = p.running ? ("läuft · " + fmtDur(p.elapsed)) : "gestoppt";
    profState.className = "pill" + (p.running ? " run" : "");
  } catch (e) {
    live.className = "live off"; live.textContent = "Server offline";
  }
}

// ─────────────────────────────────────── chart
let rangeMin = 60;
function fitCanvas() { canvas.width = canvas.clientWidth || 900; canvas.height = 320; }

async function pollLog() {
  try {
    const since = Date.now() / 1000 - rangeMin * 60;
    const rows = await (await fetch("/api/log?since=" + since)).json();
    drawChart(rows);
  } catch (e) { /* keep last frame */ }
}

function drawChart(rows) {
  const ctx = canvas.getContext("2d"), W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  const pad = { l: 44, r: 44, t: 12, b: 26 };
  const x0 = pad.l, x1 = W - pad.r, y0 = H - pad.b, y1 = pad.t;
  ctx.strokeStyle = css("--line"); ctx.lineWidth = 1; ctx.strokeRect(x0, y1, x1 - x0, y0 - y1);
  ctx.font = "11px system-ui"; ctx.textBaseline = "middle";
  if (!rows.length) {
    ctx.fillStyle = css("--dim"); ctx.textAlign = "left";
    ctx.fillText("keine Daten im Zeitraum", x0 + 12, y1 + 20); return;
  }
  const t0 = rows[0].ts, t1 = rows[rows.length - 1].ts, tSpan = Math.max(1, t1 - t0);
  let temps = [];
  rows.forEach(r => { if (r.core != null) temps.push(r.core); if (r.chamber != null) temps.push(r.chamber); });
  let tmin, tmax;
  if (temps.length) { tmin = Math.min(...temps); tmax = Math.max(...temps); } else { tmin = 0; tmax = 40; }
  tmin = Math.floor(tmin - 1); tmax = Math.ceil(tmax + 1); if (tmax - tmin < 4) tmax = tmin + 4;

  const sx = ts => x0 + (ts - t0) / tSpan * (x1 - x0);
  const syT = v => y0 - (v - tmin) / (tmax - tmin) * (y0 - y1);
  const syR = v => y0 - v / 100 * (y0 - y1);

  // gridlines + temp (left) and RH (right) scales
  const steps = 4;
  for (let i = 0; i <= steps; i++) {
    const v = tmin + (tmax - tmin) * i / steps, y = syT(v);
    ctx.strokeStyle = "#1c232b"; ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    ctx.textAlign = "right"; ctx.fillStyle = css("--core"); ctx.fillText(v.toFixed(0), x0 - 6, y);
    ctx.textAlign = "left"; ctx.fillStyle = css("--rh"); ctx.fillText((100 * i / steps).toFixed(0), x1 + 6, syR(100 * i / steps));
  }
  // heater on = orange strip along the bottom
  ctx.fillStyle = css("--heat"); ctx.globalAlpha = 0.75;
  for (let i = 1; i < rows.length; i++) {
    if (rows[i - 1].heater) {
      const xa = sx(rows[i - 1].ts), xb = sx(rows[i].ts);
      ctx.fillRect(xa, y0 - 6, Math.max(1, xb - xa), 5);
    }
  }
  ctx.globalAlpha = 1;
  // series
  const line = (key, sy, color, dash) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.setLineDash(dash || []);
    ctx.beginPath(); let started = false;
    rows.forEach(r => {
      const v = r[key];
      if (v == null) { started = false; return; }
      const X = sx(r.ts), Y = sy(v);
      if (!started) { ctx.moveTo(X, Y); started = true; } else ctx.lineTo(X, Y);
    });
    ctx.stroke(); ctx.setLineDash([]);
  };
  line("core", syT, css("--core"));
  line("chamber", syT, css("--cham"));
  line("rh", syR, css("--rh"), [4, 3]);
  // time labels
  ctx.fillStyle = css("--dim"); ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (let i = 0; i <= steps; i++) {
    const ts = t0 + tSpan * i / steps, d = new Date(ts * 1000);
    ctx.fillText(("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2), sx(ts), y0 + 6);
  }
}

// ─────────────────────────────────────── profile editor
function pointRow(pt) {
  pt = pt || { t: 0, setpoints: {} };
  const sp = pt.setpoints || {};
  const tr = document.createElement("tr");
  tr.innerHTML =
    `<td><input type="number" step="0.5" class="pt-t" value="${(pt.t || 0) / 3600}"></td>` +
    TEMP_FIELDS.map(([k]) => `<td><input type="number" step="0.1" data-k="${k}" value="${sp[k] ?? ""}"></td>`).join("") +
    `<td><button class="rm">✕</button></td>`;
  tr.querySelector(".rm").onclick = () => tr.remove();
  return tr;
}
async function loadProfile() {
  const d = await (await fetch("/api/profile")).json();
  profBody.innerHTML = "";
  (d.points || []).forEach(p => profBody.appendChild(pointRow(p)));
  if (!profBody.children.length) profBody.appendChild(pointRow());
}
function collectProfile() {
  const points = [];
  [...profBody.children].forEach(tr => {
    const t = (parseFloat(tr.querySelector(".pt-t").value) || 0) * 3600;
    const sp = {};
    tr.querySelectorAll("input[data-k]").forEach(inp => {
      if (inp.value !== "") { const v = parseFloat(inp.value); if (!isNaN(v)) sp[inp.dataset.k] = v; }
    });
    points.push({ t, setpoints: sp });
  });
  return points;
}
async function saveProfile() {
  await fetch("/api/profile", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ points: collectProfile() }),
  });
  flash($("saveProf"));
}

// ─────────────────────────────────────── manual setpoints
function manualForm(m) {
  const el = $("manualForm"); el.innerHTML = "";
  ENABLES.forEach(([k, lbl]) => {
    const l = document.createElement("label"); l.className = "chk";
    l.innerHTML = `<input type="checkbox" data-k="${k}" ${m[k] ? "checked" : ""}> ${lbl}`;
    el.appendChild(l);
  });
  TEMP_FIELDS.concat(PWM_FIELDS).forEach(([k, lbl]) => {
    const l = document.createElement("label");
    l.innerHTML = `${lbl}<input type="number" step="0.1" data-k="${k}" value="${m[k] ?? ""}">`;
    el.appendChild(l);
  });
}
async function loadManual() { manualForm(await (await fetch("/api/manual")).json()); }
async function saveManual() {
  const out = {};
  document.querySelectorAll("#manualForm [data-k]").forEach(inp => {
    if (inp.type === "checkbox") out[inp.dataset.k] = inp.checked ? 1 : 0;
    else { const v = parseFloat(inp.value); if (!isNaN(v)) out[inp.dataset.k] = v; }
  });
  await fetch("/api/manual", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(out),
  });
  flash($("saveManual"));
}

// ─────────────────────────────────────── wiring + loops
$("addPoint").onclick = () => profBody.appendChild(pointRow());
$("saveProf").onclick = saveProfile;
$("startProf").onclick = async () => { await saveProfile(); await fetch("/api/profile/start", { method: "POST" }); pollStatus(); };
$("stopProf").onclick = async () => { await fetch("/api/profile/stop", { method: "POST" }); pollStatus(); };
$("saveManual").onclick = saveManual;

function powerScreen(title, note) {
  document.body.innerHTML =
    `<div style="padding:3rem;font-family:system-ui;color:#e6e9ee;` +
    `background:#0f1216;min-height:100vh"><h1>${title}</h1><p>${note}</p></div>`;
}
$("btnShutdown").onclick = async () => {
  if (!confirm("Pi wirklich herunterfahren?")) return;
  await fetch("/api/power/shutdown", { method: "POST" });
  powerScreen("⏻ Pi fährt herunter …",
    "Warte, bis die grüne LED am Pi aus ist — dann kannst du den Strom trennen.");
};
$("btnReboot").onclick = async () => {
  if (!confirm("Pi neu starten?")) return;
  await fetch("/api/power/reboot", { method: "POST" });
  powerScreen("↻ Pi startet neu …", "Die Seite ist in ~30 s wieder erreichbar. Lade sie dann neu.");
};

ranges.querySelectorAll("button").forEach(b => b.onclick = () => {
  ranges.querySelectorAll("button").forEach(x => x.classList.remove("active"));
  b.classList.add("active"); rangeMin = +b.dataset.min; pollLog();
});
window.addEventListener("resize", () => { fitCanvas(); pollLog(); });

fitCanvas();
loadProfile(); loadManual();
pollStatus(); pollLog();
setInterval(pollStatus, 2000);
setInterval(pollLog, 8000);
