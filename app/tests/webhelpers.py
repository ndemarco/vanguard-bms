"""Shared helpers for the web app tests."""

from vanguard.encoder import encode
from vanguard.web.client import ClientCore
from vanguard.web.state import PackState, StatusLog
from vanguard.wire import Frame, format_frame


def frame(bus, can_id, values, ts_us=1_000_000, direction="rx"):
    return Frame(ts_us, bus, can_id, encode(bus, can_id, values), direction)


def line(bus, can_id, values, ts_us=1_000_000):
    return format_frame(frame(bus, can_id, values, ts_us))


def make_core():
    sent = []
    core = ClientCore(PackState(), StatusLog())
    core.sink = sent.append
    core.on_connected(0.0)
    return core, sent


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt
        return self.t
