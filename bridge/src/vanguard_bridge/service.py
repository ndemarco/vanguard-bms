"""Event loop, Unix socket server and command line for the bridge.

One controlling client at a time over a Unix stream socket. A second connection
is told it is refused and closed. Lines from the client go to ``core.Bridge``;
frames from both buses go back as ``R`` lines, frames the bridge sent as ``T``
lines, status as ``S`` lines.

Fail-closed paths handled here: client disconnect, client too slow to read its
output (buffer above :data:`MAX_OUTBUF`), a frame only the pack sends seen on the
wrong interface (wiring swapped: STOPPED, START refused until a new client connects), and, with exit status 3, a
CAN transmit error, a CAN socket error or a failed background interface check;
SIGTERM/SIGINT; any exception escaping the loop (the process exits; the pack opens
its contactors about 1 s after the last keep-alive). systemd re-runs the interface
setup before every start, which flushes frames still queued for transmit.
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
import threading
import time
from dataclasses import replace
from typing import Callable, Optional

from vanguard.wire import format_status

from . import config as config_mod
from .canio import Can2ReceiveOnly, CanReceiver, describe, open_pack_can1, open_pack_can2
from vanguard.frames import CAN1, CAN1_SPECS, PACK_TX

from .canframe import RxFrame
from .core import Bridge, LineAssembler
from .netcheck import check_all
from .simlink import SimCan1Socket, SimCan2Socket, SimLink

MAX_OUTBUF = 256 * 1024
READ_CHUNK = 4096
MAX_CLIENT_READ_PER_STEP = 64 * 1024


PACK_SOURCE_ADDRESS = 0xF3
# 11-bit IDs the pack itself transmits on CAN11 (from vanguard's frame tables).
PACK_CAN1_TX_IDS = frozenset(s.can_id for s in CAN1_SPECS if s.bus == CAN1 and s.direction == PACK_TX)


def wiring_swap_evidence(bus: int, f: RxFrame) -> Optional[str]:
    """A frame only the pack sends, seen on the wrong socket: the wiring is swapped.

    Other nodes' frames (a stray J1939 frame on CAN11, say) and remote frames are ignored.
    """
    if f.err or f.rtr:
        return None
    if bus == 1 and f.eff and f.can_id & 0xFF == PACK_SOURCE_ADDRESS:
        return f"pack CAN2 frame {f.can_id:#010x} (source 0xF3) on the pack CAN1 interface"
    if bus == 2 and not f.eff and f.can_id in PACK_CAN1_TX_IDS:
        return f"pack CAN1 frame {f.can_id:#05x} on the pack CAN2 interface"
    return None


def is_pack_can1_evidence(bus: int, f: RxFrame) -> bool:
    """A frame the pack sends on CAN11, received on the pack CAN11 (TX) interface."""
    return bus == 1 and not (f.err or f.rtr or f.eff) and f.can_id in PACK_CAN1_TX_IDS


def monotonic_us() -> int:
    return time.monotonic_ns() // 1000


class BackgroundChecker:
    """Runs the interface checks in a thread so ``ip``/``tc`` never stall the event loop.

    The loop only calls :meth:`poll`. A check that has not completed for
    ``3 * interval + 5 s`` (hung subprocess, dead thread) is reported as a failure.
    """

    def __init__(self, checker: Callable[[], list], interval_s: float, clock_s: Callable[[], float] = time.monotonic):
        self.checker = checker
        self.interval_s = interval_s
        self.clock_s = clock_s
        self.max_age_s = 3 * interval_s + 5
        self._lock = threading.Lock()
        self._result: Optional[list] = None
        self._last_done = clock_s()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="vanguard-netcheck", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def run_once(self) -> None:
        try:
            errs = list(self.checker())
        except Exception as e:  # a crashing check is a failing check
            errs = [f"interface check raised {type(e).__name__}: {e}"]
        with self._lock:
            self._result = errs
            self._last_done = self.clock_s()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.run_once()

    def poll(self) -> Optional[list]:
        """Problems from the latest completed check (once), [] if it passed, None if nothing new."""
        with self._lock:
            r, self._result = self._result, None
            stale = self.clock_s() - self._last_done > self.max_age_s
        if stale:
            return [f"interface check has not completed for more than {self.max_age_s:.0f} s"]
        return r


class Service:
    def __init__(self, *, listener: socket.socket, can1_writer, receivers: list, clock_us: Callable[[], int],
                 log: Callable[[str], None], info_lines: list,
                 recheck_poll: Optional[Callable[[], Optional[list]]] = None,
                 pump: Optional[Callable[[], None]] = None, sim_status=None, max_wait_s: float = 0.2):
        self.listener = listener
        self.receivers = receivers
        self.clock = clock_us
        self.log = log
        self.info_lines = list(info_lines)
        self.recheck_poll = recheck_poll
        # Lower bounds on the arrival time of client bytes not yet read (see _read_client),
        # and of connections (with their bytes) still waiting in the listen queue.
        self.client_since_us = clock_us()
        self.listener_since_us = clock_us()
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
        self.client_since_us = self.listener_since_us  # its bytes may predate accept()
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
        """Drain the client socket and run its lines.

        Commands are stamped with ``client_since_us``, a time no later than their real
        arrival (the moment the socket was last seen empty). So if the loop stalls,
        ``HB`` lines that sat in the socket meanwhile cannot move the watchdog past
        1 s of real client silence, and a ``START`` that is already 1 s old is refused.
        """
        stamp = self.client_since_us
        data = bytearray()
        eof = False
        while len(data) < MAX_CLIENT_READ_PER_STEP:
            t_try = self.clock()
            try:
                chunk = self.client.recv(READ_CHUNK)
            except (BlockingIOError, InterruptedError):
                self.client_since_us = t_try  # empty at t_try: later bytes arrive after it
                break
            except OSError as e:
                self.log(f"client read error: {e}")
                chunk = b""
            if not chunk:
                eof = True
                break
            data += chunk
        # If the per-step cap was hit, client_since_us stays: the rest may be older.
        for line, err in self.assembler.feed(bytes(data)):
            if self.client is None:
                return
            if err is not None:
                self.bridge.status(err)
            else:
                self.bridge.handle_line(line, self.clock(), arrival_us=stamp)
        if eof:
            self._drop_client("disconnected")

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

    def _fail(self, reason: str) -> None:
        """Stop and end the loop with exit status 3 (systemd restarts; ExecStartPre resets the CAN interfaces)."""
        self.log(f"FATAL {reason}")
        if self.bridge.fatal is None:
            self.bridge.fatal = reason  # the state machine refuses START from here on
        self.bridge.force_stop(reason)
        self.exit_code = 3
        self.running = False

    def step(self, max_wait_s: Optional[float] = None) -> None:
        wait = self.max_wait_s if max_wait_s is None else max_wait_s
        now = self.clock()
        timeout = max(0.0, min(wait, (self.bridge.next_deadline() - now) / 1e6))
        rl: list = [self.listener]
        if self.client is not None:
            rl.append(self.client)
        rl += [r for r in self.receivers if r.fileno() is not None]
        wl = [self.client] if self.client is not None and self.outbuf else []
        t_select = self.clock()
        try:
            readable, _, _ = select.select(rl, wl, [], timeout)
        except InterruptedError:
            readable = []
        if self.client is not None and self.client not in readable:
            self.client_since_us = t_select  # no client bytes were waiting at t_select
        if self.listener not in readable:
            self.listener_since_us = t_select  # no connection was waiting at t_select
        self.bridge.tick(self.clock())  # watchdog first, before any buffered input is read
        if self.pump is not None:
            self.pump()
            while self.sim_status:
                self.bridge.status(f"SIM {self.sim_status.popleft()}")
        if self.listener in readable:
            self._accept()
        for rcv in self.receivers:
            for f in rcv.read_frames():
                swap = wiring_swap_evidence(rcv.bus, f)
                if swap is not None:
                    self.bridge.latch_wiring_fault(swap)
                elif is_pack_can1_evidence(rcv.bus, f):
                    self.bridge.note_pack_can1_frame(self.clock())
                if f.err or f.rtr:
                    self.bridge.status(f"RX {rcv.bus} {describe(f)}")
                else:
                    self.bridge.received(rcv.bus, f.can_id, f.data, self.clock())
            if rcv.dead is not None and self.running:
                self._fail(f"pack CAN{rcv.bus} socket error: {rcv.dead}")
        # Client input after the receivers, so START sees this step's bus evidence.
        if self.client is not None and self.client in readable and self.running:
            self._read_client()
        self.bridge.tick(self.clock())
        if self.bridge.fatal is not None and self.running:
            self._fail(self.bridge.fatal)
        if self.recheck_poll is not None and self.running:
            errs = self.recheck_poll()
            if errs:
                for e in errs:
                    self.log(f"CHECK FAILED: {e}")
                self._fail(f"interface check failed: {errs[0]}")
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
        bg = BackgroundChecker(lambda: checker(cfg), cfg.recheck_interval_s)
        bg.start()
        extra = dict(recheck_poll=bg.poll)

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
