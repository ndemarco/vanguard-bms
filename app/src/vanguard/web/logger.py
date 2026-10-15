"""Session logs: frames in the JSON lines format of :mod:`vanguard.wire`, status lines beside them.

``wire.py``'s log format has no status record (its reader would reject one), so ``S`` lines
and app events go to a second file, ``<session>.status.jsonl``, one ``{"ts_us", "text"}`` per line.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Callable, Optional

from ..wire import Frame, frame_to_json

FLUSH_INTERVAL_S = 1.0


class SessionLogger:
    def __init__(self, directory: str, *, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime] = datetime.now):
        self.directory = directory
        self.clock = clock
        os.makedirs(directory, mode=0o750, exist_ok=True)
        stamp = wall().strftime("%Y%m%d-%H%M%S")
        self.frames_path = self._create(f"vanguard-{stamp}", ".jsonl")
        self.status_path = self.frames_path[:-len(".jsonl")] + ".status.jsonl"
        self._frames = open(self.frames_path, "a", encoding="ascii")
        self._status = open(self.status_path, "a", encoding="utf-8")
        self.error: Optional[str] = None
        self._last_flush = clock()
        self.frames_written = 0

    def _create(self, stem: str, ext: str) -> str:
        """Reserve a new file name (never reuse an existing log)."""
        for n in range(1000):
            path = os.path.join(self.directory, stem + ("" if n == 0 else f"-{n}") + ext)
            status = path[:-len(ext)] + ".status.jsonl"
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
            except FileExistsError:
                continue
            os.close(fd)
            if os.path.exists(status):
                continue
            return path
        raise OSError("cannot find a free log file name")

    def _guard(self, fn) -> None:
        if self.error:
            return
        try:
            fn()
        except OSError as e:
            self.error = f"logging disabled: {e}"

    def log_frame(self, frame: Frame, now: float) -> None:
        def w():
            self._frames.write(frame_to_json(frame) + "\n")
            self.frames_written += 1
            self._maybe_flush(now)
        self._guard(w)

    def log_status(self, text: str, now: float, ts_us: Optional[int] = None) -> None:
        def w():
            ts = int(now * 1_000_000) if ts_us is None else ts_us
            self._status.write(json.dumps({"ts_us": ts, "text": text}, separators=(",", ":")) + "\n")
            self._maybe_flush(now)
        self._guard(w)

    def _maybe_flush(self, now: float) -> None:
        if now - self._last_flush >= FLUSH_INTERVAL_S:
            self._last_flush = now
            self._frames.flush()
            self._status.flush()

    def flush(self) -> None:
        self._guard(lambda: (self._frames.flush(), self._status.flush()))

    def close(self) -> None:
        self._guard(lambda: None)
        for f in (self._frames, self._status):
            try:
                f.close()
            except OSError:
                pass
