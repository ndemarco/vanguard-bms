"""Build the JSON snapshot the page renders from the state model and client status.

All layout knowledge (which decoded signal goes where on the page) lives here, so the
JavaScript only draws what it is given. Every value carries its raw integer, unit and
confidence tag. Nothing here derives anything from the sign of a current.
"""

from __future__ import annotations

from typing import List, Optional

from ..frames import FAULT_BITS, LATCHED_WORD_BITS, LIVE_WORD_BITS
from .state import PackState, StatusLog

CELLS = 14
SIGN_NOTE = "sign unverified (positive = charge is inferred only)"
LIMIT_NOTE = "direction and sign unverified"


def _cell_key(n: int) -> str:
    lo = ((n - 1) // 4) * 4 + 1
    frame = "c2_cells_13_14" if n >= 13 else f"c2_cells_{lo}_{lo + 3}"
    return f"{frame}.cell{n}_mv"


def _temp_key(n: int) -> str:
    frame = "c2_temps_1_7" if n <= 7 else "c2_temps_8_14"
    return f"{frame}.temp{n}_c"


def _item(state: PackState, now: float, label: str, key: str, note: str = "", table: Optional[dict] = None) -> dict:
    d = state.signal_view(key, now, table)
    d["label"] = label
    if note:
        d["label_note"] = note
    return d


def build_view(*, state: PackState, status: StatusLog, now: float, mode: str, bridge: dict,
               controls: dict, replies: dict, rates_extra: Optional[dict] = None, logging: Optional[dict] = None,
               replay: Optional[dict] = None, n_status: int = 40) -> dict:
    S = lambda label, key, note="": _item(state, now, label, key, note)  # noqa: E731
    group = lambda *items: list(items)  # noqa: E731

    headline = {
        "pack_voltage": group(
            S("Pack voltage (CAN1 0x112)", "status_voltages.pack_voltage_v"),
            S("Pack voltage (CAN2 0x0CF090F3)", "c2_pack_voltage_current.pack_voltage_v"),
            S("Sum of cells (CAN2 0x0CFF20F3)", "c2_summary.cell_sum_mv"),
        ),
        "current": group(
            S("Current (CAN2 0x0CFF20F3)", "c2_summary.current_a", SIGN_NOTE),
            S("Current (CAN2 0x0CF090F3)", "c2_pack_voltage_current.current_a", SIGN_NOTE),
            S("Smoothed current", "c2_smoothed_current.smoothed_current_a", SIGN_NOTE),
            S("Mean current", "c2_smoothed_current.mean_current_a", SIGN_NOTE),
            S("Current (CAN1 0x111, mA)", "pack_current_limits.pack_current_ma", SIGN_NOTE),
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
    valid = [c for c in cells if c["seen"] and c["valid"] and isinstance(c["value"], (int, float))]
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
            if it["seen"] and not it["stale"]:
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
        d.update(name=name, byte=byte, bit=bit, blocking=blocking, active=bool(d["value"]) if d["seen"] else None)
        can1_faults.append(d)
    latched = []
    for bit, name in LATCHED_WORD_BITS.items():
        d = S(name, f"c2_status_words.latched_{name}")
        d.update(name=name, bit=bit, active=bool(d["value"]) if d["seen"] else None)
        latched.append(d)
    live = []
    for bit, name in LIVE_WORD_BITS.items():
        d = S(name, f"c2_status_words.live_{name}")
        d.update(name=name, bit=bit, active=bool(d["value"]) if d["seen"] else None)
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
        _item(state, now, "Keep-alive mode bits (sent)", "host_keepalive.mode_bits", table=state.tx_signals),
        _item(state, now, "Keep-alive enable (sent)", "host_keepalive.enable", table=state.tx_signals),
        _item(state, now, "Keep-alive clear faults (sent)", "host_keepalive.clear_faults", table=state.tx_signals),
        _item(state, now, "Keep-alive extend timeout (sent)", "host_keepalive.extend_timeout", table=state.tx_signals),
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
        "cells": cells,
        "cell_stats": cell_stats,
        "balancing_mask": mask,
        "temps": temps,
        "temp_stats": temp_stats,
        "contactors": contactors,
        "faults": faults,
        "inputs": inputs,
        "counters": counters,
        "host_tx": host_tx,
        "rates": rates,
        "frames": {"total": state.total_frames, "unknown": state.unknown_frames, "last_frame_age": age},
        "status_lines": status.last(n_status),
        "logging": logging or {},
        "replay": replay,
        "notes": {"current": SIGN_NOTE, "limits": LIMIT_NOTE},
    }
