"""Startup (and periodic) check that the OS-layer guards are in place.

SAFETY-CRITICAL. The bridge refuses to start, and stops and exits while running,
unless all of these hold:

* each configured interface sits on its configured SPI device
  (``/sys/class/net/<if>/device`` resolves to ``.../<spi_device>``), so a swapped
  can0/can1 enumeration is caught;
* both interfaces are CAN, administratively UP, at 500000 bit/s;
* pack CAN1 is not listen-only or loopback (it must transmit the keep-alive);
* pack CAN1 has root qdisc ``prio 1:`` with exactly the three ``u32`` filters in
  :data:`CAN1_U32_RULES` (allow the exact ``0x105`` and ``0x515`` frames, drop everything
  else, in that preference order);
* pack CAN2 has root qdisc ``prio 1:`` with exactly one filter: ``basic`` without
  ematch (matches everything) with ``gact action drop``;
* pack CAN2 is ``LISTEN-ONLY`` when the configured guard is ``listen-only``.

The parsers read the text printed by iproute2 (``ip -details link show``,
``tc qdisc show``, ``tc filter show``). ``filter_can1_ok.txt`` in tests/ was captured on the
Pi; the other samples were written from the iproute2 print code (README).
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

@dataclass(frozen=True)
class U32Rule:
    """One ``tc u32`` rule on pack CAN1. Each match is ``(value, mask, byte offset)``.

    Offsets are into the SocketCAN ``struct can_frame`` (the network header of a CAN skb):
    0 = ``can_id`` incl. EFF/RTR/ERR flag bits, 4 = ``len``, 8 = data[0..3], 12 = data[4..7].
    The values are the memory bytes of the little-endian struct, as tc prints them
    (``0x105`` is bytes ``05 01 00 00``). ``flowid`` None means ``action drop``.
    """

    pref: int
    matches: tuple
    flowid: Optional[str]


# Same allowlist as txgate.check_tx, enforced a second time by the kernel. The kernel in use has
# no em_canid, so the ID and the payload are matched byte-wise with u32. The ID word is matched
# whole, so extended, RTR and error frames never match an allow rule.
CAN1_U32_RULES = (
    # 0x105: len 8, D0 in {00,01}, D1 in {11,13}, D2..D7 = 0
    U32Rule(1, ((0x05010000, 0xFFFFFFFF, 0), (0x08000000, 0xFF000000, 4),
                (0x00110000, 0xFEFDFFFF, 8), (0x00000000, 0xFFFFFFFF, 12)), "1:1"),
    # 0x515: len 8, D0 in {10,30}, D1..D7 = 0
    U32Rule(2, ((0x15050000, 0xFFFFFFFF, 0), (0x08000000, 0xFF000000, 4),
                (0x10000000, 0xDFFFFFFF, 8), (0x00000000, 0xFFFFFFFF, 12)), "1:1"),
    U32Rule(3, ((0x00000000, 0x00000000, 0),), None),  # everything else: drop
)
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


def check_link(text: str, ifname: str, *, want_listen_only: bool, bitrate: int = BITRATE) -> list:
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
    if info.bitrate != bitrate:
        errs.append(f"{ifname}: bitrate {info.bitrate} is not {bitrate}")
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


_U32_RULE_HDR = re.compile(r"\bfh\s+\S+:\S*:\S+")
_U32_MATCH = re.compile(r"^match\s+([0-9a-fA-F]{8})/([0-9a-fA-F]{8})\s+at\s+(\d+)$")
_U32_ACTION_DETAIL = re.compile(r"^(random type none pass val 0|index \d+ ref \d+ bind \d+)$")


def parse_u32_rules(text: str) -> tuple:
    """Return ``(rules, problems)``: each rule is ``(pref, matches, flowid, actions)``.

    ``problems`` lists everything unexpected in a rule (kind, chain, protocol, unknown tokens or
    lines) so a rule with extra features such as a link, offset or police cannot pass unnoticed.
    """
    problems: list = []
    raw: list = []
    cur = None
    for ln in text.splitlines():
        m = re.match(r"^filter\s+(?:parent\s+\S+\s+)?protocol\s+(\S+)\s+pref\s+(\d+)\s+(\S+)(.*)$", ln)
        if m:
            cur = None
            if _U32_RULE_HDR.search(m.group(4)) or re.search(r"\bhandle\b", m.group(4)):
                cur = {"proto": m.group(1), "pref": int(m.group(2)), "kind": m.group(3), "rest": m.group(4), "body": []}
                raw.append(cur)
        elif cur is not None and ln.strip():
            cur["body"].append(ln.strip())
    rules = []
    for r in raw:
        who = f"pref {r['pref']}"
        if r["kind"] != "u32":
            problems.append(f"{who}: filter kind {r['kind']!r} is not 'u32'")
        if r["proto"] != "all":
            problems.append(f"{who}: filter protocol {r['proto']!r} is not 'all'")
        rest = r["rest"]
        cm = re.search(r"\bchain\s+(\S+)", rest)
        if cm and cm.group(1) != "0":
            problems.append(f"{who}: filter is not in chain 0")
        rest = re.sub(r"\bchain\s+\S+", "", rest)
        rest = re.sub(r"\bfh\s+\S+", "", rest)
        rest = re.sub(r"\border\s+\d+", "", rest)
        rest = re.sub(r"\bkey\s+ht\s+\S+\s+bkt\s+\d+", "", rest)
        rest = re.sub(r"\bterminal\b(\s+flowid(?=\s|$)(\s+\?\?\?)?)?", "", rest)
        rest = re.sub(r"\b(not_in_hw|in_hw)\b", "", rest)
        flowid = None
        fm = re.search(r"\bflowid\s+(\S+)", rest)
        if fm:
            flowid = fm.group(1)
            rest = rest.replace(fm.group(0), "")
        if rest.strip():
            problems.append(f"{who}: unexpected filter text {rest.strip()!r}")
        matches: list = []
        actions: list = []
        for b in r["body"]:
            mm = _U32_MATCH.match(b)
            am = re.match(r"^action order \d+:\s*(.*)$", b)
            if mm:
                matches.append((int(mm.group(1), 16), int(mm.group(2), 16), int(mm.group(3))))
            elif am:
                actions.append(am.group(1).strip())
            elif not _U32_ACTION_DETAIL.match(b):
                problems.append(f"{who}: unexpected line {b!r}")
        rules.append((r["pref"], tuple(matches), flowid, tuple(actions)))
    return rules, problems


def check_can1_filter(text: str, ifname: str) -> list:
    rules, problems = parse_u32_rules(text)
    errs = [f"{ifname}: {p}" for p in problems]
    got = []
    for pref, matches, flowid, actions in rules:
        drop = len(actions) == 1 and re.match(r"^gact action drop\b", actions[0]) is not None
        if actions and not drop:
            errs.append(f"{ifname}: pref {pref}: action is not a single 'gact action drop'")
        got.append((pref, matches, "DROP" if drop else flowid))
    want = [(r.pref, r.matches, r.flowid or "DROP") for r in CAN1_U32_RULES]
    if sorted(got) != sorted(want):
        errs.append(f"{ifname}: tc filters are not exactly the {len(want)} expected u32 rules "
                    f"(found {len(rules)}; see CAN1_U32_RULES)")
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
    for bus, ifname, spi, listen_only, bitrate, check_filter in (
        (1, cfg.can1_interface, cfg.can1_spi_device, False, BITRATE, check_can1_filter),
        (2, cfg.can2_interface, cfg.can2_spi_device, cfg.can2_guard == "listen-only", cfg.can2_bitrate, check_can2_filter),
    ):
        actual = spi_of(ifname)
        if actual != spi:
            errs.append(f"pack CAN{bus}: {ifname} is on SPI device {actual!r}, config says {spi!r}")
        rc, out = run(["ip", "-details", "link", "show", "dev", ifname])
        errs += [f"pack CAN{bus}: {e}" for e in
                 (check_link(out, ifname, want_listen_only=listen_only, bitrate=bitrate) if rc == 0 else [f"{ifname}: ip failed: {_one_line(out)}"])]
        rc, out = run(["tc", "qdisc", "show", "dev", ifname])
        errs += [f"pack CAN{bus}: {e}" for e in
                 (check_qdisc(out, ifname) if rc == 0 else [f"{ifname}: tc qdisc failed: {_one_line(out)}"])]
        rc, out = run(["tc", "filter", "show", "dev", ifname, "parent", "1:"])
        errs += [f"pack CAN{bus}: {e}" for e in
                 (check_filter(out, ifname) if rc == 0 else [f"{ifname}: tc filter failed: {_one_line(out)}"])]
    return errs
