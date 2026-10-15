"""Build the JSON snapshot the page renders from the state model and client status.

All layout knowledge (which decoded signal goes where on the page) lives here, so the
JavaScript only draws what it is given. Every value carries its raw integer, unit and
confidence tag. Current sign: positive = into the pack (bench-confirmed 2026-10-11 on CAN1 0x111).
"""

from __future__ import annotations

from typing import List, Optional

from ..frames import FAULT_BITS, LATCHED_WORD_BITS, LIVE_WORD_BITS
from .state import PackState, StatusLog

CELLS = 14
LIMIT_NOTE = "direction and sign unverified"


def _cell_key(n: int) -> str:
    lo = ((n - 1) // 4) * 4 + 1
    frame = "c2_cells_13_14" if n >= 13 else f"c2_cells_{lo}_{lo + 3}"
    return f"{frame}.cell{n}_mv"


def _temp_key(n: int) -> str:
    frame = "c2_temps_1_7" if n <= 7 else "c2_temps_8_14"
    return f"{frame}.temp{n}_c"


# Comms: the page goes dark when no CAN1 0x112 or 0x111 frame arrived for this long (both are
# 50 ms frames), or when the bridge is disconnected. The page itself also goes dark when its
# websocket drops.
COMMS_STALE_S = 0.5

# Actual (not commanded) contactor state, inferred from CAN1 0x112 voltages. Thresholds from the
# firmware's contactor sequence, CANBUS_PROTOCOL.md section 6.2: the closed-state checks allow 5000 mV
# between voltages, the open check needs the voltage below 2000 mV.
CONTACTOR_CLOSED_MATCH_V = 5.0   # closed: |V_out - V_pack| <= 5.0 V (6.2 state 6, within 5000 mV)
CONTACTOR_OPEN_BELOW_V = 2.0     # open: V_out < 2.0 V (6.2 state 1, below 2000 mV)
CONTACTOR_STALE_S = 0.5          # 0x112 is a 50 ms frame; older than this = NO DATA

CLOSED_OK = "CLOSED (confirmed)"
OPEN_OK = "OPEN (confirmed)"
CLOSING = "CLOSING"
CLOSED_UNCONFIRMED = "COMMANDED CLOSED, NOT CONFIRMED"
OPEN_VOLTAGE = "OPEN BUT VOLTAGE ON OUTPUT"
NO_DATA = "NO DATA"


def _item(state: PackState, now: float, label: str, key: str, note: str = "", table: Optional[dict] = None,
          comms_lost: bool = False) -> dict:
    """One signal for the page. ``blank`` is True when the value must not be shown (never seen, stale, or
    comms lost); then value and raw are None, so no old number can ever look current. ``seen`` and ``age``
    are kept so the page can say how old the last value was."""
    d = state.signal_view(key, now, table)
    d["label"] = label
    if note:
        d["label_note"] = note
    d["blank"] = bool(comms_lost or not d["seen"] or d["stale"])
    if d["blank"]:
        d["value"] = None
        d["raw"] = None
    elif d.get("unit") == "mA":
        # Currents are shown in A everywhere; raw stays the integer mA count.
        if isinstance(d["value"], (int, float)) and not isinstance(d["value"], bool):
            d["value"] = d["value"] / 1000.0
        d["unit"] = "A"
    return d


def pack_power(v_item: dict, i_item: dict) -> dict:
    """Power from CAN1 0x112 pack voltage x CAN1 0x111 pack current (A). Positive current = into the pack.
    Blank (kw None) when either input is blank (never seen, stale, comms lost) or invalid."""
    if not (_fresh(v_item) and _fresh(i_item)):
        return {"kw": None, "signed_kw": None, "direction": "",
                "source": "CAN1 0x112 B4-B5 x CAN1 0x111"}
    kw = v_item["value"] * i_item["value"] / 1000.0
    return {"kw": round(abs(kw), 2), "signed_kw": round(kw, 2),
            "direction": "in" if kw > 0 else "out" if kw < 0 else "",
            "source": "CAN1 0x112 B4-B5 x CAN1 0x111"}


def actual_contactor_state(a_on: Optional[bool], b_on: Optional[bool], v_out: Optional[float],
                           v_pack: Optional[float]) -> str:
    """Actual contactor state from the 0x112 driver bits (commanded) and voltages. None = no fresh data."""
    if a_on is None or b_on is None or v_out is None or v_pack is None:
        return NO_DATA
    if a_on and b_on:
        if abs(v_out - v_pack) <= CONTACTOR_CLOSED_MATCH_V and v_out > CONTACTOR_OPEN_BELOW_V:
            return CLOSED_OK
        return CLOSED_UNCONFIRMED
    if not a_on and not b_on:
        return OPEN_OK if v_out < CONTACTOR_OPEN_BELOW_V else OPEN_VOLTAGE
    return CLOSING


def comms_status(state: PackState, now: float, bridge: dict, mode: str) -> dict:
    """{"ok", "reason"}: lost when the bridge is disconnected (live mode) or no CAN1 0x112/0x111 for COMMS_STALE_S."""
    if mode != "replay" and not bridge.get("connected"):
        return {"ok": False, "reason": "bridge disconnected"}
    ts = [t for t in (state.last_rx.get((1, 0x112)), state.last_rx.get((1, 0x111))) if t is not None]
    if not ts:
        return {"ok": False, "reason": "no CAN1 0x112 or 0x111 frame received yet"}
    age = now - max(ts)
    if age > COMMS_STALE_S:
        return {"ok": False, "reason": "no CAN1 0x112 or 0x111 for %.1f s" % age}
    return {"ok": True, "reason": ""}


def session_state(bridge: dict, mode: str) -> str:
    """Bridge session label from the last bridge STATE line: STOPPED, RUNNING DISCHARGE, RUNNING CHARGE, ..."""
    if mode == "replay":
        return "REPLAY"
    if not bridge.get("connected"):
        return "BRIDGE DISCONNECTED"
    st = (bridge.get("state") or "").strip().upper()
    if not st:
        return "UNKNOWN"
    if st == "STOPPED":
        return "STOPPED"
    if st.startswith("RUNNING"):
        m = st[len("RUNNING"):].strip()
        return "RUNNING " + m if m in ("DISCHARGE", "CHARGE") else "RUNNING"
    return "UNKNOWN"


def _fresh(item: dict) -> bool:
    return bool(item["seen"] and item["valid"] and not item["stale"] and isinstance(item["value"], (int, float)))


def _top_status(S, bridge: dict, mode: str, faults: dict, can1_faults: list, latched: list) -> dict:
    """The always-visible banner at the top of the page. Contactors come from CAN1 0x112 only: the B0/B1
    bit 5 driver bits are what the BMS commands; the actual state is inferred from its voltages."""
    a1 = S("Contactor A driven (CAN1 0x112)", "status_voltages.contactor_a_driven")
    b1 = S("Contactor B driven (CAN1 0x112)", "status_voltages.contactor_b_driven")
    vo = S("Output-side voltage (CAN1 0x112)", "status_voltages.output_side_adc_v")
    vp = S("Pack voltage (CAN1 0x112)", "status_voltages.pack_voltage_v")
    ok112 = all(not it["blank"] and it["age"] is not None and it["age"] <= CONTACTOR_STALE_S for it in (a1, b1, vo, vp))
    a_on = bool(a1["value"]) if ok112 else None
    b_on = bool(b1["value"]) if ok112 else None
    v_out = vo["value"] if ok112 and vo["valid"] else None
    v_pack = vp["value"] if ok112 and vp["valid"] else None
    actual = actual_contactor_state(a_on, b_on, v_out, v_pack)
    if actual == NO_DATA:
        age = a1["age"]
        detail = "CAN1 0x112 " + ("never received" if age is None else "last received %.1f s ago" % age)
    else:
        onoff = lambda x: "on" if x else "off"  # noqa: E731
        detail = "drivers A %s, B %s; V_out %.2f V, V_pack %.2f V" % (onoff(a_on), onoff(b_on), v_out, v_pack)
    note = ""
    if actual == CLOSING:
        pct = 100.0 * v_out / v_pack if v_pack else None
        note = "precharge or closing sequence: V_out is " + ("%.0f %% of V_pack" % pct if pct is not None else "? % of V_pack")
    elif actual == OPEN_VOLTAGE:
        note = "external source (e.g. charger) or welded contact"
    elif actual == CLOSED_UNCONFIRMED:
        note = "drivers are on but the output voltage does not match the pack voltage"

    def pick(*items):
        for it in items:
            if _fresh(it):
                return it
        return None
    pv = pick(S("Pack voltage (CAN1 0x112)", "status_voltages.pack_voltage_v"),
              S("Pack voltage (CAN2 0x0CF090F3)", "c2_pack_voltage_current.pack_voltage_v"))
    cur = pick(S("Current (CAN2 0x0CFF20F3)", "c2_summary.current_a"),
               S("Current (CAN2 0x0CF090F3)", "c2_pack_voltage_current.current_a"),
               S("Current (CAN1 0x111)", "pack_current_limits.pack_current_ma"))
    out = pick(S("Output-side voltage (CAN1 0x112)", "status_voltages.output_side_adc_v"),
               S("Output voltage (CAN2 0x0CF095F3)", "c2_output_voltage.output_voltage_v"))

    def meas(it):
        if it is None:
            return {"value": None, "unit": "", "label": "", "conf": "", "raw": None}
        return {"value": it["value"], "unit": it["unit"], "label": it["label"], "conf": it["conf"], "raw": it["raw"]}

    seen_faults = any(f["active"] is not None for f in can1_faults + latched)
    by_name = {f["name"]: f for f in can1_faults}
    by_latched = {f["name"]: f for f in latched}
    hints = []
    if (by_name.get("keepalive_timeout") or {}).get("active") or (by_latched.get("host_timeout") or {}).get("active"):
        hints.append("keep-alive timeout fault is set: press Clear faults")
    if (by_name.get("enable_input_released") or {}).get("active") or \
            (by_latched.get("enable_input_released") or {}).get("active"):
        hints.append("enable latch is set: press Clear faults")
    return {
        "contactors": actual, "contactor_detail": detail, "contactor_note": note,
        "contactor_a_on": a_on, "contactor_b_on": b_on,
        "contactor_source": "CAN1 0x112: drivers B0/B1 bit 5 (commanded), voltages B4-B5 and B6-B7 [I]",
        "session": session_state(bridge, mode),
        "fault_count": faults["active_count"] if seen_faults else None,
        "fault_hints": hints,
        "pack_voltage": meas(pv), "current": meas(cur), "output_voltage": meas(out),
        "power": pack_power(S("Pack voltage (CAN1 0x112)", "status_voltages.pack_voltage_v"),
                            S("Current (CAN1 0x111)", "pack_current_limits.pack_current_ma")),
    }


# Bits the pack sets on its own right after a wake or a host timeout. Clearing them is expected.
WAKE_LATCH_NAMES = ("keepalive_timeout", "enable_input_released", "host_timeout")


def control_faults(can1_faults: list, latched: list, live: list, session: str, drivers_on: bool) -> dict:
    """The fault list and instruction shown in the Controls box.

    Blocking (CANBUS_PROTOCOL.md 6.4): every set CAN1 0x120 bit except B1 bits 4 and 6 and B5 bit 5
    (FAULT_BITS blocking=False), and every set bit of the CAN2 latched summary word. The CAN2 live word
    is documented as warnings, so it is tagged WARNING. State: latched word = "latched", live word =
    "live", 0x120 bits = "status" (the data does not say whether the pack will hold them).
    """
    items = []
    for f in can1_faults:
        if f["active"]:
            items.append({"name": f["name"], "source": "CAN1 0x120 B%d bit %d" % (f["byte"], f["bit"]),
                          "blocking": bool(f["blocking"]), "state": "status",
                          "wake": f["name"] in WAKE_LATCH_NAMES})
    for f in latched:
        if f["active"]:
            items.append({"name": f["name"], "source": "CAN2 0x18FF08F3 latched bit %d" % f["bit"],
                          "blocking": True, "state": "latched", "wake": f["name"] in WAKE_LATCH_NAMES})
    for f in live:
        if f["active"]:
            items.append({"name": f["name"], "source": "CAN2 0x18FF08F3 live bit %d" % f["bit"],
                          "blocking": False, "state": "live", "wake": False})
    blocking = [i for i in items if i["blocking"]]
    running = session.startswith("RUNNING")
    kind, text = "none", ""
    if blocking and session == "STOPPED":
        kind = "stopped_latched"
        text = ("Faults are latched. Start, then press Clear faults "
                "(the pack only accepts a clear while running).")
    elif blocking and running and not drivers_on:
        kind = "running_waiting"
        text = "Contactors are waiting on faults: press Clear faults"
    wake_note = ""
    if kind != "none" and any(i["wake"] for i in items):
        wake_note = ("The keep-alive timeout and enable latches are normal right after a wake; "
                     "Clear faults is expected.")
    return {"items": items, "blocking_count": len(blocking), "count": len(items),
            "instruction_kind": kind, "instruction": text, "wake_note": wake_note,
            "emphasise_clear": kind == "running_waiting"}


def build_view(*, state: PackState, status: StatusLog, now: float, mode: str, bridge: dict,
               controls: dict, replies: dict, rates_extra: Optional[dict] = None, logging: Optional[dict] = None,
               replay: Optional[dict] = None, n_status: int = 40) -> dict:
    comms = comms_status(state, now, bridge, mode)
    lost = not comms["ok"]
    S = lambda label, key, note="": _item(state, now, label, key, note, comms_lost=lost)  # noqa: E731
    group = lambda *items: list(items)  # noqa: E731

    headline = {
        "pack_voltage": group(
            S("Pack voltage (CAN1 0x112)", "status_voltages.pack_voltage_v"),
            S("Pack voltage (CAN2 0x0CF090F3)", "c2_pack_voltage_current.pack_voltage_v"),
            S("Sum of cells (CAN2 0x0CFF20F3)", "c2_summary.cell_sum_mv"),
        ),
        "current": group(
            S("Current (CAN2 0x0CFF20F3)", "c2_summary.current_a"),
            S("Current (CAN2 0x0CF090F3)", "c2_pack_voltage_current.current_a"),
            S("Smoothed current", "c2_smoothed_current.smoothed_current_a"),
            S("Mean current", "c2_smoothed_current.mean_current_a"),
            S("Current (CAN1 0x111)", "pack_current_limits.pack_current_ma"),
        ),
        "soc": group(
            S("SOC (CAN1 0x113)", "state_soc.soc_pct"),
            S("SOC (CAN2 0x0CF094F3)", "c2_soc_x640.soc_pct"),
            S("SOC (CAN2 0x0CFF06F3)", "c2_soc_precharge.soc_pct"),
        ),
        "soh": group(
            S("SOH (CAN1 0x113)", "state_soc.soh_pct"),
            S("SOH (CAN2 0x0CFF33F3)", "c2_learned_capacity.soh_pct"),
        ),
        "limits": group(
            S("Discharge limit", "pack_current_limits.discharge_limit_a", LIMIT_NOTE),
            S("Charge limit", "pack_current_limits.charge_limit_a", LIMIT_NOTE),
            S("Temperature charge limit", "state_soc.temp_charge_limit_a", LIMIT_NOTE),
        ),
        "pack_state": group(
            S("Pack mode (CAN2 0x0CFF20F3)", "c2_summary.mode"),
            S("Pack mode (CAN2 0x18FF08F3)", "c2_status_words.mode_raw"),
            S("Sub-state", "c2_status_words.substate"),
            S("Fault present (CAN2)", "c2_summary.fault_present"),
        ),
    }

    cells: List[dict] = []
    for n in range(1, CELLS + 1):
        c = S(f"Cell {n}", _cell_key(n))
        c["n"] = n
        c["balancing"] = S(f"Balancing cell {n}", f"c2_capacity_balancing.balancing_cell{n}")
        cells.append(c)
    valid = [c for c in cells if not c["blank"] and c["valid"] and isinstance(c["value"], (int, float))]
    cell_stats = {"min_mv": None, "max_mv": None, "spread_mv": None, "min_cell": None, "max_cell": None}
    if valid:
        lo = min(valid, key=lambda c: c["value"])
        hi = max(valid, key=lambda c: c["value"])
        cell_stats.update(min_mv=lo["value"], max_mv=hi["value"], spread_mv=hi["value"] - lo["value"])
        if hi["value"] != lo["value"]:
            cell_stats.update(min_cell=lo["n"], max_cell=hi["n"])
    for c in cells:
        c["is_min"] = c["n"] == cell_stats["min_cell"]
        c["is_max"] = c["n"] == cell_stats["max_cell"]
    cell_stats["pack_reported"] = group(
        S("Max cell (pack-reported)", "c2_extremes.max_cell_mv"),
        S("Min cell (pack-reported)", "c2_extremes.min_cell_mv"),
        S("Spread (pack-reported)", "c2_cell_stats.spread_mv"),
        S("Mean cell (pack-reported)", "c2_cell_stats.mean_cell_mv"),
        S("Max cell number (pack-reported)", "c2_cell_stats.max_cell_number"),
        S("Min cell number (pack-reported)", "c2_cell_stats.min_cell_number"),
        S("Max cell (CAN1 0x114)", "temp_cell_extremes.max_cell_mv"),
        S("Min cell (CAN1 0x114)", "temp_cell_extremes.min_cell_mv"),
    )
    mask = S("Balancing mask", "c2_capacity_balancing.balancing_mask")

    temps = []
    for n in range(1, CELLS + 1):
        t = S(f"Temp {n}", _temp_key(n))
        t["n"] = n
        temps.append(t)
    temp_stats = group(
        S("Max temperature", "c2_temp_stats.max_temp_c"),
        S("Min temperature", "c2_temp_stats.min_temp_c"),
        S("Mean temperature", "c2_temp_stats.mean_temp_c"),
        S("Hottest (CAN1 0x114)", "temp_cell_extremes.hottest_temp_c"),
        S("Coolest (CAN1 0x114)", "temp_cell_extremes.coolest_temp_c"),
    )

    a = S("Contactor driver A (CAN2 0x0CF096F3 B0)", "c2_output_state.driver_a_on")
    b = S("Contactor driver B (CAN2 0x0CF096F3 B0)", "c2_output_state.driver_b_on")
    a1 = S("Contactor A driven (CAN1 0x112)", "status_voltages.contactor_a_driven")
    b1 = S("Contactor B driven (CAN1 0x112)", "status_voltages.contactor_b_driven")

    def on(*items):
        for it in items:
            if not it["blank"]:
                return bool(it["value"])
        return None
    a_on, b_on = on(a, a1), on(b, b1)
    if a_on is None or b_on is None:
        summary = "UNKNOWN"
    elif a_on and b_on:
        summary = "CLOSED"
    elif not a_on and not b_on:
        summary = "OPEN"
    else:
        summary = "TRANSITION" + (" (B on, A off: closing or precharge phase)" if b_on else " (A on, B off)")
    contactors = {
        "summary": summary, "a_on": a_on, "b_on": b_on,
        "drivers": group(a, b, a1, b1,
                         S("Output driver B (CAN2 0x0CF096F3 B7)", "c2_output_state.out_driver_b"),
                         S("Output driver A (CAN2 0x0CF096F3 B7)", "c2_output_state.out_driver_a")),
        "precharge": {"direct_signal": False,
                      "note": "The precharge output has no CAN signal. Driver B on with driver A off, with the "
                              "output-side voltage rising, is the precharge phase [?]."},
        "sequence": group(
            S("Substate entered", "state_soc.substate_entered"),
            S("Substate closing or closed", "state_soc.substate_closing_or_closed"),
            S("Substate blocked by fault", "state_soc.substate_blocked_by_fault"),
            S("Output-side voltage (CAN1 0x112)", "status_voltages.output_side_adc_v"),
            S("Output voltage (CAN2 0x0CF095F3)", "c2_output_voltage.output_voltage_v"),
            S("Board-on outputs", "c2_output_state.board_on_outputs"),
        ),
    }

    can1_faults = []
    for byte, bit, name, conf, blocking in FAULT_BITS:
        d = S(name, f"fault_bytes.{name}")
        d.update(name=name, byte=byte, bit=bit, blocking=blocking, active=bool(d["value"]) if not d["blank"] else None)
        can1_faults.append(d)
    latched = []
    for bit, name in LATCHED_WORD_BITS.items():
        d = S(name, f"c2_status_words.latched_{name}")
        d.update(name=name, bit=bit, active=bool(d["value"]) if not d["blank"] else None)
        latched.append(d)
    live = []
    for bit, name in LIVE_WORD_BITS.items():
        d = S(name, f"c2_status_words.live_{name}")
        d.update(name=name, bit=bit, active=bool(d["value"]) if not d["blank"] else None)
        live.append(d)
    faults = {
        "can1_0x120": can1_faults,
        "can1_raw_bytes": [S(f"B{i}", f"fault_bytes.b{i}") for i in range(8)],
        "can1_flags": group(S("Any fault byte non-zero (0x112)", "status_voltages.any_fault_byte_nonzero"),
                            S("Warning word non-zero (0x112)", "status_voltages.warning_word_nonzero")),
        "can2_0x18FF08F3_latched": latched,
        "can2_0x18FF08F3_live": live,
        "can2_words": group(S("Latched word", "c2_status_words.latched_word"),
                            S("Live word", "c2_status_words.live_word"),
                            S("Config byte", "c2_status_words.config_byte")),
    }
    all_bits = can1_faults + latched + live
    faults["active_count"] = sum(1 for f in all_bits if f["active"])
    faults["blocking_active"] = sum(1 for f in can1_faults if f["active"] and f["blocking"])

    top = _top_status(S, bridge, mode, faults, can1_faults, latched)
    cfaults = control_faults(can1_faults, latched, live, top["session"],
                             bool(top["contactor_a_on"] and top["contactor_b_on"]))
    cfaults["seen"] = any(f["active"] is not None for f in all_bits)

    inputs = group(
        S("RB13 asserted (CAN1 0x113 B2 b0)", "state_soc.input_rb13_asserted",
          "key switch or charger input, which is which unknown"),
        S("RB14 asserted (CAN1 0x113 B2 b1)", "state_soc.input_rb14_asserted",
          "key switch or charger input, which is which unknown"),
        S("RB13 low (CAN2 0x0CF096F3)", "c2_output_state.input_rb13_low"),
        S("RB14 low (CAN2 0x0CF096F3)", "c2_output_state.input_rb14_low"),
        S("RB12 low (enable input?)", "c2_output_state.input_rb12_low"),
        S("RB11 low", "c2_output_state.input_rb11_low"),
        S("Charge mode", "state_soc.charge_mode"),
        S("Balancing active", "state_soc.balancing_active"),
    )

    counter_keys = [
        ("Frame counter (0x111)", "pack_current_limits.counter"),
        ("Trip Ah discharge", "trip_ah.trip_ah_discharge"), ("Trip Ah net", "trip_ah.trip_ah_net"),
        ("Trip Ah regen", "trip_ah.trip_ah_regen"), ("Trip Ah charger", "trip_ah.trip_ah_charger"),
        ("Trip Wh discharge", "trip_wh.trip_wh_discharge"), ("Trip Wh net", "trip_wh.trip_wh_net"),
        ("Trip Wh regen", "trip_wh.trip_wh_regen"), ("Trip Wh charger", "trip_wh.trip_wh_charger"),
        ("Lifetime Wh net", "lifetime_wh_net_regen.lifetime_wh_net"),
        ("Lifetime Wh regen", "lifetime_wh_net_regen.lifetime_wh_regen"),
        ("Lifetime Wh charger", "lifetime_wh_charger_discharge.lifetime_wh_charger"),
        ("Lifetime Wh discharge", "lifetime_wh_charger_discharge.lifetime_wh_discharge"),
        ("Lifetime Ah net", "lifetime_ah_net_regen.lifetime_ah_net"),
        ("Lifetime Ah regen", "lifetime_ah_net_regen.lifetime_ah_regen"),
        ("Lifetime Ah charger", "lifetime_ah_charger_discharge.lifetime_ah_charger"),
        ("Lifetime Ah discharge", "lifetime_ah_charger_discharge.lifetime_ah_discharge"),
        ("Overcurrent counter A", "c2_smoothed_current.overcurrent_counter_a"),
        ("Overcurrent counter B", "c2_smoothed_current.overcurrent_counter_b"),
        ("Capacity measurements done", "c2_capacity_counters.measurements_done"),
    ]
    counters = [S(label, key) for label, key in counter_keys]

    host_tx = group(
        _item(state, now, "Keep-alive mode bits (sent)", "host_keepalive.mode_bits", table=state.tx_signals, comms_lost=lost),
        _item(state, now, "Keep-alive enable (sent)", "host_keepalive.enable", table=state.tx_signals, comms_lost=lost),
        _item(state, now, "Keep-alive clear faults (sent)", "host_keepalive.clear_faults", table=state.tx_signals, comms_lost=lost),
        _item(state, now, "Keep-alive extend timeout (sent)", "host_keepalive.extend_timeout", table=state.tx_signals, comms_lost=lost),
    )

    rates = state.rates(now)
    rates.update(rates_extra or {})
    age = None if state.last_frame_t is None else round(max(0.0, now - state.last_frame_t), 2)
    return {
        "mode": mode,  # "live" or "replay"
        "now": round(now, 3),
        "bridge": bridge,
        "controls": controls,
        "replies": replies,
        "headline": headline,
        "power": top["power"],
        "cells": cells,
        "cell_stats": cell_stats,
        "balancing_mask": mask,
        "temps": temps,
        "temp_stats": temp_stats,
        "contactors": contactors,
        "top_status": top,
        "comms": comms,
        "control_faults": cfaults,
        "faults": faults,
        "inputs": inputs,
        "counters": counters,
        "host_tx": host_tx,
        "rates": rates,
        "frames": {"total": state.total_frames, "unknown": state.unknown_frames, "last_frame_age": age,
                   # True once any frame of that bus was received (ever). The page hides the CAN2-only
                   # cell and temperature grids while can2_seen is False (CAN2 not wired).
                   "can1_seen": state.rx_seen(1), "can2_seen": state.rx_seen(2)},
        "status_lines": status.last(n_status),
        "logging": logging or {},
        "replay": replay,
        "notes": {"limits": LIMIT_NOTE},
    }
