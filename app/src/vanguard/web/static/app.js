"use strict";
// Draws the snapshot the server pushes over /ws. All text is set with textContent.

const $ = (id) => document.getElementById(id);
function h(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

let snap = null;
const local = {};      // last local result per command word
let confirmTimers = {};

function fmtNum(v) {
  if (Number.isInteger(v)) return String(v);
  return String(Number(v.toFixed(3)));
}
function fmtVal(s) {
  if (!s.seen) return "no data";
  if (!s.valid) return "invalid";
  const v = s.value;
  if (typeof v === "boolean") return v ? "ON" : "OFF";
  if (typeof v === "number") return fmtNum(v) + (s.unit ? " " + s.unit : "");
  return String(v);
}
function confTag(s) { return s.conf ? "[" + s.conf + "]" : ""; }
function metaText(s) {
  if (!s.seen) return "no frame yet";
  let t = "raw " + s.raw + " " + confTag(s);
  if (s.age !== null) t += (s.stale ? "  STALE " : "  ") + s.age.toFixed(1) + " s";
  return t;
}
function row(s, big) {
  const cls = ["row"];
  if (big) cls.push("big");
  if (!s.seen || s.stale) cls.push("stale");
  if (s.seen && !s.valid) cls.push("invalid");
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

function renderStatusLine(d) {
  const b = d.bridge, bar = $("statusline"), items = [];
  if (d.mode === "replay") {
    const r = d.replay || {};
    items.push(pill("REPLAY", "warn"));
    items.push(h("span", "", "frame " + (r.position || 0) + " of " + (r.file_frames || 0) + (r.finished ? " (finished)" : "") + ", speed " + (r.speed || 1) + "x"));
  } else {
    items.push(pill(b.connected ? "BRIDGE CONNECTED" : "BRIDGE NOT CONNECTED", b.connected ? "ok" : "bad"));
    if (b.mode) items.push(pill(b.mode === "simulate" ? "SIMULATE (no hardware)" : "HARDWARE", b.mode === "simulate" ? "warn" : ""));
    if (b.state) {
      const running = b.state.startsWith("RUNNING");
      items.push(pill(b.state, running ? "ok" : ""));
    }
    items.push(h("span", "", "app keeps pack running: " + (b.app_wants_running ? "yes (" + b.app_mode + ")" : "no")));
  }
  items.push(h("span", "muted", "frames/s  CAN1 rx " + d.rates.bus1_rx + "  CAN2 rx " + d.rates.bus2_rx + "  tx " + d.rates.bus1_tx));
  fill(bar, items);
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
  const c = h("div", "card");
  const t = h("h3", "", title);
  if (note) t.appendChild(h("span", "warnbadge", note));
  c.appendChild(t);
  sigs.forEach((s, i) => c.appendChild(row(s, big && i === 0)));
  return c;
}
function renderHeadline(d) {
  const hl = d.headline;
  fill($("headline"), [
    card("Pack voltage", hl.pack_voltage, true),
    card("Current", hl.current, true, "sign unverified"),
    card("State of charge", hl.soc, true),
    card("State of health", hl.soh, true),
    card("Limits", hl.limits, false, "direction unverified"),
    card("Pack state", hl.pack_state, false),
  ]);
}

function faultTile(f, kind) {
  const cls = ["fault"];
  if (f.active) cls.push("active");
  if (f.active && kind === "warning") cls.push("nonblocking");
  if (f.active === null) cls.push("unseen");
  const t = h("div", cls.join(" "));
  const label = f.name.replace(/_/g, " ") + (f.byte !== undefined ? "  (0x120 B" + f.byte + " bit " + f.bit + ")" : "  (bit " + f.bit + ")");
  t.appendChild(h("span", "", label + " " + confTag(f)));
  t.appendChild(h("span", "", f.active === null ? "no data" : f.active ? "ACTIVE" : "off"));
  return t;
}
function renderFaults(d) {
  const f = d.faults;
  const groups = [
    ["CAN1 0x120", f.can1_0x120, (x) => x.blocking ? "fault" : "warning"],
    ["CAN2 0x18FF08F3 latched word", f.can2_0x18FF08F3_latched, () => "fault"],
    ["CAN2 0x18FF08F3 live word", f.can2_0x18FF08F3_live, () => "warning"],
  ];
  const active = [];
  groups.forEach(([title, list, kindOf]) => list.forEach((x) => { if (x.active) active.push(faultTile({ ...x, name: title + ": " + x.name }, kindOf(x))); }));
  const anySeen = groups.some(([, list]) => list.some((x) => x.active !== null));
  if (!anySeen) fill($("faults-active"), [h("p", "muted", "No fault frames received yet.")]);
  else if (!active.length) fill($("faults-active"), [h("p", "nofault", "No fault or warning bits set.")]);
  else { const g = h("div", "faultlist"); g.replaceChildren(...active); fill($("faults-active"), [g]); }
  const all = [];
  groups.forEach(([title, list, kindOf]) => {
    all.push(h("h3", "", title));
    const g = h("div", "faultlist");
    list.forEach((x) => g.appendChild(faultTile(x, kindOf(x))));
    all.push(g);
  });
  all.push(h("h3", "", "Raw fault bytes and words"));
  const rr = h("div", "rows");
  f.can1_raw_bytes.concat(f.can1_flags, f.can2_words).forEach((s) => rr.appendChild(row(s)));
  all.push(rr);
  fill($("faults-all"), all);
}

function renderCells(d) {
  const st = d.cell_stats, fmt = (v) => v === null ? "no data" : v + " mV";
  fill($("cell-stats"), [
    h("span", "", "Min " + fmt(st.min_mv) + (st.min_cell ? " (cell " + st.min_cell + ")" : "")),
    h("span", "", "Max " + fmt(st.max_mv) + (st.max_cell ? " (cell " + st.max_cell + ")" : "")),
    h("span", "", "Spread " + fmt(st.spread_mv)),
    ...st.pack_reported.filter((s) => s.seen).map((s) => h("span", "muted", s.label + " " + fmtVal(s) + " (raw " + s.raw + " " + confTag(s) + ")")),
  ]);
  fill($("cells"), d.cells.map((c) => {
    const cls = ["cell"];
    if (c.is_min) cls.push("min");
    if (c.is_max) cls.push("max");
    if (c.seen && !c.valid) cls.push("invalid");
    if (!c.seen || c.stale) cls.push("stale");
    const e = h("div", cls.join(" "));
    e.appendChild(h("div", "n", "Cell " + c.n));
    e.appendChild(h("div", "v", c.seen ? (c.valid ? c.value + " mV" : "invalid") : "no data"));
    e.appendChild(h("div", "r", c.seen ? "raw " + c.raw + " " + confTag(c) : ""));
    const b = c.balancing;
    e.appendChild(h("div", "bal" + (b.seen && b.value ? " on" : ""), b.seen ? (b.value ? "balancing ON" : "balancing off") + " " + confTag(b) : "balancing ?"));
    return e;
  }));
  const m = d.balancing_mask;
  $("mask-line").textContent = m.seen ? "Balancing mask raw 0x" + m.raw.toString(16).toUpperCase().padStart(4, "0") + " " + confTag(m) + " (bit n-1 = cell n)" : "Balancing mask: no data";
}

function renderTemps(d) {
  fill($("temps"), d.temps.map((t) => {
    const cls = ["cell"];
    if (t.seen && !t.valid) cls.push("invalid");
    if (!t.seen || t.stale) cls.push("stale");
    const e = h("div", cls.join(" "));
    e.appendChild(h("div", "n", "Temp " + t.n));
    e.appendChild(h("div", "v", t.seen ? (t.valid ? t.value + " °C" : "invalid (no sensor)") : "no data"));
    e.appendChild(h("div", "r", t.seen ? "raw " + t.raw + " " + confTag(t) : ""));
    return e;
  }));
  fill($("temp-stats"), d.temp_stats.map((s) => row(s)));
}

function renderContactors(d) {
  const c = d.contactors;
  fill($("contactor-summary"), [pill("Contactors: " + c.summary, c.summary === "CLOSED" ? "ok" : c.summary === "OPEN" ? "" : "warn"), h("span", "muted", c.precharge.note)]);
  fill($("contactors"), c.drivers.concat(c.sequence).map((s) => row(s)));
}

function renderLists(d) {
  fill($("inputs"), d.inputs.map((s) => row(s)));
  fill($("counters"), d.counters.map((s) => row(s)));
  fill($("host-tx"), d.host_tx.map((s) => row(s)));
  const r = d.rates, f = d.frames;
  fill($("rates"), [
    h("span", "", "CAN1 rx " + r.bus1_rx + " frames/s"), h("span", "", "CAN2 rx " + r.bus2_rx + " frames/s"),
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

function renderControls(d) {
  const en = d.controls.enabled, b = d.bridge;
  const wants = b.app_wants_running;
  $("controls-disabled").hidden = en;
  $("controls-disabled").textContent = d.controls.reason;
  $("btn-start").disabled = !en || wants || b.cooldown_s > 0;
  ["btn-stop", "btn-clear", "btn-sleep"].forEach((id) => { $(id).disabled = !en; });
  $("btn-sleep").disabled = !en || wants;
  if (!en) { hideConfirm("start"); hideConfirm("sleep"); }
  ["START", "STOP", "CLEAR", "SLEEP"].forEach((w) => {
    const el = $("reply-" + w), parts = [];
    const loc = local[w];
    if (loc) parts.push(loc.text);
    const br = d.replies[w];
    let err = loc ? loc.err : false;
    if (br) { parts.push("Bridge: " + br.text); if (!br.ok) err = true; }
    el.textContent = parts.join("  |  ");
    el.className = "reply" + (err ? " err" : "");
  });
}

function render(d) {
  snap = d;
  renderStatusLine(d); renderBanners(d); renderHeadline(d); renderFaults(d); renderCells(d);
  renderTemps(d); renderContactors(d); renderLists(d); renderLog(d); renderControls(d);
}

async function post(path, body, word) {
  let res, data;
  try {
    res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    data = await res.json();
  } catch (e) {
    local[word] = { text: "request failed: " + e, err: true };
    return;
  }
  if (!res.ok && !data.message) data.message = typeof data.detail === "string" ? data.detail : "refused (HTTP " + res.status + ")";
  local[word] = { text: data.message || "", err: !res.ok };
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

$("btn-start").addEventListener("click", () => showConfirm("start", "Close the contactors and run in " + startMode() + " mode?"));
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
    ["btn-start", "btn-stop", "btn-clear", "btn-sleep"].forEach((id) => { $(id).disabled = true; });
    setTimeout(connect, 1000);
  };
}
connect();
