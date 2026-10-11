"""Random client input never transmits outside the allowlist and never raises."""

import random

from conftest import Harness

from vanguard_bridge.core import COMMANDS, LineAssembler, RUNNING, STOPPED

VALID = sorted(COMMANDS)


def _random_bytes(rng):
    kind = rng.random()
    if kind < 0.4:
        return (rng.choice(VALID) + "\n").encode()
    if kind < 0.6:
        s = rng.choice(VALID)
        i = rng.randrange(len(s) + 1)
        return (s[:i] + chr(rng.randrange(256)) + s[i:] + "\n").encode("latin-1")
    if kind < 0.8:
        return bytes(rng.randrange(256) for _ in range(rng.randrange(1, 200)))
    return rng.choice([b"SLEEP 2\n", b"START\n", b"START BOTH\n", b"\n", b"\r\n", b"HB" * 50 + b"\n",
                       b"0x614\n", b"T 1 614 8 2351\n", b"SEND 105 FF\n", b"x" * 5000])


def test_fuzz_client_input():
    for seed in range(40):
        rng = random.Random(seed)
        h = Harness()
        a = LineAssembler()
        for _ in range(1500):
            for line, err in a.feed(_random_bytes(rng)):
                if err is None:
                    h.bridge.handle_line(line, h.now)
                else:
                    h.bridge.status(err)
            h.now += rng.choice([0, 1, 1_000, 10_000, 50_000, 100_000, 400_000, 1_100_000])
            h.bridge.tick(h.now)
            if rng.random() < 0.01:
                h.bridge.disconnect(h.now)
                h.bridge.connect(h.now)
            assert h.bridge.state in (RUNNING, STOPPED)
        # FakeWriter.send asserts check_tx on every write; also re-check the record here.
        for f in h.writer.frames():
            assert f.can_id in (0x105, 0x515) and len(f.data) == 8
            assert not f.eff and not f.rtr and not f.err


def test_keepalive_only_while_running_and_fed():
    """Every 0x105 goes out while RUNNING with a heartbeat younger than 1 s."""
    rng = random.Random(1234)
    h = Harness()
    last_feed = None
    sent_ok = []
    orig = h.writer.send

    def send(raw):
        sent_ok.append((h.bridge.state, h.now - (last_feed or -10**12)))
        return orig(raw)
    h.writer.send = send
    for _ in range(20000):
        line = rng.choice(VALID + ["junk", "HB", "HB"])
        # Independent model of what feeds the watchdog: HB while running, START when stopped
        # or in the same mode.
        if (line == "HB" and h.bridge.state == RUNNING) or (line.startswith("START ") and (
                h.bridge.state == STOPPED or h.bridge.mode == line[6:])):
            last_feed = h.now
        h.bridge.handle_line(line, h.now)
        h.now += rng.choice([1_000, 30_000, 100_000, 300_000, 1_000_000])
        h.bridge.tick(h.now)
    for (state, age), f in zip(sent_ok, h.writer.frames()):
        if f.can_id == 0x105:
            assert state == RUNNING and age < 1_000_000
        else:
            assert state == STOPPED
