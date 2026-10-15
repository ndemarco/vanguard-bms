"use strict";
// Draws the snapshot the server pushes over /ws. All text is set with textContent.
// The page never sends START, CLEAR or SLEEP on its own: only the click handlers at the bottom post.

const $ = (id) => document.getElementById(id);
function h(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

let snap = null;
const local = {};      // last local result per command word: {text, err, t}
let confirmTimers = {};
const nowS = () => performance.now() / 1000;
const REPLY_SHOW_S = 10;

// "Show all signals" toggle. Off by default: signals never received are hidden.
// A signal that has been seen is never hidden, even when stale.
const SHOW_ALL_KEY = "vanguard.showAllSignals";
let showAll = false;
try { showAll = localStorage.getItem(SHOW_ALL_KEY) === "1"; } catch (e) { showAll = false; }
const visible = (s) => showAll || s.seen;

function fmtNum(v) {
  if (Number.isInteger(v)) return String(v);
  return String(Number(v.toFixed(3)));
}
// Currents (unit A) always with 2 decimals, e.g. "17.96 A".
function fmtUnit(v, unit) {
  if (unit === "A") return v.toFixed(2) + " A";
  return fmtNum(v) + (unit ? " " + unit : "");
}
// Power from the server ({kw, direction}): "1.23 kW in" (charging) / "0.85 kW out"; "--" when blank.
function fmtPower(p) {
  if (!p || p.kw === null || p.kw === undefined) return "--";
  return p.kw.toFixed(2) + " kW" + (p.direction ? " " + p.direction : "");
}
function fmtVal(s) {
  if (!s.seen) return "not received";
  if (s.blank) return "--";
  if (!s.valid) return "invalid";
  const v = s.value;
  if (typeof v === "boolean") return v ? "ON" : "OFF";
  if (typeof v === "number") return fmtUnit(v, s.unit);
  return String(v);
}
function confTag(s) { return s.conf ? "[" + s.conf + "]" : ""; }
function metaText(s) {
  if (!s.seen) return "no frame yet";
  if (s.blank) return (s.stale ? "STALE, " : "") + "last value " + (s.age !== null ? s.age.toFixed(1) + " s ago" : "");
  let t = "raw " + s.raw + " " + confTag(s);
  if (s.age !== null) t += "  " + s.age.toFixed(1) + " s";
  return t;
}
function row(s, big) {
  const cls = ["row"];
  if (big) cls.push("big");
  if (!s.seen || s.blank) cls.push("stale");
  if (s.seen && !s.blank && !s.valid) cls.push("invalid");
  const r = h("div", cls.join(" "));
  const lab = h("div", "lab", s.label);
  if (s.label_note) lab.appendChild(h("span", "warnbadge", s.label_note));
  r.appendChild(lab);
  r.appendChild(h("div", "val", fmtVal(s)));
  r.appendChild(h("div", "meta", metaText(s)));
  return r;
}
function fill(el, nodes) { el.replaceChildren(...nodes); }
function pill(text, kind) { return h("span", "pill " + (kind || ""), text); }
// Rows for the visible signals. Signals never received stay hidden unless "Show all signals" is on.
function rowsFor(sigs, bigFirst) {
  const out = [], vis = sigs.filter(visible);
  vis.forEach((s, i) => out.push(row(s, bigFirst && i === 0)));
  return out;
}

function renderCommsBar(d) {
  const bar = $("commsbar"), lost = !d.comms.ok;
  bar.hidden = !lost;
  bar.textContent = lost ? "NO DATA: comms lost (" + d.comms.reason + ")" : "";
  document.body.classList.toggle("commslost", lost);
}

function renderStatusLine(d) {
  const b = d.bridge, items = [];
  if (d.mode === "replay") {
    const r = d.replay || {};
    items.push(pill("REPLAY of a log file", "warn"));
    items.push(h("span", "", "frame " + (r.position || 0) + " of " + (r.file_frames || 0) + (r.finished ? " (finished)" : "") + ", speed " + (r.speed || 1) + "x"));
  } else if (b.mode) {
    items.push(h("span", "", b.mode === "simulate" ? "Simulator (no real pack)" : "Real pack (hardware)"));
  }
  items.push(h("span", "", "frames/s: CAN11 " + d.rates.bus1_rx + ", CAN " + d.rates.bus2_rx + ", sent " + d.rates.bus1_tx));
  fill($("statusline"), items);
}

const CONTACTOR_KIND = {
  "CLOSED (confirmed)": "ok", "OPEN (confirmed)": "neutral", "CLOSING": "warn",
  "COMMANDED CLOSED, NOT CONFIRMED": "warn", "OPEN BUT VOLTAGE ON OUTPUT": "warn", "NO DATA": "bad",
};
function renderTopStatus(d) {
  const t = d.top_status;
  const setCell = (id, kind, big, note) => {
    $(id + "-cell").className = "ts-cell " + kind;
    $(id).textContent = big;
    $(id).classList.toggle("long", id !== "ts-contactors" && big.length > 14);
    $(id + "-note").textContent = note || "";
  };
  const c = t.contactors;
  setCell("ts-contactors", CONTACTOR_KIND[c] || "bad", c, t.contactor_detail);
  $("ts-contactors-note2").textContent = t.contactor_note || "";
  const s = t.session, running = s.startsWith("RUNNING"), b = d.bridge;
  let sNote = "";
  if (d.mode === "replay") sNote = "replaying a log file; controls are off";
  else if (!b.connected) sNote = "the app is not connected to the bridge";
  else if (running) sNote = b.app_wants_running ? "the app is keeping the pack on (" + b.app_mode + " mode)" : "bridge is running but the app is not keeping it on";
  else if (s === "STOPPED") sNote = "the pack is not being kept on";
  setCell("ts-session", running ? "ok" : s === "STOPPED" || s === "REPLAY" ? "neutral" : "bad", s, sNote);
  const n = t.fault_count;
  setCell("ts-faults", n === null ? "warn" : n === 0 ? "ok" : "bad", n === null ? (d.comms.ok ? "UNKNOWN" : "--") : n === 0 ? "none" : n + " present",
    t.fault_hints.length ? t.fault_hints.join("; ") : n === null ? (d.comms.ok ? "no fault frames received yet" : "no current data") : n ? "see the list in Controls" : "");
  const fmtM = (m) => m.value === null ? "--" : fmtUnit(m.value, m.unit);
  const pv = t.pack_voltage, cu = t.current, ov = t.output_voltage;
  $("ts-meas-cell").className = "ts-cell neutral";
  $("ts-meas").textContent = fmtM(pv) + " / " + fmtM(cu);
  $("ts-power").textContent = fmtPower(t.power);
  $("ts-meas-note").textContent = "Output side: " + fmtM(ov) + (ov.value === null ? "" : " " + confTag(ov));
}

function renderBanners(d) {
  const b = d.bridge, out = [];
  if (b.bind_warning) out.push(h("div", "banner", b.bind_warning));
  if (d.mode === "live") {
    if (!b.connected) {
      let t = "Not connected to the bridge.";
      if (b.cooldown_s > 0) t += " Next attempt in about " + Math.ceil(b.cooldown_s) + " s.";
      if (b.halt_reason) t += " " + b.halt_reason + ".";
      out.push(h("div", "banner", t));
    } else if (b.halt_reason && !b.app_wants_running && b.halt_reason !== "stopped by user") {
      out.push(h("div", "banner", "Pack not being kept running: " + b.halt_reason + ". Heartbeats stopped."));
    }
  }
  const lg = d.logging || {};
  if (lg.error) out.push(h("div", "banner info", lg.error));
  fill($("banners"), out);
}

function card(title, sigs, big, note) {
  if (!sigs.some(visible)) return null;
  const c = h("div", "card");
  const t = h("h3", "", title);
  if (note) t.appendChild(h("span", "warnbadge", note));
  c.appendChild(t);
  rowsFor(sigs, big).forEach((r) => c.appendChild(r));
  return c;
}
// Power card in the Pack panel: CAN11 0x112 pack voltage x CAN11 0x111 current, blank when either is stale.
function powerCard(p) {
  const c = h("div", "card");
  c.appendChild(h("h3", "", "Power"));
  const blank = !p || p.kw === null;
  const r = h("div", "row big" + (blank ? " stale" : ""));
  r.appendChild(h("div", "lab", "Power (" + ((p && p.source) || "CAN11 0x112 x 0x111") + ")"));
  r.appendChild(h("div", "val", fmtPower(p)));
  r.appendChild(h("div", "meta", blank ? "needs fresh pack voltage and current" : "in = charging, out = discharging"));
  c.appendChild(r);
  return c;
}
function renderHeadline(d) {
  const hl = d.headline;
  const cards = [
    card("Pack voltage", hl.pack_voltage, true),
    card("Current", hl.current, true),
    powerCard(d.power),
    card("State of charge", hl.soc, true),
    card("State of health", hl.soh, true),
    card("Limits", hl.limits, false, "direction unverified"),
    card("Pack state", hl.pack_state, false),
  ].filter((c) => c);
  fill($("headline"), cards.length ? cards : [h("p", "muted", "No pack data received yet.")]);
}

function faultTile(f, kind) {
  const cls = ["fault"];
  if (f.active) cls.push("active");
  if (f.active && kind === "warning") cls.push("nonblocking");
  if (f.active === null) cls.push("unseen");
  const t = h("div", cls.join(" "));
  const label = f.name.replace(/_/g, " ") + (f.byte !== undefined ? "  (0x120 B" + f.byte + " bit " + f.bit + ")" : "  (bit " + f.bit + ")");
  t.appendChild(h("span", "", label + " " + confTag(f)));
  t.appendChild(h("span", "", f.active === null ? (f.seen ? "--" : "not received") : f.active ? "SET" : "off"));
  return t;
}
function renderFaults(d) {
  const f = d.faults;
  const groups = [
    ["CAN11 0x120", f.can1_0x120, (x) => x.blocking ? "fault" : "warning"],
    ["CAN 0x18FF08F3 latched word", f.can2_0x18FF08F3_latched, () => "fault"],
    ["CAN 0x18FF08F3 live word", f.can2_0x18FF08F3_live, () => "warning"],
  ];
  const all = [];
  groups.forEach(([title, list, kindOf]) => {
    if (!list.some(visible)) return;
    all.push(h("h3", "", title));
    const g = h("div", "faultlist");
    list.forEach((x) => g.appendChild(faultTile(x, kindOf(x))));
    all.push(g);
  });
  all.push(h("h3", "", "Raw fault bytes and words"));
  const rr = h("div", "rows");
  rowsFor(f.can1_raw_bytes.concat(f.can1_flags, f.can2_words)).forEach((r) => rr.appendChild(r));
  all.push(rr);
  fill($("faults-all"), all);
}

function can2Note(id, what, d) {
  const hide = !d.frames.can2_seen && !showAll;
  const el = $(id);
  el.hidden = !hide;
  el.textContent = hide ? "No CAN frames received (CAN may not be connected). " + what + " come only from CAN, so the grid is hidden." : "";
  return hide;
}

function renderCells(d) {
  const st = d.cell_stats, fmt = (v) => v === null ? "--" : v + " mV";
  const hide = can2Note("cells-can2-note", "Per-cell voltages", d);
  const stats = [];
  if (!hide) {
    stats.push(h("span", "", "Min " + fmt(st.min_mv) + (st.min_cell ? " (cell " + st.min_cell + ")" : "")),
      h("span", "", "Max " + fmt(st.max_mv) + (st.max_cell ? " (cell " + st.max_cell + ")" : "")),
      h("span", "", "Spread " + fmt(st.spread_mv)));
  }
  st.pack_reported.filter((s) => s.seen).forEach((s) => stats.push(h("span", "muted", s.label + " " + fmtVal(s) + (s.blank ? "" : " (raw " + s.raw + " " + confTag(s) + ")"))));
  fill($("cell-stats"), stats);
  $("cells").hidden = hide;
  $("mask-line").hidden = hide;
  if (hide) return;
  fill($("cells"), d.cells.map((c) => {
    const cls = ["cell"];
    if (c.is_min) cls.push("min");
    if (c.is_max) cls.push("max");
    if (c.seen && !c.blank && !c.valid) cls.push("invalid");
    if (c.blank) cls.push("stale");
    const e = h("div", cls.join(" "));
    e.appendChild(h("div", "n", "Cell " + c.n));
    e.appendChild(h("div", "v", !c.seen ? "not received" : c.blank ? "--" : c.valid ? c.value + " mV" : "invalid"));
    e.appendChild(h("div", "r", c.seen && !c.blank ? "raw " + c.raw + " " + confTag(c) : ""));
    const b = c.balancing;
    e.appendChild(h("div", "bal" + (!b.blank && b.value ? " on" : ""), b.blank ? "balancing --" : (b.value ? "balancing ON" : "balancing off") + " " + confTag(b)));
    return e;
  }));
  const m = d.balancing_mask;
  $("mask-line").textContent = !m.blank ? "Balancing mask raw 0x" + m.raw.toString(16).toUpperCase().padStart(4, "0") + " " + confTag(m) + " (bit n-1 = cell n)" : "Balancing mask: --";
}

function renderTemps(d) {
  const hide = can2Note("temps-can2-note", "Per-sensor temperatures", d);
  $("temps").hidden = hide;
  if (!hide) {
    fill($("temps"), d.temps.map((t) => {
      const cls = ["cell"];
      if (t.seen && !t.blank && !t.valid) cls.push("invalid");
      if (t.blank) cls.push("stale");
      const e = h("div", cls.join(" "));
      e.appendChild(h("div", "n", "Temp " + t.n));
      e.appendChild(h("div", "v", !t.seen ? "not received" : t.blank ? "--" : t.valid ? t.value + " °C" : "invalid (no sensor)"));
      e.appendChild(h("div", "r", t.seen && !t.blank ? "raw " + t.raw + " " + confTag(t) : ""));
      return e;
    }));
  }
  fill($("temp-stats"), rowsFor(d.temp_stats));
}

function renderContactors(d) {
  const c = d.contactors;
  fill($("contactor-summary"), [pill("Drivers commanded: " + c.summary, c.summary === "CLOSED" ? "ok" : c.summary === "OPEN" ? "" : "warn"),
    h("span", "muted", "The actual state (from voltages) is in the banner at the top. " + c.precharge.note)]);
  fill($("contactors"), rowsFor(c.drivers.concat(c.sequence)));
}

function renderLists(d) {
  fill($("inputs"), rowsFor(d.inputs));
  fill($("counters"), rowsFor(d.counters));
  fill($("host-tx"), rowsFor(d.host_tx));
  const r = d.rates, f = d.frames;
  fill($("rates"), [
    h("span", "", "CAN11 rx " + r.bus1_rx + " frames/s"), h("span", "", "CAN rx " + r.bus2_rx + " frames/s"),
    h("span", "", "bridge tx " + r.bus1_tx + " frames/s"), h("span", "", "total frames " + f.total + ", undecoded " + f.unknown),
    h("span", "muted", f.last_frame_age === null ? "no frames yet" : "last frame " + f.last_frame_age + " s ago"),
  ]);
}

function renderLog(d) {
  const lg = d.logging || {};
  $("logging-line").textContent = lg.file ? "Logging " + lg.frames + " frames to " + lg.file + " (status lines in " + lg.status_file + ")" : (d.mode === "replay" ? "Replay: nothing is logged." : "Logging is off.");
  const ol = $("statuslog");
  const atBottom = ol.scrollTop + ol.clientHeight >= ol.scrollHeight - 8;
  fill(ol, d.status_lines.map((l) => {
    const li = h("li", "");
    li.appendChild(h("span", "src", (l.t - d.now).toFixed(1) + "s " + l.source + "  "));
    li.appendChild(document.createTextNode(l.text));
    return li;
  }));
  if (atBottom) ol.scrollTop = ol.scrollHeight;
}

// Which set faults make the Start confirmation dialog name them. Start itself is never disabled
// by faults (a latched fault can only be cleared by Start then Clear). Edit this one function to
// change the policy: the server marks the two expected wake latches (keep-alive timeout, enable
// input released) with wake=true; every other blocking fault gates the extra confirmation sentence.
function faultsGatingStart(items) {
  return items.filter((i) => i.blocking && !i.wake);
}
const faultLabel = (i) => i.name.replace(/_/g, " ");

function renderControlFaults(d) {
  const cf = d.control_faults;
  const ul = $("ctl-faults-list");
  // The whole fault block (list, instruction, Clear faults) only shows when a fault or warning is present.
  const show = d.comms.ok && cf.seen && cf.items.length > 0;
  $("ctl-faults").hidden = !show;
  if (!show) { fill(ul, []); return; }
  fill(ul, cf.items.map((i) => {
    const li = h("li", "cf " + (i.blocking ? "blocking" : "warning"));
    li.appendChild(pill(i.blocking ? "BLOCKING" : "WARNING", i.blocking ? "bad" : "warn"));
    li.appendChild(pill(i.state, ""));
    li.appendChild(h("span", "", " " + faultLabel(i)));
    li.appendChild(h("span", "muted", "  (" + i.source + ")"));
    return li;
  }));
  const ins = $("ctl-faults-instr"), wk = $("ctl-faults-wake");
  ins.hidden = !cf.instruction; ins.textContent = cf.instruction;
  ins.className = "ctl-instr" + (cf.emphasise_clear ? " urgent" : "");
  wk.hidden = !cf.wake_note; wk.textContent = cf.wake_note;
  $("btn-clear").classList.toggle("emphasis", !!cf.emphasise_clear);
}

// Mode of the current session, or null when not running.
function runningMode(d) {
  const s = d.top_status.session;
  if (s === "RUNNING CHARGE") return "charge";
  if (s === "RUNNING DISCHARGE") return "discharge";
  if (d.bridge.app_wants_running && d.bridge.app_mode) return d.bridge.app_mode;
  return null;
}

let lastSession = null, sessionChangedAt = -Infinity;
const bridgeReplyT = {};  // word -> {text, t} on the page clock, so an unchanged reply keeps its time

function renderReplies(d) {
  const s = d.top_status.session, now = nowS();
  if (lastSession !== null && s !== lastSession) sessionChangedAt = now;
  lastSession = s;
  // The server also says when the bridge STATE last changed, so a freshly loaded page hides old replies too.
  const ca = d.bridge.state_changed_age;
  if (typeof ca === "number") sessionChangedAt = Math.max(sessionChangedAt, now - ca - 0.5);
  ["START", "STOP", "CLEAR", "SLEEP"].forEach((w) => {
    const cands = [];
    const loc = local[w];
    if (loc) cands.push(loc);
    const br = d.replies[w];
    if (br) {
      const prev = bridgeReplyT[w];
      const t = prev && prev.text === br.text && Math.abs(prev.t - (now - br.age)) < 1.5 ? prev.t : now - br.age;
      bridgeReplyT[w] = { text: br.text, t: t };
      cands.push({ text: (br.ok ? "Bridge accepted: " : "Bridge refused: ") + br.text, err: !br.ok, t: t });
    }
    // Only the latest reply, only for 10 s, and none from before the last session change
    // (a reply up to 1.5 s before the change, e.g. the START that caused it, is kept).
    const shown = cands.filter((c) => now - c.t <= REPLY_SHOW_S && c.t >= sessionChangedAt - 1.5)
      .sort((a, b) => b.t - a.t)[0];
    const el = $("reply-" + w);
    el.textContent = shown ? shown.text : "";
    el.hidden = !shown;
    el.className = "reply" + (shown && shown.err ? " err" : "");
  });
}

function renderControls(d) {
  const en = d.controls.enabled, b = d.bridge;
  const wants = b.app_wants_running;
  const cf = d.control_faults;
  const running = d.top_status.session.startsWith("RUNNING");
  $("controls-disabled").hidden = en;
  $("controls-disabled").textContent = d.controls.reason;
  renderControlFaults(d);
  // Mode: while running, show the running mode and lock it.
  const rm = runningMode(d);
  if (rm) $("mode-" + rm).checked = true;
  $("startmode-set").disabled = !!rm || !en;
  $("mode-why").textContent = rm ? "Mode is locked to " + rm + " while running. Press Stop to change it." : "";
  $("btn-start").textContent = "Start (" + startMode() + ")";
  // Start: never gated on faults. Disabled only when already running, bridge down, or controls off.
  let startWhy = "";
  if (!en) startWhy = "Controls are off: " + d.controls.reason + ".";
  else if (!b.connected) startWhy = "The bridge is not connected.";
  else if (running || wants) startWhy = "Already running.";
  else if (b.cooldown_s > 0) startWhy = "Waiting to reconnect.";
  $("btn-start").disabled = !!startWhy;
  $("btn-start").title = startWhy;
  $("start-why").textContent = rm && en ? "" : startWhy;  // the mode line already says it is running
  if (startWhy) hideConfirm("start");
  // Clear: the bridge only accepts CLEAR while RUNNING, and there must be something to clear.
  let clearWhy = "";
  if (!en) clearWhy = "Controls are off.";
  else if (!running) clearWhy = "Only works while running. Press Start first.";
  else if (!cf.count) clearWhy = "No faults to clear.";
  $("btn-clear").disabled = !!clearWhy;
  $("btn-clear").title = clearWhy;
  $("clear-why").textContent = clearWhy;
  $("btn-stop").disabled = !en;
  let sleepWhy = "";
  if (!en) sleepWhy = "Controls are off.";
  else if (running || wants) sleepWhy = "Only works while stopped. Press Stop first.";
  $("btn-sleep").disabled = !!sleepWhy;
  $("sleeplevel-set").disabled = !!sleepWhy;
  $("sleep-why").textContent = sleepWhy;
  if (sleepWhy) hideConfirm("sleep");
  renderReplies(d);
}

let lastSnapAt = 0, dark = false;
function render(d) {
  snap = d; lastSnapAt = nowS(); dark = false;
  renderCommsBar(d); renderStatusLine(d); renderTopStatus(d); renderBanners(d); renderHeadline(d); renderFaults(d);
  renderCells(d); renderTemps(d); renderContactors(d); renderLists(d); renderLog(d); renderControls(d);
}

// The page has no current data (websocket lost or no update): blank every value, never leave old numbers.
function goDark(reason) {
  dark = true;
  const bar = $("commsbar");
  bar.hidden = false;
  bar.textContent = "NO DATA: comms lost (" + reason + ")";
  document.body.classList.add("commslost");
  document.querySelectorAll(".row .val, .cell .v, #ts-meas, #ts-faults, #ts-session, .stats span").forEach((e) => { e.textContent = "--"; });
  document.querySelectorAll(".row .meta, .cell .r, .ts-note, #ts-power").forEach((e) => { e.textContent = ""; });
  $("ts-contactors-cell").className = "ts-cell bad"; $("ts-contactors").textContent = "NO DATA";
  $("ts-contactors-note").textContent = reason;
  fill($("ctl-faults-list"), []); $("ctl-faults").hidden = true;
  ["btn-start", "btn-stop", "btn-clear", "btn-sleep"].forEach((id) => { $(id).disabled = true; });
  hideConfirm("start"); hideConfirm("sleep");
}
setInterval(() => {
  if (!dark && snap && nowS() - lastSnapAt > 2) goDark("no update from the app for " + Math.round(nowS() - lastSnapAt) + " s");
}, 500);

async function post(path, body, word) {
  let res, data;
  try {
    res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    data = await res.json();
  } catch (e) {
    local[word] = { text: "Request failed: " + e, err: true, t: nowS() };
    if (snap) renderControls(snap);
    return;
  }
  if (!res.ok && !data.message) data.message = typeof data.detail === "string" ? data.detail : "refused (HTTP " + res.status + ")";
  local[word] = { text: (res.ok ? "" : "Refused: ") + (data.message || ""), err: !res.ok, t: nowS() };
  if (snap) renderControls(snap);
}

function hideConfirm(which) {
  $("confirm-" + which).hidden = true;
  if (confirmTimers[which]) { clearTimeout(confirmTimers[which]); confirmTimers[which] = null; }
}
function showConfirm(which, text) {
  $("confirm-" + which + "-text").textContent = text;
  $("confirm-" + which).hidden = false;
  if (confirmTimers[which]) clearTimeout(confirmTimers[which]);
  confirmTimers[which] = setTimeout(() => hideConfirm(which), 10000);
}
const startMode = () => document.querySelector("input[name=startmode]:checked").value;
const sleepLevel = () => Number(document.querySelector("input[name=sleeplevel]:checked").value);

document.querySelectorAll("input[name=startmode]").forEach((r) => r.addEventListener("change", () => {
  hideConfirm("start");
  $("btn-start").textContent = "Start (" + startMode() + ")";
}));
$("show-all").checked = showAll;
$("show-all").addEventListener("change", () => {
  showAll = $("show-all").checked;
  try { localStorage.setItem(SHOW_ALL_KEY, showAll ? "1" : "0"); } catch (e) { /* storage blocked: toggle still works for this visit */ }
  if (snap && !dark) render(snap);
});

$("btn-start").addEventListener("click", () => {
  let text = "Close the contactors and run in " + startMode() + " mode?";
  const gate = snap && snap.control_faults ? faultsGatingStart(snap.control_faults.items) : [];
  if (gate.length) text = "Blocking faults present: " + gate.map(faultLabel).join(", ") + ". Start anyway? " + text;
  showConfirm("start", text);
});
$("btn-start-no").addEventListener("click", () => hideConfirm("start"));
$("btn-start-yes").addEventListener("click", () => { const m = startMode(); hideConfirm("start"); post("/api/start", { mode: m }, "START"); });
$("btn-stop").addEventListener("click", () => { hideConfirm("start"); post("/api/stop", {}, "STOP"); });
$("btn-clear").addEventListener("click", () => post("/api/clear", {}, "CLEAR"));
$("btn-sleep").addEventListener("click", () => showConfirm("sleep", "Send sleep " + sleepLevel() + " to the pack?"));
$("btn-sleep-no").addEventListener("click", () => hideConfirm("sleep"));
$("btn-sleep-yes").addEventListener("click", () => { const l = sleepLevel(); hideConfirm("sleep"); post("/api/sleep", { level: l }, "SLEEP"); });

function connect() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onmessage = (ev) => { try { render(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
  ws.onclose = () => {
    fill($("banners"), [h("div", "banner", "Page lost its connection to the app. Retrying.")]);
    goDark("page lost its connection to the app");
    setTimeout(connect, 1000);
  };
}
connect();
