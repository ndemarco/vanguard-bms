"""SocketCAN ``struct can_frame`` packing (classic CAN, 16 bytes).

Layout (linux/can.h)::

    canid_t can_id;   /* 32 bit: 11/29 bit ID | EFF | RTR | ERR flags */
    __u8    len;      /* 0..8 */
    __u8    __pad, __res0, len8_dlc;
    __u8    data[8];

Native byte order: the kernel and this process share the CPU's endianness.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_SFF_MASK = 0x000007FF
CAN_EFF_MASK = 0x1FFFFFFF

FRAME_FMT = "=IB3s8s"
FRAME_SIZE = struct.calcsize(FRAME_FMT)
assert FRAME_SIZE == 16


@dataclass(frozen=True)
class RxFrame:
    can_id: int  # flags stripped
    data: bytes
    eff: bool = False
    rtr: bool = False
    err: bool = False


def pack(can_id: int, data: bytes, *, eff: bool = False) -> bytes:
    """Build a classic CAN frame. ``eff`` selects a 29-bit ID (used by the simulator only)."""
    data = bytes(data)
    if len(data) > 8:
        raise ValueError("classic CAN carries at most 8 data bytes")
    if eff:
        if not 0 <= can_id <= CAN_EFF_MASK:
            raise ValueError(f"29-bit ID out of range: {can_id:#x}")
        word = can_id | CAN_EFF_FLAG
    else:
        if not 0 <= can_id <= CAN_SFF_MASK:
            raise ValueError(f"11-bit ID out of range: {can_id:#x}")
        word = can_id
    return struct.pack(FRAME_FMT, word, len(data), b"\x00\x00\x00", data.ljust(8, b"\x00"))


def unpack(raw: bytes) -> RxFrame:
    if len(raw) != FRAME_SIZE:
        raise ValueError(f"expected {FRAME_SIZE} bytes, got {len(raw)}")
    word, dlc, _pad, payload = struct.unpack(FRAME_FMT, raw)
    eff = bool(word & CAN_EFF_FLAG)
    can_id = word & (CAN_EFF_MASK if eff else CAN_SFF_MASK)
    return RxFrame(can_id, payload[:min(dlc, 8)], eff, bool(word & CAN_RTR_FLAG), bool(word & CAN_ERR_FLAG))
