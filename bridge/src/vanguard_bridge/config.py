"""Bridge configuration (TOML, default /etc/vanguard-bridge/bridge.toml).

The file maps each pack bus to a kernel CAN interface and to the SPI device that
interface must sit on. The mapping is printed at startup. Nothing assumes that
kernel ``can0`` is pack CAN1.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from typing import Optional

DEFAULT_PATH = "/etc/vanguard-bridge/bridge.toml"
BITRATE = 500_000
CAN2_BITRATES = (125_000, 250_000, 500_000)  # pack CAN2 channel only; pack CAN1 is always BITRATE
CAN2_GUARDS = ("listen-only", "tc-drop-all")
_IFNAME = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
_SPIDEV = re.compile(r"^spi\d+\.\d+$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    can1_interface: str
    can1_spi_device: str
    can2_interface: str
    can2_spi_device: str
    can2_guard: str = "listen-only"
    can2_bitrate: int = BITRATE
    socket_path: str = "/run/vanguard-bridge/bridge.sock"
    socket_mode: int = 0o660
    socket_group: Optional[str] = "vanguard"
    recheck_interval_s: float = 5.0

    def mapping_lines(self) -> list:
        return [
            f"CONFIG pack-can1 interface={self.can1_interface} spi={self.can1_spi_device} "
            f"bitrate={BITRATE} ids=11-bit transmit=allowlist(0x105,0x515)",
            f"CONFIG pack-can2 interface={self.can2_interface} spi={self.can2_spi_device} "
            f"bitrate={self.can2_bitrate} ids=29-bit transmit=none guard={self.can2_guard}",
        ]


def _get(d: dict, section: str, key: str, typ, default=...):
    sec = d.get(section, {})
    if not isinstance(sec, dict):
        raise ConfigError(f"[{section}] must be a table")
    if key not in sec:
        if default is ...:
            raise ConfigError(f"missing [{section}] {key}")
        return default
    v = sec[key]
    if not isinstance(v, typ) or isinstance(v, bool) and typ is not bool:
        raise ConfigError(f"[{section}] {key} has the wrong type")
    return v


def parse(d: dict) -> Config:
    c1i = _get(d, "pack_can1", "interface", str)
    c1s = _get(d, "pack_can1", "spi_device", str)
    c2i = _get(d, "pack_can2", "interface", str)
    c2s = _get(d, "pack_can2", "spi_device", str)
    guard = _get(d, "pack_can2", "guard", str, "listen-only")
    bitrate2 = _get(d, "pack_can2", "bitrate", int, BITRATE)
    path = _get(d, "socket", "path", str, Config.socket_path)
    mode_s = _get(d, "socket", "mode", str, "0660")
    group = _get(d, "socket", "group", str, "vanguard") or None
    recheck = float(_get(d, "checks", "recheck_interval_s", (int, float), 5.0))
    for name in (c1i, c2i):
        if not _IFNAME.match(name):
            raise ConfigError(f"bad interface name {name!r}")
    for name in (c1s, c2s):
        if not _SPIDEV.match(name):
            raise ConfigError(f"bad spi_device {name!r} (expected e.g. spi1.1)")
    if c1i == c2i or c1s == c2s:
        raise ConfigError("pack CAN1 and pack CAN2 must use different interfaces and SPI devices")
    if guard not in CAN2_GUARDS:
        raise ConfigError(f"[pack_can2] guard must be one of {CAN2_GUARDS}")
    if bitrate2 not in CAN2_BITRATES:
        raise ConfigError(f"[pack_can2] bitrate must be one of {CAN2_BITRATES}")
    if not re.fullmatch(r"0?[0-7]{3}", mode_s):
        raise ConfigError("[socket] mode must be an octal string such as \"0660\"")
    mode = int(mode_s, 8)
    if mode & 0o007:
        raise ConfigError("[socket] mode must give no access to others (group-restricted)")
    if not path.startswith("/"):
        raise ConfigError("[socket] path must be absolute")
    if not 1.0 <= recheck <= 60.0:
        raise ConfigError("[checks] recheck_interval_s must be 1..60")
    return Config(c1i, c1s, c2i, c2s, guard, bitrate2, path, mode, group, recheck)


def load(path: str = DEFAULT_PATH) -> Config:
    with open(path, "rb") as fh:
        try:
            return parse(tomllib.load(fh))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: {e}") from None
