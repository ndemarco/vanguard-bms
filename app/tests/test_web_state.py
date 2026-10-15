from vanguard.web.state import PackState, RateMeter, StatusLog
from vanguard.web.view import build_view
from webhelpers import frame


def test_signal_update_keeps_raw_scaled_conf_and_time():
    st = PackState()
    st.update(frame(1, 0x112, {"pack_voltage_v": 52.30}), 10.0)
    r = st.get("status_voltages.pack_voltage_v")
    assert r.raw == 5230 and r.value == 52.3 and r.unit == "V" and r.conf == "C" and r.t == 10.0
    st.update(frame(1, 0x112, {"pack_voltage_v": 51.0}), 11.0)
    r = st.get("status_voltages.pack_voltage_v")
    assert r.raw == 5100 and r.count == 2
    v = st.signal_view("status_voltages.pack_voltage_v", 11.5)
    assert v["age"] == 0.5 and not v["stale"]
    assert st.signal_view("status_voltages.pack_voltage_v", 20)["stale"]
    assert st.signal_view("nope.nothing", 0)["seen"] is False


def test_invalid_temperature_is_marked_invalid():
    st = PackState()
    st.update(frame(2, 0x0CFF01F3, {"temp1_c": None, "temp2_c": 25}), 1.0)
    v = st.signal_view("c2_temps_1_7.temp1_c", 1.0)
    assert v["seen"] and not v["valid"] and v["value"] is None and v["raw"] == 0
    assert st.signal_view("c2_temps_1_7.temp2_c", 1.0)["value"] == 25


def test_unknown_frame_counted():
    from vanguard.wire import Frame
    st = PackState()
    assert st.update(Frame(0, 1, 0x7FF, b"\0"), 0) is False
    assert st.unknown_frames == 1 and st.total_frames == 1


def test_tx_frames_go_to_tx_table():
    st = PackState()
    st.update(frame(1, 0x105, {"enable": True}, direction="tx"), 1.0)
    assert st.signal_view("host_keepalive.enable", 1.0, st.tx_signals)["value"] is True
    assert st.get("host_keepalive.enable") is None


def test_rate_meter():
    m = RateMeter(5.0)
    for i in range(100):
        m.add(i * 0.1)
    assert abs(m.rate(9.9) - 10.0) < 0.5
    assert m.rate(60) == 0.0


def test_view_cells_min_max_and_faults():
    st = PackState()
    mv = [3700 + i for i in range(14)]
    for lo, name in ((1, 0x18FF03F3), (5, 0x18FF04F3), (9, 0x18FF05F3), (13, 0x18FF06F3)):
        vals = {f"cell{n}_mv": mv[n - 1] for n in range(lo, min(lo + 4, 15))}
        st.update(frame(2, name, vals), 1.0)
    st.update(frame(1, 0x120, {"overcurrent_trip": True}), 1.0)
    st.update(frame(2, 0x18FF08F3, {"latched_host_timeout": True}), 1.0)
    st.update(frame(1, 0x111, {}), 1.0)
    v = build_view(state=st, status=StatusLog(), now=1.0, mode="live", bridge={"connected": True}, controls={},
                   replies={})
    assert v["cell_stats"] == {**v["cell_stats"], "min_mv": 3700, "max_mv": 3713, "spread_mv": 13,
                               "min_cell": 1, "max_cell": 14}
    assert [c["is_min"] for c in v["cells"]].count(True) == 1 and v["cells"][13]["is_max"]
    active = [f["name"] for f in v["faults"]["can1_0x120"] if f["active"]]
    assert active == ["overcurrent_trip"]
    assert [f["name"] for f in v["faults"]["can2_0x18FF08F3_latched"] if f["active"]] == ["host_timeout"]
    assert v["faults"]["active_count"] == 2


def test_current_has_no_sign_unverified_note_but_limits_keep_theirs():
    st = PackState()
    v = build_view(state=st, status=StatusLog(), now=0, mode="live", bridge={}, controls={}, replies={})
    assert not any("unverified" in s.get("label_note", "") for s in v["headline"]["current"])
    assert all("unverified" in s["label_note"] for s in v["headline"]["limits"])
    assert "current" not in v["notes"]


def _pw(st, now):
    return build_view(state=st, status=StatusLog(), now=now, mode="live",
                      bridge={"connected": True, "state": "RUNNING CHARGE"}, controls={}, replies={})


def test_current_in_amps_and_power_kw_sign():
    st = PackState()
    st.update(frame(1, 0x112, {"pack_voltage_v": 52.30}), 1.0)
    st.update(frame(1, 0x111, {"pack_current_ma": 17960}), 1.0)
    v = _pw(st, 1.1)
    cur = next(s for s in v["headline"]["current"] if "0x111" in s["label"])
    assert cur["unit"] == "A" and abs(cur["value"] - 17.96) < 1e-9 and cur["raw"] == 17960 and cur["conf"] == "C"
    t = v["top_status"]
    assert t["current"]["unit"] == "A" and abs(t["current"]["value"] - 17.96) < 1e-9
    p = t["power"]
    assert p == v["power"]
    assert p["kw"] == round(52.3 * 17.96 / 1000, 2) == 0.94 and p["direction"] == "in" and p["signed_kw"] > 0
    st.update(frame(1, 0x111, {"pack_current_ma": -40000}), 1.2)
    p = _pw(st, 1.3)["power"]
    assert p["kw"] == round(52.3 * 40 / 1000, 2) and p["direction"] == "out" and p["signed_kw"] < 0


def test_power_blank_when_either_input_stale():
    st = PackState()
    st.update(frame(1, 0x112, {"pack_voltage_v": 52.30}), 1.0)
    assert _pw(st, 1.1)["power"]["kw"] is None  # no current yet
    st.update(frame(1, 0x111, {"pack_current_ma": 10000}), 1.0)
    assert _pw(st, 1.1)["power"]["kw"] == 0.52
    st.update(frame(1, 0x111, {"pack_current_ma": 10000}), 5.0)  # 0x112 now stale
    p = _pw(st, 5.1)["power"]
    assert p["kw"] is None and p["direction"] == ""
    assert _pw(st, 99.0)["power"]["kw"] is None  # comms lost


def _top(st, now, bridge=None, mode="live"):
    bridge = bridge or {"connected": True, "state": "STOPPED"}
    return build_view(state=st, status=StatusLog(), now=now, mode=mode, bridge=bridge, controls={}, replies={})["top_status"]


def _c112(a, b, **kw):
    return frame(1, 0x112, {"contactor_a_driven": a, "contactor_b_driven": b, **kw})


def _contactor_at(a, b, v_out, v_pack, now=1.1):
    st = PackState()
    st.update(_c112(a, b, output_side_adc_v=v_out, pack_voltage_v=v_pack), 1.0)
    return _top(st, now)


def test_actual_contactor_state_every_case():
    from vanguard.web import view
    assert view.CONTACTOR_CLOSED_MATCH_V == 5.0 and view.CONTACTOR_OPEN_BELOW_V == 2.0
    cases = [
        (True, True, 52.0, 52.3, "CLOSED (confirmed)"),
        (True, True, 47.4, 52.3, "CLOSED (confirmed)"),          # 4.9 V apart: still within 5 V
        (True, True, 47.2, 52.3, "COMMANDED CLOSED, NOT CONFIRMED"),  # 5.1 V apart
        (True, True, 0.1, 52.3, "COMMANDED CLOSED, NOT CONFIRMED"),
        (True, True, 1.9, 2.0, "COMMANDED CLOSED, NOT CONFIRMED"),  # matches V_pack but V_out not above 2 V
        (False, False, 0.13, 49.9, "OPEN (confirmed)"),
        (False, False, 2.0, 49.9, "OPEN BUT VOLTAGE ON OUTPUT"),
        (False, False, 54.0, 49.9, "OPEN BUT VOLTAGE ON OUTPUT"),
        (False, True, 26.0, 52.0, "CLOSING"),
        (True, False, 26.0, 52.0, "CLOSING"),
    ]
    for a, b, vo, vp, want in cases:
        assert _contactor_at(a, b, vo, vp)["contactors"] == want, (a, b, vo, vp)
    t = _contactor_at(False, True, 26.0, 52.0)
    assert "50 % of V_pack" in t["contactor_note"]
    assert t["contactor_detail"] == "drivers A off, B on; V_out 26.00 V, V_pack 52.00 V"
    assert "charger" in _contactor_at(False, False, 54.0, 49.9)["contactor_note"]


def test_actual_contactor_state_no_data_and_stale():
    st = PackState()
    t = _top(st, 0.0)
    assert t["contactors"] == "NO DATA" and "never received" in t["contactor_detail"]
    st.update(_c112(True, True, output_side_adc_v=52.0, pack_voltage_v=52.3), 1.0)
    assert _top(st, 1.5)["contactors"] == "CLOSED (confirmed)"
    t = _top(st, 1.6)  # 0.6 s old: 0x112 is a 50 ms frame, stale after 0.5 s
    assert t["contactors"] == "NO DATA" and t["contactor_a_on"] is None
    st2 = PackState()  # CAN driver data alone must not count as CAN11 0x112
    st2.update(frame(2, 0x0CF096F3, {"driver_a_on": True, "driver_b_on": True}), 1.0)
    assert _top(st2, 1.1)["contactors"] == "NO DATA"
    st.update(_c112(True, True, output_side_adc_v=52.0, pack_voltage_v=52.3), 2.0)
    t = _top(st, 2.1, bridge={"connected": False, "state": "RUNNING CHARGE"})  # bridge gone: no data
    assert t["contactors"] == "NO DATA"


def test_top_session_states():
    st = PackState()
    s = lambda **b: _top(st, 0.0, bridge=b)["session"]  # noqa: E731
    assert s(connected=False, state="RUNNING DISCHARGE") == "BRIDGE DISCONNECTED"
    assert s(connected=True, state="STOPPED") == "STOPPED"
    assert s(connected=True, state="RUNNING DISCHARGE") == "RUNNING DISCHARGE"
    assert s(connected=True, state="RUNNING CHARGE") == "RUNNING CHARGE"
    assert s(connected=True, state=None) == "UNKNOWN"
    assert _top(st, 0.0, mode="replay", bridge={"connected": False})["session"] == "REPLAY"


def test_top_faults_hints_and_measurements():
    st = PackState()
    assert _top(st, 0.0)["fault_count"] is None
    st.update(frame(1, 0x120, {"keepalive_timeout": True}), 1.0)
    st.update(_c112(True, True, pack_voltage_v=52.3), 1.0)
    t = _top(st, 1.1)
    assert t["fault_count"] >= 1 and any("Clear faults" in h for h in t["fault_hints"])
    assert t["pack_voltage"]["value"] == 52.3 and "current_note" not in t
    assert _top(st, 99.0)["pack_voltage"]["value"] is None
    assert _top(st, 99.0)["fault_count"] is None


def _live(st, now, connected=True):
    return build_view(state=st, status=StatusLog(), now=now, mode="live",
                      bridge={"connected": connected, "state": "RUNNING CHARGE"}, controls={}, replies={})


def test_comms_lost_blanks_every_value():
    st = PackState()
    st.update(_c112(True, True, output_side_adc_v=52.0, pack_voltage_v=52.3), 1.0)
    st.update(frame(1, 0x111, {"discharge_limit_a": 240}), 1.0)
    st.update(frame(1, 0x113, {"soc_pct": 50}), 1.0)
    st.update(frame(1, 0x105, {"enable": True}, direction="tx"), 1.0)
    st.update(frame(2, 0x0CFF01F3, {"temp1_c": 25}), 1.0)
    v = _live(st, 1.2)
    assert v["comms"] == {"ok": True, "reason": ""}
    soc = v["headline"]["soc"][0]
    assert soc["value"] == 50 and not soc["blank"]

    def values(v):
        out = []
        for g in v["headline"].values():
            out += g
        out += v["temps"] + v["temp_stats"] + v["inputs"] + v["counters"] + v["host_tx"]
        out += v["contactors"]["drivers"] + v["contactors"]["sequence"]
        return out

    for v, why in ((_live(st, 1.6), "no CAN11 0x112 or 0x111 for 0.6 s"), (_live(st, 1.2, connected=False), "bridge disconnected")):
        assert v["comms"] == {"ok": False, "reason": why}
        for it in values(v):
            assert it["blank"] and it["value"] is None and it["raw"] is None, it["key"]
        soc = v["headline"]["soc"][0]
        assert soc["seen"] and soc["age"] is not None  # still known to have been seen, so never hidden
        top = v["top_status"]
        assert top["contactors"] == "NO DATA" and top["pack_voltage"]["value"] is None and top["fault_count"] is None
        assert all(f["active"] is None for f in v["faults"]["can1_0x120"])


def test_stale_signal_blanks_individually():
    st = PackState()
    st.update(frame(1, 0x113, {"soc_pct": 50}), 1.0)  # 100 ms frame: stale after 2 s
    for t in range(2, 7):  # keep CAN11 comms alive with 0x112/0x111
        st.update(_c112(False, False, pack_voltage_v=50.0), float(t))
        st.update(frame(1, 0x111, {}), float(t))
    v = _live(st, 6.1)
    assert v["comms"]["ok"]
    soc = v["headline"]["soc"][0]
    assert soc["stale"] and soc["blank"] and soc["value"] is None and soc["seen"]
    pv = v["headline"]["pack_voltage"][0]
    assert not pv["blank"] and pv["value"] == 50.0


def _view(frames, bridge_state="STOPPED"):
    st = PackState()
    st.update(frame(1, 0x111, {}), 1.0)  # CAN11 comms alive
    for i, f in enumerate(frames):
        st.update(f, 1.0)
    return build_view(state=st, status=StatusLog(), now=1.0, mode="live",
                      bridge={"connected": True, "state": bridge_state}, controls={"enabled": True}, replies={})


def _contactors(on):
    return frame(1, 0x112, {"contactor_a_driven": on, "contactor_b_driven": on})


def test_control_faults_classification_and_tags():
    v = _view([frame(1, 0x120, {"keepalive_timeout": True, "cell_overvoltage_warning": True,
                                "temperature_spread_warning": True, "overtemperature": True}),
               frame(2, 0x18FF08F3, {"latched_host_timeout": True, "live_low_cell_warning": True})])
    cf = v["control_faults"]
    by = {(i["name"], i["source"].split(" ")[0]): i for i in cf["items"]}
    assert by[("keepalive_timeout", "CAN11")]["blocking"] and by[("keepalive_timeout", "CAN11")]["wake"]
    assert not by[("cell_overvoltage_warning", "CAN11")]["blocking"]
    assert not by[("temperature_spread_warning", "CAN11")]["blocking"]
    assert by[("overtemperature", "CAN11")]["blocking"] and not by[("overtemperature", "CAN11")]["wake"]
    assert by[("host_timeout", "CAN")]["state"] == "latched" and by[("host_timeout", "CAN")]["blocking"]
    assert by[("low_cell_warning", "CAN")]["state"] == "live" and not by[("low_cell_warning", "CAN")]["blocking"]
    assert cf["blocking_count"] == 3 and cf["count"] == 6


def test_control_faults_none():
    v = _view([frame(1, 0x120, {})])
    cf = v["control_faults"]
    assert cf["items"] == [] and cf["instruction_kind"] == "none" and cf["seen"]


def test_control_faults_instruction_selection():
    wake = frame(1, 0x120, {"keepalive_timeout": True})
    cf = _view([wake], "STOPPED")["control_faults"]
    assert cf["instruction"].startswith("Faults are latched. Start, then press Clear faults")
    assert "normal right after a wake" in cf["wake_note"] and not cf["emphasise_clear"]
    cf = _view([wake, _contactors(False)], "RUNNING CHARGE")["control_faults"]
    assert cf["instruction"] == "Contactors are waiting on faults: press Clear faults" and cf["emphasise_clear"]
    cf = _view([wake, _contactors(True)], "RUNNING CHARGE")["control_faults"]
    assert cf["instruction"] == "" and not cf["emphasise_clear"]
    cf = _view([frame(1, 0x120, {"cell_undervoltage_warning": True})], "STOPPED")["control_faults"]
    assert cf["instruction_kind"] == "none" and cf["count"] == 1 and cf["blocking_count"] == 0
    cf = _view([frame(1, 0x120, {"overtemperature": True})], "STOPPED")["control_faults"]
    assert cf["wake_note"] == ""


def test_view_reports_which_buses_were_ever_received():
    st = PackState()
    v = build_view(state=st, status=StatusLog(), now=0, mode="live", bridge={}, controls={}, replies={})
    assert v["frames"]["can1_seen"] is False and v["frames"]["can2_seen"] is False
    st.update(frame(1, 0x105, {"enable": True}, direction="tx"), 1.0)  # our own keep-alive is not pack traffic
    st.update(frame(1, 0x112, {"pack_voltage_v": 52.0}), 1.0)
    v = build_view(state=st, status=StatusLog(), now=100.0, mode="live", bridge={}, controls={}, replies={})
    assert v["frames"]["can1_seen"] is True and v["frames"]["can2_seen"] is False
    # a seen signal stays seen (and is marked stale) long after its last frame; the page never hides it
    pv = v["headline"]["pack_voltage"][0]
    assert pv["seen"] and pv["stale"]
    st.update(frame(2, 0x0CFF01F3, {"temp1_c": 25}), 101.0)
    v = build_view(state=st, status=StatusLog(), now=101.0, mode="live", bridge={}, controls={}, replies={})
    assert v["frames"]["can2_seen"] is True
