import pathlib

import pytest

from vanguard_bridge import netcheck, netsetup
from vanguard_bridge.config import Config
from vanguard_bridge.netcheck import (check_all, check_can1_filter, check_can2_filter, check_link, check_qdisc,
                                      parse_ip_details)

S = pathlib.Path(__file__).parent / "samples"


def sample(name):
    return (S / name).read_text()


CAN1_OK = sample("filter_can1_ok.txt")
CAN2_OK = sample("filter_can2_dropall_ok.txt")


def test_parse_ip_details():
    i = parse_ip_details(sample("ip_can2_listen_only_ok.txt"))
    assert i.name == "can1" and "UP" in i.flags and i.is_can and i.bitrate == 500000
    assert i.ctrlmodes == {"LISTEN-ONLY"} and i.can_state == "ERROR-ACTIVE"


def test_link_checks():
    assert check_link(sample("ip_can1_ok.txt"), "can0", want_listen_only=False) == []
    assert check_link(sample("ip_can2_listen_only_ok.txt"), "can1", want_listen_only=True) == []
    errs = check_link(sample("ip_can1_ok.txt").replace("can0:", "can1:"), "can1", want_listen_only=True)
    assert errs == ["can1: listen-only mode is not on"]
    errs = check_link(sample("ip_can2_listen_only_ok.txt").replace("can1:", "can0:"), "can0", want_listen_only=False)
    assert "mapping swapped" in errs[0]
    assert "can0: not UP" in check_link(sample("ip_down.txt"), "can0", want_listen_only=False)
    assert any("bitrate" in e for e in check_link(sample("ip_can1_ok.txt").replace("500000", "250000"), "can0",
                                                   want_listen_only=False))
    assert check_link(sample("ip_can1_ok.txt"), "can1", want_listen_only=False)  # name mismatch
    assert check_link("", "can0", want_listen_only=False)
    assert check_link("Device \"can0\" does not exist.", "can0", want_listen_only=False)
    lb = sample("ip_can1_ok.txt").replace("can state", "can <LOOPBACK> state")
    assert check_link(lb, "can0", want_listen_only=False) == ["can0: loopback mode is on"]


def test_qdisc():
    assert check_qdisc(sample("qdisc_prio_ok.txt"), "can0") == []
    assert check_qdisc(sample("qdisc_default.txt"), "can0")
    assert check_qdisc("", "can0")
    assert check_qdisc("qdisc prio 2: root refcnt 2 bands 3", "can0")


def test_can1_filter_ok():
    assert check_can1_filter(CAN1_OK, "can0") == []


@pytest.mark.parametrize("mutate", [
    lambda t: "",                                                          # no filter
    lambda t: t.split("filter protocol all pref 3")[0],                    # no drop-everything rule
    lambda t: t.split("filter protocol all pref 2")[0],                    # missing the 0x515 rule
    lambda t: t.replace("05010000/ffffffff", "06010000/ffffffff"),         # wrong ID
    lambda t: t.replace("05010000/ffffffff", "05010000/ffff0000"),         # looser ID mask (matches flag bits)
    lambda t: t.replace("08000000/ff000000", "08000000/00000000"),         # length not checked
    lambda t: t.replace("00110000/fefdffff", "00110000/fcfdffff"),         # D0 bit 1 unchecked
    lambda t: t.replace("00110000/fefdffff", "00110000/fefd0000"),         # D2/D3 unchecked
    lambda t: t.replace("00000000/ffffffff at 12", "00000000/ffff0000 at 12"),  # D6/D7 unchecked
    lambda t: t.replace("10000000/dfffffff", "10000000/cfffffff"),         # 0x515 D0 allows deep shutdown
    lambda t: t.replace("gact action drop", "gact action pass"),           # pass instead of drop
    lambda t: t.replace("gact action drop", "gact action continue"),
    lambda t: t.replace("u32 chain 0 fh 800::800", "basic chain 0 fh 800::800"),  # wrong kind
    lambda t: t.replace("protocol all pref 1", "protocol ip pref 1"),
    lambda t: t + t.replace("pref 1", "pref 4").replace("fh 800", "fh 803"),     # a fourth rule
    lambda t: t.replace("flowid 1:1", "flowid 1:2", 1),                    # allow rule steered elsewhere
    lambda t: t.replace("\taction order 1: gact action drop\n", ""),       # no action on the drop rule
    lambda t: t.replace("action order 1: gact action drop",
                        "action order 1: gact action drop\n\taction order 2: gact action pass"),
    lambda t: t.replace("bkt 0 flowid 1:1", "bkt 0 link 801: flowid 1:1", 1),  # jump to another table
    lambda t: t.replace("match 08000000/ff000000 at 4", "match 08000000/ff000000 at 4\n  offset 0f00"),
])
def test_can1_filter_rejects(mutate):
    assert check_can1_filter(mutate(CAN1_OK), "can0")


def _u32_verdict(raw: bytes) -> bool:
    """Evaluate CAN1_U32_RULES the way the kernel does: first matching rule wins."""
    for rule in sorted(netcheck.CAN1_U32_RULES, key=lambda r: r.pref):
        if all(int.from_bytes(raw[off:off + 4], "big") & mask == value for value, mask, off in rule.matches):
            return rule.flowid is not None
    raise AssertionError("no rule matched")


def test_can1_u32_rules_equal_the_software_allowlist():
    """The kernel layer must allow exactly what txgate.check_tx allows (padding aside)."""
    import struct
    from vanguard_bridge.canframe import CAN_EFF_FLAG, CAN_RTR_FLAG, CAN_ERR_FLAG, FRAME_FMT
    from vanguard_bridge.txgate import check_tx
    n_allowed = 0
    for word in (0x105, 0x515, 0x614, 0x106, 0x104, 0x7FF, 0x000, 0x105 | CAN_EFF_FLAG, 0x105 | CAN_RTR_FLAG,
                 0x515 | CAN_ERR_FLAG, 0x515 | CAN_EFF_FLAG, 0x0CFF00F3 | CAN_EFF_FLAG, 0x405, 0x505):
        for dlc in (0, 4, 7, 8):
            for d0 in range(256):
                for d1 in range(256):
                    data = bytes([d0, d1, 0, 0, 0, 0, 0, 0])
                    raw = struct.pack(FRAME_FMT, word, dlc, b"\0\0\0", data)
                    want = check_tx(raw) is None
                    assert _u32_verdict(raw) == want, (hex(word), dlc, d0, d1)
                    n_allowed += want
    assert n_allowed == 2 * 2 + 2  # 0x105: D0{00,01} x D1{11,13}; 0x515: D0{10,30}
    for i in range(2, 8):  # any non-zero payload byte beyond D1 is refused
        for v in (1, 0x80, 0xFF):
            data = bytearray(8)
            data[1] = 0x11
            data[i] = v
            for word in (0x105, 0x515):
                raw = struct.pack(FRAME_FMT, word, 8, b"\0\0\0", bytes(data))
                assert not _u32_verdict(raw)


def test_can2_filter():
    assert check_can2_filter(CAN2_OK, "can1") == []
    assert check_can2_filter("", "can1")
    assert check_can2_filter(CAN1_OK, "can1")  # three u32 rules, not a single drop-all
    assert check_can2_filter(CAN2_OK.replace("drop", "pass"), "can1")


CFG = Config("can0", "spi1.1", "can1", "spi1.2")


def fake_env(overrides=None):
    out = {
        ("ip", "can0"): (0, sample("ip_can1_ok.txt")),
        ("ip", "can1"): (0, sample("ip_can2_listen_only_ok.txt")),
        ("qdisc", "can0"): (0, sample("qdisc_prio_ok.txt")),
        ("qdisc", "can1"): (0, sample("qdisc_prio_ok.txt")),
        ("filter", "can0"): (0, CAN1_OK),
        ("filter", "can1"): (0, CAN2_OK),
    }
    out.update(overrides or {})
    calls = []

    def run(argv):
        calls.append(argv)
        dev = argv[argv.index("dev") + 1]
        key = "ip" if argv[0] == "ip" else argv[1]
        return out[(key, dev)]
    return run, calls


SPI = {"can0": "spi1.1", "can1": "spi1.2"}.get


def test_check_all_ok_and_commands():
    run, calls = fake_env()
    assert check_all(CFG, run, SPI) == []
    assert ["tc", "filter", "show", "dev", "can0", "parent", "1:"] in calls
    assert ["ip", "-details", "link", "show", "dev", "can1"] in calls


@pytest.mark.parametrize("key,value,needle", [
    (("ip", "can1"), (0, sample("ip_can1_ok.txt").replace("can0:", "can1:")), "listen-only mode is not on"),
    (("filter", "can0"), (0, ""), "expected u32 rules"),
    (("filter", "can0"), (2, "Error: Parent Qdisc doesn't exists."), "tc filter failed"),
    (("filter", "can1"), (0, ""), "pack CAN2"),
    (("qdisc", "can0"), (0, sample("qdisc_default.txt")), "root qdisc"),
    (("ip", "can0"), (1, 'Device "can0" does not exist.'), "ip failed"),
])
def test_check_all_refuses(key, value, needle):
    run, _ = fake_env({key: value})
    errs = check_all(CFG, run, SPI)
    assert errs and any(needle in e for e in errs)


def test_check_all_detects_swapped_enumeration():
    run, _ = fake_env()
    errs = check_all(CFG, run, {"can0": "spi1.2", "can1": "spi1.1"}.get)
    assert len([e for e in errs if "SPI device" in e]) == 2


def test_tc_drop_all_guard_does_not_need_listen_only():
    from dataclasses import replace
    cfg = replace(CFG, can2_guard="tc-drop-all")
    run, _ = fake_env({("ip", "can1"): (0, sample("ip_can1_ok.txt").replace("can0:", "can1:"))})
    assert check_all(cfg, run, SPI) == []
    run, _ = fake_env({("filter", "can1"): (0, "")})
    assert check_all(cfg, run, SPI)


def test_run_cmd_handles_missing_binary():
    rc, out = netcheck.run_cmd(["/nonexistent/tc"])
    assert rc != 0


# ---------------------------------------------------------------- netsetup

def test_netsetup_commands():
    cmds = [c for c, _ in netsetup.commands(CFG)]
    assert ["ip", "link", "set", "dev", "can0", "type", "can", "bitrate", "500000", "listen-only", "off"] in cmds
    assert ["ip", "link", "set", "dev", "can1", "type", "can", "bitrate", "500000", "listen-only", "on"] in cmds
    can0_filters = [c for c in cmds if c[:5] == ["tc", "filter", "add", "dev", "can0"]]
    assert [c[9:11] for c in can0_filters] == [["prio", "1"], ["prio", "2"], ["prio", "3"]]
    assert can0_filters[2][-2:] == ["action", "drop"]
    assert all(c[-2:] == ["flowid", "1:1"] for c in can0_filters[:2])
    assert ["tc", "filter", "add", "dev", "can1", "parent", "1:", "protocol", "all", "prio", "1", "basic", "action",
            "drop"] in cmds
    # filters are installed before each interface is brought up
    for dev in ("can0", "can1"):
        i_filter = cmds.index(next(c for c in cmds if c[:2] == ["tc", "filter"] and dev in c))
        i_up = cmds.index(["ip", "link", "set", "dev", dev, "up"])
        assert i_filter < i_up
    from dataclasses import replace
    cmds = [c for c, _ in netsetup.commands(replace(CFG, can2_guard="tc-drop-all"))]
    assert ["ip", "link", "set", "dev", "can1", "type", "can", "bitrate", "500000", "listen-only", "off"] in cmds


def _write_cfg(tmp_path, extra=""):
    p = tmp_path / "bridge.toml"
    p.write_text('[pack_can1]\ninterface = "can0"\nspi_device = "spi1.1"\n'
                 '[pack_can2]\ninterface = "can1"\nspi_device = "spi1.2"\n' + extra)
    return str(p)


def test_netsetup_main_runs_and_verifies(tmp_path):
    import io
    run, calls = fake_env()
    executed = []

    def runner(argv):
        executed.append(argv)
        if argv[1] in ("link", "qdisc", "filter") and ("set" in argv or "add" in argv or "del" in argv):
            return 0, ""
        return run(argv)
    out = io.StringIO()
    assert netsetup.main(["--config", _write_cfg(tmp_path)], run=runner, spi_of=SPI, out=out) == 0
    assert "CONFIG pack-can1 interface=can0" in out.getvalue()


def test_netsetup_refuses_wrong_spi(tmp_path):
    import io
    executed = []
    rc = netsetup.main(["--config", _write_cfg(tmp_path)], run=lambda a: executed.append(a) or (0, ""),
                       spi_of={"can0": "spi1.2", "can1": "spi1.1"}.get, out=io.StringIO())
    assert rc == 1 and executed == []


def test_netsetup_stops_on_command_failure(tmp_path):
    import io
    executed = []

    def runner(argv):
        executed.append(argv)
        return (1, "RTNETLINK answers: Operation not permitted") if argv[:2] == ["ip", "link"] else (0, "")
    assert netsetup.main(["--config", _write_cfg(tmp_path)], run=runner, spi_of=SPI, out=io.StringIO()) == 1
    assert len(executed) == 1


# ---------------------------------------------- pack CAN channel bitrate (e.g. a 125 kbit/s charger bus)

def test_can2_bitrate_defaults_to_500k_and_is_configurable():
    from dataclasses import replace
    assert CFG.can2_bitrate == 500_000
    cmds = [c for c, _ in netsetup.commands(replace(CFG, can2_bitrate=125_000))]
    assert ["ip", "link", "set", "dev", "can1", "type", "can", "bitrate", "125000", "listen-only", "on"] in cmds
    assert ["ip", "link", "set", "dev", "can0", "type", "can", "bitrate", "500000", "listen-only", "off"] in cmds  # pack CAN11 unchanged


def test_check_link_uses_the_given_bitrate():
    t = sample("ip_can2_listen_only_ok.txt")
    assert check_link(t, "can1", want_listen_only=True) == []
    assert check_link(t, "can1", want_listen_only=True, bitrate=125_000)  # 500k link does not match 125k config
    assert check_link(t.replace("bitrate 500000", "bitrate 125000"), "can1", want_listen_only=True, bitrate=125_000) == []


def test_config_bitrate_validation():
    from vanguard_bridge.config import ConfigError, parse
    base = {"pack_can1": {"interface": "can0", "spi_device": "spi0.0"},
            "pack_can2": {"interface": "can1", "spi_device": "spi1.0"}}
    assert parse(base).can2_bitrate == 500_000
    ok = {**base, "pack_can2": {**base["pack_can2"], "bitrate": 125000}}
    assert parse(ok).can2_bitrate == 125_000
    assert "bitrate=125000" in parse(ok).mapping_lines()[1]
    assert "bitrate=500000" in parse(ok).mapping_lines()[0]
    for bad in (0, 100000, 1000000, "125000", True):
        with pytest.raises(ConfigError):
            parse({**base, "pack_can2": {**base["pack_can2"], "bitrate": bad}})
