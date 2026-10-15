#!/usr/bin/env python3
"""rdsup: charge supervisor for a Riden RD6018 charging the Vanguard 14S pack.

Watches the pack (CAN1 on can0, receive only) and the Riden (Modbus RTU) and
switches the Riden output OFF when anything looks wrong.  It NEVER switches the
output on and NEVER transmits on CAN.  The only Modbus write it can make is
function 0x06, register 18 (OUTPUT), value 0, built in exactly one place:
riden_output_off().  Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import select
import signal
import socket
import struct
import sys
import time
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Modbus RTU
# --------------------------------------------------------------------------

FUNC_READ_HOLDING = 0x03
REG_OUTPUT = 18            # Riden OUTPUT register (0 off, 1 on)
OFF_VALUE = 0
N_REGS = 19                # poll r0..r18 in one read


class ModbusError(Exception):
    pass


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def add_crc(body: bytes) -> bytes:
    return body + struct.pack("<H", crc16(body))


def check_crc(frame: bytes) -> bool:
    return len(frame) >= 4 and struct.unpack("<H", frame[-2:])[0] == crc16(frame[:-2])


def frame_read(slave: int, start: int, count: int) -> bytes:
    return add_crc(struct.pack(">BBHH", slave, FUNC_READ_HOLDING, start, count))


def _frame_write_single(slave: int, reg: int, value: int) -> bytes:
    # Only ever called from riden_output_off(); see tests/test_rdsup.py.
    return add_crc(struct.pack(">BBHH", slave, 0x06, reg, value))


def off_frame(slave: int) -> bytes:
    """The single permitted write frame, computed independently for gating."""
    return add_crc(bytes([slave, 0x06, 0x00, REG_OUTPUT, 0x00, OFF_VALUE]))


def expected_len(func: int, count: int = 0) -> int:
    if func == FUNC_READ_HOLDING:
        return 5 + 2 * count
    return 8  # 0x06 echo


def parse_response(frame: bytes, slave: int, func: int, count: int = 0):
    """Parse a reply. Returns list of registers (0x03) or the echoed (reg, value) (0x06)."""
    if len(frame) < 5:
        raise ModbusError(f"short reply ({len(frame)} bytes)")
    if frame[0] != slave:
        raise ModbusError(f"wrong slave {frame[0]}")
    if frame[1] == (func | 0x80):
        if not check_crc(frame[:5]):
            raise ModbusError("bad CRC on exception reply")
        raise ModbusError(f"modbus exception code {frame[2]}")
    if frame[1] != func:
        raise ModbusError(f"wrong function {frame[1]:#x}")
    if not check_crc(frame):
        raise ModbusError("bad CRC")
    if func == FUNC_READ_HOLDING:
        n = frame[2]
        if n != 2 * count or len(frame) != 5 + n:
            raise ModbusError("bad byte count")
        return list(struct.unpack(f">{count}H", frame[3:3 + n]))
    if len(frame) != 8:
        raise ModbusError("bad write echo length")
    return struct.unpack(">HH", frame[2:6])


class SerialPort:
    """Raw termios serial port, 8N1.  Refuses any write frame other than the OFF frame."""

    def __init__(self, path: str, baud: int = 115200, slave: int = 1, allow_off_write: bool = True):
        import termios
        self.slave = slave
        self.allow_off_write = allow_off_write
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(self.fd)
        speed = getattr(termios, f"B{baud}")
        attrs[0] = 0                                   # iflag
        attrs[1] = 0                                   # oflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0                                   # lflag
        attrs[4] = speed
        attrs[5] = speed
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        self._termios = termios

    def transact(self, frame: bytes, reply_len: int, timeout: float = 0.25) -> bytes:
        if frame[1] != FUNC_READ_HOLDING:
            if not (self.allow_off_write and frame == off_frame(self.slave)):
                raise ModbusError(f"transport refuses frame {frame.hex()}")
        self._termios.tcflush(self.fd, self._termios.TCIFLUSH)
        os.write(self.fd, frame)
        buf = b""
        deadline = time.monotonic() + timeout
        while len(buf) < reply_len:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            r, _, _ = select.select([self.fd], [], [], left)
            if r:
                buf += os.read(self.fd, reply_len - len(buf))
                if len(buf) >= 5 and buf[1] & 0x80:
                    break  # exception reply is 5 bytes
        return buf


class RidenLink:
    def __init__(self, transport, slave: int = 1):
        self.t = transport
        self.slave = slave

    def read_regs(self, start: int = 0, count: int = N_REGS) -> list[int]:
        reply = self.t.transact(frame_read(self.slave, start, count),
                                expected_len(FUNC_READ_HOLDING, count))
        return parse_response(reply, self.slave, FUNC_READ_HOLDING, count)


def riden_output_off(link: RidenLink, register: int = REG_OUTPUT, value: int = OFF_VALUE) -> None:
    """THE ONLY Modbus write in this program: function 0x06, register 18, value 0."""
    if register != REG_OUTPUT or value != OFF_VALUE or type(register) is not int or type(value) is not int:
        raise ValueError(f"riden_output_off refuses register={register!r} value={value!r}")
    frame = _frame_write_single(link.slave, register, value)
    if frame != off_frame(link.slave):
        raise ValueError("riden_output_off built an unexpected frame")
    reply = link.t.transact(frame, expected_len(0x06))
    if parse_response(reply, link.slave, 0x06) != (REG_OUTPUT, OFF_VALUE):
        raise ModbusError("write echo mismatch")


@dataclass
class RidenState:
    v_set: float
    i_set: float
    v_out: float
    i_out: float
    v_in: float
    temp_c: int
    protect: int
    cvcc: int
    output: int

    @classmethod
    def from_regs(cls, r: list[int]) -> "RidenState":
        temp = -r[5] if r[4] else r[5]
        return cls(r[8] / 100, r[9] / 100, r[10] / 100, r[11] / 100, r[14] / 100,
                   temp, r[16], r[17], r[18])


# --------------------------------------------------------------------------
# CAN (receive only)
# --------------------------------------------------------------------------

CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_FRAME = struct.Struct("=IB3x8s")
NONBLOCKING_MASK = {1: 0x50, 5: 0x20}   # 0x120 B1 bits 4,6 and B5 bit 5 are warnings


def open_can(ifname: str) -> socket.socket:
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((ifname,))
    s.setblocking(False)
    return s


def parse_can_frame(raw: bytes):
    """Return (std_id, data) or None for extended/RTR/error/short frames."""
    if len(raw) < CAN_FRAME.size:
        return None
    can_id, dlc, data = CAN_FRAME.unpack(raw[:CAN_FRAME.size])
    if can_id & (CAN_EFF_FLAG | CAN_RTR_FLAG | CAN_ERR_FLAG):
        return None
    return can_id & 0x7FF, data[:min(dlc, 8)]


def blocking_faults(b: bytes) -> list[str]:
    out = []
    for i, v in enumerate(b[:8]):
        blk = v & ~NONBLOCKING_MASK.get(i, 0) & 0xFF
        for bit in range(8):
            if blk & (1 << bit):
                out.append(f"B{i}.{bit}")
    return out


@dataclass
class PackState:
    current_ma: int | None = None
    drv_a: bool | None = None
    drv_b: bool | None = None
    pack_v: float | None = None
    out_v: float | None = None
    hot_c: int | None = None
    cold_c: int | None = None
    max_mv: int | None = None
    min_mv: int | None = None
    faults: bytes | None = None
    t112: float | None = None
    t114: float | None = None

    def update(self, cid: int, d: bytes, now: float) -> None:
        if cid == 0x111 and len(d) >= 3:
            self.current_ma = int.from_bytes(d[0:3], "big", signed=True)
        elif cid == 0x112 and len(d) >= 8:
            self.drv_a = bool(d[0] & 0x20)
            self.drv_b = bool(d[1] & 0x20)
            self.pack_v = int.from_bytes(d[4:6], "big") / 100
            self.out_v = int.from_bytes(d[6:8], "big") / 100
            self.t112 = now
        elif cid == 0x114 and len(d) >= 6:
            self.hot_c = d[0] - 40 if d[0] else None
            self.cold_c = d[1] - 40 if d[1] else None
            self.max_mv = int.from_bytes(d[2:4], "big")
            self.min_mv = int.from_bytes(d[4:6], "big")
            self.t114 = now
        elif cid == 0x120 and len(d) >= 8:
            self.faults = bytes(d[:8])


# --------------------------------------------------------------------------
# Supervisor logic (no I/O except through link / out callbacks)
# --------------------------------------------------------------------------

@dataclass
class Limits:
    max_cell_mv: int = 4150
    min_cell_mv: int = 3000
    max_temp_c: float = 45
    min_temp_c: float = 0
    max_pack_v: float = 58.1
    max_current_a: float = 10.5
    can_timeout_s: float = 1.0
    done_current_a: float = 1.0
    done_seconds: float = 120
    contactor_grace_s: float = 1.0
    riden_v_margin: float = 0.3
    riden_i_margin: float = 0.5
    max_poll_failures: int = 3


class Supervisor:
    def __init__(self, link: RidenLink, limits: Limits, start: float, dry_run: bool = False,
                 out=print, log=None):
        self.link = link
        self.lim = limits
        self.dry_run = dry_run
        self.out = out
        self.log = log or (lambda rec: None)
        self.start = start
        self.pack = PackState()
        self.riden: RidenState | None = None
        self.poll_failures = 0
        self.trip_reason: str | None = None
        self.trip_kind: str | None = None        # "fault" | "complete"
        self.off_confirmed = False
        self.off_failed = False
        self.last_banner = -1e9
        self.contactor_bad_since: float | None = None
        self.done_since: float | None = None
        self.stale_reported = False

    # -- inputs -----------------------------------------------------------
    def on_can(self, cid: int, data: bytes, now: float) -> None:
        self.pack.update(cid, data, now)

    def poll_riden(self, now: float) -> None:
        try:
            self.riden = RidenState.from_regs(self.link.read_regs())
            self.poll_failures = 0
        except (ModbusError, OSError) as e:
            self.poll_failures += 1
            self.riden = None if self.poll_failures >= self.lim.max_poll_failures else self.riden
            self.out(f"rdsup: Riden poll failed ({self.poll_failures}): {e}")
        self.step(now)

    # -- evaluation -------------------------------------------------------
    def output_on(self) -> bool:
        return self.riden is not None and self.riden.output == 1

    def check(self, now: float) -> tuple[str, str] | None:
        """Return (kind, reason) for the first tripped condition, else None."""
        L, p, r = self.lim, self.pack, self.riden
        if self.poll_failures >= L.max_poll_failures:
            return "fault", f"Riden comms lost ({self.poll_failures} consecutive failed polls)"
        on = self.output_on()
        if p.max_mv is not None and p.max_mv > L.max_cell_mv:
            return "fault", f"max cell {p.max_mv} mV > {L.max_cell_mv}"
        if on and p.min_mv is not None and p.min_mv < L.min_cell_mv:
            return "fault", f"min cell {p.min_mv} mV < {L.min_cell_mv}"
        if p.hot_c is not None and p.hot_c > L.max_temp_c:
            return "fault", f"hottest {p.hot_c} C > {L.max_temp_c}"
        if p.cold_c is not None and p.cold_c < L.min_temp_c:
            return "fault", f"coolest {p.cold_c} C < {L.min_temp_c}"
        if p.faults is not None:
            bad = blocking_faults(p.faults)
            if bad:
                return "fault", f"0x120 blocking fault bits {','.join(bad)} ({p.faults.hex()})"
        if p.pack_v is not None and p.pack_v > L.max_pack_v:
            return "fault", f"pack voltage {p.pack_v:.2f} V > {L.max_pack_v}"
        if r is not None:
            if r.protect != 0:
                return "fault", f"Riden protection status r16={r.protect}"
            if r.v_out > L.max_pack_v + L.riden_v_margin:
                return "fault", f"Riden V_OUT {r.v_out:.2f} V > {L.max_pack_v + L.riden_v_margin:.2f}"
            if r.i_out > r.i_set + L.riden_i_margin:
                return "fault", f"Riden I_OUT {r.i_out:.2f} A > I_SET {r.i_set:.2f} + {L.riden_i_margin}"
            if r.i_out > L.max_current_a:
                return "fault", f"Riden I_OUT {r.i_out:.2f} A > {L.max_current_a}"
        if on:
            for name, t in (("0x112", p.t112), ("0x114", p.t114)):
                age = now - (t if t is not None else self.start)
                if age > L.can_timeout_s:
                    return "fault", f"CAN {name} stale ({age:.1f} s) with output on"
            if not (p.drv_a and p.drv_b):
                if self.contactor_bad_since is None:
                    self.contactor_bad_since = now
                elif now - self.contactor_bad_since > L.contactor_grace_s:
                    return "fault", (f"output on with contactors not both driven "
                                     f"(A={p.drv_a} B={p.drv_b}) for {now - self.contactor_bad_since:.1f} s")
            else:
                self.contactor_bad_since = None
            if r.i_out < L.done_current_a and (r.cvcc == 0 or r.i_out < L.done_current_a):
                if self.done_since is None:
                    self.done_since = now
                elif now - self.done_since >= L.done_seconds:
                    return "complete", (f"charge complete (I_OUT {r.i_out:.2f} A < {L.done_current_a} "
                                        f"for {L.done_seconds:.0f} s, {'CV' if r.cvcc == 0 else 'CC'})")
            else:
                self.done_since = None
        else:
            self.contactor_bad_since = None
            self.done_since = None
        return None

    def step(self, now: float) -> None:
        if self.trip_reason is None:
            hit = self.check(now)
            if hit:
                self.trip_kind, self.trip_reason = hit
                tag = "CHARGE COMPLETE" if self.trip_kind == "complete" else "TRIP"
                self.out(f"rdsup: *** {tag}: {self.trip_reason} -> switching Riden output OFF ***")
                self.log({"event": "trip", "kind": self.trip_kind, "reason": self.trip_reason, "t": now})
                self.switch_off(now)
            elif not self.output_on():
                stale = [n for n, t in (("0x112", self.pack.t112), ("0x114", self.pack.t114))
                         if now - (t if t is not None else self.start) > self.lim.can_timeout_s]
                if stale and not self.stale_reported:
                    self.out(f"rdsup: note: CAN {','.join(stale)} stale (output off, not tripping)")
                    self.stale_reported = True
                elif not stale:
                    self.stale_reported = False
            return
        # Latched: never re-arm.  Keep output off; re-switch if someone turns it back on.
        if self.dry_run:
            return
        if self.off_confirmed and self.riden is not None and self.riden.output != 0:
            self.out("rdsup: output is ON again after trip -> switching OFF")
            self.log({"event": "output_on_after_trip", "t": now})
            self.off_confirmed = False
        if not self.off_confirmed:
            self.switch_off(now)

    def switch_off(self, now: float) -> None:
        if self.dry_run:
            self.out(f"rdsup: WOULD SWITCH OFF (dry run): {self.trip_reason}")
            self.log({"event": "would_switch_off", "reason": self.trip_reason, "t": now})
            return
        for attempt in range(1, 4):
            try:
                riden_output_off(self.link)
                if self.link.read_regs(REG_OUTPUT, 1)[0] == 0:
                    self.off_confirmed = True
                    if self.off_failed:
                        self.out("rdsup: output OFF now confirmed after earlier failures")
                    self.off_failed = False
                    self.out(f"rdsup: Riden output OFF confirmed (attempt {attempt})")
                    self.log({"event": "off_confirmed", "attempt": attempt, "t": now})
                    return
            except (ModbusError, OSError) as e:
                self.out(f"rdsup: switch-off attempt {attempt} failed: {e}")
        self.off_failed = True
        self.log({"event": "off_failed", "t": now})
        if now - self.last_banner >= 1.0:
            self.last_banner = now
            self.out("\n" + "!" * 70 + "\n!!! FAILED TO CONFIRM RIDEN OUTPUT OFF -- SWITCH IT OFF BY HAND !!!\n"
                     f"!!! reason: {self.trip_reason}\n" + "!" * 70)

    # -- reporting --------------------------------------------------------
    def status(self, now: float) -> dict:
        r, p = self.riden, self.pack
        return {
            "t": round(now, 3),
            "riden": None if r is None else {"v_set": r.v_set, "i_set": r.i_set, "v": r.v_out, "i": r.i_out,
                                             "out": r.output, "cvcc": "CC" if r.cvcc else "CV",
                                             "protect": r.protect, "temp_c": r.temp_c, "v_in": r.v_in},
            "pack": {"v": p.pack_v, "out_v": p.out_v, "i_ma": p.current_ma, "max_mv": p.max_mv,
                     "min_mv": p.min_mv, "hot_c": p.hot_c, "cold_c": p.cold_c,
                     "drv_a": p.drv_a, "drv_b": p.drv_b,
                     "faults": None if p.faults is None else p.faults.hex()},
            "trip": self.trip_reason, "off_confirmed": self.off_confirmed, "dry_run": self.dry_run,
        }

    def status_line(self, now: float) -> str:
        r, p = self.riden, self.pack
        f = lambda v, fmt: "?" if v is None else format(v, fmt)
        rs = ("Riden ?" if r is None else
              f"Riden {r.v_out:.2f}V {r.i_out:.2f}A out={'ON' if r.output else 'off'} "
              f"{'CC' if r.cvcc else 'CV'} set {r.v_set:.2f}V/{r.i_set:.2f}A")
        ct = "?" if p.drv_a is None else f"{'A' if p.drv_a else '-'}{'B' if p.drv_b else '-'}"
        st = "OK" if self.trip_reason is None else f"TRIPPED: {self.trip_reason}"
        return (f"{time.strftime('%H:%M:%S')} {rs} | pack {f(p.pack_v, '.2f')}V "
                f"cell {f(p.max_mv, 'd')}/{f(p.min_mv, 'd')}mV T {f(p.hot_c, 'd')}/{f(p.cold_c, 'd')}C "
                f"cont {ct} flt {'?' if p.faults is None else p.faults.hex()} | {st}")


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="rdsup",
        description="Charge supervisor: switches the Riden RD6018 output OFF on any pack/charger anomaly. "
                    "Never switches the output on; never transmits on CAN. After a trip it stays latched "
                    "until restarted.",
        epilog="SIGINT/SIGTERM (Ctrl-C) just exits and does NOT touch the Riden output: "
               "if it was on, it stays on and is then unsupervised.")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--slave", type=int, default=1)
    ap.add_argument("--can", default="can0", help="SocketCAN interface (receive only)")
    ap.add_argument("--max-cell-mv", type=int, default=4150)
    ap.add_argument("--min-cell-mv", type=int, default=3000)
    ap.add_argument("--max-temp-c", type=float, default=45)
    ap.add_argument("--min-temp-c", type=float, default=0)
    ap.add_argument("--max-pack-v", type=float, default=58.1)
    ap.add_argument("--max-current-a", type=float, default=10.5)
    ap.add_argument("--can-timeout-s", type=float, default=1.0)
    ap.add_argument("--done-current-a", type=float, default=1.0)
    ap.add_argument("--done-seconds", type=float, default=120)
    ap.add_argument("--log", default=os.path.expanduser(f"~/rdsup-{_dt.date.today().isoformat()}.jsonl"))
    ap.add_argument("--dry-run", action="store_true", help="never write to the Riden; print WOULD SWITCH OFF")
    ap.add_argument("--run-seconds", type=float, default=None, help="exit after this many seconds")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    lim = Limits(a.max_cell_mv, a.min_cell_mv, a.max_temp_c, a.min_temp_c, a.max_pack_v,
                 a.max_current_a, a.can_timeout_s, a.done_current_a, a.done_seconds)
    logf = open(a.log, "a", buffering=1)

    def log(rec):
        print(json.dumps(rec), file=logf)

    def bye(signum, frame):
        print(f"\nrdsup: signal {signum}, exiting WITHOUT touching the Riden output")
        raise SystemExit(0)

    signal.signal(signal.SIGINT, bye)
    signal.signal(signal.SIGTERM, bye)

    port = SerialPort(a.port, a.baud, a.slave, allow_off_write=not a.dry_run)
    can = open_can(a.can)
    t0 = time.monotonic()
    sup = Supervisor(RidenLink(port, a.slave), lim, t0, dry_run=a.dry_run, log=log)
    print(f"rdsup: watching {a.port} + {a.can}{' (DRY RUN)' if a.dry_run else ''}; log {a.log}; "
          f"limits {lim}")
    log({"event": "start", "t": t0, "dry_run": a.dry_run, "limits": lim.__dict__})
    next_poll = t0
    next_status = t0 + 2.0
    while True:
        now = time.monotonic()
        if a.run_seconds is not None and now - t0 >= a.run_seconds:
            print("rdsup: --run-seconds elapsed, exiting (output not touched)")
            return 0
        r, _, _ = select.select([can], [], [], max(0.0, min(next_poll, next_status) - now))
        if r:
            while True:
                try:
                    raw = can.recv(16)
                except BlockingIOError:
                    break
                fr = parse_can_frame(raw)
                if fr:
                    sup.on_can(fr[0], fr[1], time.monotonic())
        now = time.monotonic()
        if now >= next_poll:
            next_poll = now + 0.5
            sup.poll_riden(now)
        if now >= next_status:
            next_status = now + 2.0
            print(sup.status_line(now), flush=True)
            log(sup.status(now))


if __name__ == "__main__":
    sys.exit(main())
