"""Bridge state machine: STOPPED / RUNNING, keep-alive, watchdog and client grammar.

SAFETY-CRITICAL. Pure logic: no sockets, no clock. Every method takes ``now_us``
(monotonic microseconds). Frames leave only through the injected
:class:`~vanguard_bridge.txgate.TxGate`, which re-checks every frame.

Rules implemented here (bridge/README.md has the full list):

* Power-on state is STOPPED. Nothing is transmitted until ``START``.
* RUNNING sends ``0x105`` every 100 ms. Only ``HB`` and ``START`` feed the 1 s
  watchdog. Watchdog expiry, client disconnect, a refused or failed transmit, or
  any internal error enter STOPPED, and only a fresh ``START`` leaves it.
* ``CLEAR`` (RUNNING only, at most once per 100 ms) sets D1 = 0x13 on the next
  keep-alive only.
* ``SLEEP 1`` / ``SLEEP 3`` (STOPPED only) send one ``0x515``.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from vanguard.wire import Frame, format_frame, format_status

from .canframe import pack, unpack
from .txgate import KEEPALIVE_ID, SLEEP_ID, TxGate

STOPPED = "STOPPED"
RUNNING = "RUNNING"

KEEPALIVE_PERIOD_US = 100_000
WATCHDOG_US = 1_000_000
CLEAR_MIN_INTERVAL_US = 100_000
SLEEP_MIN_INTERVAL_US = 1_000_000
SLEEP_AFTER_KEEPALIVE_US = 1_500_000  # owner policy: contactors must already be open
STATE_REPORT_US = 1_000_000
MAX_LINE_BYTES = 64

D0_BY_MODE = {"DISCHARGE": 0x00, "CHARGE": 0x01}
D1_ENABLE = 0x11  # bit 4 enable + bit 0 request gated frames
D1_ENABLE_CLEAR = 0x13  # plus bit 1 fault clear
SLEEP_D0 = {"SLEEP 1": 0x10, "SLEEP 3": 0x30}

COMMANDS = frozenset({"START DISCHARGE", "START CHARGE", "STOP", "HB", "CLEAR", "SLEEP 1", "SLEEP 3"})


class Bridge:
    def __init__(self, writer, *, emit: Callable[[str], None], log: Callable[[str], None] = lambda s: None):
        """``writer``: the pack CAN1 socket (or its simulated stand-in); handed straight to the TxGate."""
        self._emit_fn = emit
        self._log = log
        self._now = 0
        self.gate = TxGate(writer, on_sent=self._on_sent, on_refused=self._on_refused, on_failed=self._on_failed)
        self.state = STOPPED
        self.mode: Optional[str] = None
        self.last_hb_us = 0
        self.next_keepalive_us = 0
        self.pending_clear = False
        self.last_clear_us: Optional[int] = None
        self.last_sleep_us: Optional[int] = None
        self.last_keepalive_sent_us: Optional[int] = None
        self.wiring_fault: Optional[str] = None  # latched until restart
        self.fatal: Optional[str] = None  # set when the process must exit (service checks it)
        self.client_connected = False
        self.next_state_report_us = 0

    # ----------------------------------------------------------- output

    def _emit(self, line: str) -> None:
        if self.client_connected:
            self._emit_fn(line)

    def status(self, text: str) -> None:
        """Send an S line. Never raises: text is reduced to one line of printable ASCII."""
        self._emit(format_status(sanitize(text)))

    def state_text(self) -> str:
        return f"STATE {self.state}" + (f" {self.mode}" if self.state == RUNNING else "")

    def _report_state(self) -> None:
        self.status(self.state_text())
        self.next_state_report_us = self._now + STATE_REPORT_US

    # ----------------------------------------------------- stop paths

    def force_stop(self, reason: str) -> None:
        """Enter STOPPED. Safe to call in any state, from any path, more than once."""
        was = self.state
        self.state = STOPPED
        self.mode = None
        self.pending_clear = False
        self._log(f"STOP {reason}")
        self.status(f"STOP {reason}")
        if was != STOPPED:
            self._report_state()

    def _fail_closed(self, where: str, exc: BaseException) -> None:
        # Set the state first, with no other code in between, then try to report.
        self.state = STOPPED
        self.mode = None
        self.pending_clear = False
        try:
            self.force_stop(f"internal error in {where}: {type(exc).__name__}")
        except Exception:  # reporting must never re-open a path to transmit
            pass

    # ---------------------------------------------------- TxGate hooks

    def _on_sent(self, raw: bytes) -> None:
        f = unpack(raw)
        if f.can_id == KEEPALIVE_ID:
            self.last_keepalive_sent_us = self._now
        self._emit(format_frame(Frame(self._now, 1, f.can_id, f.data, "tx"), "T"))

    def _on_refused(self, raw: bytes, reason: str) -> None:
        self.status(f"TXREFUSED {raw.hex().upper() or '-'} {reason}")
        self.force_stop("transmit refused by allowlist")

    def _on_failed(self, raw: bytes, reason: str) -> None:
        # The frame (and others) may still sit in the qdisc or the controller's TX buffer and
        # go out when the bus recovers. Stop, and ask the service to exit so systemd re-runs
        # netsetup (interface down/up), which discards them before the bridge runs again.
        self.fatal = f"transmit failed: {reason}"
        self.status(f"TXERROR {reason}")
        self.force_stop("transmit failed; bridge exits so the CAN queue is flushed")

    def latch_wiring_fault(self, reason: str) -> None:
        """A frame with the wrong ID format arrived on a bus: refuse START until restart."""
        if self.wiring_fault is None:
            self.wiring_fault = reason
            self.force_stop(f"bus format mismatch: wiring swapped? {reason}")

    # ----------------------------------------------------- client side

    def connect(self, now_us: int) -> None:
        self._now = now_us
        self.client_connected = True
        self._report_state()

    def disconnect(self, now_us: int) -> None:
        self._now = now_us
        self.client_connected = False
        self.force_stop("client disconnected")

    def handle_line(self, line: str, now_us: int) -> None:
        """One complete client line (without the newline). Never raises."""
        self._now = now_us
        try:
            self._handle(line, now_us)
        except Exception as e:
            self._fail_closed("command", e)

    def _handle(self, line: str, now: int) -> None:
        if line not in COMMANDS:
            if line == "SLEEP 2":
                self.status("ERR SLEEP 2 refused: deep shutdown is not allowed")
            else:
                self.status("ERR unknown command")
            return
        if line.startswith("START "):
            self._start(line[6:], now)
        elif line == "HB":
            if self.state == RUNNING:
                self.last_hb_us = max(self.last_hb_us, now)
        elif line == "STOP":
            if self.state == RUNNING:
                self.force_stop("client STOP")
            self.status("OK STOP")
        elif line == "CLEAR":
            self._clear(now)
        else:
            self._sleep(line, now)

    def _start(self, mode: str, now: int) -> None:
        if self.fatal is not None:
            self.status(f"ERR START refused: bridge is exiting ({self.fatal})")
            return
        if self.wiring_fault is not None:
            self.status(f"ERR START refused: bus format mismatch latched ({self.wiring_fault}); restart the bridge")
            return
        if self.state == RUNNING:
            if mode != self.mode:
                self.status(f"ERR START {mode} refused: running {self.mode}, send STOP first")
                return
            self.last_hb_us = max(self.last_hb_us, now)  # same-mode START is a heartbeat
            self.status(f"OK START {mode}")
            return
        self.state = RUNNING
        self.mode = mode
        self.last_hb_us = now
        self.next_keepalive_us = now
        self.pending_clear = False
        self.status(f"OK START {mode}")
        self._report_state()
        self._tick(now)  # first keep-alive now

    def _clear(self, now: int) -> None:
        if self.state != RUNNING:
            self.status("ERR CLEAR refused: not running")
        elif self.last_clear_us is not None and now - self.last_clear_us < CLEAR_MIN_INTERVAL_US:
            self.status("ERR CLEAR refused: less than 100 ms since the last CLEAR")
        else:
            self.last_clear_us = now
            self.pending_clear = True
            self.status("OK CLEAR")

    def _sleep(self, line: str, now: int) -> None:
        if self.fatal is not None:
            self.status(f"ERR {line} refused: bridge is exiting ({self.fatal})")
            return
        if self.wiring_fault is not None:
            self.status(f"ERR {line} refused: bus format mismatch latched; restart the bridge")
            return
        if self.state != STOPPED:
            self.status(f"ERR {line} refused: only allowed while STOPPED")
            return
        if self.last_keepalive_sent_us is not None and now - self.last_keepalive_sent_us < SLEEP_AFTER_KEEPALIVE_US:
            self.status(f"ERR {line} refused: less than 1.5 s since the last 0x105")
            return
        if self.last_sleep_us is not None and now - self.last_sleep_us < SLEEP_MIN_INTERVAL_US:
            self.status(f"ERR {line} refused: less than 1 s since the last SLEEP")
            return
        self.last_sleep_us = now
        if self.gate.send(pack(SLEEP_ID, bytes([SLEEP_D0[line], 0, 0, 0, 0, 0, 0, 0]))):
            self.status(f"OK {line}")

    # --------------------------------------------------------- timing

    def tick(self, now_us: int) -> None:
        """Run timers. Call often (at least at every :meth:`next_deadline`). Never raises."""
        self._now = now_us
        try:
            self._tick(now_us)
        except Exception as e:
            self._fail_closed("tick", e)

    def _tick(self, now: int) -> None:
        if self.state == RUNNING:
            if now - self.last_hb_us >= WATCHDOG_US:
                self.force_stop("watchdog: no HB or START for 1 s")
            elif now >= self.next_keepalive_us:
                d1 = D1_ENABLE_CLEAR if self.pending_clear else D1_ENABLE
                self.pending_clear = False
                self.next_keepalive_us += KEEPALIVE_PERIOD_US
                if self.next_keepalive_us <= now:  # fell behind: resynchronise, never burst
                    self.next_keepalive_us = now + KEEPALIVE_PERIOD_US
                self.gate.send(pack(KEEPALIVE_ID, bytes([D0_BY_MODE[self.mode], d1, 0, 0, 0, 0, 0, 0])))
        if self.client_connected and now >= self.next_state_report_us:
            self._report_state()

    def next_deadline(self) -> int:
        """Earliest time at which :meth:`tick` has work to do."""
        t = self.next_state_report_us if self.client_connected else self._now + STATE_REPORT_US
        if self.state == RUNNING:
            t = min(t, self.next_keepalive_us, self.last_hb_us + WATCHDOG_US)
        return t

    # -------------------------------------------------------- receive

    def received(self, bus: int, can_id: int, data: bytes, now_us: int) -> None:
        self._emit(format_frame(Frame(now_us, bus, can_id, data, "rx")))


class LineAssembler:
    """Split a client byte stream into lines; enforce ASCII and the length limit.

    ``feed`` returns a list of ``(line, error)`` pairs, exactly one of which is set.
    A line is printable ASCII (0x20..0x7E), at most :data:`MAX_LINE_BYTES` bytes,
    terminated by ``\\n`` (one ``\\r`` before it is tolerated). Longer input is
    discarded up to the next ``\\n`` and reported once.
    """

    def __init__(self, max_len: int = MAX_LINE_BYTES):
        self.max_len = max_len
        self.buf = bytearray()
        self.discarding = False

    def feed(self, data: bytes) -> list:
        out = []
        start = 0
        while True:
            nl = data.find(b"\n", start)
            piece = data[start:] if nl < 0 else data[start:nl]
            if self.discarding:
                if nl >= 0:
                    self.discarding = False
            else:
                self.buf += piece
                if len(self.buf) > self.max_len + 1:  # +1 leaves room for a trailing \r
                    self.buf.clear()
                    out.append((None, f"ERR line longer than {self.max_len} bytes discarded"))
                    self.discarding = nl < 0
                elif nl >= 0:
                    out.append(self._finish(bytes(self.buf)))
                    self.buf.clear()
            if nl < 0:
                return out
            start = nl + 1

    def _finish(self, line: bytes) -> tuple:
        if line.endswith(b"\r"):
            line = line[:-1]
        if len(line) > self.max_len:
            return None, f"ERR line longer than {self.max_len} bytes discarded"
        if any(b < 0x20 or b > 0x7E for b in line):
            return None, "ERR line contains non-ASCII or control bytes"
        return line.decode("ascii"), None


_UNPRINTABLE = re.compile(r"[^\x20-\x7e]+")


def sanitize(text: object, limit: int = 400) -> str:
    """One line of printable ASCII, whitespace runs collapsed, at most ``limit`` characters."""
    t = " ".join(_UNPRINTABLE.sub(" ", str(text)).split())
    return t if len(t) <= limit else t[:limit - 3] + "..."
