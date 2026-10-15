"""Bring up both CAN interfaces and install the tc egress filters (run as root, once per boot).

Run by ``vanguard-can-setup.service`` before the bridge. Reads the same config
file as the bridge, so the pack-bus-to-interface mapping lives in one place.

Commands per interface (shown for the shipped config; ``--dry-run`` prints them)::

    ip link set dev can0 down
    ip link set dev can0 type can bitrate 500000 listen-only off
    ip link set dev can0 txqueuelen 10
    tc qdisc del dev can0 root                      # failure ignored (none yet)
    tc qdisc add dev can0 root handle 1: prio
    tc filter add dev can0 parent 1: protocol all prio 1 basic match 'not canid(sff 0x105 sff 0x515)' action drop
    ip link set dev can0 up

    ip link set dev can1 down
    ip link set dev can1 type can bitrate 500000 listen-only on   # "off" with guard = "tc-drop-all"
    ip link set dev can1 txqueuelen 10
    tc qdisc del dev can1 root
    tc qdisc add dev can1 root handle 1: prio
    tc filter add dev can1 parent 1: protocol all prio 1 basic action drop
    ip link set dev can1 up

Filter syntax: tc-ematch(8) ("canid( IDLIST )", "IDSPEC := { 'sff' CANID | 'eff' CANID }",
"TERM := [ not ] { MATCH | '(' EXPR ')' }") with the ``basic`` classifier (tc-basic(8)); the
kernel side is net/sched/em_canid.c (Linux 3.6 and later, CONFIG_NET_EMATCH_CANID).
In em_canid an ``sff`` rule only matches frames without the EFF flag, so
``not canid(sff 0x105 sff 0x515)`` is true for every extended frame and every other
standard ID. A ``basic`` filter without ``match`` matches every packet.
After setup the same checks as the bridge's startup check run; any failure exits 1.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import config as config_mod
from .config import BITRATE, Config
from .netcheck import check_all, run_cmd, spi_device_of

CAN1_FILTER_EMATCH = "not canid(sff 0x105 sff 0x515)"
TXQUEUELEN = 10


def commands(cfg: Config) -> list:
    """Return ``(argv, may_fail)`` pairs, in order."""
    out = []
    for ifname, listen_only, filt in (
        (cfg.can1_interface, False, ["basic", "match", CAN1_FILTER_EMATCH, "action", "drop"]),
        (cfg.can2_interface, cfg.can2_guard == "listen-only", ["basic", "action", "drop"]),
    ):
        out += [
            (["ip", "link", "set", "dev", ifname, "down"], False),
            (["ip", "link", "set", "dev", ifname, "type", "can", "bitrate", str(BITRATE),
              "listen-only", "on" if listen_only else "off"], False),
            (["ip", "link", "set", "dev", ifname, "txqueuelen", str(TXQUEUELEN)], False),
            (["tc", "qdisc", "del", "dev", ifname, "root"], True),
            (["tc", "qdisc", "add", "dev", ifname, "root", "handle", "1:", "prio"], False),
            (["tc", "filter", "add", "dev", ifname, "parent", "1:", "protocol", "all", "prio", "1"] + filt, False),
            (["ip", "link", "set", "dev", ifname, "up"], False),
        ]
    return out


def main(argv: Optional[list] = None, *, run=run_cmd, spi_of=spi_device_of, out=sys.stdout) -> int:
    p = argparse.ArgumentParser(prog="vanguard-bridge-netsetup", description=__doc__.split("\n\n")[0])
    p.add_argument("--config", default=config_mod.DEFAULT_PATH)
    p.add_argument("--dry-run", action="store_true", help="print the commands, run nothing")
    a = p.parse_args(argv)
    cfg = config_mod.load(a.config)
    for line in cfg.mapping_lines():
        print(line, file=out)
    errs = []
    for ifname, spi in ((cfg.can1_interface, cfg.can1_spi_device), (cfg.can2_interface, cfg.can2_spi_device)):
        actual = spi_of(ifname)
        if actual != spi and not a.dry_run:
            errs.append(f"{ifname} is on SPI device {actual!r}, config says {spi!r}; refusing to configure")
    if errs:
        print("\n".join(errs), file=out)
        return 1
    for cmd, may_fail in commands(cfg):
        printable = " ".join(f"'{c}'" if " " in c else c for c in cmd)
        print(printable, file=out)
        if a.dry_run:
            continue
        rc, text = run(cmd)
        if rc != 0 and not may_fail:
            print(f"FAILED ({rc}): {text.strip()}", file=out)
            return 1
    if a.dry_run:
        return 0
    errs = check_all(cfg, run, spi_of)
    for e in errs:
        print(f"CHECK FAILED: {e}", file=out)
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
