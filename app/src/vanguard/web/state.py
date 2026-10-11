"""Live pack-state model: latest value per decoded signal, frame rates and the status log."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Optional, Tuple

from ..decoder import decode
from ..frames import SPEC_BY_KEY
from ..wire import Frame

RATE_WINDOW_S = 5.0
MIN_STALE_S = 2.0
DEFAULT_STALE_S = 10.0


@dataclass
class SignalRecord:
    frame: str
    name: str
    raw: int
    value: object
    unit: str
    conf: str
    valid: bool
    note: str
    t: float  # arrival time, on the clock the caller uses (monotonic or replay time)
    bus: int
    can_id: int
    count: int = 1


class RateMeter:
    """Events per second over a sliding window, on the caller's clock."""

    def __init__(self, window_s: float = RATE_WINDOW_S):
        self.window = window_s
        self._times: Deque[float] = deque()
        self._first: Optional[float] = None

    def add(self, t: float) -> None:
        if self._first is None:
            self._first = t
        self._times.append(t)
        self._trim(t)

    def _trim(self, now: float) -> None:
        while self._times and self._times[0] < now - self.window:
            self._times.popleft()

    def rate(self, now: float) -> float:
        self._trim(now)
        if not self._times or self._first is None:
            return 0.0
        span = min(self.window, max(now - self._first, 0.25))
        return len(self._times) / span


class StatusLog:
    """The last N status lines (bridge ``S`` lines and app events)."""

    def __init__(self, maxlen: int = 200):
        self.lines: Deque[dict] = deque(maxlen=maxlen)
        self._seq = 0

    def add(self, text: str, t: float, source: str = "bridge") -> None:
        self._seq += 1
        self.lines.append({"seq": self._seq, "t": t, "source": source, "text": text})

    def last(self, n: int) -> list:
        return list(self.lines)[-n:]


class PackState:
    def __init__(self) -> None:
        self.signals: Dict[str, SignalRecord] = {}
        self.tx_signals: Dict[str, SignalRecord] = {}
        self.rx_rate = {1: RateMeter(), 2: RateMeter()}
        self.tx_rate = RateMeter()
        self.frame_counts: Dict[Tuple[int, int], int] = {}
        self.unknown_frames = 0
        self.total_frames = 0
        self.last_frame_t: Optional[float] = None

    @staticmethod
    def key(frame_name: str, signal: str) -> str:
        return f"{frame_name}.{signal}"

    def update(self, frame: Frame, now: float) -> bool:
        """Apply one frame. Returns False if the ID is not described by the protocol document."""
        self.total_frames += 1
        self.last_frame_t = now
        if frame.direction == "tx":
            self.tx_rate.add(now)
        else:
            self.rx_rate.setdefault(frame.bus, RateMeter()).add(now)
        k = (frame.bus, frame.can_id)
        self.frame_counts[k] = self.frame_counts.get(k, 0) + 1
        dec = decode(frame.bus, frame.can_id, frame.data)
        if dec is None:
            self.unknown_frames += 1
            return False
        table = self.tx_signals if frame.direction == "tx" else self.signals
        for s in dec.signals:
            key = self.key(dec.name, s.name)
            old = table.get(key)
            table[key] = SignalRecord(dec.name, s.name, s.raw, s.value, s.unit, s.conf, s.valid, s.note, now,
                                      frame.bus, frame.can_id, (old.count + 1) if old else 1)
        return True

    def get(self, key: str) -> Optional[SignalRecord]:
        return self.signals.get(key)

    def stale_after(self, rec: SignalRecord) -> float:
        spec = SPEC_BY_KEY.get((rec.bus, rec.can_id))
        if spec is None or not spec.period_ms:
            return DEFAULT_STALE_S
        return max(MIN_STALE_S, 3 * spec.period_ms / 1000.0)

    def signal_view(self, key: str, now: float, table: Optional[dict] = None) -> dict:
        rec = (self.signals if table is None else table).get(key)
        if rec is None:
            return {"key": key, "seen": False, "value": None, "raw": None, "unit": "", "conf": "", "valid": False,
                    "age": None, "stale": True, "note": ""}
        age = max(0.0, now - rec.t)
        return {"key": key, "seen": True, "value": rec.value, "raw": rec.raw, "unit": rec.unit, "conf": rec.conf,
                "valid": rec.valid, "age": round(age, 2), "stale": age > self.stale_after(rec), "note": rec.note}

    def rates(self, now: float) -> dict:
        return {"bus1_rx": round(self.rx_rate[1].rate(now), 1), "bus2_rx": round(self.rx_rate[2].rate(now), 1),
                "bus1_tx": round(self.tx_rate.rate(now), 1)}


Clock = Callable[[], float]
