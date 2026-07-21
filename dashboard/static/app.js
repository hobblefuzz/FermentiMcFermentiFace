"use strict";

// Field order matches the profile table columns in index.html.
// Heating is a single target per step; the deltas are global (in Settings).
const TEMP_FIELDS = [
  ["target", "Target"],
  ["temp_upper", "Cool on"], ["temp_lower", "Cool off"],
  ["rh_upper", "RH high"], ["rh_lower", "RH low"],
];

// "Settings" grouped by function; third element = boolean toggle
const MANUAL_GROUPS = [
  ["Heating", [["heat_enabled", "On", true], ["target", "Target T"],
    ["dt_lower", "Δ core"], ["dt_floor", "Δ floor"], ["dt_ceil", "Δ ceiling"]]],
  ["Cooling", [["temp_enabled", "On", true], ["temp_upper", "Cool on"],
    ["temp_lower", "Cool off"], ["temp_pwm_f1", "PWM 1"], ["temp_pwm_f2", "PWM 2"]]],
  ["Humidity", [["rh_enabled", "On", true], ["rh_upper", "RH high"],
    ["rh_lower", "RH low"], ["rh_pwm_f1", "PWM 1"], ["rh_pwm_f2", "PWM 2"]]],
  ["Idle ventilation", [["idle_enabled", "On", true], ["idle_max_idle", "Idle time (min)"],
    ["idle_pwm_f1", "PWM 1"], ["idle_pwm_f2", "PWM 2"]]],
];

const $ = id => document.getElementById(id);
const live = $("live"), profState = $("profState"), profBody = $("profBody"),
      ranges = $("ranges"), canvas = $("chart"), tip = $("tip");
const mCanvas = $("mChart"), mLive = $("mLive"), mTip = $("mTip");

const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const fmt = v => (v === null || v === undefined) ? "--" : v;
// numeric readings: always 2 decimals, in the plot tooltips AND the status tiles
const fmt2 = v => (v === null || v === undefined || isNaN(v)) ? "--" : Number(v).toFixed(2);

// Parse the fan status string ("R--", "-T-", "RTI", "EMG", "OFF", …) into flags.
function fanFlags(s) {
  s = s || "";
  return { rh: s.indexOf("R") >= 0, t: s.indexOf("T") >= 0 || s === "EMG", i: s.indexOf("I") >= 0 };
}

function fmtDur(s) {
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${("0" + m).slice(-2)}m` : `${m}m ${("0" + (s % 60)).slice(-2)}s`;
}
function flash(btn) {
  const t = btn.textContent; btn.textContent = "✓ saved";
  setTimeout(() => btn.textContent = t, 1200);
}

// ─────────────────────────────────────── live status
function tile(lbl, val, cls) {
  return `<div class="stat ${cls || ""}"><div class="lbl">${lbl}</div><div class="val">${val}</div></div>`;
}
async function pollStatus() {
  try {
    const d = await (await fetch("/api/status")).json();
    const t = d.telemetry || {}, p = d.profile || {};
    let h = "";
    h += tile("Core", fmt2(t.core) + " °C");
    h += tile("Chamber", fmt2(t.chamber) + " °C");
    h += tile("Humidity", fmt2(t.rh) + " %");
    h += tile("Heater", t.heater ? "ON" : "off", t.heater ? "hot" : "");
    h += tile("Fan", fmt(t.fan));
    h += tile("Mode", t.ext ? "external" : "local");
    h += tile("Profile", p.running ? ((p.step || "running") + " · " + fmtDur(p.elapsed)) : "stopped", p.running ? "run" : "");
    if (t.chamber_alarm) h += tile("Alarm", "OVERTEMP", "alarm");
    if (t.core_fault) h += tile("Alarm", "Core sensor", "alarm");
    live.className = "stats";
    live.innerHTML = h;
    profState.textContent = p.running ? ((p.step || "running") + " · " + fmtDur(p.elapsed)) : "stopped";
    profState.className = "pill" + (p.running ? " run" : "");
    const lg = $("logToggle");
    if (lg) { lg.textContent = "Logging: " + (d.logging ? "ON" : "OFF"); lg.className = d.logging ? "go" : ""; }
  } catch (e) {
    live.className = "stats off";
    live.innerHTML = '<div class="stat"><div class="val">Server offline</div></div>';
  }
}

// ─────────────────────────────────────── chart
let rangeMin = 60;
let scaleMode = "auto", scaleTmin = 20, scaleTmax = 40;   // temperature y-axis
let lastRows = [], lastEvents = [], plot = null;  // last data + scale info for hover
const PLAUSIBLE = v => v != null && v > -20 && v < 80;   // reject sensor glitches
// Which curves are drawn (toggled from the legend, persisted per browser).
// Activity bands (heater / fan reasons) are intentionally always shown.
let show  = { core: true, chamber: true, rh: true };
let mShow = { temp: true, batt: true };
let CW = 900, CH = 360;          // logical (CSS-pixel) chart size
let activeView = "fermenter";
let mRangeMin = 1440, mCW = 900, mCH = 300;        // tracker chart
let mRows = [], mPlot = null;                        // last tracker data + scale for hover
function fitCanvas() {
  const dpr = window.devicePixelRatio || 1;
  CW = canvas.clientWidth || 900;
  canvas.width = Math.round(CW * dpr);   // backing store scaled for crisp lines
  canvas.height = Math.round(CH * dpr);
  canvas.style.height = CH + "px";
  canvas.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);  // draw in CSS px
}

async function pollLog() {
  if (activeView !== "fermenter") return;
  try {
    const since = Date.now() / 1000 - rangeMin * 60;
    const [rows, events] = await Promise.all([
      fetch("/api/log?since=" + since).then(r => r.json()),
      fetch("/api/events?since=" + since).then(r => r.json()),
    ]);
    lastRows = rows;
    lastEvents = events;
    drawChart(rows);
  } catch (e) { /* keep last frame */ }
}

function drawChart(rows) {
  const ctx = canvas.getContext("2d"), W = CW, H = CH;
  ctx.clearRect(0, 0, W, H);
  const pad = { l: 44, r: 44, t: 12, b: 60 };
  const x0 = pad.l, x1 = W - pad.r, y0 = H - pad.b, y1 = pad.t;
  ctx.strokeStyle = css("--line"); ctx.lineWidth = 1; ctx.strokeRect(x0, y1, x1 - x0, y0 - y1);
  ctx.font = "11px system-ui"; ctx.textBaseline = "middle";
  if (!rows.length) {
    ctx.fillStyle = css("--dim"); ctx.textAlign = "left";
    ctx.fillText("no data in this range", x0 + 12, y1 + 20); plot = null; return;
  }
  const t0 = rows[0].ts, t1 = rows[rows.length - 1].ts, tSpan = Math.max(1, t1 - t0);
  let tmin, tmax;
  if (scaleMode === "manual") {
    tmin = scaleTmin; tmax = scaleTmax; if (tmax <= tmin) tmax = tmin + 1;
  } else {
    const temps = [];
    rows.forEach(r => {
      if (show.core && PLAUSIBLE(r.core)) temps.push(r.core);
      if (show.chamber && PLAUSIBLE(r.chamber)) temps.push(r.chamber);
    });
    if (temps.length) { tmin = Math.min(...temps); tmax = Math.max(...temps); } else { tmin = 0; tmax = 40; }
    tmin = Math.floor(tmin - 1); tmax = Math.ceil(tmax + 1); if (tmax - tmin < 4) tmax = tmin + 4;
  }

  const sx = ts => x0 + (ts - t0) / tSpan * (x1 - x0);
  const syT = v => y0 - (v - tmin) / (tmax - tmin) * (y0 - y1);
  const syR = v => y0 - v / 100 * (y0 - y1);

  // gridlines + temp (left) and RH (right) scales
  const steps = 4;
  for (let i = 0; i <= steps; i++) {
    const v = tmin + (tmax - tmin) * i / steps, y = syT(v);
    ctx.strokeStyle = "#1c232b"; ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    // One decimal: with a narrow scale the steps are well under 1 °C, so whole
    // numbers would print duplicates (…10, 9, 9, 8…) down the axis.
    if (show.core || show.chamber) {
      ctx.textAlign = "right"; ctx.fillStyle = css("--core"); ctx.fillText(v.toFixed(1), x0 - 6, y);
    }
    if (show.rh) {
      ctx.textAlign = "left"; ctx.fillStyle = css("--rh"); ctx.fillText((100 * i / steps).toFixed(0), x1 + 6, syR(100 * i / steps));
    }
  }
  // Activity bands below the plot: heater + the three fan reasons.
  const bandDefs = [
    ["H", css("--heat"),   r => !!r.heater],
    ["T", css("--fan-t"),  r => fanFlags(r.fan).t],
    ["R", css("--fan-rh"), r => fanFlags(r.fan).rh],
    ["I", css("--fan-i"),  r => fanFlags(r.fan).i],
  ];
  const bandH = 6, bandGap = 3, bandTop = y0 + 8;
  ctx.font = "bold 11px system-ui"; ctx.textBaseline = "middle";
  bandDefs.forEach((b, bi) => {
    const by = bandTop + bi * (bandH + bandGap);
    ctx.fillStyle = b[1];
    ctx.globalAlpha = 1; ctx.textAlign = "right"; ctx.fillText(b[0], x0 - 6, by + bandH / 2);
    ctx.globalAlpha = 0.85;
    for (let i = 1; i < rows.length; i++) {
      if (b[2](rows[i - 1])) {
        const xa = sx(rows[i - 1].ts), xb = sx(rows[i].ts);
        ctx.fillRect(xa, by, Math.max(1, xb - xa), bandH);
      }
    }
  });
  ctx.globalAlpha = 1;
  // series — smoothed (quadratic curve through segment midpoints), split on gaps
  const smooth = pts => {
    if (pts.length < 3) { pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); return; }
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length - 1; i++) {
      ctx.quadraticCurveTo(pts[i].x, pts[i].y, (pts[i].x + pts[i + 1].x) / 2, (pts[i].y + pts[i + 1].y) / 2);
    }
    const n = pts.length - 1; ctx.lineTo(pts[n].x, pts[n].y);
  };
  const series = (key, sy, color, dash) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.8; ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.setLineDash(dash || []);
    let seg = [];
    const flush = () => { if (seg.length) { ctx.beginPath(); smooth(seg); ctx.stroke(); seg = []; } };
    rows.forEach(r => { const v = r[key]; if (v == null) { flush(); return; } seg.push({ x: sx(r.ts), y: sy(v) }); });
    flush(); ctx.setLineDash([]);
  };
  ctx.save();                                    // clip so glitches don't spike out
  ctx.beginPath(); ctx.rect(x0, y1, x1 - x0, y0 - y1); ctx.clip();
  if (show.core)    series("core", syT, css("--core"));
  if (show.chamber) series("chamber", syT, css("--cham"));
  if (show.rh)      series("rh", syR, css("--rh"), [5, 4]);
  ctx.restore();

  // profile step markers (vertical line + name)
  ctx.font = "9px system-ui"; ctx.textBaseline = "top";
  lastEvents.forEach(ev => {
    if (ev.ts < t0 || ev.ts > t1) return;
    const ex = sx(ev.ts);
    ctx.strokeStyle = "rgba(176,123,255,.6)"; ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(ex, y1); ctx.lineTo(ex, y0); ctx.stroke(); ctx.setLineDash([]);
    const nearRight = ex > (x0 + x1) / 2;
    ctx.fillStyle = "#c9b3ff"; ctx.textAlign = nearRight ? "right" : "left";
    ctx.fillText(ev.name || "", ex + (nearRight ? -3 : 3), y1 + 2);
  });
  // time labels (below the activity bands)
  ctx.fillStyle = css("--dim"); ctx.textAlign = "center"; ctx.textBaseline = "top";
  const labelY = bandTop + bandDefs.length * (bandH + bandGap) + 2;
  for (let i = 0; i <= steps; i++) {
    const ts = t0 + tSpan * i / steps, d = new Date(ts * 1000);
    ctx.fillText(("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2), sx(ts), labelY);
  }

  plot = { rows, t0, tSpan, x0, x1, y0, y1, sx, syT, syR };
}

// ─────────────────────────────────────── hover tooltip
function nearestRow(mx) {
  const r = plot.rows;
  const t = plot.t0 + (mx - plot.x0) / (plot.x1 - plot.x0) * plot.tSpan;
  let lo = 0, hi = r.length - 1;
  while (lo < hi) { const m = (lo + hi) >> 1; if (r[m].ts < t) lo = m + 1; else hi = m; }
  if (lo > 0 && Math.abs(r[lo - 1].ts - t) < Math.abs(r[lo].ts - t)) lo -= 1;
  return r[lo];
}

function drawHover(row) {
  const ctx = canvas.getContext("2d"), px = plot.sx(row.ts);
  ctx.save();
  ctx.strokeStyle = "rgba(255,255,255,.28)"; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(px, plot.y1); ctx.lineTo(px, plot.y0); ctx.stroke();
  ctx.setLineDash([]);
  const dot = (v, sy, color) => {
    if (v == null) return;
    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(px, sy(v), 3.4, 0, 6.29); ctx.fill();
    ctx.strokeStyle = "#0c0f13"; ctx.lineWidth = 1.2; ctx.stroke();
  };
  if (show.core)    dot(row.core, plot.syT, css("--core"));
  if (show.chamber) dot(row.chamber, plot.syT, css("--cham"));
  if (show.rh)      dot(row.rh, plot.syR, css("--rh"));
  ctx.restore();
}

function hideTip() {
  tip.hidden = true;
  if (lastRows.length) drawChart(lastRows);
}

function onHover(e) {
  if (!plot || !plot.rows.length) { hideTip(); return; }
  const rect = canvas.getBoundingClientRect();
  const mx = (e.clientX - rect.left) * (CW / rect.width);
  if (mx < plot.x0 || mx > plot.x1) { hideTip(); return; }
  const row = nearestRow(mx);
  drawChart(lastRows);      // redraw base, then overlay crosshair
  drawHover(row);
  const d = new Date(row.ts * 1000);
  const clock = [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => ("0" + n).slice(-2)).join(":");
  let th = `<div class="t">${clock}</div>`;
  if (show.core)    th += `<div class="trow"><span>Core</span><b class="core">${fmt2(row.core)} °C</b></div>`;
  if (show.chamber) th += `<div class="trow"><span>Chamber</span><b class="cham">${fmt2(row.chamber)} °C</b></div>`;
  if (show.rh)      th += `<div class="trow"><span>RH</span><b class="rh">${fmt2(row.rh)} %</b></div>`;
  tip.innerHTML = th;
  tip.hidden = false;
  const cssX = plot.sx(row.ts) * (rect.width / CW);
  tip.style.left = Math.max(60, Math.min(rect.width - 60, cssX)) + "px";
}

canvas.addEventListener("mousemove", onHover);
canvas.addEventListener("mouseleave", hideTip);

// ─────────────────────────────────────── Moromi tracker view
function fitMoromi() {
  const dpr = window.devicePixelRatio || 1;
  mCW = mCanvas.clientWidth || 900;
  mCanvas.width = Math.round(mCW * dpr);
  mCanvas.height = Math.round(mCH * dpr);
  mCanvas.style.height = mCH + "px";
  mCanvas.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
}

function mLabel(d) {
  if (mRangeMin >= 2880)   // >= 2 days → date
    return ("0" + d.getDate()).slice(-2) + "." + ("0" + (d.getMonth() + 1)).slice(-2);
  return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
}

function drawMoromi(rows) {
  const ctx = mCanvas.getContext("2d"), W = mCW, H = mCH;
  ctx.clearRect(0, 0, W, H);
  const pad = { l: 44, r: 44, t: 12, b: 26 };
  const x0 = pad.l, x1 = W - pad.r, y0 = H - pad.b, y1 = pad.t;
  ctx.strokeStyle = css("--line"); ctx.lineWidth = 1; ctx.strokeRect(x0, y1, x1 - x0, y0 - y1);
  ctx.font = "11px system-ui"; ctx.textBaseline = "middle";
  if (!rows.length) {
    ctx.fillStyle = css("--dim"); ctx.textAlign = "left";
    ctx.fillText("no data in this range", x0 + 12, y1 + 20); mPlot = null; return;
  }
  const t0 = rows[0].ts, t1 = rows[rows.length - 1].ts, tSpan = Math.max(1, t1 - t0);
  const temps = mShow.temp ? rows.filter(r => PLAUSIBLE(r.temp)).map(r => r.temp) : [];
  let tmin, tmax;
  if (temps.length) { tmin = Math.min(...temps); tmax = Math.max(...temps); } else { tmin = 0; tmax = 30; }
  tmin = Math.floor(tmin - 1); tmax = Math.ceil(tmax + 1); if (tmax - tmin < 3) tmax = tmin + 3;
  const hasBatt = mShow.batt && rows.some(r => r.batt != null);
  const bmin = 3.0, bmax = 4.3;
  const sx = ts => x0 + (ts - t0) / tSpan * (x1 - x0);
  const syT = v => y0 - (v - tmin) / (tmax - tmin) * (y0 - y1);
  const syB = v => y0 - (v - bmin) / (bmax - bmin) * (y0 - y1);

  const steps = 4;
  for (let i = 0; i <= steps; i++) {
    const v = tmin + (tmax - tmin) * i / steps, y = syT(v);
    ctx.strokeStyle = "#1c232b"; ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    if (mShow.temp) {
      ctx.textAlign = "right"; ctx.fillStyle = css("--core"); ctx.fillText(v.toFixed(1), x0 - 6, y);
    }
    if (hasBatt) {
      const bv = bmin + (bmax - bmin) * i / steps;
      ctx.textAlign = "left"; ctx.fillStyle = css("--batt"); ctx.fillText(bv.toFixed(1), x1 + 6, syB(bv));
    }
  }
  const smooth = pts => {
    if (pts.length < 3) { pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); return; }
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length - 1; i++)
      ctx.quadraticCurveTo(pts[i].x, pts[i].y, (pts[i].x + pts[i + 1].x) / 2, (pts[i].y + pts[i + 1].y) / 2);
    const n = pts.length - 1; ctx.lineTo(pts[n].x, pts[n].y);
  };
  const series = (key, sy, color, dash) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.8; ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.setLineDash(dash || []);
    let seg = [];
    const flush = () => { if (seg.length) { ctx.beginPath(); smooth(seg); ctx.stroke(); seg = []; } };
    rows.forEach(r => { const v = r[key]; if (v == null) { flush(); return; } seg.push({ x: sx(r.ts), y: sy(v) }); });
    flush(); ctx.setLineDash([]);
  };
  ctx.save();                                    // clip so glitches don't spike out
  ctx.beginPath(); ctx.rect(x0, y1, x1 - x0, y0 - y1); ctx.clip();
  if (mShow.temp) series("temp", syT, css("--core"));
  if (hasBatt)    series("batt", syB, css("--batt"), [5, 4]);

  // dots per sample (so even a single/sparse reading is visible)
  if (rows.length <= 300) {
    const dots = (key, sy, color) => {
      ctx.fillStyle = color;
      rows.forEach(r => {
        const v = r[key]; if (v == null) return;
        ctx.beginPath(); ctx.arc(sx(r.ts), sy(v), 2.4, 0, 6.29); ctx.fill();
      });
    };
    if (mShow.temp) dots("temp", syT, css("--core"));
    if (hasBatt)    dots("batt", syB, css("--batt"));
  }
  ctx.restore();

  ctx.fillStyle = css("--dim"); ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (let i = 0; i <= steps; i++) {
    const ts = t0 + tSpan * i / steps;
    ctx.fillText(mLabel(new Date(ts * 1000)), sx(ts), y0 + 6);
  }
  mPlot = { rows, t0, tSpan, x0, x1, y0, y1, sx, syT, syB, hasBatt };
}

// ─────────────────────────────────────── Moromi hover tooltip
function mNearestRow(mx) {
  const r = mPlot.rows;
  const t = mPlot.t0 + (mx - mPlot.x0) / (mPlot.x1 - mPlot.x0) * mPlot.tSpan;
  let lo = 0, hi = r.length - 1;
  while (lo < hi) { const m = (lo + hi) >> 1; if (r[m].ts < t) lo = m + 1; else hi = m; }
  if (lo > 0 && Math.abs(r[lo - 1].ts - t) < Math.abs(r[lo].ts - t)) lo -= 1;
  return r[lo];
}
function drawMoromiHover(row) {
  const ctx = mCanvas.getContext("2d"), px = mPlot.sx(row.ts);
  ctx.save();
  ctx.strokeStyle = "rgba(255,255,255,.28)"; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(px, mPlot.y1); ctx.lineTo(px, mPlot.y0); ctx.stroke();
  ctx.setLineDash([]);
  const dot = (v, sy, color) => {
    if (v == null) return;
    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(px, sy(v), 3.4, 0, 6.29); ctx.fill();
    ctx.strokeStyle = "#0c0f13"; ctx.lineWidth = 1.2; ctx.stroke();
  };
  if (mShow.temp)    dot(row.temp, mPlot.syT, css("--core"));
  if (mPlot.hasBatt) dot(row.batt, mPlot.syB, css("--batt"));
  ctx.restore();
}
function mHideTip() {
  mTip.hidden = true;
  if (mRows.length) drawMoromi(mRows);
}
function mOnHover(e) {
  if (!mPlot || !mPlot.rows.length) { mHideTip(); return; }
  const rect = mCanvas.getBoundingClientRect();
  const mx = (e.clientX - rect.left) * (mCW / rect.width);
  if (mx < mPlot.x0 || mx > mPlot.x1) { mHideTip(); return; }
  const row = mNearestRow(mx);
  drawMoromi(mRows);        // redraw base, then overlay crosshair
  drawMoromiHover(row);
  const d = new Date(row.ts * 1000);
  const clock = [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => ("0" + n).slice(-2)).join(":");
  let html = `<div class="t">${clock}</div>`;
  if (mShow.temp)
    html += `<div class="trow"><span>Temp</span><b class="core">${fmt2(row.temp)} °C</b></div>`;
  if (mShow.batt && row.batt != null)
    html += `<div class="trow"><span>Battery</span><b class="batt">${fmt2(row.batt)} V</b></div>`;
  mTip.innerHTML = html;
  mTip.hidden = false;
  const cssX = mPlot.sx(row.ts) * (rect.width / mCW);
  mTip.style.left = Math.max(60, Math.min(rect.width - 60, cssX)) + "px";
}
mCanvas.addEventListener("mousemove", mOnHover);
mCanvas.addEventListener("mouseleave", mHideTip);

async function pollMoromi() {
  if (activeView !== "tracker") return;
  try {
    const since = Date.now() / 1000 - mRangeMin * 60;
    const [rows, latest] = await Promise.all([
      fetch("/api/track/log?device=moromi&since=" + since).then(r => r.json()),
      fetch("/api/track/latest").then(r => r.json()),
    ]);
    mRows = rows;
    drawMoromi(rows);
    const m = latest.moromi;
    let h = "";
    if (m && m.ts) {
      // Show the reading even when the sensor failed: temperature comes through
      // as null (loose plug / out-of-range / all-zeros glitch), but battery and
      // time are still useful — so flag the temp and keep the rest visible.
      h += (m.temp != null)
        ? tile("Temperature", fmt2(m.temp) + " °C")
        : tile("Temperature", "no reading", "alarm");
      if (m.batt != null) h += tile("Battery", fmt2(m.batt) + " V");
      const d = new Date(m.ts * 1000);
      const hhmm = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
      const sameDay = d.toDateString() === new Date().toDateString();
      h += tile("Last reading", sameDay ? hhmm
        : ("0" + d.getDate()).slice(-2) + "." + ("0" + (d.getMonth() + 1)).slice(-2) + ". " + hhmm);
    } else {
      h += tile("Status", "no data");
    }
    mLive.className = "stats"; mLive.innerHTML = h;
  } catch (e) {
    mLive.className = "stats off";
    mLive.innerHTML = '<div class="stat"><div class="val">offline</div></div>';
  }
}

function showView(name) {
  activeView = name;
  document.querySelectorAll(".view").forEach(v => { v.hidden = (v.id !== "view-" + name); });
  document.querySelectorAll(".navbtn").forEach(b => b.classList.toggle("active", b.dataset.view === name));
  if (name === "fermenter") { fitCanvas(); pollLog(); pollStatus(); }
  else { fitMoromi(); pollMoromi(); }
}

// ─────────────────────────────────────── profile editor
function pointRow(pt) {
  pt = pt || { t: 0, name: "", setpoints: {} };
  const sp = pt.setpoints || {};
  const nameVal = (pt.name || "").replace(/"/g, "&quot;");
  const tr = document.createElement("tr");
  tr.innerHTML =
    `<td><input type="number" step="0.5" class="pt-t" value="${(pt.t || 0) / 3600}"></td>` +
    `<td><input type="text" class="pt-name" placeholder="z.B. Kirikaeshi" value="${nameVal}"></td>` +
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
    const name = tr.querySelector(".pt-name").value.trim();
    const sp = {};
    tr.querySelectorAll("input[data-k]").forEach(inp => {
      if (inp.value !== "") { const v = parseFloat(inp.value); if (!isNaN(v)) sp[inp.dataset.k] = v; }
    });
    points.push({ t, name, setpoints: sp });
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

// ─────────────────────────────────────── calendar export (.ics)
const pad2 = n => ("0" + n).slice(-2);
const icsTime = e => {
  const d = new Date(e * 1000);
  return d.getUTCFullYear() + pad2(d.getUTCMonth() + 1) + pad2(d.getUTCDate()) + "T" +
         pad2(d.getUTCHours()) + pad2(d.getUTCMinutes()) + pad2(d.getUTCSeconds()) + "Z";
};
const icsEsc = s => String(s).replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\n/g, "\\n");

function stepDesc(p) {
  const sp = p.setpoints || {}, parts = [];
  if (sp.target != null)
    parts.push(`target ${sp.target} C`);
  if (sp.temp_upper != null) parts.push(`fan above ${sp.temp_upper} C`);
  if (sp.rh_lower != null || sp.rh_upper != null)
    parts.push(`RH ${sp.rh_lower ?? "?"}-${sp.rh_upper ?? "?"} %`);
  return "Stir the substrate / redistribute moisture." + (parts.length ? " Setpoints: " + parts.join(", ") : "");
}

function buildICS(points, startEpoch) {
  let ics = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" +
            "PRODID:-//FermentiMcFermentiFace//Fermenter//EN\r\nCALSCALE:GREGORIAN\r\n";
  points.forEach((p, i) => {
    const title = p.name || ("Step " + (i + 1));
    ics += "BEGIN:VEVENT\r\n" +
      "UID:fmcff-" + startEpoch + "-" + i + "@fermentimcfermentiface\r\n" +
      "DTSTAMP:" + icsTime(startEpoch) + "\r\n" +
      "DTSTART:" + icsTime(startEpoch + (p.t || 0)) + "\r\n" +
      "DURATION:PT15M\r\n" +
      "SUMMARY:" + icsEsc("Koji: " + title) + "\r\n" +
      "DESCRIPTION:" + icsEsc(stepDesc(p)) + "\r\n" +
      "BEGIN:VALARM\r\nTRIGGER:-PT5M\r\nACTION:DISPLAY\r\n" +
      "DESCRIPTION:" + icsEsc("Koji: " + title) + "\r\nEND:VALARM\r\n" +
      "END:VEVENT\r\n";
  });
  return ics + "END:VCALENDAR\r\n";
}

function download(name, text, mime) {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a"); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

// ─────────────────────────────────────── manual setpoints
function manualForm(m) {
  const el = $("manualForm"); el.innerHTML = "";
  MANUAL_GROUPS.forEach(([title, fields]) => {
    const g = document.createElement("div"); g.className = "mgroup";
    let html = `<h3>${title}</h3>`;
    fields.forEach(([k, lbl, isBool]) => {
      html += isBool
        ? `<label class="chk"><input type="checkbox" data-k="${k}" ${m[k] ? "checked" : ""}> ${lbl}</label>`
        : `<label>${lbl}<input type="number" step="0.1" data-k="${k}" value="${m[k] ?? ""}"></label>`;
    });
    g.innerHTML = html;
    el.appendChild(g);
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
$("startProf").onclick = async () => {
  if (!confirm("Start the profile now? It begins running immediately and an .ics file is downloaded.")) return;
  await saveProfile();
  const d = await (await fetch("/api/profile/start", { method: "POST" })).json();
  pollStatus();
  const startEpoch = d.started_at || (Date.now() / 1000);
  if (d.points && d.points.length) download("koji-profil.ics", buildICS(d.points, startEpoch), "text/calendar");
};
$("stopProf").onclick = async () => {
  if (!confirm("Really stop the profile?")) return;
  await fetch("/api/profile/stop", { method: "POST" });
  pollStatus();
};
$("dlIcs").onclick = async () => {
  const st = await (await fetch("/api/status")).json();
  const startEpoch = (st.profile && st.profile.started_at) || (Date.now() / 1000);
  const points = collectProfile();
  if (points.length) download("koji-profil.ics", buildICS(points, startEpoch), "text/calendar");
  else alert("Keine Setpoints vorhanden.");
};
$("saveManual").onclick = saveManual;
if ($("resetManual")) $("resetManual").onclick = async () => {
  if (!confirm("Reset settings to factory defaults?")) return;
  manualForm(await (await fetch("/api/manual/reset", { method: "POST" })).json());
  flash($("resetManual"));
};

// profile library (save/load/delete named profiles)
async function loadProfiles() {
  const sel = $("profSelect"); if (!sel) return;
  const names = await (await fetch("/api/profiles")).json();
  const cur = sel.value;
  sel.innerHTML = '<option value="">— saved profile —</option>' +
    names.map(n => `<option>${n}</option>`).join("");
  if (names.includes(cur)) sel.value = cur;
}
if ($("loadProf")) $("loadProf").onclick = async () => {
  const name = $("profSelect").value; if (!name) return;
  const d = await (await fetch("/api/profiles/" + encodeURIComponent(name))).json();
  profBody.innerHTML = "";
  (d.points || []).forEach(p => profBody.appendChild(pointRow(p)));
  if (!profBody.children.length) profBody.appendChild(pointRow());
};
if ($("saveAsProf")) $("saveAsProf").onclick = async () => {
  const name = (prompt("Save profile as:") || "").trim(); if (!name) return;
  await fetch("/api/profiles/" + encodeURIComponent(name), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ points: collectProfile() }),
  });
  await loadProfiles(); $("profSelect").value = name;
};
if ($("delProf")) $("delProf").onclick = async () => {
  const name = $("profSelect").value;
  if (!name || !confirm("Delete profile: " + name + " ?")) return;
  await fetch("/api/profiles/" + encodeURIComponent(name), { method: "DELETE" });
  await loadProfiles();
};

$("logToggle").onclick = async () => { await fetch("/api/logging/toggle", { method: "POST" }); pollStatus(); };
$("csvExport").onclick = () => { location.href = "/api/export.csv"; };
$("plotReset").onclick = async () => {
  if (!confirm("Permanently delete all logged data (plot + CSV)?")) return;
  await fetch("/api/log/reset", { method: "POST" });
  pollLog();
};

function powerScreen(title, note) {
  document.body.innerHTML =
    `<div style="padding:3rem;font-family:system-ui;color:#e6e9ee;` +
    `background:#0f1216;min-height:100vh"><h1>${title}</h1><p>${note}</p></div>`;
}
$("btnShutdown").onclick = async () => {
  if (!confirm("Really shut the Pi down?")) return;
  await fetch("/api/power/shutdown", { method: "POST" });
  powerScreen("⏻ Pi is shutting down …",
    "Wait until the green LED on the Pi is off — then it is safe to cut the power.");
};
$("btnReboot").onclick = async () => {
  if (!confirm("Reboot the Pi?")) return;
  await fetch("/api/power/reboot", { method: "POST" });
  powerScreen("↻ Pi is rebooting …", "The page will be reachable again in ~30 s. Reload it then.");
};

ranges.querySelectorAll("button").forEach(b => b.onclick = () => {
  ranges.querySelectorAll("button").forEach(x => x.classList.remove("active"));
  b.classList.add("active"); rangeMin = +b.dataset.min; pollLog();
});
$("mRanges").querySelectorAll("button").forEach(b => b.onclick = () => {
  $("mRanges").querySelectorAll("button").forEach(x => x.classList.remove("active"));
  b.classList.add("active"); mRangeMin = +b.dataset.min; pollMoromi();
});
$("mCsv").onclick = () => { location.href = "/api/track/export.csv?device=moromi"; };
$("mPlotReset").onclick = async () => {
  if (!confirm("Permanently delete all tracker readings (plot + CSV)?")) return;
  await fetch("/api/track/reset?device=moromi", { method: "POST" });
  pollMoromi();
};
document.querySelectorAll(".navbtn").forEach(b => b.onclick = () => showView(b.dataset.view));

// temperature-scale control (auto / manual), persisted in the browser
const SCALE_KEY = "koji_tscale";
try {
  const s = JSON.parse(localStorage.getItem(SCALE_KEY) || "null");
  if (s) { scaleMode = s.mode || "auto"; scaleTmin = s.tmin ?? 20; scaleTmax = s.tmax ?? 40; }
} catch (e) { /* ignore */ }
function saveScale() {
  localStorage.setItem(SCALE_KEY, JSON.stringify({ mode: scaleMode, tmin: scaleTmin, tmax: scaleTmax }));
}
if ($("scaleManual")) {
  const sm = $("scaleManual"), si = $("scaleInputs"), s0 = $("scaleTmin"), s1 = $("scaleTmax");
  const syncScaleUI = () => {
    sm.checked = scaleMode === "manual"; si.hidden = scaleMode !== "manual";
    s0.value = scaleTmin; s1.value = scaleTmax;
  };
  sm.onchange = () => { scaleMode = sm.checked ? "manual" : "auto"; syncScaleUI(); saveScale(); drawChart(lastRows); };
  s0.onchange = () => { scaleTmin = parseFloat(s0.value); if (isNaN(scaleTmin)) scaleTmin = 0; saveScale(); drawChart(lastRows); };
  s1.onchange = () => { scaleTmax = parseFloat(s1.value); if (isNaN(scaleTmax)) scaleTmax = 40; saveScale(); drawChart(lastRows); };
  syncScaleUI();
}

// series visibility (legend checkboxes), persisted in the browser
const SHOW_KEY = "fmcff_series";
try {
  const v = JSON.parse(localStorage.getItem(SHOW_KEY) || "null");
  if (v) { Object.assign(show, v.show || {}); Object.assign(mShow, v.mShow || {}); }
} catch (e) { /* ignore */ }
function saveShow() { localStorage.setItem(SHOW_KEY, JSON.stringify({ show, mShow })); }

function wireSeries(attr, state, redraw) {
  document.querySelectorAll(`[${attr}]`).forEach(cb => {
    const key = cb.getAttribute(attr);
    cb.checked = state[key] !== false;
    cb.closest("label").classList.toggle("off", !cb.checked);
    cb.onchange = () => {
      state[key] = cb.checked;
      cb.closest("label").classList.toggle("off", !cb.checked);
      saveShow(); redraw();
    };
  });
}
wireSeries("data-series",  show,  () => drawChart(lastRows));
wireSeries("data-mseries", mShow, () => drawMoromi(mRows));

window.addEventListener("resize", () => {
  if (activeView === "fermenter") { fitCanvas(); pollLog(); }
  else { fitMoromi(); pollMoromi(); }
});

fitCanvas();
loadProfile(); loadManual(); loadProfiles();
pollStatus(); pollLog();
setInterval(pollStatus, 2000);
setInterval(pollLog, 8000);
setInterval(pollMoromi, 15000);
