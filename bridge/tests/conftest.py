import pytest

from vanguard_bridge.canframe import unpack
from vanguard_bridge.core import Bridge
from vanguard_bridge.txgate import check_tx


class FakeWriter:
    """Stands in for the pack CAN11 socket. Records (time, raw) and re-checks every write."""

    def __init__(self, clock=None):
        self.clock = clock
        self.raw = []
        self.times = []
        self.fail = None

    def send(self, raw):
        assert check_tx(raw) is None, "a frame outside the allowlist reached the socket"
        if self.fail is not None:
            raise self.fail
        self.raw.append(raw)
        self.times.append(self.clock() if self.clock else None)
        return len(raw)

    def frames(self):
        return [unpack(r) for r in self.raw]


class Harness:
    """Bridge + fake writer + fake clock (microseconds). No wall-clock time anywhere."""

    def __init__(self, pack_alive=True):
        self.pack_alive = pack_alive  # feed pack CAN11 evidence (the 0x111 the pack sends every 50 ms)
        self.now = 1_000_000_000  # arbitrary monotonic origin
        self.t0 = self.now
        self.writer = FakeWriter(lambda: self.now - self.t0)
        self.lines = []
        self.log = []
        self.bridge = Bridge(self.writer, emit=self.lines.append, log=self.log.append)
        self.bridge.connect(self.now)

    def cmd(self, line):
        if self.pack_alive:
            self.bridge.note_pack_can1_frame(self.now)
        self.bridge.handle_line(line, self.now)

    def run(self, until_us, step_us=1_000, every=None):
        """Advance to t0 + until_us ticking every step_us. ``every``: {period_us: command} sent on schedule."""
        while self.now - self.t0 < until_us:
            self.now += step_us
            for period, line in (every or {}).items():
                if (self.now - self.t0) % period == 0:
                    self.cmd(line)
            self.bridge.tick(self.now)

    def status(self):
        return [ln for ln in self.lines if ln.startswith("S ")]


@pytest.fixture
def h():
    return Harness()
