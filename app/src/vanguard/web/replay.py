"""Replay a JSON lines frame log into the same state model the live client feeds."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, List, Optional

from ..wire import Frame, WireError, read_jsonl
from .state import PackState, StatusLog


def load_log(path: str) -> List[Frame]:
    with open(path, "r", encoding="utf-8") as f:
        try:
            return list(read_jsonl(f))
        except WireError as e:
            raise SystemExit(f"cannot read replay log {path}: {e}") from None


class ReplaySource:
    """Feeds frames at ``speed`` times real time. Its clock is the log's own time."""

    def __init__(self, frames: List[Frame], state: PackState, status: StatusLog, *, speed: float = 1.0,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep):
        if speed <= 0:
            raise ValueError("speed must be positive")
        self.frames = frames
        self.state = state
        self.status = status
        self.speed = speed
        self.sleep = sleep
        self.index = 0
        self.finished = False
        self.t = frames[0].ts_us / 1e6 if frames else 0.0
        self._task: Optional[asyncio.Task] = None

    def now(self) -> float:
        return self.t

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self.run())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def run(self) -> None:
        self.status.add(f"replay started: {len(self.frames)} frames at {self.speed:g}x", self.t, "app")
        prev = self.frames[0].ts_us if self.frames else 0
        owed = 0.0
        for i, f in enumerate(self.frames):
            owed += max(0, f.ts_us - prev) / 1e6 / self.speed
            if owed >= 0.005:  # batch tiny gaps so a busy log does not wake the loop per frame
                await self.sleep(owed)
                owed = 0.0
            prev = max(prev, f.ts_us)
            self.t = prev / 1e6
            self.state.update(f, self.t)
            self.index = i + 1
        self.finished = True
        self.status.add("replay finished", self.t, "app")

    def summary(self) -> dict:
        return {"file_frames": len(self.frames), "position": self.index, "finished": self.finished,
                "speed": self.speed}
