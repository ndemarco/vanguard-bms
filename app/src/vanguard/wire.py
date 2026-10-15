"""Text formats shared by the simulator, the bridge service and the app.

Line protocol (one line per event, UTF-8, ``\\n`` terminated)::

    R <bus> <id hex> <dlc> <data hex> <timestamp us>
    T <bus> <id hex> <dlc> <data hex> <timestamp us>
    S <text>

* ``R`` is a frame seen on a bus. ``bus`` is 1 or 2. ``id hex`` is the CAN identifier
  in upper-case hex without prefix or flag bits, 3 digits on bus 1 (``111``) and 8 digits
  on bus 2 (``18FF03F3``, ``0CFF06F3``); the
  parser accepts lower case and an optional ``0x``. ``dlc`` is decimal, 0..8.
  ``data hex`` is ``dlc`` bytes as contiguous hex pairs, or ``-`` when ``dlc`` is 0.
  ``timestamp us`` is a non-negative integer, microseconds on the sender's clock.
* ``T`` has the same fields as ``R`` and marks a frame the bridge itself transmitted, so a
  log of the line protocol also carries the host's own traffic. It parses to a
  :class:`Frame` with ``direction="tx"``.
* ``S`` carries a free-text status message.

JSON lines log: one JSON object per frame, one per line::

    {"ts_us": 1500000, "bus": 1, "id": 273, "dlc": 8, "data": "0000000000000000", "direction": "rx"}

Keys: ``ts_us`` integer microseconds; ``bus`` 1 or 2; ``id`` the CAN identifier as an
integer (a hex string such as ``"0x111"`` is also accepted on read); ``dlc`` integer;
``data`` hex string of ``dlc`` bytes (empty string for none); ``direction`` is ``rx``
for a frame the logger received from the pack side of the bus and ``tx`` for a frame
the host sent towards the pack. Blank lines and lines starting with ``#`` are skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Iterator, Union


class WireError(ValueError):
    """Malformed line."""


@dataclass(frozen=True)
class Frame:
    ts_us: int
    bus: int
    can_id: int
    data: bytes
    direction: str = "rx"

    @property
    def dlc(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class Status:
    text: str


def format_frame(f: Frame, tag: str = "R") -> str:
    """Format an ``R`` line (or a ``T`` line with ``tag="T"``)."""
    if tag not in ("R", "T"):
        raise WireError(f"frame tag must be R or T, got {tag!r}")
    data = f.data.hex().upper() if f.data else "-"
    cid = f"{f.can_id:03X}" if f.bus == 1 else f"{f.can_id:08X}"
    return f"{tag} {f.bus} {cid} {len(f.data)} {data} {f.ts_us}"


def format_status(text: str) -> str:
    if "\n" in text or "\r" in text:
        raise WireError("status text must be a single line")
    return f"S {text}"


def _check_bus(bus: int) -> int:
    if bus not in (1, 2):
        raise WireError(f"bus must be 1 or 2, got {bus!r}")
    return bus


def parse_line(line: str) -> Union[Frame, Status]:
    """Parse an ``R``, ``T`` or ``S`` line. Raises :class:`WireError` if malformed."""
    line = line.rstrip("\r\n")
    if line.startswith("S "):
        return Status(line[2:])
    if line == "S":
        return Status("")
    parts = line.split(" ")
    if len(parts) != 6 or parts[0] not in ("R", "T"):
        raise WireError(f"not an R, T or S line: {line!r}")
    try:
        bus = _check_bus(int(parts[1]))
        can_id = int(parts[2], 16)
        dlc = int(parts[3])
        data = b"" if parts[4] == "-" else bytes.fromhex(parts[4])
        ts = int(parts[5])
    except ValueError as e:
        raise WireError(f"bad field in {line!r}: {e}") from None
    if not 0 <= dlc <= 8 or len(data) != dlc:
        raise WireError(f"dlc {dlc} does not match data in {line!r}")
    if can_id < 0 or can_id > 0x1FFFFFFF or ts < 0:
        raise WireError(f"id or timestamp out of range in {line!r}")
    return Frame(ts, bus, can_id, data, "tx" if parts[0] == "T" else "rx")


def frame_to_json(f: Frame) -> str:
    return json.dumps({"ts_us": f.ts_us, "bus": f.bus, "id": f.can_id, "dlc": len(f.data),
                       "data": f.data.hex(), "direction": f.direction}, separators=(",", ":"))


def frame_from_json(line: str) -> Frame:
    try:
        o = json.loads(line)
        cid = o["id"]
        can_id = int(cid, 0) if isinstance(cid, str) else int(cid)
        data = bytes.fromhex(o["data"])
        direction = o.get("direction", "rx")
        if direction not in ("rx", "tx"):
            raise WireError(f"direction must be rx or tx, got {direction!r}")
        if int(o["dlc"]) != len(data):
            raise WireError("dlc does not match data length")
        return Frame(int(o["ts_us"]), _check_bus(int(o["bus"])), can_id, data, direction)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
        if isinstance(e, WireError):
            raise
        raise WireError(f"bad JSON log line {line!r}: {e!r}") from None


def read_jsonl(lines: Iterable[str]) -> Iterator[Frame]:
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#"):
            yield frame_from_json(s)
