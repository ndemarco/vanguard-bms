"""Regression tests for the second safety review (findings A to F)."""

import pathlib
import socket

import pytest
from conftest import Harness

from vanguard_bridge.canframe import RxFrame, pack
from vanguard_bridge.core import RUNNING, STOPPED
from vanguard_bridge.service import PACK_CAN1_TX_IDS, wiring_swap_evidence
from test_service import rig  # noqa: F401  (fixture)

ROOT = pathlib.Path(__file__).parents[1]


# A: the README promise behind the restored tripwire
def test_readme_tripwire_sentence_still_true():
    import test_structure
    assert "fails if any new write call appears" in (ROOT / "README.md").read_text()
    assert hasattr(test_structure, "test_only_known_write_calls_exist")
    assert len(test_structure._write_calls()) == 5


# B: wiring-swap evidence only from the pack's own frames
def test_pack_can1_tx_ids_come_from_frame_tables():
    assert {0x111, 0x112, 0x113, 0x114, 0x120} <= PACK_CAN1_TX_IDS
    assert not PACK_CAN1_TX_IDS & {0x105, 0x515, 0x614}


@pytest.mark.parametrize("bus,frame", [
    (1, RxFrame(0x18FEF100, bytes(8), eff=True)),       # J1939 frame from another node (SA 0x00)
    (1, RxFrame(0x0CFF20F3, b"", eff=True, rtr=True)),  # RTR, even with SA 0xF3
    (2, RxFrame(0x105, bytes(8))),                      # standard frame the pack does not send
    (2, RxFrame(0x111, b"", rtr=True)),                 # RTR
    (1, RxFrame(0x111, bytes(8))),                      # normal traffic
    (2, RxFrame(0x0CFF20F3, bytes(8), eff=True)),
    (1, RxFrame(0x0CFF20F3, b"", eff=True, err=True)),
])
def test_no_wiring_evidence(bus, frame):
    assert wiring_swap_evidence(bus, frame) is None


@pytest.mark.parametrize("bus,frame", [
    (1, RxFrame(0x0CFF20F3, bytes(8), eff=True)),
    (1, RxFrame(0x18FF08F3, bytes(8), eff=True)),
    (2, RxFrame(0x112, bytes(8))),
    (2, RxFrame(0x594, bytes(5))),
])
def test_wiring_evidence(bus, frame):
    assert wiring_swap_evidence(bus, frame)


def test_stray_j1939_frame_on_can1_does_not_stop(rig):
    c = rig.connect()
    c.sendall(b"START DISCHARGE\n")
    rig.advance(200_000, client=c, hb_every=100_000)
    rig.link._rx[1].append(pack(0x18FEF100, bytes(8), eff=True))
    rig.advance(200_000, client=c, hb_every=100_000)
    assert rig.svc.bridge.state == RUNNING and rig.svc.bridge.wiring_fault is None
    c.close()


def test_wiring_latch_clears_on_new_client(rig):
    c = rig.connect()
    rig.link._rx[2].append(pack(0x111, bytes(8)))
    rig.svc.step(0)
    assert rig.svc.bridge.wiring_fault
    c.close()
    rig.svc.step(0)
    c2 = rig.connect()
    assert rig.svc.bridge.wiring_fault is None
    c2.sendall(b"START DISCHARGE\n")
    rig.advance(100_000, client=c2, hb_every=50_000)
    assert rig.svc.bridge.state == RUNNING
    c2.close()


# C: arrival time feeds only the watchdog and the stale rule
def test_backdated_start_uses_current_time_for_timers():
    h = Harness()
    arrival = h.now
    h.now += 600_000  # START sat in the socket for 0.6 s
    h.bridge.note_pack_can1_frame(h.now)
    h.bridge.handle_line("START DISCHARGE", h.now, arrival_us=arrival)
    h.bridge.tick(h.now)
    assert h.writer.times == [600_000]                 # one 0x105, not two
    assert h.bridge.last_keepalive_sent_us == h.now
    assert h.bridge.next_keepalive_us == h.now + 100_000
    assert h.bridge.last_hb_us == arrival              # watchdog fed at arrival
    assert [ln for ln in h.lines if ln.startswith("T ")] == [f"T 1 105 8 0011000000000000 {h.now}"]
    h.now += 400_000                                   # 1.0 s after arrival
    h.bridge.tick(h.now)
    assert h.bridge.state == STOPPED


def test_stale_start_refused_in_core():
    h = Harness()
    arrival = h.now
    h.now += 1_000_000
    h.bridge.handle_line("START CHARGE", h.now, arrival_us=arrival)
    assert h.bridge.state == STOPPED and h.writer.raw == []


def test_sleep_holdoff_uses_real_send_time():
    h = Harness()
    h.cmd("START DISCHARGE")
    h.cmd("STOP")
    arrival = h.now - 5_000_000  # an old stamp must not shorten the hold-off
    h.now += 1_000_000
    h.bridge.handle_line("SLEEP 1", h.now, arrival_us=arrival)
    assert all(f.can_id == 0x105 for f in h.writer.frames())


# D: bytes queued before accept() keep their age
def test_start_queued_before_accept_is_stale(rig):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    rig.svc.step(0)                    # listener seen empty now
    c.connect(rig.path)
    c.sendall(b"START DISCHARGE\n")
    rig.clock.t += 5_000_000           # loop stalls 5 s before accepting
    rig.svc.step(0)                    # accept
    rig.svc.step(0)                    # read
    assert rig.svc.bridge.state == STOPPED
    c.setblocking(False)
    assert any("refused: stale" in ln for ln in rig.read(c))
    c.close()


# E: a fatal service failure blocks START in the state machine
def test_fail_sets_bridge_fatal(rig):
    c = rig.connect()
    rig.svc._fail("interface check failed: test")
    assert rig.svc.bridge.fatal
    rig.svc.bridge.handle_line("START DISCHARGE", rig.clock.t)
    assert rig.svc.bridge.state == STOPPED
    c.close()


# F: restart rate limit
def test_bridge_unit_restart_rate_limit():
    unit = (ROOT / "system" / "vanguard-bridge.service").read_text()
    unit_section = unit.split("[Service]")[0]
    assert "StartLimitIntervalSec=60" in unit_section.splitlines()
    assert "StartLimitBurst=10" in unit_section.splitlines()
    assert "back off" in (ROOT / "README.md").read_text()
