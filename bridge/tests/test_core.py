import pytest

from vanguard_bridge import core
from vanguard_bridge.core import RUNNING, STOPPED, Bridge

ALL_CMDS = sorted(core.COMMANDS)


def ka_frames(h):
    return [f for f in h.writer.frames() if f.can_id == 0x105]


def test_power_on_state_is_stopped_and_silent(h):
    assert h.bridge.state == STOPPED
    h.run(10_000_000)
    assert h.writer.raw == []


@pytest.mark.parametrize("line", ["HB", "CLEAR", "STOP", "hb", "start discharge", "START", "", "SLEEP 2"])
def test_nothing_transmitted_before_start(h, line):
    h.run(3_000_000, every={100_000: line})
    assert h.writer.raw == []
    assert h.bridge.state == STOPPED


def test_keepalive_cadence_100ms_and_payload(h):
    h.cmd("START DISCHARGE")
    h.run(5_000_000, every={250_000: "HB"})
    times = h.writer.times
    assert times[0] == 0
    assert all(b - a == 100_000 for a, b in zip(times, times[1:]))
    assert len(times) == 51
    assert all(f.data == bytes([0x00, 0x11, 0, 0, 0, 0, 0, 0]) for f in h.writer.frames())
    assert h.bridge.state == RUNNING


def test_start_charge_payload(h):
    h.cmd("START CHARGE")
    assert h.writer.frames()[0].data == bytes([0x01, 0x11, 0, 0, 0, 0, 0, 0])


def test_t_lines_for_every_transmitted_frame(h):
    h.cmd("START DISCHARGE")
    h.run(300_000, every={250_000: "HB"})
    t = [ln for ln in h.lines if ln.startswith("T ")]
    assert len(t) == len(h.writer.raw) == 4
    assert t[0] == "T 1 105 8 0011000000000000 1000000000"


def test_watchdog_fires_at_1s_and_transmission_stops(h):
    h.cmd("START DISCHARGE")
    h.run(3_000_000)
    assert h.bridge.state == STOPPED
    assert h.writer.times == [i * 100_000 for i in range(10)]  # 0..900 ms, nothing at or after 1 s
    assert any("STOP watchdog" in ln for ln in h.lines)


def test_watchdog_boundary(h):
    h.cmd("START DISCHARGE")
    h.now = h.t0 + 999_999
    h.bridge.tick(h.now)
    assert h.bridge.state == RUNNING
    h.now = h.t0 + 1_000_000
    h.bridge.tick(h.now)
    assert h.bridge.state == STOPPED


def test_watchdog_with_coarse_ticks(h):
    h.cmd("START DISCHARGE")
    h.run(5_000_000, step_us=370_000)
    assert h.bridge.state == STOPPED
    assert max(h.writer.times) < 1_000_000


def test_hb_every_250ms_keeps_alive(h):
    h.cmd("START DISCHARGE")
    h.run(30_000_000, every={250_000: "HB"})
    assert h.bridge.state == RUNNING
    assert len(ka_frames(h)) == 301


@pytest.mark.parametrize("line", ["CLEAR", "STOP ", "SLEEP 1", "SLEEP 3", "START CHARGE", "hb", "HB ", " HB",
                                  "HBHB", "HB\r", "", "garbage", "SLEEP 2"])
def test_non_hb_commands_do_not_feed_watchdog(h, line):
    h.cmd("START DISCHARGE")
    h.run(1_000_000, every={50_000: line})  # after STOPPED, SLEEP / START are legitimately accepted
    assert h.bridge.state == STOPPED
    assert "STOP watchdog: no HB or START for 1 s" in h.log
    assert [t for f, t in zip(h.writer.frames(), h.writer.times) if f.can_id == 0x105][-1] == 900_000


def test_same_mode_start_feeds_watchdog(h):
    h.cmd("START DISCHARGE")
    h.run(3_000_000, every={500_000: "START DISCHARGE"})
    assert h.bridge.state == RUNNING
    assert h.writer.times[-1] == 3_000_000


def test_mode_change_while_running_refused(h):
    h.cmd("START DISCHARGE")
    h.cmd("START CHARGE")
    assert h.bridge.mode == "DISCHARGE"
    assert any("ERR START CHARGE refused" in ln for ln in h.lines)


def test_disconnect_stops_immediately(h):
    h.cmd("START DISCHARGE")
    h.run(500_000, every={250_000: "HB"})
    n = len(h.writer.raw)
    h.bridge.disconnect(h.now)
    assert h.bridge.state == STOPPED
    h.run(5_000_000)
    assert len(h.writer.raw) == n


def test_no_auto_resume_after_watchdog(h):
    h.cmd("START DISCHARGE")
    h.run(1_500_000)
    n = len(h.writer.raw)
    h.run(5_000_000, every={100_000: "HB", 300_000: "CLEAR"})
    assert h.bridge.state == STOPPED and len(h.writer.raw) == n
    h.cmd("START CHARGE")
    assert h.bridge.state == RUNNING and len(h.writer.raw) == n + 1


def test_stop_command(h):
    h.cmd("START DISCHARGE")
    h.run(200_000, every={100_000: "HB"})
    h.cmd("STOP")
    n = len(h.writer.raw)
    h.run(2_000_000, every={100_000: "HB"})
    assert h.bridge.state == STOPPED and len(h.writer.raw) == n


def test_clear_single_shot_and_rate_limit(h):
    h.cmd("CLEAR")
    assert any("ERR CLEAR refused: not running" in ln for ln in h.lines)
    h.cmd("START DISCHARGE")  # frame at 0 with 0x11
    h.run(50_000, every={250_000: "HB"})
    h.cmd("CLEAR")             # at 50 ms: accepted
    h.run(99_000, every={250_000: "HB"})
    h.cmd("CLEAR")             # at 99 ms (49 ms later): refused
    assert any("less than 100 ms" in ln for ln in h.lines)
    h.run(400_000, every={250_000: "HB"})
    d1 = [f.data[1] for f in ka_frames(h)]
    assert d1 == [0x11, 0x13, 0x11, 0x11, 0x11]
    h.cmd("CLEAR")             # at 400 ms: accepted again
    h.run(600_000, every={250_000: "HB"})
    d1 = [f.data[1] for f in ka_frames(h)]
    assert d1.count(0x13) == 2 and d1[-2:] == [0x13, 0x11]


def test_clear_at_exactly_100ms_accepted(h):
    h.cmd("START DISCHARGE")
    h.cmd("CLEAR")
    h.now += 100_000
    h.cmd("CLEAR")
    assert sum("OK CLEAR" in ln for ln in h.lines) == 2


def test_clear_does_not_survive_stop(h):
    h.cmd("START DISCHARGE")
    h.cmd("CLEAR")
    h.cmd("STOP")
    h.cmd("START DISCHARGE")
    assert [f.data[1] for f in ka_frames(h)] == [0x11, 0x11]


@pytest.mark.parametrize("line,d0", [("SLEEP 1", 0x10), ("SLEEP 3", 0x30)])
def test_sleep_while_stopped(h, line, d0):
    h.cmd(line)
    assert [(f.can_id, f.data) for f in h.writer.frames()] == [(0x515, bytes([d0, 0, 0, 0, 0, 0, 0, 0]))]
    assert h.bridge.state == STOPPED


def test_sleep_refused_while_running_and_rate_limited(h):
    h.cmd("START DISCHARGE")
    h.cmd("SLEEP 1")
    assert all(f.can_id == 0x105 for f in h.writer.frames())
    h.cmd("STOP")
    h.now += 1_500_000
    h.cmd("SLEEP 1")
    h.cmd("SLEEP 3")  # within 1 s: refused
    assert sum(f.can_id == 0x515 for f in h.writer.frames()) == 1
    h.now += 1_000_000
    h.cmd("SLEEP 3")
    assert sum(f.can_id == 0x515 for f in h.writer.frames()) == 2


@pytest.mark.parametrize("line", ["SLEEP 2", "SLEEP 0", "SLEEP 4", "SLEEP", "SLEEP 1 ", "sleep 1", "SLEEP 01"])
def test_other_sleeps_refused(h, line):
    h.cmd(line)
    assert h.writer.raw == []
    assert h.status()[-1].startswith("S ERR")


def test_state_line_periodic_and_on_change(h):
    h.lines.clear()
    h.run(3_000_000)
    assert sum(ln == "S STATE STOPPED" for ln in h.lines) == 3
    h.cmd("START CHARGE")
    assert "S STATE RUNNING CHARGE" in h.lines


def test_refused_transmit_forces_stopped(h):
    h.cmd("START DISCHARGE")
    core.D0_BY_MODE["DISCHARGE"] = 0x04  # simulate a bug that would power a vehicle load
    try:
        h.now += 100_000
        h.bridge.tick(h.now)
    finally:
        core.D0_BY_MODE["DISCHARGE"] = 0x00
    assert h.bridge.state == STOPPED
    assert len(h.writer.raw) == 1
    assert any(ln.startswith("S TXREFUSED") for ln in h.lines)
    h.run(2_000_000, every={100_000: "HB"})
    assert len(h.writer.raw) == 1


def test_send_error_forces_stopped_without_retry(h):
    h.cmd("START DISCHARGE")
    h.writer.fail = OSError(105, "No buffer space available")
    attempts = []
    orig = h.writer.send
    h.writer.send = lambda raw: (attempts.append(raw), orig(raw))[1]
    h.run(2_000_000, every={100_000: "HB"})
    assert h.bridge.state == STOPPED
    assert len(attempts) == 1
    assert any(ln.startswith("S TXERROR") for ln in h.lines)


def test_internal_error_fails_closed(h, monkeypatch):
    h.cmd("START DISCHARGE")
    monkeypatch.setattr(core, "pack", lambda *a, **k: 1 / 0)
    h.now += 100_000
    h.bridge.tick(h.now)
    assert h.bridge.state == STOPPED
    assert any("internal error" in ln for ln in h.lines)


def test_internal_error_with_broken_emit_still_stops():
    def emit(line):
        raise RuntimeError("client gone")
    from conftest import FakeWriter
    b = Bridge(FakeWriter(), emit=emit)
    b.client_connected = True
    b.handle_line("START DISCHARGE", 0)
    assert b.state == STOPPED


def test_next_deadline(h):
    h.cmd("START DISCHARGE")
    assert h.bridge.next_deadline() == h.t0 + 100_000
