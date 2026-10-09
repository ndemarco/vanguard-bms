"""Event loop, Unix socket server and command line for the bridge.

One controlling client at a time over a Unix stream socket. A second connection
is told it is refused and closed. Lines from the client go to ``core.Bridge``;
frames from both buses go back as ``R`` lines, frames the bridge sent as ``T``
lines, status as ``S`` lines.

Fail-closed paths handled here: client disconnect, client too slow to read its
output (buffer above :data:`MAX_OUTBUF`), interface check failing while running
(the bridge stops and exits with status 3), SIGTERM/SIGINT, and any exception
escaping the loop (the process exits; the pack opens its contactors about 1 s
after the last keep-alive).
"""

from __future__ import annotations

import argparse
import grp
import os
import select
import signal
import socket
import stat
import sys
import time
from dataclasses import replace
from typing import Callable, Optional

from vanguard.wire import format_status

from . import config as config_mod
from .canio import Can2ReceiveOnly, CanReceiver, describe, open_pack_can1, open_pack_can2
from .core import Bridge, LineAssembler
from .netcheck import check_all
from .simlink import SimCan1Socket, SimCan2Socket, SimLink

MAX_OUTBUF = 256 * 1024
READ_CHUNK = 4096


def monotonic_us() -> int:
    return time.monotonic_ns() // 1000


class Service:
    def __init__(self, *, listener: socket.socket, can1_writer, receivers: list, clock_us: Callable[[], int],
                 log: Callable[[str], None], info_lines: list, recheck: Optional[Callable[[], list]] = None,
                 recheck_interval_us: int = 5_000_000, pump: Optional[Callable[[], None]] = None,
                 sim_status=None, max_wait_s: float = 0.2):
        self.listener = listener
        self.receivers = receivers
        self.clock = clock_us
        self.log = log
        self.info_lines = list(info_lines)
        self.recheck = recheck
        self.recheck_interval_us = recheck_interval_us
        self.next_recheck_us = clock_us() + recheck_interval_us
        self.pump = pump
        self.sim_status = sim_status
        self.max_wait_s = max_wait_s
        self.client: Optional[socket.socket] = None
        self.assembler = None
        self.outbuf = bytearray()
        self.overflow = False
        self.running = True
        self.exit_code = 0
        self.bridge = Bridge(can1_writer, emit=self._queue, log=log)

    # ------------------------------------------------------------ client

    def _queue(self, line: str) -> None:
        if self.client is None:
            return
        self.outbuf += line.encode("ascii", "replace") + b"\n"
        if len(self.outbuf) > MAX_OUTBUF:
            self.overflow = True

    def _accept(self) -> None:
        try:
            conn, _ = self.listener.accept()
        except (BlockingIOError, InterruptedError):
            return
        if self.client is not None:
            self._refuse_second_client(conn)
            return
        conn.setblocking(False)
        self.client = conn
        self.assembler = LineAssembler()
        self.outbuf.clear()
        self.overflow = False
        self.log("client connected")
        for line in self.info_lines:
            self._queue(format_status(line))
        self.bridge.connect(self.clock())

    def _refuse_second_client(self, conn: socket.socket) -> None:
        self.log("second client refused")
        try:
            conn.setblocking(False)
            conn.send(b"S ERR busy: another client is connected\n")
        except OSError:
            pass
        conn.close()

    def _drop_client(self, reason: str) -> None:
        if self.client is None:
            return
        self.log(f"client dropped: {reason}")
        try:
            self.client.close()
        except OSError:
            pass
        self.client = None
        self.outbuf.clear()
        self.overflow = False
        self.bridge.disconnect(self.clock())

    def _read_client(self) -> None:
        try:
            data = self.client.recv(READ_CHUNK)
        except (BlockingIOError, InterruptedError):
            return
        except OSError as e:
            data = b""
            self.log(f"client read error: {e}")
        if not data:
            self._drop_client("disconnected")
            return
        for line, err in self.assembler.feed(data):
            if self.client is None:
                return
            if err is not None:
                self.bridge.status(err)
            else:
                self.bridge.handle_line(line, self.clock())

    def _flush_client(self) -> None:
        if self.client is None or not self.outbuf:
            return
        try:
            n = self.client.send(self.outbuf)
            del self.outbuf[:n]
        except (BlockingIOError, InterruptedError):
            pass
        except OSError as e:
            self._drop_client(f"write error: {e}")

    # -------------------------------------------------------------- loop

    def step(self, max_wait_s: Optional[float] = None) -> None:
        wait = self.max_wait_s if max_wait_s is None else max_wait_s
        now = self.clock()
        timeout = max(0.0, min(wait, (self.bridge.next_deadline() - now) / 1e6))
        rl: list = [self.listener]
        if self.client is not None:
            rl.append(self.client)
        rl += [r for r in self.receivers if r.fileno() is not None]
        wl = [self.client] if self.client is not None and self.outbuf else []
        try:
            readable, _, _ = select.select(rl, wl, [], timeout)
        except InterruptedError:
            readable = []
        if self.pump is not None:
            self.pump()
            while self.sim_status:
                self.bridge.status(f"SIM {self.sim_status.popleft()}")
        if self.listener in readable:
            self._accept()
        if self.client is not None and self.client in readable:
            self._read_client()
        for rcv in self.receivers:
            for f in rcv.read_frames():
                if f.err or f.rtr:
                    self.bridge.status(f"RX {rcv.bus} {describe(f)}")
                else:
                    self.bridge.received(rcv.bus, f.can_id, f.data, self.clock())
        self.bridge.tick(self.clock())
        if self.recheck is not None and self.clock() >= self.next_recheck_us:
            self.next_recheck_us = self.clock() + self.recheck_interval_us
            errs = self.recheck()
            if errs:
                for e in errs:
                    self.log(f"CHECK FAILED: {e}")
                self.bridge.force_stop(f"interface check failed: {errs[0]}")
                self.exit_code = 3
                self.running = False
        if self.overflow:
            self._drop_client("client is not reading its output")
        self._flush_client()

    def run(self) -> int:
        try:
            while self.running:
                self.step()
        finally:
            self.bridge.force_stop("bridge shutting down")
            self._flush_client()
            if self.client is not None:
                self.client.close()
            self.listener.close()
        return self.exit_code


def make_listener(path: str, mode: int, group: Optional[str]) -> socket.socket:
    if os.path.lexists(path):
        if not stat.S_ISSOCK(os.lstat(path).st_mode):
            raise RuntimeError(f"{path} exists and is not a socket")
        os.unlink(path)  # stale socket from a previous run
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o777 & ~mode)
    try:
        s.bind(path)
    finally:
        os.umask(old)
    os.chmod(path, mode)
    if group:
        os.chown(path, -1, grp.getgrnam(group).gr_gid)
    s.listen(2)
    s.setblocking(False)
    return s


def _log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def main(argv: Optional[list] = None, *, checker=check_all) -> int:
    p = argparse.ArgumentParser(prog="vanguard-bridge", description="Vanguard BMS CAN bridge service")
    p.add_argument("--config", help=f"TOML config (default {config_mod.DEFAULT_PATH}; "
                                    "optional with --simulate)")
    p.add_argument("--simulate", action="store_true", help="use the in-process pack simulator, no CAN hardware")
    p.add_argument("--socket", help="override the Unix socket path")
    p.add_argument("--check", action="store_true", help="run the interface checks and exit")
    a = p.parse_args(argv)

    if a.config is None and a.simulate:
        cfg = config_mod.Config("can0", "spi1.1", "can1", "spi1.2", socket_group=None,
                                socket_path=a.socket or "/tmp/vanguard-bridge.sock")
    else:
        cfg = config_mod.load(a.config or config_mod.DEFAULT_PATH)
    if a.socket:
        cfg = replace(cfg, socket_path=a.socket)

    mode_line = "MODE simulate (no CAN hardware)" if a.simulate else "MODE hardware"
    info = [mode_line] + cfg.mapping_lines()
    for line in info:
        _log(line)

    if not a.simulate:
        errs = checker(cfg)
        for e in errs:
            _log(f"CHECK FAILED: {e}")
        if errs:
            _log("refusing to start: OS-layer guards are not in place (see bridge/README.md)")
            return 2
        _log("interface checks passed")
        if a.check:
            return 0

    if a.simulate:
        link = SimLink(monotonic_us)
        can1_writer = SimCan1Socket(link)
        receivers = [CanReceiver(1, can1_writer), Can2ReceiveOnly(SimCan2Socket(link))]
        extra = dict(pump=link.pump, sim_status=link.status, max_wait_s=0.01)
    else:
        can1_writer, rx1 = open_pack_can1(cfg.can1_interface)
        receivers = [rx1, open_pack_can2(cfg.can2_interface)]
        extra = dict(recheck=lambda: checker(cfg), recheck_interval_us=int(cfg.recheck_interval_s * 1e6))

    listener = make_listener(cfg.socket_path, cfg.socket_mode, cfg.socket_group)
    _log(f"listening on {cfg.socket_path}; state STOPPED")
    svc = Service(listener=listener, can1_writer=can1_writer, receivers=receivers, clock_us=monotonic_us,
                  log=_log, info_lines=info, **extra)

    def _term(signum, _frame):
        svc.running = False
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    return svc.run()


if __name__ == "__main__":
    raise SystemExit(main())
