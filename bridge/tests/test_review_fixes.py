"""Regression tests for the first safety review (findings 1 to 6)."""

import errno
import pathlib

import pytest
from conftest import FakeWriter, Harness

from vanguard_bridge import netcheck, netsetup
from vanguard_bridge.canframe import pack
from vanguard_bridge.canio import CanReceiver
from vanguard_bridge.config import Config
from vanguard_bridge.core import RUNNING, STOPPED, Bridge, sanitize
from vanguard_bridge.netcheck import check_can1_filter, check_link
from vanguard_bridge.service import BackgroundChecker
from test_netcheck import CAN1_OK, CAN2_OK, SPI, fake_env, sample
from test_service import rig  # noqa: F401  (fixture)

SYSTEM = pathlib.Path(__file__).parents[1] / "system"


# ---------------------------------------------- 1. stale keep-alives after a TX error

def test_txqueuelen_is_one():
    assert netsetup.TXQUEUELEN == 1
    cmds = [c for c, _ in netsetup.commands(Config("can0", "spi1.1", "can1", "spi1.2"))]
    assert ["ip", "link", "set", "dev", "can0", "txqueuelen", "1"] in cmds


def test_bridge_unit_resets_interfaces_before_every_start():
    unit = (SYSTEM / "vanguard-bridge.service").read_text()
    pre = "ExecStartPre=+/opt/vanguard-bridge/venv/bin/vanguard-bridge-netsetup --config /etc/vanguard-bridge/bridge.toml"
    assert pre in unit.splitlines()
    assert "Restart=on-failure" in unit and "RestartPreventExitStatus=2" in unit


def test_tx_error_stops_and_exits(rig):
    c = rig.connect()
    c.sendall(b"START DISCHARGE\n")
    rig.advance(300_000, client=c, hb_every=250_000)
    assert rig.svc.bridge.state == RUNNING
    rig.svc.bridge.gate._writer = FakeWriter()
    rig.svc.bridge.gate._writer.fail = OSError(errno.ENOBUFS, "No buffer space available")
    c.sendall(b"HB\n")
    rig.advance(200_000)
    lines = rig.read(c)
    assert any(ln.startswith("S TXERROR") for ln in lines)
    assert rig.svc.bridge.state == STOPPED and not rig.svc.running and rig.svc.exit_code == 3
    c.close()


def test_start_refused_after_tx_error_in_same_batch():
    h = Harness()
    h.cmd("START DISCHARGE")
    h.writer.fail = OSError(errno.ENOBUFS, "No buffer space available")
    h.now += 100_000
    h.bridge.tick(h.now)
    h.writer.fail = None
    h.cmd("START DISCHARGE")
    h.cmd("SLEEP 1")
    assert h.bridge.state == STOPPED and h.bridge.fatal
    assert len(h.writer.raw) == 1


# ------------------------------------------------------ 2. wiring swap at runtime

@pytest.mark.parametrize("bus,eff", [(1, True), (2, False)])
def test_wrong_id_format_latches_and_refuses_start(rig, bus, eff):
    c = rig.connect()
    c.sendall(b"START DISCHARGE\n")
    rig.advance(200_000, client=c, hb_every=100_000)
    assert rig.svc.bridge.state == RUNNING
    rig.link._rx[bus].append(pack(0x111 if not eff else 0x0CFF20F3, bytes(8), eff=eff))
    rig.svc.step(0)
    assert rig.svc.bridge.state == STOPPED
    c.sendall(b"START DISCHARGE\nSLEEP 1\n")
    rig.advance(2_000_000, client=c, hb_every=100_000)
    lines = rig.read(c)
    assert any("bus format mismatch" in ln and ln.startswith("S STOP") for ln in lines)
    assert any(ln.startswith("S ERR START refused: bus format mismatch") for ln in lines)
    assert rig.svc.bridge.state == STOPPED
    assert not [ln for ln in lines if ln.startswith("T ") and int(ln.split()[5]) > rig.clock.t - 2_000_000]
    c.close()


def test_simulator_traffic_never_trips_wiring_check(rig):
    c = rig.connect()
    c.sendall(b"START CHARGE\n")
    rig.advance(3_000_000, client=c, hb_every=250_000)
    assert rig.svc.bridge.wiring_fault is None and rig.svc.bridge.state == RUNNING
    c.close()


# ------------------------------------------------------ 3a. receiver socket errors

class DyingSock:
    def __init__(self):
        self.calls = 0

    def fileno(self):
        return -1

    def recv(self, n):
        self.calls += 1
        raise OSError(errno.ENETDOWN, "Network is down")


def test_receiver_socket_error_marks_dead_without_raising():
    s = DyingSock()
    r = CanReceiver(1, s)
    assert r.read_frames() == [] and "Network is down" in r.dead
    assert r.read_frames() == [] and s.calls == 1


def test_receiver_socket_error_stops_and_exits(rig):
    c = rig.connect()
    c.sendall(b"START DISCHARGE\n")
    rig.advance(200_000, client=c, hb_every=100_000)
    rig.svc.receivers.append(CanReceiver(2, DyingSock()))
    rig.svc.step(0)
    assert rig.svc.bridge.state == STOPPED and rig.svc.exit_code == 3 and not rig.svc.running
    c.close()


# ------------------------------------------------- 3b. multi-line text in S lines

def test_force_stop_with_multiline_reason_does_not_raise():
    lines = []
    b = Bridge(FakeWriter(), emit=lines.append)
    b.connect(0)
    b.handle_line("START DISCHARGE", 0)
    b.force_stop("Error: Parent Qdisc doesn't exists.\nWe have an error talking to the kernel\r\x00\xff")
    assert b.state == STOPPED
    assert all("\n" not in ln and "\r" not in ln for ln in lines)
    assert "S STOP Error: Parent Qdisc doesn't exists. We have an error talking to the kernel" in lines


def test_sanitize():
    assert sanitize("a\n\n b\t\x00cé") == "a b c"
    assert len(sanitize("x" * 1000)) == 400


def test_check_errors_are_single_line():
    run, _ = fake_env({("filter", "can0"): (2, "Error: Parent Qdisc doesn't exists.\nWe have an error\n")})
    errs = netcheck.check_all(Config("can0", "spi1.1", "can1", "spi1.2"), run, SPI)
    assert errs and all("\n" not in e for e in errs)


# ------------------------------------------------------------- 4. stricter checks

@pytest.mark.parametrize("state", ["BUS-OFF", "STOPPED", "SLEEPING"])
def test_can1_bad_states_refused(state):
    t = sample("ip_can1_ok.txt").replace("ERROR-ACTIVE", state)
    assert f"can0: CAN state is {state}" in check_link(t, "can0", want_listen_only=False)


def test_can1_error_passive_allowed_and_lower_up_required():
    t = sample("ip_can1_ok.txt")
    assert check_link(t.replace("ERROR-ACTIVE", "ERROR-PASSIVE"), "can0", want_listen_only=False) == []
    errs = check_link(t.replace("UP,LOWER_UP", "UP"), "can0", want_listen_only=False)
    assert errs == ["can0: no LOWER_UP (controller not running)"]


def test_filter_chain_must_be_zero():
    assert check_can1_filter(CAN1_OK, "can0") == []
    assert check_can1_filter(CAN1_OK.replace(" chain 0", ""), "can0") == []
    assert "can0: filter is not in chain 0" in check_can1_filter(CAN1_OK.replace("chain 0", "chain 1"), "can0")
    assert netcheck.check_can2_filter(CAN2_OK.replace("chain 0", "chain 7"), "can1")


def test_subprocess_timeout_is_short():
    assert netcheck.CMD_TIMEOUT_S <= 0.5


# ------------------------------------------------- 5. SLEEP after the last 0x105

def test_sleep_refused_until_1_5s_after_last_keepalive():
    h = Harness()
    h.cmd("START DISCHARGE")
    h.run(400_000, every={100_000: "HB"})
    last = h.now - h.t0  # keep-alive at 400 ms
    h.cmd("STOP")
    for dt in (0, 500_000, 1_499_999):
        h.now = h.t0 + last + dt
        h.cmd("SLEEP 1")
    assert not [f for f in h.writer.frames() if f.can_id == 0x515]
    assert "S ERR SLEEP 1 refused: less than 1.5 s since the last 0x105" in h.lines
    h.now = h.t0 + last + 1_500_000
    h.cmd("SLEEP 1")
    assert [f.can_id for f in h.writer.frames()][-1] == 0x515


# -------------------------------------------- 6. recheck off the loop; stalls

def test_background_checker_results_and_staleness():
    t = [0.0]
    errs = [[], ["bad"]]
    bg = BackgroundChecker(lambda: errs.pop(0), 5.0, clock_s=lambda: t[0])
    assert bg.poll() is None
    bg.run_once()
    assert bg.poll() == [] and bg.poll() is None
    bg.run_once()
    assert bg.poll() == ["bad"]
    t[0] = 100.0  # no check completed for 100 s
    assert "has not completed" in bg.poll()[0]


def test_background_checker_exception_is_a_failure():
    bg = BackgroundChecker(lambda: 1 / 0, 5.0)
    bg.run_once()
    assert "ZeroDivisionError" in bg.poll()[0]


def test_background_checker_thread_runs():
    import threading
    done = threading.Event()
    bg = BackgroundChecker(lambda: (done.set(), [])[1], 0.001)
    bg.start()
    assert done.wait(5)
    bg.stop()


def test_buffered_hb_after_stall_cannot_extend_keepalive(rig):
    c = rig.connect()
    c.sendall(b"START DISCHARGE\n")
    rig.advance(500_000, client=c, hb_every=100_000)
    t_last_real_hb = rig.clock.t
    c.sendall(b"HB\n")         # last real heartbeat, arrives at t_last_real_hb
    rig.svc.step(0)
    rig.clock.t += 1_200_000   # loop stalls 1.2 s; client sent nothing more
    c.sendall(b"HB\n")         # not delivered before the stall ended: arrives now
    rig.svc.step(0)
    assert rig.svc.bridge.state == STOPPED


def test_hb_queued_during_stall_is_stamped_no_later_than_arrival(rig):
    c = rig.connect()
    c.sendall(b"START DISCHARGE\n")
    rig.advance(500_000, client=c, hb_every=100_000)
    t_hb = rig.clock.t
    c.sendall(b"HB\n")         # sits in the socket while the loop is stalled
    rig.clock.t += 900_000     # stall: the HB is read 0.9 s after it arrived
    rig.svc.step(0)
    rig.advance(1_000_000)     # client silent from now on
    lines = rig.read(c)
    sent = [int(ln.split()[5]) for ln in lines if ln.startswith("T ")]
    assert rig.svc.bridge.state == STOPPED
    assert all(ts < t_hb + 1_000_000 for ts in sent)


def test_stale_start_refused(rig):
    c = rig.connect()
    c.sendall(b"START DISCHARGE\n")
    rig.clock.t += 1_100_000   # START waited 1.1 s in the socket
    rig.svc.step(0)
    assert rig.svc.bridge.state == STOPPED
    assert any("refused: stale" in ln for ln in rig.read(c))
    c.close()
