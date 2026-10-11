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
    v = build_view(state=st, status=StatusLog(), now=1.0, mode="live", bridge={}, controls={}, replies={})
    assert v["cell_stats"] == {**v["cell_stats"], "min_mv": 3700, "max_mv": 3713, "spread_mv": 13,
                               "min_cell": 1, "max_cell": 14}
    assert [c["is_min"] for c in v["cells"]].count(True) == 1 and v["cells"][13]["is_max"]
    active = [f["name"] for f in v["faults"]["can1_0x120"] if f["active"]]
    assert active == ["overcurrent_trip"]
    assert [f["name"] for f in v["faults"]["can2_0x18FF08F3_latched"] if f["active"]] == ["host_timeout"]
    assert v["faults"]["active_count"] == 2


def test_current_has_sign_note_and_no_charge_discharge_logic():
    st = PackState()
    v = build_view(state=st, status=StatusLog(), now=0, mode="live", bridge={}, controls={}, replies={})
    assert all("sign unverified" in s["label_note"] for s in v["headline"]["current"])
