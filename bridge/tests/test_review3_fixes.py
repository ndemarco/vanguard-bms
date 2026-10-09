"""Regression tests for the third safety review (finding G): START/SLEEP need positive evidence."""

import pytest
from conftest import Harness

from vanguard_bridge.canframe import pack, unpack
from vanguard_bridge.core import PACK_EVIDENCE_US, RUNNING, STOPPED
from vanguard_bridge.simlink import SimLink
import tempfile

from test_service import Rig, rig  # noqa: F401  (fixture)


class SwappedSimLink(SimLink):
    """Simulated pack wired the wrong way round: the TX interface is on pack CAN2."""

    def pump(self):
        target = self._clock() - self._t0
        if target > self.sim.now_us:
            for f in self.sim.advance(target - self.sim.now_us):
                self._rx[3 - f.bus].append(pack(f.can_id, f.data, eff=f.bus == 2))
        self.sim.drain_status()

    def _deliver(self, raw):
        self.pump()
        f = unpack(raw)
        self.sim.receive(2, f.can_id, f.data)  # our "CAN1" frames land on pack CAN2


@pytest.fixture
def swapped_rig():
    with tempfile.TemporaryDirectory(prefix="vb") as d:
        r = Rig(d, link_cls=SwappedSimLink)
        yield r
        r.listener.close()


def test_swapped_bus_never_transmits_across_reconnects(swapped_rig):
    rig = swapped_rig
    rig.advance(300_000)
    total_t = 0
    for _ in range(10):
        c = rig.connect(prime=False)
        c.sendall(b"START DISCHARGE\n")
        rig.svc.step(0)
        rig.advance(50_000, client=c, hb_every=10_000)
        lines = rig.read(c)
        rig.buf = b""
        total_t += sum(ln.startswith("T ") for ln in lines)
        assert any("refused: wiring latch" in ln or "refused: no pack CAN1 traffic" in ln for ln in lines)
        c.close()
        rig.svc.step(0)
    assert total_t == 0
    assert rig.link.sim.last_105_us is None  # the simulated pack never received a keep-alive


def test_start_at_service_start_before_any_frame_is_refused(rig):
    c = rig.connect(prime=False)
    c.sendall(b"START DISCHARGE\n")
    rig.svc.step(0)
    lines = rig.read(c)
    assert rig.svc.bridge.state == STOPPED
    assert "S ERR START DISCHARGE refused: no pack CAN1 traffic seen in the last 250 ms" in lines
    assert not [ln for ln in lines if ln.startswith("T ")]
    c.close()


def test_start_succeeds_on_healthy_simulated_bus(rig):
    c = rig.connect()  # primes 100 ms of simulated pack traffic
    c.sendall(b"START DISCHARGE\n")
    rig.svc.step(0)
    assert rig.svc.bridge.state == RUNNING
    assert any(ln.startswith("T 1 105 ") for ln in rig.read(c))
    c.close()


def test_evidence_must_be_recent():
    h = Harness(pack_alive=False)
    h.bridge.note_pack_can1_frame(h.now)
    h.now += PACK_EVIDENCE_US
    h.bridge.handle_line("START DISCHARGE", h.now)
    assert h.bridge.state == RUNNING
    h.bridge.handle_line("STOP", h.now)
    h.now += 1
    h.bridge.handle_line("START DISCHARGE", h.now)
    assert h.bridge.state == STOPPED and len(h.writer.raw) == 1


@pytest.mark.parametrize("line", ["SLEEP 1", "SLEEP 3"])
def test_sleep_gated_on_evidence(line):
    h = Harness(pack_alive=False)
    h.cmd(line)
    assert h.writer.raw == []
    assert h.lines[-1] == f"S ERR {line} refused: no pack CAN1 traffic seen in the last 250 ms"
    h.bridge.note_pack_can1_frame(h.now)
    h.cmd(line)
    assert [f.can_id for f in h.writer.frames()] == [0x515]


def test_sleep_gated_by_wiring_latch():
    h = Harness()
    h.bridge.latch_wiring_fault("test")
    h.cmd("SLEEP 1")
    assert h.writer.raw == [] and "refused: wiring latch" in h.lines[-1]


def test_non_pack_ids_on_can1_are_not_evidence(rig):
    c = rig.connect(prime=False)
    rig.link._rx[1].append(pack(0x105, bytes(8)))       # host traffic, not a pack frame
    rig.link._rx[1].append(pack(0x7FF, bytes(8)))
    rig.svc.step(0)
    assert rig.svc.bridge.pack_can1_seen_us is None
    rig.link._rx[1].append(pack(0x112, bytes(8)))
    rig.svc.step(0)
    assert rig.svc.bridge.pack_can1_seen_us is not None
    c.close()
