import asyncio
import json
import os
from datetime import datetime

import pytest

from vanguard.web.client import ClientCore
from vanguard.web.logger import SessionLogger
from vanguard.web.replay import ReplaySource, load_log
from vanguard.web.state import PackState, StatusLog
from vanguard.wire import read_jsonl
from webhelpers import frame, line


def test_log_round_trips_through_replay(tmp_path):
    d = str(tmp_path / "logs")
    core = ClientCore(PackState(), StatusLog())
    lg = SessionLogger(d, wall=lambda: datetime(2026, 1, 2, 3, 4, 5))
    core.on_frame, core.on_status = lg.log_frame, lg.log_status
    core.on_connected(1.0)
    lines = [line(1, 0x112, {"pack_voltage_v": 52.3, "contactor_a_driven": True}, 1_000_000),
             line(2, 0x18FF03F3, {"cell1_mv": 3700, "cell2_mv": 3701}, 3_000_000),
             "T 1 105 8 0011000000000000 5000000",
             "S STATE RUNNING DISCHARGE"]
    for i, ln in enumerate(lines):
        core.feed_line(ln, 2.0 + i)
    lg.close()
    assert os.path.basename(lg.frames_path) == "vanguard-20260102-030405.jsonl"
    rows = [json.loads(x) for x in open(lg.frames_path)]
    assert [r["direction"] for r in rows] == ["rx", "rx", "tx"]
    assert set(rows[0]) == {"ts_us", "bus", "id", "dlc", "data", "direction"} and rows[0]["id"] == 0x112
    status = [json.loads(x) for x in open(lg.status_path)]
    assert status[-1]["text"] == "STATE RUNNING DISCHARGE"
    frames = load_log(lg.frames_path)
    assert len(frames) == 3 and frames == list(read_jsonl(open(lg.frames_path)))

    st, log = PackState(), StatusLog()
    slept = []

    async def fake_sleep(s):
        slept.append(s)
    rp = ReplaySource(frames, st, log, speed=2.0, sleep=fake_sleep)
    asyncio.run(rp.run())
    assert rp.finished and rp.index == 3
    assert st.get("c2_cells_1_4.cell1_mv").raw == 3700 and st.get("status_voltages.pack_voltage_v").value == 52.3
    assert st.tx_signals["host_keepalive.enable"].value is True
    assert sum(slept) == pytest.approx(2.0)  # 4 s of log time at 2x
    assert rp.now() == 5.0


def test_new_file_per_session_even_in_same_second(tmp_path):
    w = lambda: datetime(2026, 1, 2, 3, 4, 5)  # noqa: E731
    a = SessionLogger(str(tmp_path), wall=w)
    b = SessionLogger(str(tmp_path), wall=w)
    assert a.frames_path != b.frames_path and os.path.exists(a.frames_path)
    a.close(); b.close()


def test_logger_disables_itself_on_os_error(tmp_path):
    lg = SessionLogger(str(tmp_path))

    class Boom:
        def write(self, _):
            raise OSError("disk full")

        def flush(self):
            pass

        def close(self):
            pass
    lg._frames = Boom()
    lg.log_frame(frame(1, 0x112, {}), 0.0)
    assert "disk full" in lg.error
    lg.log_frame(frame(1, 0x112, {}), 0.0)  # no raise


def test_replay_cli_builds_controller_without_bridge(tmp_path):
    from vanguard.web import cli
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps({"ts_us": 1, "bus": 1, "id": 0x112, "dlc": 8, "data": "0" * 16, "direction": "rx"}) + "\n")
    ctl = cli.build_controller(cli.parse_args(["--replay", str(p), "--speed", "4"]))
    assert ctl.mode == "replay" and ctl.core is None and ctl.link is None and ctl.replay.speed == 4
    with pytest.raises(SystemExit):
        cli.parse_args(["--speed", "0"])


def test_bad_replay_file_is_reported(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text("{not json\n")
    with pytest.raises(SystemExit):
        load_log(str(p))
