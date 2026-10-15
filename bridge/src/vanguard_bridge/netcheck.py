"""Startup (and periodic) check that the OS-layer guards are in place.

SAFETY-CRITICAL. The bridge refuses to start, and stops and exits while running,
unless all of these hold:

* each configured interface sits on its configured SPI device
  (``/sys/class/net/<if>/device`` resolves to ``.../<spi_device>``), so a swapped
  can0/can1 enumeration is caught;
* both interfaces are CAN, administratively UP, at 500000 bit/s;
* pack CAN1 is not listen-only or loopback (it must transmit the keep-alive);
* pack CAN1 has root qdisc ``prio 1:`` with exactly one filter:
  ``basic`` ematch ``NOT canid(sff 0x105 sff 0x515)`` with ``gact action drop``;
* pack CAN2 has root qdisc ``prio 1:`` with exactly one filter: ``basic`` without
  ematch (matches everything) with ``gact action drop``;
* pack CAN2 is ``LISTEN-ONLY`` when the configured guard is ``listen-only``.

The parsers read the text printed by iproute2 (``ip -details link show``,
``tc qdisc show``, ``tc filter show``). The sample texts in tests/ were written from
the iproute2 print code, not captured on the Pi: verify on the Pi (README).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import BITRATE, Config

Runner = Callable[[list], tuple]  # argv -> (returncode, combined output)
SysfsResolver = Callable[[str], Optional[str]]  # ifname -> SPI device name or None

CAN1_ALLOWED_IDS = frozenset({("sff", 0x105), ("sff", 0x515)})
CMD_TIMEOUT_S = 0.5
_CMD_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


def _one_line(text: str) -> str:
    return " ".join(text.split())


def run_cmd(argv: list, timeout_s: float = CMD_TIMEOUT_S) -> tuple:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, env=_CMD_ENV, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, f"{type(e).__name__}: {e}"
    return p.returncode, p.stdout + p.stderr


def spi_device_of(ifname: str) -> Optional[str]:
    path = f"/sys/class/net/{ifname}/device"
    if not os.path.exists(path):
        return None
    return os.path.basename(os.path.realpath(path))


# ------------------------------------------------------------------ ip link


@dataclass
class LinkInfo:
    name: str = ""
    flags: set = field(default_factory=set)
    is_can: bool = False
    ctrlmodes: set = field(default_factory=set)
    can_state: str = ""
    bitrate: Optional[int] = None


def parse_ip_details(text: str) -> Optional[LinkInfo]:
    lines = text.splitlines()
    if not lines:
        return None
    m = re.match(r"^\d+:\s+([^:@\s]+)(?:@\S+)?:\s+<([^>]*)>", lines[0])
    if not m:
        return None
    info = LinkInfo(name=m.group(1), flags=set(filter(None, m.group(2).split(","))))
    for ln in lines[1:]:
        s = ln.strip()
        if s.startswith("link/can"):
            info.is_can = True
        cm = re.match(r"^can\s+(?:<([^>]*)>\s+)?state\s+(\S+)", s)
        if cm:
            info.ctrlmodes = set(filter(None, (cm.group(1) or "").split(",")))
            info.can_state = cm.group(2)
        bm = re.match(r"^bitrate\s+(\d+)", s)
        if bm and info.bitrate is None:
            info.bitrate = int(bm.group(1))
    return info


BAD_CAN1_STATES = frozenset({"BUS-OFF", "STOPPED", "SLEEPING"})


def check_link(text: str, ifname: str, *, want_listen_only: bool) -> list:
    info = parse_ip_details(text)
    if info is None:
        return [f"{ifname}: cannot parse 'ip -details link show' output"]
    errs = []
    if info.name != ifname:
        errs.append(f"{ifname}: output is for {info.name!r}")
    if not info.is_can:
        errs.append(f"{ifname}: not a CAN interface")
    if "UP" not in info.flags:
        errs.append(f"{ifname}: not UP")
    if info.bitrate != BITRATE:
        errs.append(f"{ifname}: bitrate {info.bitrate} is not {BITRATE}")
    lo = "LISTEN-ONLY" in info.ctrlmodes
    if want_listen_only and not lo:
        errs.append(f"{ifname}: listen-only mode is not on")
    if not want_listen_only and lo:
        errs.append(f"{ifname}: listen-only is on but this is the transmit bus (pack CAN1); mapping swapped?")
    if not want_listen_only:  # pack CAN1, the transmit bus
        if "LOOPBACK" in info.ctrlmodes:
            errs.append(f"{ifname}: loopback mode is on")
        if "LOWER_UP" not in info.flags:
            errs.append(f"{ifname}: no LOWER_UP (controller not running)")
        if not info.can_state:
            errs.append(f"{ifname}: CAN state not reported")
        elif info.can_state in BAD_CAN1_STATES:
            errs.append(f"{ifname}: CAN state is {info.can_state}")
    return errs


# ---------------------------------------------------------------------- tc


def check_qdisc(text: str, ifname: str) -> list:
    roots = [ln for ln in text.splitlines() if re.match(r"^qdisc\s+\S+\s+\S+\s+root\b", ln)]
    if len(roots) != 1 or not re.match(r"^qdisc\s+prio\s+1:\s+root\b", roots[0]):
        return [f"{ifname}: root qdisc is not 'prio 1:'"]
    return []


@dataclass
class TcFilter:
    protocol: str
    pref: int
    kind: str
    header_rest: str
    body: list

    @property
    def chain(self) -> Optional[int]:
        """Chain number; 0 when iproute2 prints no chain word. None if unparseable."""
        m = re.search(r"\bchain\s+(\S+)", self.header_rest)
        if not m:
            return 0
        return int(m.group(1)) if m.group(1).isdigit() else None


def parse_tc_filters(text: str) -> list:
    """Return the filters that carry a handle (the header-only lines iproute2 prints are skipped)."""
    out: list = []
    cur: Optional[TcFilter] = None
    for ln in text.splitlines():
        m = re.match(r"^filter\s+(?:parent\s+\S+\s+)?protocol\s+(\S+)\s+pref\s+(\d+)\s+(\S+)(.*)$", ln)
        if m:
            cur = None
            if re.search(r"\bhandle\b", m.group(4)):
                cur = TcFilter(m.group(1), int(m.group(2)), m.group(3), m.group(4), [])
                out.append(cur)
        elif cur is not None and ln.strip():
            cur.body.append(ln.strip())
    return out


_IDSPEC = re.compile(r"(sff|eff)\s+0x([0-9a-fA-F]+)(?::0x([0-9a-fA-F]+))?")


def _canid_list(arg: str) -> Optional[set]:
    """Parse ``sff 0x105 sff 0x515``; None if anything is left over or a mask is present."""
    ids = set()
    pos = 0
    arg = arg.strip()
    while pos < len(arg):
        m = _IDSPEC.match(arg, pos)
        if not m or m.group(3) is not None:
            return None
        ids.add((m.group(1), int(m.group(2), 16)))
        pos = m.end()
        while pos < len(arg) and arg[pos] == " ":
            pos += 1
    return ids


def _single_drop_filter(text: str, ifname: str) -> tuple:
    filters = parse_tc_filters(text)
    if len(filters) != 1:
        return None, [f"{ifname}: expected exactly one tc filter under 1:, found {len(filters)}"]
    f = filters[0]
    errs = []
    if f.chain != 0:
        errs.append(f"{ifname}: filter is not in chain 0")
    if f.kind != "basic":
        errs.append(f"{ifname}: filter kind {f.kind!r} is not 'basic'")
    if f.protocol != "all":
        errs.append(f"{ifname}: filter protocol {f.protocol!r} is not 'all'")
    body = "\n".join(f.body)
    actions = re.findall(r"action order \d+:\s*(.*)", body)
    if len(actions) != 1 or not re.match(r"^gact action drop\b", actions[0]):
        errs.append(f"{ifname}: filter action is not a single 'gact action drop'")
    if re.search(r"\b(flowid|classid|police)\b", f.header_rest + " " + body):
        errs.append(f"{ifname}: filter has a class or policer; expected drop only")
    return f, errs


def check_can1_filter(text: str, ifname: str) -> list:
    f, errs = _single_drop_filter(text, ifname)
    if f is None:
        return errs
    body = "\n".join(f.body)
    matches = re.findall(r"(?i)(\bNOT\s+)?\b(\w+)\(([^()]*)\)", body)
    if len(matches) != 1:
        return errs + [f"{ifname}: expected exactly one ematch, found {len(matches)}"]
    neg, kind, arg = matches[0]
    if kind.lower() != "canid":
        errs.append(f"{ifname}: ematch is {kind!r}, not canid")
    if not neg:
        errs.append(f"{ifname}: canid ematch is not inverted (NOT); it would drop the allowed IDs")
    if re.search(r"(?i)\b(AND|OR)\b", body):
        errs.append(f"{ifname}: ematch tree has AND/OR terms")
    ids = _canid_list(arg)
    if ids != CAN1_ALLOWED_IDS:
        errs.append(f"{ifname}: canid list {arg.strip()!r} is not exactly 'sff 0x105 sff 0x515' without masks")
    return errs


def check_can2_filter(text: str, ifname: str) -> list:
    f, errs = _single_drop_filter(text, ifname)
    if f is None:
        return errs
    if re.search(r"\w+\(", "\n".join(f.body)):
        errs.append(f"{ifname}: drop-all filter has an ematch; it must match every frame")
    return errs


# --------------------------------------------------------------- top level


def check_all(cfg: Config, run: Runner = run_cmd, spi_of: SysfsResolver = spi_device_of) -> list:
    """Return a list of problems; empty means the bridge may start."""
    errs: list = []
    for bus, ifname, spi, listen_only, check_filter in (
        (1, cfg.can1_interface, cfg.can1_spi_device, False, check_can1_filter),
        (2, cfg.can2_interface, cfg.can2_spi_device, cfg.can2_guard == "listen-only", check_can2_filter),
    ):
        actual = spi_of(ifname)
        if actual != spi:
            errs.append(f"pack CAN{bus}: {ifname} is on SPI device {actual!r}, config says {spi!r}")
        rc, out = run(["ip", "-details", "link", "show", "dev", ifname])
        errs += [f"pack CAN{bus}: {e}" for e in
                 (check_link(out, ifname, want_listen_only=listen_only) if rc == 0 else [f"{ifname}: ip failed: {_one_line(out)}"])]
        rc, out = run(["tc", "qdisc", "show", "dev", ifname])
        errs += [f"pack CAN{bus}: {e}" for e in
                 (check_qdisc(out, ifname) if rc == 0 else [f"{ifname}: tc qdisc failed: {_one_line(out)}"])]
        rc, out = run(["tc", "filter", "show", "dev", ifname, "parent", "1:"])
        errs += [f"pack CAN{bus}: {e}" for e in
                 (check_filter(out, ifname) if rc == 0 else [f"{ifname}: tc filter failed: {_one_line(out)}"])]
    return errs
