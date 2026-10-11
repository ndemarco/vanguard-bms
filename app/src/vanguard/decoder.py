"""Decode CAN frames of the Vanguard BMS into named signals.

Entry point: :func:`decode`. Tables live in :mod:`vanguard.frames`.

Every signal keeps the raw integer next to the scaled value, because current sign,
limit direction, enable polarity and the 0x105 D0 bit-to-output order are unverified.
Unknown IDs return ``None``; decoding never raises on bad input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union

from .frames import (
    CAN1, CAN2, SPEC_BY_KEY, Bit, Const, Field, FrameSpec, fields_of, LOG_EVENT_CODES,
)

Value = Union[int, float, bool, str, None]


@dataclass(frozen=True)
class Signal:
    name: str
    raw: int
    value: Value  # scaled value; bool for bit fields; None when invalid
    unit: str = ""
    conf: str = "C"  # "C" confirmed, "I" inferred, "?" unknown
    valid: bool = True
    note: str = ""


@dataclass(frozen=True)
class DecodedFrame:
    name: str
    bus: int
    can_id: int
    data: bytes
    signals: list = field(default_factory=list)
    direction: str = ""  # "pack_tx" or "pack_rx"
    notes: list = field(default_factory=list)

    def signal(self, name: str) -> Signal:
        for s in self.signals:
            if s.name == name:
                return s
        raise KeyError(name)

    def value(self, name: str) -> Value:
        return self.signal(name).value

    def raw(self, name: str) -> int:
        return self.signal(name).raw

    def values(self) -> dict:
        return {s.name: s.value for s in self.signals}


def _mask_id(bus: int, can_id: int) -> int:
    # Strip SocketCAN flag bits (EFF/RTR/ERR) if the caller left them on.
    return can_id & 0x1FFFFFFF


def _decode_field(f: Field, data: bytes) -> Optional[Signal]:
    if f.offset + f.size > len(data):
        return None
    raw = int.from_bytes(data[f.offset:f.offset + f.size], f.order, signed=f.signed)
    if f.mask is not None:
        raw = (raw & f.mask) >> f.shift
    if f.always_invalid or raw in f.invalid:
        return Signal(f.name, raw, None, f.unit, f.conf, False, f.note)
    if f.enum is not None:
        return Signal(f.name, raw, f.enum.get(raw, f"unknown({raw})"), f.unit, f.conf, True, f.note)
    v = raw - f.bias
    if f.scale != 1:
        v = round(v * f.scale, 6)
    return Signal(f.name, raw, v, f.unit, f.conf, True, f.note)


def _decode_bit(b: Bit, data: bytes) -> Optional[Signal]:
    idx = b.offset + b.bit // 8
    if idx >= len(data):
        return None
    on = bool((data[idx] >> (b.bit % 8)) & 1)
    return Signal(b.name, int(on), on, "", b.conf, True, b.note)


def _table_signals(spec: FrameSpec, data: bytes, notes: list) -> list:
    out = []
    missing = False
    for f in fields_of(spec):
        if isinstance(f, Const):
            continue
        s = _decode_bit(f, data) if isinstance(f, Bit) else _decode_field(f, data)
        if s is None:
            missing = True
        else:
            out.append(s)
    if missing:
        notes.append(f"short frame: DLC {len(data)} is less than the {spec.dlc} bytes the layout needs")
    return out


# ---------------------------------------------------------------------------
# Hand-written decoders for the few frames that do not fit the table
# ---------------------------------------------------------------------------

_SDO_OBJECTS = {
    (0x1000, 0): "device_type",
    (0x1018, 0): "identity_entry_count",
    (0x1018, 1): "vendor_id",
    (0x1018, 2): "product_code",
    (0x1018, 3): "revision",
    (0x1018, 4): "serial",
    (0x1F51, 1): "program_control",
}


def _sdo_request(data: bytes, notes: list) -> list:
    if len(data) < 4:
        notes.append("short frame")
        return []
    cmd = data[0]
    kind = "read" if cmd == 0x40 else "write" if 0x20 <= cmd <= 0x3F else "other"
    index = int.from_bytes(data[1:3], "little")
    sub = data[3]
    sigs = [
        Signal("command", cmd, kind, "", "C"),
        Signal("index", index, index, "", "C"),
        Signal("subindex", sub, sub, "", "C"),
        Signal("object", index, _SDO_OBJECTS.get((index, sub), "unknown"), "", "C"),
    ]
    if kind == "write" and len(data) >= 8:
        val = int.from_bytes(data[4:8], "little")
        sigs.append(Signal("write_data", val, val, "", "C"))
        reboot = index == 0x1F51 and sub == 1 and val == 0
        sigs.append(Signal("bootloader_reboot_request", int(reboot), reboot, "", "C"))
        if reboot:
            notes.append("DANGEROUS: reboots the pack into the bootloader with no password")
    return sigs


def _sdo_reply(data: bytes, notes: list) -> list:
    if len(data) < 4:
        notes.append("short frame")
        return []
    index = int.from_bytes(data[1:3], "little")
    sub = data[3]
    payload = data[4:]
    val = int.from_bytes(payload, "little") if payload else 0
    return [
        Signal("command", data[0], data[0], "", "C"),
        Signal("index", index, index, "", "C"),
        Signal("subindex", sub, sub, "", "C"),
        Signal("object", index, _SDO_OBJECTS.get((index, sub), "unknown"), "", "C"),
        Signal("value", val, val, "", "C", True, f"{len(payload)} data byte(s), little-endian"),
    ]


_MAINT_NAMES = {
    0x00: "leave_service_mode", 0xC3: "set_balance_parameters", 0xC4: "set_cell_charge_limit",
    0xC5: "set_precharge_threshold", 0xC6: "set_identity", 0xC7: "erase_event_log",
    0xC9: "read_log_index", 0xCB: "dump_log_record", 0xCF: "clear_permanent_fault",
    0xD3: "select_variant", 0xDC: "service_output_group_off_1", 0xDE: "service_output_group_off_2",
    0xEC: "set_current_gain",
}
_MAINT_DANGEROUS = {0xC6, 0xC7, 0xD3, 0xEC}


def _sig(name, v, unit="", conf="C", note=""):
    return Signal(name, int(v), v, unit, conf, True, note)


def _maintenance(data: bytes, notes: list) -> list:
    notes.append("any 0x0CFF00F3 frame puts the pack in service mode and opens the contactors")
    if not data:
        return []
    d = data.ljust(8, b"\x00")
    cmd = d[0]
    name = _MAINT_NAMES.get(cmd, "unknown")
    sigs = [Signal("command", cmd, name, "", "C")]
    inv = lambda a, b: (d[a] ^ 0xFF) == d[b]  # noqa: E731
    ok = None
    if cmd == 0xC3:
        sigs += [_sig("balance_start_spread_mv", d[2], "mV"), _sig("balance_stop_spread_mv", d[3], "mV"),
                 _sig("balance_floor_mv", d[4] | d[5] << 8, "mV")]
        ok = inv(4, 6) and inv(5, 7)
    elif cmd == 0xC4:
        sigs.append(_sig("cell_charge_limit_mv", d[4] | d[5] << 8, "mV", note="firmware clamps to 3000..4245"))
        ok = inv(4, 6) and inv(5, 7)
    elif cmd == 0xC5:
        sigs += [_sig("precharge_threshold_pct", d[2], "%"), _sig("config_word", d[4] | d[5] << 8)]
        ok = inv(2, 3) and inv(4, 6) and inv(5, 7)
    elif cmd == 0xC6:
        sigs.append(_sig("identity_bytes", int.from_bytes(d[1:8], "big"), note="D1..D7 big-endian"))
    elif cmd == 0xCB:
        sigs.append(_sig("record_number", int.from_bytes(d[1:5], "little")))
    elif cmd == 0xD3:
        sigs.append(_sig("variant", d[4]))
        ok = inv(4, 5)
    elif cmd == 0xEC:
        sigs.append(_sig("current_gain_x1e4", d[4] | d[5] << 8, note="10000 = 1.0000; zero causes a divide fault"))
        ok = inv(4, 6) and inv(5, 7)
    if ok is not None:
        sigs.append(Signal("inverse_check_ok", int(ok), ok, "", "C", True, "false: the pack ignores the command"))
    if cmd in _MAINT_DANGEROUS:
        notes.append(f"DESTRUCTIVE command 0x{cmd:02X} ({name})")
    return sigs


# 0x18FF50F3 is also used for CAN1 error patterns; no byte discriminates (doc 5.2).
_CAN1_ERROR_PATTERNS = {
    bytes([1, 0] * 4): "bus_off",
    bytes([2] * 8): "transmit_bus_passive",
    bytes([1, 2] * 4): "receive_bus_passive",
    bytes([1] * 8): "receive_buffer_overflow",
}


def _log_status(spec: FrameSpec, data: bytes, notes: list) -> list:
    sigs = _table_signals(spec, data, notes)
    pat = _CAN1_ERROR_PATTERNS.get(bytes(data))
    if pat:
        sigs.append(Signal("can1_error_pattern", 1, pat, "", "I", True,
                           "bytes match a CAN1 error report; the log-status fields above are then meaningless"))
        notes.append(f"payload matches CAN1 error pattern: {pat}")
    return sigs


def _log_record(data: bytes, notes: list) -> list:
    if not data:
        return []
    k = data[0]
    sigs = [_sig("frame_index", k, conf="C")]
    body = data[1:]
    if k == 0 and len(body) >= 7:
        sigs.append(_sig("record_number_low_bytes", int.from_bytes(body[0:3], "little"), conf="C"))
        sigs.append(_sig("record_bytes", int.from_bytes(body[3:7], "little"), conf="?",
                         note="first 4 record bytes; layout not mapped"))
    else:
        sigs.append(_sig("record_bytes", int.from_bytes(body, "little"), conf="?",
                         note=f"7 record bytes at offsets 7k-3..7k+3 for k={k}; layout not mapped"))
    return sigs


def _charger_config(spec: FrameSpec, data: bytes, notes: list) -> list:
    sigs = _table_signals(spec, data, notes)
    if len(data) >= 5:
        warn = data[4] not in (3, 5)
        sigs.append(Signal("raises_config_warning", int(warn), warn, "", "C", True, "data[4] not 3 or 5"))
    return sigs


def _fault_bytes(spec: FrameSpec, data: bytes, notes: list) -> list:
    sigs = _table_signals(spec, data, notes)
    if any(data):
        notes.append("fault/status byte(s) non-zero")
    return sigs


def decode(bus: int, can_id: int, data: bytes) -> Optional[DecodedFrame]:
    """Decode one frame. Returns ``None`` for IDs the document does not describe."""
    try:
        data = bytes(data)
        key = (int(bus), _mask_id(bus, int(can_id)))
    except (TypeError, ValueError):
        return None
    spec = SPEC_BY_KEY.get(key)
    if spec is None:
        return None
    notes: list = []
    c = spec.custom
    if c == "sdo_request":
        sigs = _sdo_request(data, notes)
    elif c == "sdo_reply":
        sigs = _sdo_reply(data, notes)
    elif c == "maintenance":
        sigs = _maintenance(data, notes)
    elif c == "log_status":
        sigs = _log_status(spec, data, notes)
    elif c == "log_record":
        sigs = _log_record(data, notes)
    elif c == "charger_config":
        sigs = _charger_config(spec, data, notes)
    elif c == "fault_bytes":
        sigs = _fault_bytes(spec, data, notes)
    else:
        sigs = _table_signals(spec, data, notes)
    if spec.note and spec.direction == "pack_rx" and c is None and not spec.fields:
        notes.append(spec.note)
    return DecodedFrame(spec.name, spec.bus, key[1], data, sigs, spec.direction, notes)
