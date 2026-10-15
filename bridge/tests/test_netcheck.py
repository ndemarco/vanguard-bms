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
    assert check_can1_filter(CAN1_OK.replace("NOT canid", "not canid"), "can0") == []


@pytest.mark.parametrize("mutate", [
    lambda t: "",                                                          # no filter
    lambda t: t.replace("NOT ", ""),                                       # not inverted
    lambda t: t.replace("sff 0x515", "sff 0x515 sff 0x614"),               # extra ID
    lambda t: t.replace(" sff 0x515", ""),                                 # missing ID
    lambda t: t.replace("sff 0x515", "eff 0x515"),                         # wrong frame format
    lambda t: t.replace("sff 0x105", "sff 0x105:0x700"),                   # mask
    lambda t: t.replace("sff 0x105", "sff 0x100:0x700"),
    lambda t: t.replace("gact action drop", "gact action pass"),           # pass instead of drop
    lambda t: t.replace("gact action drop", "gact action continue"),
    lambda t: t.replace("basic", "u32"),
    lambda t: t.replace("protocol all", "protocol ip"),
    lambda t: t + t.replace("handle 0x1", "handle 0x2"),                   # a second filter
    lambda t: t.replace("NOT canid(sff 0x105 sff 0x515)", "NOT canid(sff 0x105 sff 0x515)\nAND meta(...)"),
    lambda t: t.replace("NOT canid(sff 0x105 sff 0x515)", "NOT cmp(u8 at 0 layer 2 eq 5)"),
    lambda t: t.replace("handle 0x1 ", "handle 0x1 flowid 1:1 "),
    lambda t: t.replace("\taction order 1: gact action drop\n", ""),        # no action
    lambda t: t.replace("action order 1: gact action drop",
                        "action order 1: gact action drop\n\taction order 2: gact action pass"),
    lambda t: t.replace("NOT canid(sff 0x105 sff 0x515)", "NOT canid(sff 0x105 sff 0x515 junk)"),
])
def test_can1_filter_rejects(mutate):
    assert check_can1_filter(mutate(CAN1_OK), "can0")


def test_can2_filter():
    assert check_can2_filter(CAN2_OK, "can1") == []
    assert check_can2_filter("", "can1")
    assert check_can2_filter(CAN1_OK, "can1")  # has an ematch: does not drop everything
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
    (("filter", "can0"), (0, ""), "exactly one tc filter"),
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
    assert ["tc", "filter", "add", "dev", "can0", "parent", "1:", "protocol", "all", "prio", "1", "basic", "match",
            "not canid(sff 0x105 sff 0x515)", "action", "drop"] in cmds
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
