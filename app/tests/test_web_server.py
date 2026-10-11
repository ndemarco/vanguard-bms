import json
import random
import re

import pytest
from fastapi.testclient import TestClient

from vanguard.web import cli, commands, server
from vanguard.web.client import ClientCore
from vanguard.web.replay import ReplaySource
from vanguard.web.server import AppController, create_app
from vanguard.web.state import PackState, StatusLog
from webhelpers import frame

BASE = "http://127.0.0.1:8000"


@pytest.fixture(autouse=True)
def fast_reply(monkeypatch):
    monkeypatch.setattr(server, "REPLY_WAIT_S", 0.01)


def live_app(connected=True):
    state, status = PackState(), StatusLog()
    core = ClientCore(state, status)
    sent = []
    core.sink = sent.append
    if connected:
        core.on_connected(0.0)
    ctl = AppController(mode="live", state=state, status=status, core=core, clock=lambda: 1000.0)
    return ctl, core, sent


def test_state_endpoint_and_static_pages():
    ctl, core, sent = live_app()
    with TestClient(create_app(ctl, allowed_hosts={"127.0.0.1", "localhost"}), base_url=BASE) as c:
        r = c.get("/api/state")
        assert r.status_code == 200 and r.json()["mode"] == "live" and r.json()["controls"]["enabled"]
        for path in ("/", "/app.js", "/style.css"):
            r = c.get(path)
            assert r.status_code == 200
            assert "default-src 'none'" in r.headers["content-security-policy"]
        assert c.get("/docs").status_code == 404 and c.get("/static/../x").status_code == 404


def test_start_stop_endpoints_send_fixed_strings():
    ctl, core, sent = live_app()
    with TestClient(create_app(ctl), base_url=BASE) as c:
        assert c.post("/api/start", json={"mode": "charge"}).json()["command"] == "START CHARGE"
        assert c.post("/api/stop", json={}).json()["command"] == "STOP"
        assert c.post("/api/clear", json={}).json()["command"] == "CLEAR"
        assert c.post("/api/sleep", json={"level": 3}).json()["command"] == "SLEEP 3"
    assert sent == ["START CHARGE", "STOP", "CLEAR", "SLEEP 3"]


def test_bridge_reply_is_returned():
    ctl, core, sent = live_app()
    with TestClient(create_app(ctl), base_url=BASE) as c:
        # the reply arrives while the endpoint waits
        orig = core.sink

        def sink(cmd):
            orig(cmd)
            if cmd == "CLEAR":
                core.feed_line("S ERR CLEAR refused: only while RUNNING", 1000.0)
        core.sink = sink
        r = c.post("/api/clear", json={})
        assert r.status_code == 200 and r.json()["reply"] == "ERR CLEAR refused: only while RUNNING"


def test_disabled_when_not_connected():
    ctl, core, sent = live_app(connected=False)
    with TestClient(create_app(ctl), base_url=BASE) as c:
        assert c.get("/api/state").json()["controls"]["enabled"] is False
        assert c.post("/api/start", json={"mode": "discharge"}).status_code == 409
        assert c.post("/api/sleep", json={"level": 1}).status_code == 409
    assert sent == []


def test_replay_mode_disables_controls():
    st, log = PackState(), StatusLog()
    ctl = AppController(mode="replay", state=st, status=log, replay=ReplaySource([frame(1, 0x112, {})], st, log))
    with TestClient(create_app(ctl), base_url=BASE) as c:
        s = c.get("/api/state").json()
        assert s["mode"] == "replay" and s["controls"]["enabled"] is False
        for path, body in (("/api/start", {"mode": "discharge"}), ("/api/stop", {}), ("/api/clear", {}),
                           ("/api/sleep", {"level": 1})):
            assert c.post(path, json=body).status_code == 409


def test_websocket_pushes_snapshot():
    ctl, core, sent = live_app()
    with TestClient(create_app(ctl), base_url=BASE) as c:
        with c.websocket_connect("/ws") as ws:
            d = json.loads(ws.receive_text())
            assert d["mode"] == "live" and len(d["cells"]) == 14


def test_guard_rejects_foreign_host_origin_and_content_type():
    ctl, core, sent = live_app()
    app = create_app(ctl, allowed_hosts={"127.0.0.1", "localhost"})
    with TestClient(app, base_url=BASE) as c:
        assert c.get("/api/state", headers={"host": "evil.example"}).status_code == 421
        r = c.post("/api/start", json={"mode": "discharge"}, headers={"origin": "http://evil.example"})
        assert r.status_code == 403
        assert c.post("/api/start", json={"mode": "discharge"}, headers={"origin": "null"}).status_code == 403
        r = c.post("/api/start", content='{"mode":"discharge"}', headers={"content-type": "text/plain"})
        assert r.status_code == 415
        assert c.post("/api/start", content='mode=discharge',
                      headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 415
        ok = c.post("/api/stop", json={}, headers={"origin": "http://127.0.0.1:8000"})
        assert ok.status_code == 200
    assert sent == ["STOP"]


def test_fuzz_http_inputs_only_allowed_commands():
    rng = random.Random(1234)
    junk = ["discharge", "charge", "DISCHARGE", "discharge\nSTOP", "charge; HB", "", " ", "START DISCHARGE", "1", "3",
            "2", 1, 3, 2, 0, -1, 10**30, 1.0, 3.0, True, False, None, [], {}, ["discharge"], {"mode": "charge"},
            "\x00", "💥", "a" * 5000, "SLEEP 2", "HB", "STOP\nSTART CHARGE"]
    ctl, core, sent = live_app()
    paths = ["/api/start", "/api/stop", "/api/clear", "/api/sleep", "/api/hb", "/api/raw", "/api/command",
             "/api/start/", "/api/../start", "/api/start?mode=charge", "/api/sleep?level=1", "/"]
    expected_ok = []
    with TestClient(create_app(ctl), base_url=BASE) as c:
        for _ in range(400):
            path = rng.choice(paths)
            kind = rng.choice(["mode", "level", "both", "raw", "list"])
            if kind == "mode":
                body = {"mode": rng.choice(junk)}
            elif kind == "level":
                body = {"level": rng.choice(junk)}
            elif kind == "both":
                body = {"mode": rng.choice(junk), "level": rng.choice(junk), "command": rng.choice(junk)}
            elif kind == "list":
                body = [rng.choice(junk)]
            else:
                body = rng.choice(junk)
            hdr = {"content-type": "application/json"}
            if kind == "raw":
                content = rng.choice(["{", "null", "'discharge'", "\x00\x01", "[]", '{"mode":"discharge"} extra'])
                c.post(path, content=content, headers=hdr)
            else:
                c.post(path, json=body)
            core.request_stop(0.0)  # keep the app able to accept the next START
            core.hold_until = 0.0
    assert sent, "fuzz sent nothing at all"
    assert set(sent) <= commands.ALLOWED
    assert "HB" not in sent  # endpoints never send HB; only the ticker does


def test_fuzz_exact_inputs_map_to_exact_commands():
    ctl, core, sent = live_app()
    starts = []
    with TestClient(create_app(ctl), base_url=BASE) as c:
        for body in ({"mode": "discharge"}, {"mode": "charge"}, {"mode": "Discharge"}, {"mode": "charge "},
                     {"mode": "discharge", "x": 1}):
            core.request_stop(0.0)
            c.post("/api/start", json=body)
            starts.extend(s for s in sent if s.startswith("START"))
            sent.clear()
        core.request_stop(0.0)
        sent.clear()
        for lv in (1, 3, 2, "1", True, 1.0):
            c.post("/api/sleep", json={"level": lv})
    assert starts == ["START DISCHARGE", "START CHARGE"]
    assert [s for s in sent if s.startswith("SLEEP")] == ["SLEEP 1", "SLEEP 3"]


def test_binds_loopback_by_default(capsys):
    assert cli.parse_args([]).host == "127.0.0.1"
    seen = {}
    rc = cli.main(["--no-log", "--socket", "/tmp/none.sock"], run=lambda app, **kw: seen.update(kw))
    assert rc == 0 and seen["host"] == "127.0.0.1"
    assert "WARNING" not in capsys.readouterr().err


def test_warns_loudly_on_non_loopback(capsys):
    seen = {}
    cli.main(["--no-log", "--host", "0.0.0.0", "--socket", "/tmp/none.sock"], run=lambda app, **kw: seen.update(kw))
    err = capsys.readouterr().err
    assert "WARNING" in err and "not a loopback" in err and seen["host"] == "0.0.0.0"
    assert cli.bind_warning_text("::1") is None and cli.bind_warning_text("localhost") is None
    assert cli.bind_warning_text("192.168.1.5")


def test_allowed_hosts():
    a = cli.parse_args([])
    assert cli.allowed_hosts_for(a) >= {"127.0.0.1", "localhost"}
    assert cli.allowed_hosts_for(cli.parse_args(["--host", "0.0.0.0"])) is None
    assert "pi.local" in cli.allowed_hosts_for(cli.parse_args(["--allow-host", "pi.local"]))
