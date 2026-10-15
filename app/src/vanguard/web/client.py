"""Bridge client: line protocol over the bridge's Unix socket.

:class:`ClientCore` holds all decisions and is pure (no I/O, time passed in), so tests
drive it with a fake clock. :class:`BridgeLink` is the asyncio shell that owns the socket,
the heartbeat ticker and the reconnect backoff.

Rules the core enforces:

* ``HB`` is sent every 250 ms only while the app wants the pack running: from the moment
  START is sent until STOP, a refusal, a fatal status line, or a lost connection.
* START is only ever sent because a user asked for it in this very connection. A new
  connection starts with ``want_run`` false and nothing is replayed.
* After a fatal event the connection is dropped, the reason is kept, and reconnecting
  waits for a growing delay.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from ..wire import Frame, Status, WireError, parse_line
from . import commands as cmds
from .state import PackState, StatusLog

HB_INTERVAL_S = 0.25


@dataclass
class CommandResult:
    accepted: bool  # the command was handed to the bridge (or, for STOP, applied locally)
    command: Optional[str]
    message: str


class Backoff:
    """Delay before the next connection attempt: initial, 2x, 4x ... capped."""

    def __init__(self, initial_s: float = 2.0, max_s: float = 60.0, stable_after_s: float = 10.0):
        self.initial = initial_s
        self.max = max_s
        self.stable_after = stable_after_s
        self.failures = 0

    def next_delay(self, connection_lasted_s: float, fatal: bool) -> float:
        if connection_lasted_s >= self.stable_after and not fatal:
            self.failures = 0
        self.failures += 1
        return min(self.max, self.initial * (2 ** (self.failures - 1)))


class ClientCore:
    def __init__(self, state: PackState, status: StatusLog, *, on_frame: Optional[Callable] = None,
                 on_status: Optional[Callable] = None, on_reply: Optional[Callable] = None):
        self.state = state
        self.status = status
        self.on_frame = on_frame  # (Frame, now)
        self.on_status = on_status  # (text, now)
        self.on_reply = on_reply  # (word, text)
        self.sink: Optional[Callable[[str], None]] = None  # writes one command line
        self.connected = False
        self.bridge_mode: Optional[str] = None
        self.bridge_state: Optional[str] = None
        self.bridge_state_t: Optional[float] = None
        self.bridge_state_changed_t: Optional[float] = None  # when the STATE text last changed (page display only)
        self.config_lines: list = []
        self.want_run = False
        self.run_mode: Optional[str] = None
        self.start_acked = False
        self.halt_reason: Optional[str] = None  # why the pack is not (or no longer) being kept running
        self.fatal = False
        self.drop_requested = False
        self.hold_until = 0.0
        self.last_hb = 0.0
        self.replies: Dict[str, dict] = {}
        self.sent_log: list = []  # every command written, for tests and diagnostics (bounded)

    # -- connection life cycle -------------------------------------------------------------

    def on_connected(self, now: float) -> None:
        self.connected = True
        self.want_run = False
        self.run_mode = None
        self.start_acked = False
        self.fatal = False
        self.drop_requested = False
        self.bridge_mode = None
        self.bridge_state = None
        self.bridge_state_t = None
        self.config_lines = []
        self.replies = {}
        self._note("connected to the bridge", now, "app")

    def on_disconnected(self, now: float, reason: str) -> None:
        was_running = self.want_run
        self.connected = False
        self.want_run = False
        self.run_mode = None
        self.start_acked = False
        self.bridge_state = None
        self.drop_requested = False
        text = f"bridge connection lost: {reason}"
        if was_running or self.halt_reason is None:
            self.halt_reason = text
        self._note(text + ("; heartbeats stopped, the bridge will stop the pack" if was_running else ""), now, "app")

    def set_hold(self, until: float) -> None:
        self.hold_until = until

    # -- incoming lines --------------------------------------------------------------------

    def feed_line(self, line: str, now: float) -> None:
        try:
            item = parse_line(line)
        except WireError as e:
            self._note(f"unparseable line from the bridge: {e}", now, "app")
            return
        if isinstance(item, Frame):
            self.state.update(item, now)
            if self.on_frame:
                self.on_frame(item, now)
        else:
            self._status(item.text, now)

    def _note(self, text: str, now: float, source: str) -> None:
        self.status.add(text, now, source)
        if self.on_status:
            self.on_status(f"[{source}] {text}", now)

    def _halt(self, reason: str, fatal: bool) -> None:
        self.want_run = False
        self.run_mode = None
        self.start_acked = False
        self.halt_reason = reason
        if fatal:
            self.fatal = True
            self.drop_requested = True

    def _status(self, text: str, now: float) -> None:
        self.status.add(text, now, "bridge")
        if self.on_status:
            self.on_status(text, now)
        head, _, rest = text.partition(" ")
        if head == "MODE":
            self.bridge_mode = "simulate" if rest.startswith("simulate") else "hardware" if rest.startswith("hardware") else rest
        elif head == "CONFIG":
            self.config_lines.append(rest)
        elif head == "STATE":
            if rest != self.bridge_state:
                self.bridge_state_changed_t = now
            self.bridge_state = rest
            self.bridge_state_t = now
            if rest == "STOPPED" and self.want_run and self.start_acked:
                self._halt("the bridge reports STOPPED", fatal=False)
        elif head == "TXERROR":
            self._halt(f"CAN transmit error: {rest}", fatal=True)
        elif head == "TXREFUSED":
            self._halt(f"the bridge refused to transmit a frame: {rest}", fatal=True)
        elif head == "STOP":
            self._halt(f"the bridge stopped: {rest}", fatal=False)
        elif head in ("OK", "ERR"):
            self._reply(head, rest, now)

    def _reply(self, head: str, rest: str, now: float) -> None:
        word = rest.split(" ", 1)[0]
        if word not in cmds.REPLY_WORDS:
            return
        full = f"{head} {rest}"
        self.replies[word] = {"ok": head == "OK", "text": full, "t": now}
        if word == "START":
            if head == "OK":
                if self.want_run:
                    self.start_acked = True
            else:
                self._halt(f"the bridge refused START: {rest}", fatal=False)
        if self.on_reply:
            self.on_reply(word, full)

    # -- outgoing --------------------------------------------------------------------------

    def _send(self, cmd: str, now: float) -> None:
        cmds.check(cmd)
        if self.sink is None:
            raise RuntimeError("no connection")
        self.sink(cmd)
        self.sent_log.append(cmd)
        if len(self.sent_log) > 1000:
            del self.sent_log[:500]

    def _refuse(self, text: str) -> CommandResult:
        return CommandResult(False, None, text)

    def request_start(self, mode: str, now: float) -> CommandResult:
        cmd = cmds.START_COMMANDS.get(mode) if isinstance(mode, str) else None
        if cmd is None:
            return self._refuse("unknown start mode")
        if not self.connected:
            return self._refuse("not connected to the bridge")
        if now < self.hold_until:
            return self._refuse(f"cooling down after an error, wait {self.hold_until - now:.0f} s")
        if self.want_run:
            return self._refuse("already started; press Stop first")
        self.halt_reason = None
        self.want_run = True
        self.run_mode = mode
        self.start_acked = False
        try:
            self._send(cmd, now)
        except OSError as e:
            self.want_run = False
            return self._refuse(f"send failed: {e}")
        self.last_hb = now
        self.replies.pop("START", None)
        return CommandResult(True, cmd, f"sent {cmd}")

    def request_stop(self, now: float) -> CommandResult:
        was = self.want_run
        self.want_run = False
        self.run_mode = None
        self.start_acked = False
        if was or self.halt_reason is None:
            self.halt_reason = "stopped by user"
        if not self.connected:
            return CommandResult(True, None, "not connected; heartbeats stopped, the bridge stops the pack itself")
        try:
            self._send(cmds.STOP, now)
        except OSError as e:
            return CommandResult(True, None, f"heartbeats stopped; STOP not delivered ({e})")
        return CommandResult(True, cmds.STOP, "sent STOP")

    def request_clear(self, now: float) -> CommandResult:
        if not self.connected:
            return self._refuse("not connected to the bridge")
        return self._send_simple(cmds.CLEAR, now)

    def request_sleep(self, level: int, now: float) -> CommandResult:
        cmd = cmds.SLEEP_COMMANDS.get(level) if type(level) is int else None  # not bool: True == 1
        if cmd is None:
            return self._refuse("sleep level must be 1 or 3")
        if not self.connected:
            return self._refuse("not connected to the bridge")
        if self.want_run:
            return self._refuse("pack is running; press Stop first")
        return self._send_simple(cmd, now)

    def _send_simple(self, cmd: str, now: float) -> CommandResult:
        self.replies.pop(cmds.REPLY_WORD[cmd], None)
        try:
            self._send(cmd, now)
        except OSError as e:
            return self._refuse(f"send failed: {e}")
        return CommandResult(True, cmd, f"sent {cmd}")

    def tick(self, now: float) -> None:
        """Send ``HB`` if the app wants the pack running and 250 ms have passed."""
        if self.connected and self.want_run and now - self.last_hb >= HB_INTERVAL_S:
            try:
                self._send(cmds.HB, now)
            except OSError:
                return
            self.last_hb = now

    # -- view ------------------------------------------------------------------------------

    @property
    def controls_enabled(self) -> bool:
        return self.connected

    def summary(self, now: float) -> dict:
        return {
            "connected": self.connected,
            "mode": self.bridge_mode,
            "state": self.bridge_state,
            "state_age": None if self.bridge_state_t is None else round(now - self.bridge_state_t, 2),
            "state_changed_age": None if self.bridge_state_changed_t is None
            else round(now - self.bridge_state_changed_t, 2),
            "config": list(self.config_lines),
            "app_wants_running": self.want_run,
            "app_mode": self.run_mode,
            "halt_reason": self.halt_reason,
            "cooldown_s": max(0.0, round(self.hold_until - now, 1)),
        }


class BridgeLink:
    def __init__(self, core: ClientCore, socket_path: str, *, clock: Callable[[], float] = time.monotonic,
                 backoff: Optional[Backoff] = None, tick_s: float = 0.05):
        self.core = core
        self.path = socket_path
        self.clock = clock
        self.backoff = backoff or Backoff()
        self.tick_s = tick_s
        self._writer: Optional[asyncio.StreamWriter] = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    def _write(self, cmd: str) -> None:
        w = self._writer
        if w is None or w.is_closing():
            raise ConnectionError("socket closed")
        w.write(cmd.encode("ascii") + b"\n")

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self.run())

    async def close(self) -> None:
        """Best-effort STOP, then shut down."""
        self._stopping = True
        if self.core.connected:
            self.core.request_stop(self.clock())
            try:
                if self._writer:
                    await asyncio.wait_for(self._writer.drain(), 0.5)
            except (OSError, asyncio.TimeoutError):
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._writer:
            self._writer.close()

    async def run(self) -> None:
        core = self.core
        while not self._stopping:
            t_start = self.clock()
            reason = "closed by the bridge"
            connected_ok = False
            try:
                reader, writer = await asyncio.open_unix_connection(self.path, limit=1 << 16)
            except OSError as e:
                reason = f"cannot connect to {self.path}: {e}"
                core._note(reason, self.clock(), "app")
            else:
                connected_ok = True
                self._writer = writer
                core.sink = self._write
                core.on_connected(self.clock())
                t_start = self.clock()
                ticker = asyncio.get_running_loop().create_task(self._ticker())
                try:
                    while not core.drop_requested:
                        raw = await reader.readline()
                        if not raw:
                            break
                        core.feed_line(raw.decode("ascii", "replace").rstrip("\r\n"), self.clock())
                    else:
                        reason = core.halt_reason or "dropped after a fatal status line"
                except (OSError, ValueError, asyncio.LimitOverrunError) as e:
                    reason = f"read error: {e}"
                finally:
                    ticker.cancel()
                    core.sink = None
                    writer.close()
                    self._writer = None
                    core.on_disconnected(self.clock(), reason)
            now = self.clock()
            lasted = now - t_start if connected_ok else 0.0
            delay = self.backoff.next_delay(lasted, core.fatal and connected_ok)
            core.set_hold(now + delay)
            if not connected_ok:
                core.connected = False
            await asyncio.sleep(delay)

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(self.tick_s)
            self.core.tick(self.clock())
