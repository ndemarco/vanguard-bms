"""``--simulate``: replace the two CAN sockets with an in-process pack simulator.

Only the bottom-most layer changes. :class:`SimCan1Socket` stands in for the pack
CAN1 ``socket.socket`` (``send`` / ``recv`` of packed 16-byte frames) and is handed
to the very same ``TxGate`` and ``Bridge`` code as the real socket.
:class:`SimCan2Socket` stands in for pack CAN2 and, like the real wrapper, has no
send method.
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Optional

from vanguard.simulator import PackSimulator

from .canframe import pack, unpack


class SimLink:
    def __init__(self, clock_us: Callable[[], int], sim: Optional[PackSimulator] = None):
        self.sim = sim or PackSimulator(seed=1)
        self._clock = clock_us
        self._t0 = clock_us() - self.sim.now_us
        self._rx = {1: deque(), 2: deque()}
        self.status: deque = deque()

    def pump(self) -> None:
        """Advance the simulator to the current clock and queue what it sent."""
        target = self._clock() - self._t0
        if target > self.sim.now_us:
            for f in self.sim.advance(target - self.sim.now_us):
                self._rx[f.bus].append(pack(f.can_id, f.data, eff=f.bus == 2))
        for _ts, text in self.sim.drain_status():
            self.status.append(text)

    def _deliver(self, raw: bytes) -> None:
        self.pump()
        f = unpack(raw)
        self.sim.receive(1, f.can_id, f.data)

    def _recv(self, bus: int) -> bytes:
        if not self._rx[bus]:
            raise BlockingIOError
        return self._rx[bus].popleft()


class SimCan1Socket:
    def __init__(self, link: SimLink):
        self._link = link

    def send(self, raw: bytes) -> int:
        self._link._deliver(raw)
        return len(raw)

    def recv(self, n: int) -> bytes:
        return self._link._recv(1)

    def fileno(self) -> int:
        return -1

    def close(self) -> None:
        pass


class SimCan2Socket:
    """Receive only, like ``canio.Can2ReceiveOnly``."""

    def __init__(self, link: SimLink):
        self._link = link

    def recv(self, n: int) -> bytes:
        return self._link._recv(2)

    def fileno(self) -> int:
        return -1

    def close(self) -> None:
        pass
