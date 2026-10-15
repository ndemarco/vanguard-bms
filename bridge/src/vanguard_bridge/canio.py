"""SocketCAN access (stdlib ``socket.AF_CAN``, no python-can).

SAFETY-CRITICAL in one respect: pack CAN2 is opened by :func:`open_pack_can2`,
which creates the socket inside a :class:`Can2ReceiveOnly` and never returns it.
That class has no method that writes. The OS layer adds listen-only mode and a
drop-everything tc filter on the same interface (``netsetup.py``, checked by
``netcheck.py``).

Pack CAN1's socket is returned to the caller only so it can be handed to the
:class:`~vanguard_bridge.txgate.TxGate` (writes) and a :class:`CanReceiver` (reads).
"""

from __future__ import annotations

import socket
from typing import Optional

from .canframe import FRAME_SIZE, RxFrame, unpack

MAX_FRAMES_PER_READ = 256


class CanReceiver:
    """Non-blocking reader over a socket-like object that has ``recv`` and ``fileno``."""

    def __init__(self, bus: int, sock):
        self.bus = bus
        self.__sock = sock

    def fileno(self) -> Optional[int]:
        fd = self.__sock.fileno()
        return fd if fd is not None and fd >= 0 else None

    def read_frames(self) -> list:
        out: list = []
        for _ in range(MAX_FRAMES_PER_READ):
            try:
                raw = self.__sock.recv(FRAME_SIZE)
            except (BlockingIOError, InterruptedError):
                break
            if len(raw) != FRAME_SIZE:
                break
            out.append(unpack(raw))
        return out

    def close(self) -> None:
        self.__sock.close()


class Can2ReceiveOnly(CanReceiver):
    """Pack CAN2. Receive only: there is deliberately no send, write or socket accessor."""

    def __init__(self, sock):
        super().__init__(2, sock)


def _open_raw(ifname: str) -> socket.socket:
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    try:
        s.bind((ifname,))
        s.setblocking(False)
    except BaseException:
        s.close()
        raise
    return s


def open_pack_can1(ifname: str):
    """Return ``(writer_socket, receiver)`` for pack CAN1. The writer goes only to the TxGate."""
    s = _open_raw(ifname)
    return s, CanReceiver(1, s)


def open_pack_can2(ifname: str) -> Can2ReceiveOnly:
    return Can2ReceiveOnly(_open_raw(ifname))


def describe(frame: RxFrame) -> str:
    kind = "error" if frame.err else "remote" if frame.rtr else "data"
    return f"{kind} id={frame.can_id:#x} dlc={len(frame.data)}"
