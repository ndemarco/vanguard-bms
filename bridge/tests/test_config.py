import pytest

from vanguard_bridge.config import ConfigError, load, parse

BASE = {"pack_can1": {"interface": "can1", "spi_device": "spi1.2"},
        "pack_can2": {"interface": "can0", "spi_device": "spi1.1"}}


def test_shipped_config_parses():
    import pathlib
    cfg = load(str(pathlib.Path(__file__).parents[1] / "system" / "bridge.toml"))
    assert cfg.can2_guard == "tc-drop-all" and cfg.can2_bitrate == 125_000 and cfg.socket_mode == 0o660 and cfg.socket_group == "vanguard"
    assert cfg.socket_path == "/run/vanguard-bridge/bridge.sock"


def test_mapping_is_explicit_and_printed():
    cfg = parse(BASE)
    lines = cfg.mapping_lines()
    assert "pack-can1 interface=can1 spi=spi1.2" in lines[0]
    assert "pack-can2 interface=can0 spi=spi1.1" in lines[1] and "transmit=none" in lines[1]


@pytest.mark.parametrize("patch", [
    {"pack_can1": {"interface": "can0", "spi_device": "spi1.1"}},              # same as CAN
    {"pack_can1": {"interface": "can0; rm", "spi_device": "spi1.2"}},
    {"pack_can1": {"interface": "can1"}},                                       # spi missing
    {"pack_can2": {"interface": "can0", "spi_device": "spi1.1", "guard": "none"}},
    {"socket": {"mode": "0666"}},                                               # world access
    {"socket": {"mode": "rw"}},
    {"socket": {"path": "relative.sock"}},
    {"checks": {"recheck_interval_s": 0}},
    {"pack_can2": "x"},
])
def test_bad_configs(patch):
    d = {**BASE, **patch}
    with pytest.raises(ConfigError):
        parse(d)
