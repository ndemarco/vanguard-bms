"""Build frame payloads from signal values.

This is a test and simulation helper, not a transmit path: it only produces bytes,
never touches a bus, and refuses the dangerous host-to-pack frames (maintenance
commands and the CANopen service channel).
"""

from __future__ import annotations

from typing import Mapping, Optional

from .frames import SPEC_BY_KEY, SPEC_BY_NAME, Bit, Const, Field, FrameSpec, fields_of

_REFUSED = {"sdo_request", "maintenance"}


def _spec(bus_or_name, can_id) -> FrameSpec:
    if isinstance(bus_or_name, str):
        return SPEC_BY_NAME[bus_or_name]
    spec = SPEC_BY_KEY.get((bus_or_name, can_id & 0x1FFFFFFF))
    if spec is None:
        raise KeyError(f"unknown frame bus={bus_or_name} id=0x{can_id:X}")
    return spec


def _encode_raw(f: Field, value) -> int:
    if value is None:
        return min(f.invalid) if f.invalid else 0
    if f.enum is not None and isinstance(value, str):
        rev = {v: k for k, v in reversed(list(f.enum.items()))}  # first key wins
        if value.startswith("unknown(") and value.endswith(")"):
            return int(value[8:-1])
        if value not in rev:
            raise ValueError(f"{f.name}: unknown enum value {value!r}")
        return rev[value]
    if f.scale == 1:
        return int(value) + f.bias  # exact for 64-bit values
    return int(round(value / f.scale)) + f.bias


def encode(bus_or_name, can_id: Optional[int] = None, values: Optional[Mapping] = None, *,
           raw: Optional[Mapping] = None, dlc: Optional[int] = None) -> bytes:
    """Encode a frame.

    ``bus_or_name`` is either a bus number (then ``can_id`` is required) or a frame
    name from :mod:`vanguard.frames`. ``values`` maps signal names to scaled values
    (``bool`` for bit signals, ``None`` for invalid); ``raw`` maps names to raw integers
    and wins over ``values``. Unspecified fields stay zero; constant padding is filled in.
    """
    spec = _spec(bus_or_name, can_id)
    if spec.custom in _REFUSED:
        raise ValueError(f"{spec.name}: encoding refused (not a general transmit path)")
    if spec.custom is not None and spec.custom not in ("fault_bytes", "log_status", "charger_config"):
        raise ValueError(f"{spec.name}: no table layout to encode")
    values = dict(values or {})
    raw = dict(raw or {})
    out = bytearray(spec.dlc if dlc is None else dlc)
    known = set()
    fl = fields_of(spec)
    for f in fl:
        if isinstance(f, Const):
            out[f.offset:f.offset + len(f.value)] = f.value
    for f in fl:
        if isinstance(f, Const):
            continue
        known.add(f.name)
        if isinstance(f, Bit):
            v = raw.get(f.name, values.get(f.name))
            if v:
                out[f.offset + f.bit // 8] |= 1 << (f.bit % 8)
            continue
        if f.name in raw:
            r = int(raw[f.name])
        elif f.name in values:
            r = _encode_raw(f, values[f.name])
        else:
            continue
        if f.mask is not None:
            out[f.offset] = (out[f.offset] & ~f.mask & 0xFF) | ((r << f.shift) & f.mask)
            if r << f.shift & ~f.mask:
                raise ValueError(f"{f.name}: value {r} does not fit mask 0x{f.mask:02X}")
        else:
            try:
                out[f.offset:f.offset + f.size] = r.to_bytes(f.size, f.order, signed=f.signed)
            except OverflowError:
                raise ValueError(f"{f.name}: raw value {r} out of range for {f.size} byte(s)") from None
    extra = (set(values) | set(raw)) - known
    if extra:
        raise ValueError(f"{spec.name}: unknown signal(s) {sorted(extra)}")
    return bytes(out)
