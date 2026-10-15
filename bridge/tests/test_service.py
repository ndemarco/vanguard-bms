"""End-to-end: real Unix socket, Service loop, SimLink pack simulator, fake clock (no sleeps)."""

import os
import socket
import stat
import tempfile

import pytest

from vanguard.decoder import decode
from vanguard.wire import Frame, Status, parse_line
from vanguard_bridge import service as service_mod
from vanguard_bridge.canio import Can2ReceiveOnly, CanReceiver
from vanguard_bridge.core import RUNNING, STOPPED
from vanguard_bridge.service import Service, make_listener
from vanguard_bridge.simlink import SimCan1Socket, SimCan2Socket, SimLink


class Clock:
    def __init__(self):
        self.t = 5_000_000_000

    def __call__(self):
        return self.t


class Rig:
    def __init__(self, tmpdir, link_cls=SimLink):
        self.clock = Clock()
        self.path = os.path.join(tmpdir, "b.sock")
        self.listener = make_listener(self.path, 0o660, None)
        self.link = link_cls(self.clock)
        w = SimCan1Socket(self.link)
        self.svc = Service(listener=self.listener, can1_writer=w,
                           receivers=[CanReceiver(1, w), Can2ReceiveOnly(SimCan2Socket(self.link))],
                           clock_us=self.clock, log=lambda s: None, info_lines=["MODE simulate", "CONFIG test"],
                           pump=self.link.pump, sim_status=self.link.status)
        self.buf = b""

    def connect(self, prime=True):
        if prime:  # let the simulated pack send some CAN11 frames first
            self.advance(100_000)
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.connect(self.path)
        c.setblocking(False)
        self.svc.step(0)
        return c

    def advance(self, us, step=10_000, client=None, hb_every=None):
        end = self.clock.t + us
        last_hb = self.clock.t
        while self.clock.t < end:
            self.clock.t += step
            if client is not None and hb_every and self.clock.t - last_hb >= hb_every:
                client.sendall(b"HB\n")
                last_hb = self.clock.t
            self.svc.step(0)

    def read(self, c):
        while True:
            try:
                d = c.recv(1 << 16)
            except BlockingIOError:
                break
            if not d:
                break
            self.buf += d
        *lines, self.buf = self.buf.split(b"\n")
        return [ln.decode() for ln in lines]


@pytest.fixture
def rig():
    with tempfile.TemporaryDirectory(prefix="vb") as d:  # short path: AF_UNIX limit is 108 bytes
        r = Rig(d)
        yield r
        r.listener.close()


def contactor_bits(lines):
    out = []
    for ln in lines:
        f = parse_line(ln) if ln[:1] in "RT" else None
        if isinstance(f, Frame) and f.direction == "rx" and f.bus == 1 and f.can_id == 0x112:
            d = decode(1, 0x112, f.data)
            out.append(d.value("contactor_a_driven") and d.value("contactor_b_driven"))
    return out


def test_socket_permissions(rig):
    assert stat.S_IMODE(os.stat(rig.path).st_mode) == 0o660


def test_end_to_end_contactors_close_then_open_after_host_silence(rig):
    c = rig.connect()
    lines = rig.read(c)
    assert "S MODE simulate" in lines and "S STATE STOPPED" in lines
    rig.advance(500_000)
    assert not [ln for ln in rig.read(c) if ln.startswith("T ")]  # nothing sent before START
    assert rig.svc.bridge.state == STOPPED

    c.sendall(b"START DISCHARGE\n")
    rig.advance(3_000_000, client=c, hb_every=250_000)
    lines = rig.read(c)
    assert "S OK START DISCHARGE" in lines
    assert rig.link.sim.contactors_closed
    assert contactor_bits(lines)[-1] is True
    t_lines = [parse_line(ln) for ln in lines if ln.startswith("T ")]
    assert len(t_lines) == 30 and all(f.can_id == 0x105 for f in t_lines)
    assert any(ln.startswith("R 2 ") for ln in lines)  # CAN telemetry arrives too

    # Host goes silent: bridge stops at 1 s, pack opens about 1 s after the last keep-alive.
    t_silent = rig.clock.t
    rig.advance(3_000_000)
    lines = rig.read(c)
    assert rig.svc.bridge.state == STOPPED
    assert any("STOP watchdog" in ln for ln in lines)
    t_after = [f.ts_us for f in (parse_line(ln) for ln in lines if ln.startswith("T "))]
    assert all(ts < t_silent + 1_000_000 for ts in t_after)
    assert not rig.link.sim.contactors_closed
    assert contactor_bits(lines)[-1] is False

    # No automatic resume: HB alone does nothing.
    c.sendall(b"HB\n")
    rig.advance(1_000_000, client=c, hb_every=250_000)
    assert not [ln for ln in rig.read(c) if ln.startswith("T ")]
    c.close()


def test_disconnect_stops_and_second_client_refused(rig):
    c = rig.connect()
    c.sendall(b"START CHARGE\n")
    rig.advance(300_000, client=c, hb_every=250_000)
    assert rig.svc.bridge.state == RUNNING

    c2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c2.connect(rig.path)
    rig.svc.step(0)
    c2.settimeout(1)
    assert c2.recv(100).startswith(b"S ERR busy")
    assert c2.recv(100) == b""
    c2.close()
    assert rig.svc.bridge.state == RUNNING

    c.close()
    rig.svc.step(0)
    assert rig.svc.bridge.state == STOPPED
    sim_rx_before = rig.link.sim.last_105_us
    rig.advance(2_000_000)
    assert rig.link.sim.last_105_us == sim_rx_before

    c3 = rig.connect()  # a new client may connect once the first is gone
    assert "S STATE STOPPED" in rig.read(c3)
    c3.close()


def test_overlong_and_garbage_input(rig):
    c = rig.connect()
    c.sendall(b"A" * 500 + b"\nHB\n\xff\xfe\n")
    rig.svc.step(0)
    rig.svc.step(0)
    lines = rig.read(c)
    assert "S ERR line longer than 64 bytes discarded" in lines
    assert "S ERR line contains non-ASCII or control bytes" in lines
    c.close()


def test_slow_client_is_dropped_and_bridge_stops(rig, monkeypatch):
    monkeypatch.setattr(service_mod, "MAX_OUTBUF", 2_000)
    c = rig.connect()
    c.sendall(b"START DISCHARGE\n")
    rig.svc.step(0)
    rig.svc.client.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1)  # stall our side
    for _ in range(300):
        rig.clock.t += 10_000
        if rig.svc.client is None:
            break
        rig.svc._flush_client = lambda: None  # client never drains
        rig.svc.step(0)
    assert rig.svc.client is None and rig.svc.bridge.state == STOPPED
    c.close()


def test_recheck_failure_stops_and_exits(rig):
    results = []
    rig.svc.recheck_poll = lambda: results.pop(0) if results else None
    c = rig.connect()
    c.sendall(b"START DISCHARGE\n")
    rig.svc.step(0)
    assert rig.svc.bridge.state == RUNNING
    results += [[], None, []]
    rig.advance(1_000_000, client=c, hb_every=250_000)
    assert rig.svc.bridge.state == RUNNING
    results.append(["pack CAN1: can0: listen-only is on"])
    rig.svc.step(0)
    assert rig.svc.bridge.state == STOPPED and rig.svc.running is False and rig.svc.exit_code == 3
    c.close()


def test_main_refuses_to_start_without_guards(tmp_path, capsys):
    cfg = tmp_path / "b.toml"
    cfg.write_text('[pack_can1]\ninterface = "can0"\nspi_device = "spi1.1"\n'
                   '[pack_can2]\ninterface = "can1"\nspi_device = "spi1.2"\n'
                   f'[socket]\npath = "{tmp_path}/x.sock"\ngroup = ""\n')
    rc = service_mod.main(["--config", str(cfg)], checker=lambda c: ["pack CAN2: can1: listen-only mode is not on"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "CONFIG pack-can1 interface=can0 spi=spi1.1" in err and "refusing to start" in err
    assert not (tmp_path / "x.sock").exists()
