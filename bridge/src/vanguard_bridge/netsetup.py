"""Bring up both CAN interfaces and install the tc egress filters (run as root, once per boot).

Run by ``vanguard-can-setup.service`` before the bridge. Reads the same config
file as the bridge, so the pack-bus-to-interface mapping lives in one place.

Commands per interface (shown for the shipped config; ``--dry-run`` prints them)::

    ip link set dev can0 down
    ip link set dev can0 type can bitrate 500000 listen-only off
    ip link set dev can0 txqueuelen 1
    tc qdisc del dev can0 root                      # failure ignored (none yet)
    tc qdisc add dev can0 root handle 1: prio
    tc filter add dev can0 parent 1: protocol all prio 1 u32 match u32 0x05010000 0xffffffff at 0 ... flowid 1:1
    tc filter add dev can0 parent 1: protocol all prio 2 u32 match u32 0x15050000 0xffffffff at 0 ... flowid 1:1
    tc filter add dev can0 parent 1: protocol all prio 3 u32 match u32 0 0 at 0 action drop
    ip link set dev can0 up

    ip link set dev can1 down
    ip link set dev can1 type can bitrate 500000 listen-only on   # "off" with guard = "tc-drop-all"
    ip link set dev can1 txqueuelen 1
    tc qdisc del dev can1 root
    tc qdisc add dev can1 root handle 1: prio
    tc filter add dev can1 parent 1: protocol all prio 1 basic action drop
    ip link set dev can1 up

Pack CAN1 uses three ``u32`` filters (netcheck.CAN1_U32_RULES): two that let the exact
0x105 and 0x515 frames through (ID word, length and payload bytes are matched on the raw
``struct can_frame``) and one that drops everything else. Raspberry Pi kernels do not ship
the ``em_canid`` ematch, so the ID is matched byte-wise. The u32 values are the little-endian
memory bytes of the struct, so this setup refuses to run on a big-endian host. A ``basic``
filter without ``match`` matches every packet (pack CAN2: drop all).
After setup the same checks as the bridge's startup check run; any failure exits 1.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import config as config_mod
from .config import BITRATE, Config
from .netcheck import CAN1_U32_RULES, check_all, run_cmd, spi_device_of

# One frame. With no ACK on pack CAN1 the controller retries its current frame forever and
# the queue fills; a deep queue would hold stale keep-alives that burst out when the pack is
# reconnected. The bridge exits on any TX error, and ExecStartPre re-runs this setup, whose
# down/up discards the queue and the controller's TX buffer.
TXQUEUELEN = 1


def _can1_filters() -> list:
    """The ``tc filter add`` tails for pack CAN1, one per rule, from the table the checker uses."""
    if sys.byteorder != "little":
        raise RuntimeError("the CAN1 u32 filter values assume a little-endian host")
    out = []
    for rule in CAN1_U32_RULES:
        argv = ["prio", str(rule.pref), "u32"]
        for value, mask, offset in rule.matches:
            argv += ["match", "u32", f"0x{value:08x}", f"0x{mask:08x}", "at", str(offset)]
        argv += ["flowid", rule.flowid] if rule.flowid else ["action", "drop"]
        out.append(argv)
    return out


def commands(cfg: Config) -> list:
    """Return ``(argv, may_fail)`` pairs, in order."""
    out = []
    for ifname, listen_only, filters in (
        (cfg.can1_interface, False, _can1_filters()),
        (cfg.can2_interface, cfg.can2_guard == "listen-only", [["prio", "1", "basic", "action", "drop"]]),
    ):
        out += [
            (["ip", "link", "set", "dev", ifname, "down"], False),
            (["ip", "link", "set", "dev", ifname, "type", "can", "bitrate", str(BITRATE),
              "listen-only", "on" if listen_only else "off"], False),
            (["ip", "link", "set", "dev", ifname, "txqueuelen", str(TXQUEUELEN)], False),
            (["tc", "qdisc", "del", "dev", ifname, "root"], True),
            (["tc", "qdisc", "add", "dev", ifname, "root", "handle", "1:", "prio"], False),
        ]
        out += [(["tc", "filter", "add", "dev", ifname, "parent", "1:", "protocol", "all"] + f, False) for f in filters]
        out.append((["ip", "link", "set", "dev", ifname, "up"], False))
    return out


def _run_setup_cmd(argv: list) -> tuple:
    return run_cmd(argv, timeout_s=10.0)  # configuration commands may load modules or reset the chip


def main(argv: Optional[list] = None, *, run=_run_setup_cmd, spi_of=spi_device_of, out=sys.stdout) -> int:
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
            print(f"FAILED ({rc}): {' '.join(text.split())}", file=out)
            return 1
    if a.dry_run:
        return 0
    errs = check_all(cfg, run_cmd if run is _run_setup_cmd else run, spi_of)
    for e in errs:
        print(f"CHECK FAILED: {e}", file=out)
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
