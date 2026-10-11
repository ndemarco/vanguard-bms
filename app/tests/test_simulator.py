"""Simulator behaviour tests. All time is simulated; nothing sleeps."""

import io
import statistics
from collections import defaultdict

import pytest

from vanguard.decoder import decode
from vanguard.frames import CAN1, CAN2, SPEC_BY_KEY
from vanguard.simulator import PackSimulator, main, replay
from vanguard.wire import Frame, frame_to_json, parse_line

KEEPALIVE = bytes([0x00, 0x11, 0, 0, 0, 0, 0, 0])
GATED = {0x510, 0x512, 0x513, 0x51D}


def run(sim, seconds, keepalive=KEEPALIVE, every_us=100_000):
    """Advance in 10 ms steps, sending the keep-alive every ``every_us`` (None = silent)."""
    frames = []
    steps = int(seconds * 100)
    for i in range(steps):
        if keepalive is not None and (i * 10_000) % every_us == 0:
            sim.receive(CAN1, 0x105, keepalive)
        frames += sim.advance(10_000)
    return frames


def last(frames, bus, can_id):
    return next(f for f in reversed(frames) if f.bus == bus and f.can_id == can_id)


def test_contactor_sequence_closes_with_keepalive():
    sim = PackSimulator(seed=3)
    frames = run(sim, 2.0)
    assert sim.contactors_closed
    f = decode(1, 0x112, last(frames, 1, 0x112).data)
    assert f.value("contactor_a_driven") and f.value("contactor_b_driven")
    # B closes first
    first_b = next(x for x in frames if x.can_id == 0x112 and decode(1, 0x112, x.data).value("contactor_b_driven"))
    first_a = next(x for x in frames if x.can_id == 0x112 and decode(1, 0x112, x.data).value("contactor_a_driven"))
    assert first_b.ts_us <= first_a.ts_us


def test_no_close_without_enable_bit_or_input():
    sim = PackSimulator()
    run(sim, 1.0, keepalive=bytes([0, 0x01, 0, 0, 0, 0, 0, 0]))  # D1 bit 4 clear
    assert sim.phase == "open"
    sim2 = PackSimulator(enable_asserted=False)
    run(sim2, 1.0)
    assert sim2.phase == "open"


def test_no_close_before_first_keepalive():
    sim = PackSimulator()
    sim.advance(3_000_000)
    assert sim.phase == "open" and sim.mode == 0x10


def test_keepalive_timeout_opens_contactors_and_latches_fault():
    sim = PackSimulator(seed=2)
    run(sim, 2.0)
    assert sim.contactors_closed
    silence_start = sim.now_us
    frames = run(sim, 1.6, keepalive=None)
    b_off = next(x.ts_us for x in frames if x.can_id == 0x112 and not decode(1, 0x112, x.data).value("contactor_b_driven"))
    assert 0.95e6 <= b_off - silence_start <= 1.15e6
    a_off = next(x.ts_us for x in frames if x.can_id == 0x112 and not decode(1, 0x112, x.data).value("contactor_a_driven"))
    assert a_off - silence_start <= 1.4e6
    assert sim.phase == "open"
    fault = decode(1, 0x120, last(frames, 1, 0x120).data)
    assert fault.value("keepalive_timeout") is True
    words = decode(2, 0x18FF08F3, last(frames, 2, 0x18FF08F3).data)
    assert words.value("latched_host_timeout") is True and words.value("substate") == "fault"


def test_keepalive_just_inside_timeout_keeps_contactors_closed():
    sim = PackSimulator()
    run(sim, 2.0)
    run(sim, 3.0, every_us=900_000)  # one frame every 0.9 s stays inside the 1 s timeout
    assert sim.contactors_closed and not any(sim.fault)


def test_fault_stays_latched_until_clear_then_recloses():
    sim = PackSimulator()
    run(sim, 2.0)
    run(sim, 1.6, keepalive=None)
    run(sim, 1.0)  # host is back, fault still latched
    assert sim.phase == "open" and sim.fault[1] & 0x04
    sim.receive(CAN1, 0x105, bytes([0, 0x13, 0, 0, 0, 0, 0, 0]))  # D1 bit 1 = clear faults
    assert not any(sim.fault) and sim.latched_word == 0
    run(sim, 2.0)
    assert sim.contactors_closed


def test_clear_requests_closer_than_100ms_ignored():
    sim = PackSimulator()
    run(sim, 0.5)
    sim.receive(CAN1, 0x105, bytes([0, 0x13, 0, 0, 0, 0, 0, 0]))
    sim.advance(10_000)
    sim._set_fault("overcurrent_trip")
    sim.receive(CAN1, 0x105, bytes([0, 0x13, 0, 0, 0, 0, 0, 0]))
    assert sim.fault[3] & 0x80
    sim.advance(100_000)
    sim.receive(CAN1, 0x105, bytes([0, 0x13, 0, 0, 0, 0, 0, 0]))
    assert not sim.fault[3] & 0x80


def test_extended_timeout_bit():
    sim = PackSimulator()
    run(sim, 2.0, keepalive=bytes([0, 0x15, 0, 0, 0, 0, 0, 0]))
    run(sim, 30.0, keepalive=None)
    assert sim.contactors_closed  # D1 bit 2: about 20 minutes


def test_gated_frames_only_with_d1_bit0():
    sim = PackSimulator()
    frames = run(sim, 4.0, keepalive=bytes([0, 0x10, 0, 0, 0, 0, 0, 0]))
    assert not any(f.bus == 1 and f.can_id in GATED for f in frames)
    assert any(f.bus == 1 and f.can_id == 0x517 for f in frames)
    t_on = sim.now_us
    frames = run(sim, 3.0, keepalive=KEEPALIVE)
    seen = {f.can_id for f in frames if f.bus == 1 and f.can_id in GATED}
    assert seen == GATED
    frames = run(sim, 3.0, keepalive=bytes([0, 0x10, 0, 0, 0, 0, 0, 0]))
    assert not any(f.bus == 1 and f.can_id in GATED and f.ts_us > t_on + 3_100_000 for f in frames)


def test_periods_within_tolerance():
    sim = PackSimulator(seed=5)
    frames = run(sim, 12.0)
    by = defaultdict(list)
    for f in frames:
        by[(f.bus, f.can_id)].append(f.ts_us)
    checked = 0
    for (bus, cid), ts in by.items():
        spec = SPEC_BY_KEY[(bus, cid)]
        if not spec.period_ms or len(ts) < 3:
            continue
        nominal = spec.period_ms * 1000
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        assert statistics.mean(gaps) == pytest.approx(nominal, abs=4_000 + 0.02 * nominal), hex(cid)
        assert min(gaps) >= nominal - 2_000, hex(cid)
        assert max(gaps) <= nominal + 60_000, hex(cid)
        checked += 1
    assert checked >= 40


def test_first_copy_one_period_after_boot_and_all_documented_frames_present():
    sim = PackSimulator()
    frames = run(sim, 12.0)
    ids = {(f.bus, f.can_id) for f in frames}
    expected = {k for k, s in SPEC_BY_KEY.items() if s.direction == "pack_tx" and s.period_ms}
    assert expected <= ids
    first = {}
    for f in frames:
        first.setdefault((f.bus, f.can_id), f.ts_us)
    assert first[(1, 0x111)] >= 50_000
    assert first[(2, 0x0CFF20F3)] >= 100_000


def test_all_emitted_frames_decode_and_have_dlc8():
    sim = PackSimulator(seed=9)
    frames = run(sim, 8.0)
    for f in frames:
        assert len(f.data) == 8
        d = decode(f.bus, f.can_id, f.data)
        assert d is not None, hex(f.can_id)
        assert not d.notes or "fault" in " ".join(d.notes), (hex(f.can_id), d.notes)


def test_plausible_values():
    sim = PackSimulator(seed=4)
    frames = run(sim, 3.0)
    cells = [decode(2, 0x18FF03F3 + 0x100 * (k), last(frames, 2, 0x18FF03F3 + 0x100 * k).data) for k in range(4)]
    mv = [c.value(f"cell{n}_mv") for c in cells for n in range(4 * cells.index(c) + 1, 4 * cells.index(c) + 5)
          if f"cell{n}_mv" in c.values()]
    assert len(mv) == 14
    assert all(3600 <= v <= 3800 for v in mv) and max(mv) > min(mv)
    temps = [decode(2, 0x0CFF01F3, last(frames, 2, 0x0CFF01F3).data), decode(2, 0x0CFF02F3, last(frames, 2, 0x0CFF02F3).data)]
    assert all(15 <= s.value <= 35 for t in temps for s in t.signals if s.name.startswith("temp"))
    soc = decode(2, 0x0CFF06F3, last(frames, 2, 0x0CFF06F3).data)
    assert soc.value("soc_pct") == 45
    cur = decode(2, 0x0CF090F3, last(frames, 2, 0x0CF090F3).data)
    assert cur.value("current_a") < 0  # drive path, discharging (sign convention [I])
    pv = decode(2, 0x0CF090F3, last(frames, 2, 0x0CF090F3).data).value("pack_voltage_v")
    assert pv == pytest.approx(sum(mv) / 1000, abs=0.06)
    c1 = decode(1, 0x112, last(frames, 1, 0x112).data)
    assert c1.value("pack_voltage_v") == pytest.approx(sum(mv) / 1000, abs=0.01)


def test_defective_0cf094f3_bytes_are_stale_copy_of_0cff21f3():
    sim = PackSimulator()
    frames = run(sim, 1.0)
    f94 = last(frames, 2, 0x0CF094F3)
    older21 = [f for f in frames if f.can_id == 0x0CFF21F3 and f.ts_us < f94.ts_us][-1]
    assert f94.data[:4] == older21.data[:4]


def test_charge_path_and_balancing_flag():
    sim = PackSimulator()
    sim.set_cells_mv([3900] * 13 + [3850])
    run(sim, 2.0, keepalive=bytes([0x01, 0x11, 0, 0, 0, 0, 0, 0]))
    assert sim.contactors_closed and sim.mode == 4
    fr = sim.advance(200_000)
    s = decode(1, 0x113, last(fr, 1, 0x113).data)
    assert s.value("charge_mode") and s.value("balancing_active")
    assert sim.balancing_mask == (1 << 13) - 1  # cells 1-13 are 50 mV above cell 14
    fr = run(sim, 1.1, keepalive=bytes([0x01, 0x11, 0, 0, 0, 0, 0, 0]))
    mask = decode(2, 0x0CF093F3, last(fr, 2, 0x0CF093F3).data)
    assert mask.value("balancing_cell1") and not mask.value("balancing_cell14")


def test_overvoltage_trips_and_blocks():
    sim = PackSimulator()
    sim.set_cells_mv([4260] + [3800] * 13)
    run(sim, 1.0)
    assert sim.phase == "open" and sim.fault[1] & 0x08


def test_enable_released_opens_contactors():
    sim = PackSimulator()
    run(sim, 2.0)
    sim.set_enable(False)
    run(sim, 1.0)
    assert sim.phase == "open" and sim.fault[2] & 0x08


def test_aux_outputs_echo_and_clear_on_timeout():
    sim = PackSimulator()
    ka = bytes([0x54, 0x11, 0, 0, 0, 0, 0, 0])
    frames = run(sim, 1.0, keepalive=ka)
    e = decode(1, 0x113, last(frames, 1, 0x113).data)
    assert e.value("aux_echo_bit2") and e.value("aux_echo_bit4") and e.value("aux_echo_bit6")
    frames = run(sim, 1.5, keepalive=None)
    e = decode(1, 0x113, last(frames, 1, 0x113).data)
    assert not (e.value("aux_echo_bit2") or e.value("aux_echo_bit4") or e.value("aux_echo_bit6"))


def test_sleep_command_after_500ms():
    sim = PackSimulator()
    run(sim, 1.0)
    t0 = sim.now_us
    sim.receive(CAN1, 0x515, bytes([0x20, 0, 0, 0, 0, 0, 0, 0]))
    frames = sim.advance(400_000)
    assert not sim.sleeping and frames
    frames = sim.advance(200_000)
    assert sim.sleeping
    assert sim.phase == "open"
    tail = sim.advance(5_000_000)
    assert tail == []
    # final one-shot frames went out at sleep entry
    assert any(f.bus == 1 and f.can_id == 0x120 for f in frames)
    assert any(f.bus == 2 and f.can_id == 0x18FF08F3 for f in frames)
    assert sim.now_us > t0


def test_wake_on_can1_activity():
    sim = PackSimulator()
    run(sim, 0.5)
    sim.receive(CAN1, 0x515, bytes([0x20] + [0] * 7))
    sim.advance(700_000)
    assert sim.sleeping
    sim.receive(CAN1, 0x105, KEEPALIVE)
    frames = run(sim, 2.5)
    assert not sim.sleeping and frames and sim.contactors_closed


def test_silent_host_eventually_sleeps():
    sim = PackSimulator()
    run(sim, 1.0)
    sim.advance(302_000_000)
    assert sim.sleeping


def test_service_frame_on_can2_opens_contactors_until_command_00():
    sim = PackSimulator()
    run(sim, 2.0)
    sim.receive(CAN2, 0x0CFF00F3, bytes([0xC9] + [0] * 7))
    frames = run(sim, 1.0)
    assert sim.phase == "open" and sim.mode == 8
    assert decode(2, 0x18FF08F3, last(frames, 2, 0x18FF08F3).data).value("mode_raw") == "service"
    sim.receive(CAN2, 0x0CFF00F3, bytes(8))
    sim.set_enable(True)
    run(sim, 2.0)
    assert sim.contactors_closed


def test_sdo_identity_read_and_write_ignored():
    sim = PackSimulator()
    sim.receive(CAN1, 0x614, bytes.fromhex("4018100100000000"))
    out = sim.advance(10_000)
    reply = next(f for f in out if f.can_id == 0x594)
    assert reply.data == bytes.fromhex("4318100101000000")
    sim.receive(CAN1, 0x614, bytes.fromhex("2351 1F01 00000000".replace(" ", "")))
    assert not any(f.can_id == 0x594 for f in sim.advance(50_000))


def test_charger_config_frame_sets_warning_and_config_byte():
    sim = PackSimulator()
    sim.receive(CAN2, 0x0CFFBC49, bytes([0, 0, 0, 0, 4, 0, 0, 0]))
    frames = sim.advance(600_000)
    w = decode(2, 0x18FF08F3, last(frames, 2, 0x18FF08F3).data)
    assert w.value("config_byte") == 4 and w.value("live_config_byte_not_3_or_5")
    sim.receive(CAN2, 0x0CFFBC49, bytes([0, 0, 0, 0, 3, 0, 0, 0]))
    frames = sim.advance(600_000)
    assert not decode(2, 0x18FF08F3, last(frames, 2, 0x18FF08F3).data).value("live_config_byte_not_3_or_5")


def test_deterministic_for_same_seed():
    a = run(PackSimulator(seed=7), 3.0)
    b = run(PackSimulator(seed=7), 3.0)
    assert a == b
    assert a != run(PackSimulator(seed=8), 3.0)


# Command line and replay ---------------------------------------------------------------

def test_cli_output_is_wire_format():
    buf = io.StringIO()
    assert main(["--duration", "1.5"], out=buf) == 0
    lines = buf.getvalue().splitlines()
    assert lines[0] == "S simulator start"
    parsed = [parse_line(x) for x in lines]
    frames = [p for p in parsed if isinstance(p, Frame)]
    assert len(frames) > 100
    assert [f.ts_us for f in frames] == sorted(f.ts_us for f in frames)


def test_cli_no_keepalive_never_closes():
    buf = io.StringIO()
    main(["--duration", "2", "--no-keepalive"], out=buf)
    frames = [p for p in map(parse_line, buf.getvalue().splitlines()) if isinstance(p, Frame)]
    assert not any(decode(1, 0x112, f.data).value("contactor_b_driven") for f in frames if f.bus == 1 and f.can_id == 0x112)


def test_replay_mode(tmp_path):
    log = tmp_path / "log.jsonl"
    src = [Frame(1000, 1, 0x105, KEEPALIVE, "tx"), Frame(2000, 1, 0x111, bytes(8), "rx"),
           Frame(3000, 2, 0x18FF03F3, bytes(8), "rx")]
    log.write_text("\n".join(frame_to_json(f) for f in src) + "\n")
    buf = io.StringIO()
    main(["--replay", str(log)], out=buf)
    lines = buf.getvalue().splitlines()
    assert lines[0].startswith("S replay")
    assert lines[1:] == ["R 1 105 8 0011000000000000 1000", "R 1 111 8 0000000000000000 2000",
                         "R 2 18FF03F3 8 0000000000000000 3000"]
    buf = io.StringIO()
    main(["--replay", str(log), "--rx-only"], out=buf)
    assert len(buf.getvalue().splitlines()) == 3


def test_replay_pacing_uses_injected_clock():
    now = [0.0]
    slept = []
    frames = [Frame(0, 1, 1, b""), Frame(1_000_000, 1, 1, b""), Frame(3_000_000, 1, 1, b"")]

    def sleep(s):
        slept.append(round(s, 6))
        now[0] += s

    out = list(replay(frames, speed=2.0, sleep=sleep, clock=lambda: now[0]))
    assert out == frames and slept == [0.5, 1.0]
