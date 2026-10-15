"""End to end: the real bridge in --simulate mode, the real app process, the HTTP API and WebSocket."""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import pytest

pytest.importorskip("vanguard_bridge")
from websockets.sync.client import connect  # noqa: E402

from vanguard.frames import FAULT_BITS, LATCHED_WORD_BITS, LIVE_WORD_BITS  # noqa: E402
from vanguard.wire import read_jsonl  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def stack():
    d = tempfile.mkdtemp(dir="/tmp", prefix="vge")
    sock = os.path.join(d, "b.sock")
    port = free_port()
    logdir = os.path.join(d, "logs")
    bridge = subprocess.Popen([sys.executable, "-m", "vanguard_bridge.service", "--simulate", "--socket", sock],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        if os.path.exists(sock):
            break
        time.sleep(0.05)
    app = subprocess.Popen([sys.executable, "-m", "vanguard.web.cli", "--socket", sock, "--port", str(port),
                            "--log-dir", logdir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/api/state", timeout=1).json()["bridge"]["connected"]:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("app never connected to the bridge")
        yield base, port, logdir, app
    finally:
        for p in (app, bridge):
            p.terminate()
        for p in (app, bridge):
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()


def ws_state(port):
    with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=5) as w:
        return json.loads(w.recv(timeout=5))


def wait_for(port, pred, timeout=15.0):
    end = time.time() + timeout
    d = None
    while time.time() < end:
        d = ws_state(port)
        if pred(d):
            return d
        time.sleep(0.2)
    raise AssertionError(f"condition not met; contactors={d['contactors']['summary']} bridge={d['bridge']}")


def all_signals(d):
    out = []
    out += [x for g in d["headline"].values() for x in g]
    out += d["cells"] + [c["balancing"] for c in d["cells"]] + d["temps"] + d["temp_stats"]
    out += d["contactors"]["drivers"] + d["contactors"]["sequence"] + d["inputs"] + d["counters"] + d["host_tx"]
    out += d["faults"]["can1_0x120"] + d["faults"]["can2_0x18FF08F3_latched"] + d["faults"]["can2_0x18FF08F3_live"]
    return out


def test_start_close_contactors_stop_open(stack):
    base, port, logdir, app = stack
    d0 = ws_state(port)
    assert d0["bridge"]["connected"] and d0["bridge"]["mode"] == "simulate" and d0["bridge"]["state"] == "STOPPED"

    r = httpx.post(base + "/api/start", json={"mode": "discharge"})
    assert r.status_code == 200 and r.json()["reply"] == "OK START DISCHARGE"
    d = wait_for(port, lambda s: s["contactors"]["summary"] == "CLOSED" and s["rates"]["bus2_rx"] > 5)
    assert d["bridge"]["state"] == "RUNNING DISCHARGE" and d["bridge"]["app_wants_running"]
    assert httpx.post(base + "/api/start", json={"mode": "charge"}).status_code == 409  # already started

    # point 3: everything is present, with raw, confidence tag and units
    assert len(d["cells"]) == 14 and len(d["temps"]) == 14
    assert all(c["balancing"]["seen"] for c in d["cells"])
    assert d["cell_stats"]["min_mv"] is not None and d["cell_stats"]["spread_mv"] is not None
    assert sum(c["is_min"] for c in d["cells"]) == 1 and sum(c["is_max"] for c in d["cells"]) == 1
    assert d["balancing_mask"]["seen"]
    assert all(t["seen"] and "valid" in t for t in d["temps"])
    hl = d["headline"]
    for k in ("pack_voltage", "current", "soc", "soh", "limits"):
        assert any(s["seen"] for s in hl[k]), k
    assert all("sign unverified" in s["label_note"] for s in hl["current"])
    assert {f["name"] for f in d["faults"]["can1_0x120"]} == {n for _, _, n, _, _ in FAULT_BITS}
    assert {f["name"] for f in d["faults"]["can2_0x18FF08F3_latched"]} == set(LATCHED_WORD_BITS.values())
    assert {f["name"] for f in d["faults"]["can2_0x18FF08F3_live"]} == set(LIVE_WORD_BITS.values())
    assert d["contactors"]["drivers"][0]["seen"] and d["contactors"]["drivers"][1]["seen"]
    assert "precharge" in d["contactors"]["precharge"]["note"].lower()
    assert sum(1 for s in d["inputs"] if s["seen"]) >= 4 and sum(1 for s in d["counters"] if s["seen"]) >= 10
    assert d["rates"]["bus1_rx"] > 5 and d["rates"]["bus2_rx"] > 5 and d["rates"]["bus1_tx"] > 5
    assert d["status_lines"] and d["bridge"]["config"]
    for s in all_signals(d):
        assert s["seen"] and s["conf"] in ("C", "I", "?") and s["raw"] is not None and "unit" in s, s["key"]

    assert httpx.post(base + "/api/clear", json={}).json()["accepted"]
    st = httpx.post(base + "/api/stop", json={})
    assert st.json()["reply"] == "OK STOP"
    d = wait_for(port, lambda s: s["contactors"]["summary"] == "OPEN")
    assert d["bridge"]["state"] == "STOPPED" and not d["bridge"]["app_wants_running"]

    # the frame log written for this session replays cleanly
    files = sorted(f for f in os.listdir(logdir) if f.endswith(".jsonl") and not f.endswith(".status.jsonl"))
    assert len(files) == 1
    time.sleep(1.2)  # logger flush interval
    frames = list(read_jsonl(open(os.path.join(logdir, files[0]))))
    assert len(frames) > 100 and {f.direction for f in frames} == {"rx", "tx"}
    status = [json.loads(x)["text"] for x in open(os.path.join(logdir, files[0][:-6] + ".status.jsonl"))]
    assert any(t.startswith("OK START") for t in status)


def test_contactors_open_when_hb_stops(stack):
    """Freeze the app (no STOP, socket stays open): heartbeats cease, the bridge watchdog opens the pack,
    and the resumed app does not restart anything."""
    base, port, logdir, app = stack
    httpx.post(base + "/api/start", json={"mode": "discharge"})
    wait_for(port, lambda s: s["contactors"]["summary"] == "CLOSED")
    os.kill(app.pid, signal.SIGSTOP)
    try:
        time.sleep(3.0)
    finally:
        os.kill(app.pid, signal.SIGCONT)
    d = wait_for(port, lambda s: s["contactors"]["summary"] == "OPEN" and s["bridge"]["state"] == "STOPPED")
    assert not d["bridge"]["app_wants_running"] and d["bridge"]["halt_reason"]
    time.sleep(1.5)
    d = ws_state(port)
    assert d["bridge"]["state"] == "STOPPED" and d["contactors"]["summary"] == "OPEN"


def test_replay_mode_cli_end_to_end(tmp_path, stack):
    base, port, logdir, app = stack
    httpx.post(base + "/api/start", json={"mode": "discharge"})
    wait_for(port, lambda s: s["contactors"]["summary"] == "CLOSED")
    time.sleep(2.2)
    log = [os.path.join(logdir, f) for f in os.listdir(logdir) if f.endswith(".jsonl") and "status" not in f][0]
    httpx.post(base + "/api/stop", json={})
    rport = free_port()
    p = subprocess.Popen([sys.executable, "-m", "vanguard.web.cli", "--replay", log, "--speed", "50", "--port",
                          str(rport)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        d = None
        for _ in range(100):
            try:
                d = ws_state(rport)
                if d["replay"]["finished"]:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        assert d["mode"] == "replay" and d["controls"]["enabled"] is False
        assert d["replay"]["finished"] and d["cell_stats"]["min_mv"] is not None
        assert httpx.post(f"http://127.0.0.1:{rport}/api/stop", json={}).status_code == 409
    finally:
        p.terminate()
        p.wait(5)
