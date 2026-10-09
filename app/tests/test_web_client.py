import asyncio
import os
import tempfile

from vanguard.web import commands
from vanguard.web.client import Backoff, BridgeLink, ClientCore
from vanguard.web.state import PackState, StatusLog
from webhelpers import make_core


def run_ticks(core, start, end, step=0.05):
    n0, n1 = round(start / step), round(end / step)
    for i in range(n0, n1 + 1):
        core.tick(i * step)


def test_no_hb_before_start():
    core, sent = make_core()
    run_ticks(core, 0, 5)
    assert sent == []


def test_hb_cadence_after_start():
    core, sent = make_core()
    assert core.request_start("discharge", 0.0).accepted
    assert sent == ["START DISCHARGE"]
    run_ticks(core, 0.0, 1.0)
    assert sent.count("HB") == 4  # 0.25, 0.5, 0.75, 1.0
    core.tick(1.1)
    assert sent.count("HB") == 4
    core.tick(1.25)
    assert sent.count("HB") == 5


def test_stop_ends_hb():
    core, sent = make_core()
    core.request_start("charge", 0.0)
    run_ticks(core, 0, 0.6)
    n = sent.count("HB")
    assert core.request_stop(0.7).command == "STOP"
    run_ticks(core, 0.7, 5)
    assert sent.count("HB") == n and sent[-1] == "STOP"
    assert not core.want_run


def test_txerror_stops_hb_and_drops():
    core, sent = make_core()
    core.request_start("discharge", 0.0)
    core.feed_line("S OK START DISCHARGE", 0.01)
    core.feed_line("S TXERROR no ACK", 0.3)
    assert core.drop_requested and core.fatal and not core.want_run
    assert "no ACK" in core.halt_reason
    n = sent.count("HB")
    run_ticks(core, 0.3, 5)
    assert sent.count("HB") == n


def test_txrefused_is_fatal():
    core, _ = make_core()
    core.request_start("discharge", 0.0)
    core.feed_line("S TXREFUSED 0105 bad", 0.1)
    assert core.drop_requested and not core.want_run


def test_start_refusal_stops_hb():
    core, sent = make_core()
    core.request_start("discharge", 0.0)
    core.feed_line("S ERR START DISCHARGE refused: no pack CAN1 traffic seen in the last 250 ms", 0.05)
    assert not core.want_run and "refused START" in core.halt_reason
    run_ticks(core, 0.05, 3)
    assert "HB" not in sent
    assert core.replies["START"]["ok"] is False


def test_unrelated_error_does_not_stop():
    core, _ = make_core()
    core.request_start("discharge", 0.0)
    core.feed_line("S OK START DISCHARGE", 0.01)
    core.feed_line("S ERR CLEAR refused: too soon", 0.1)
    assert core.want_run


def test_bridge_stop_and_state_stopped():
    core, sent = make_core()
    core.request_start("discharge", 0.0)
    core.feed_line("S STATE STOPPED", 0.0)  # state line from before START was handled: ignored
    assert core.want_run
    core.feed_line("S OK START DISCHARGE", 0.01)
    core.feed_line("S STATE STOPPED", 1.0)
    assert not core.want_run
    core2, _ = make_core()
    core2.request_start("discharge", 0.0)
    core2.feed_line("S STOP watchdog: no HB or START for 1 s", 1.0)
    assert not core2.want_run and "watchdog" in core2.halt_reason


def test_disconnect_stops_hb():
    core, sent = make_core()
    core.request_start("discharge", 0.0)
    core.on_disconnected(0.2, "closed by the bridge")
    core.sink = None
    run_ticks(core, 0.2, 5)
    assert sent == ["START DISCHARGE"]
    assert not core.want_run and "lost" in core.halt_reason


def test_never_resends_start_after_reconnect():
    core, sent = make_core()
    core.request_start("discharge", 0.0)
    core.on_disconnected(0.3, "closed")
    core.sink = sent.append
    core.on_connected(5.0)
    run_ticks(core, 5.0, 30.0)
    assert sent == ["START DISCHARGE"]
    assert not core.want_run


def test_local_refusals():
    core, sent = make_core()
    assert core.request_start("bogus", 0).accepted is False
    assert core.request_start(None, 0).accepted is False
    assert core.request_sleep(2, 0).accepted is False
    assert core.request_sleep(True, 0).accepted is False
    assert core.request_start("discharge", 0).accepted
    assert not core.request_start("charge", 0.1).accepted  # already started
    assert not core.request_sleep(1, 0.1).accepted  # running
    core.set_hold(100)
    core.request_stop(1)
    assert not core.request_start("discharge", 2).accepted  # cooling down
    assert core.request_start("discharge", 101).accepted
    assert set(sent) <= commands.ALLOWED


def test_not_connected_refuses():
    core = ClientCore(PackState(), StatusLog())
    assert not core.request_start("discharge", 0).accepted
    assert not core.request_clear(0).accepted
    assert core.request_stop(0).accepted and core.request_stop(0).command is None


def test_sink_never_gets_other_text():
    core, sent = make_core()
    for fn in (lambda: core.request_start("discharge", 0), lambda: core.request_clear(1), lambda: core.request_stop(2),
               lambda: core.request_sleep(3, 3), lambda: core.request_sleep(1, 4)):
        fn()
    run_ticks(core, 0, 3)
    assert set(sent) <= commands.ALLOWED


def test_backoff_grows_and_resets():
    b = Backoff(2.0, 10.0, 10.0)
    assert [b.next_delay(0, True) for _ in range(5)] == [2.0, 4.0, 8.0, 10.0, 10.0]
    assert b.next_delay(30, False) == 2.0  # stable connection resets
    assert b.next_delay(1, False) == 4.0


def test_link_reconnects_with_backoff_and_never_resends_start():
    async def scenario():
        d = tempfile.mkdtemp(dir="/tmp", prefix="vgt")
        path = os.path.join(d, "s.sock")
        received = []
        conns = []

        async def handle(reader, writer):
            conns.append(writer)
            writer.write(b"S MODE simulate (no CAN hardware)\nS STATE STOPPED\n")
            await writer.drain()
            idx = len(conns)
            try:
                while True:
                    ln = await reader.readline()
                    if not ln:
                        break
                    received.append((idx, ln.decode().strip()))
                    if ln.startswith(b"START"):
                        writer.write(b"S OK START DISCHARGE\n")
                        await writer.drain()
            except ConnectionError:
                pass

        server = await asyncio.start_unix_server(handle, path)
        core = ClientCore(PackState(), StatusLog())
        link = BridgeLink(core, path, backoff=Backoff(0.1, 0.4, 5.0), tick_s=0.02)
        link.start()
        for _ in range(100):
            if core.connected:
                break
            await asyncio.sleep(0.02)
        assert core.connected and core.bridge_mode == "simulate"
        assert core.request_start("discharge", link.clock()).accepted
        await asyncio.sleep(0.8)
        hbs = [r for r in received if r[1] == "HB"]
        assert 2 <= len(hbs) <= 5  # about 250 ms apart
        conns[0].close()  # bridge goes away
        await asyncio.sleep(0.1)
        assert not core.connected and not core.want_run
        for _ in range(100):
            if core.connected:
                break
            await asyncio.sleep(0.02)
        assert core.connected and len(conns) == 2
        await asyncio.sleep(0.8)
        second = [r for r in received if r[0] == 2]
        assert second == []  # no START and no HB on the new connection
        assert [r[1] for r in received].count("START DISCHARGE") == 1
        await link.close()
        server.close()
    asyncio.run(scenario())
