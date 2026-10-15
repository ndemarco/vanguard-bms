"""Transmit allowlist and the one final send function.

SAFETY-CRITICAL. Every frame the bridge puts on a CAN bus goes through
:meth:`TxGate.send`, which checks the exact 16 bytes about to be written with
:func:`check_tx` and only then calls the socket's ``send``. The check does not
consult the state machine: it would refuse a bad frame even if ``core.py`` asked
for it. Only the pack CAN11 socket is ever given to a ``TxGate``; pack CAN has no
writer anywhere (``canio.Can2ReceiveOnly``).

Allowed frames (CANBUS_PROTOCOL.md sections 3.1 and 4.1), all standard 11-bit,
DLC 8, no RTR, no error flag:

* ``0x105`` keep-alive: D0 in {0x00, 0x01}, D1 in {0x11, 0x13}, D2..D7 = 0.
  D0 bits 2/4/6 (power vehicle loads) and D1 bit 2 (20-minute timeout) are thus
  never set.
* ``0x515`` sleep: D0 in {0x10, 0x30} (sleep 1 or 3, never 2 = deep shutdown),
  D1..D7 = 0.

Everything else is refused, including ``0x614`` (bootloader reboot object) and
every extended ID (all CAN maintenance frames).
"""

from __future__ import annotations

import struct
from typing import Callable, Optional, Protocol

from .canframe import CAN_EFF_FLAG, CAN_ERR_FLAG, CAN_RTR_FLAG, CAN_SFF_MASK, FRAME_FMT, FRAME_SIZE

KEEPALIVE_ID = 0x105
SLEEP_ID = 0x515

ALLOWED_105_D0 = frozenset({0x00, 0x01})
ALLOWED_105_D1 = frozenset({0x11, 0x13})
ALLOWED_515_D0 = frozenset({0x10, 0x30})

FORBIDDEN_105_D0_BITS = 0x54  # bits 2, 4, 6: vehicle load outputs
FORBIDDEN_105_D1_BITS = 0x04  # bit 2: stretches the pack timeout to about 20 minutes

# Self-check of the tables above. Not an ``assert``: it must survive ``python -O``.
if any(v & FORBIDDEN_105_D0_BITS for v in ALLOWED_105_D0) or any(v & FORBIDDEN_105_D1_BITS for v in ALLOWED_105_D1):
    raise RuntimeError("txgate allowlist contains a forbidden 0x105 bit")
if any(v & 0x30 == 0x20 for v in ALLOWED_515_D0):
    raise RuntimeError("txgate allowlist contains 0x515 deep shutdown")


def check_tx(raw: bytes) -> Optional[str]:
    """Return ``None`` if ``raw`` (a packed ``struct can_frame``) may be sent, else the reason."""
    if not isinstance(raw, bytes):
        return "not bytes"
    if len(raw) != FRAME_SIZE:
        return f"frame size {len(raw)} is not {FRAME_SIZE} (classic CAN only)"
    word, dlc, pad, data = struct.unpack(FRAME_FMT, raw)
    if word & CAN_ERR_FLAG:
        return "error frame"
    if word & CAN_RTR_FLAG:
        return "remote frame"
    if word & CAN_EFF_FLAG:
        return "extended (29-bit) ID"
    if word > CAN_SFF_MASK:
        return f"ID word {word:#x} out of 11-bit range"
    if dlc != 8:
        return f"DLC {dlc} is not 8"
    if pad != b"\x00\x00\x00":
        return "non-zero padding bytes"
    if word == KEEPALIVE_ID:
        if data[0] not in ALLOWED_105_D0:
            return f"0x105 D0={data[0]:#04x} not in allowlist"
        if data[1] not in ALLOWED_105_D1:
            return f"0x105 D1={data[1]:#04x} not in allowlist"
        if any(data[2:]):
            return "0x105 D2..D7 not zero"
        return None
    if word == SLEEP_ID:
        if data[0] not in ALLOWED_515_D0:
            return f"0x515 D0={data[0]:#04x} not in allowlist"
        if any(data[1:]):
            return "0x515 D1..D7 not zero"
        return None
    return f"ID {word:#05x} not in allowlist"


class Writer(Protocol):
    def send(self, data: bytes) -> int: ...


class TxGate:
    """Owns the pack CAN11 writer. :meth:`send` is the only code that writes a CAN frame."""

    def __init__(self, writer: Writer, *, on_sent: Callable[[bytes], None],
                 on_refused: Callable[[bytes, str], None], on_failed: Callable[[bytes, str], None]):
        self._writer = writer
        self._on_sent = on_sent
        self._on_refused = on_refused  # caller must force STOPPED
        self._on_failed = on_failed    # caller must force STOPPED

    def send(self, raw: bytes) -> bool:
        """THE FINAL SEND FUNCTION. Check, then write. Returns True only if the frame was written."""
        raw = bytes(raw) if isinstance(raw, (bytes, bytearray, memoryview)) else raw
        reason = check_tx(raw)
        if reason is not None:
            self._on_refused(raw if isinstance(raw, bytes) else b"", reason)
            return False
        try:
            n = self._writer.send(raw)
        except OSError as e:
            self._on_failed(raw, f"{type(e).__name__}: {e}")
            return False
        if n != len(raw):
            self._on_failed(raw, f"short write {n}/{len(raw)}")
            return False
        self._on_sent(raw)
        return True
