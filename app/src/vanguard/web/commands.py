"""The only lines the app ever sends to the bridge.

Every command is a fixed string chosen from the tables below. No user input is ever
formatted into a command: HTTP inputs are mapped through these tables and anything
that does not match is rejected before it reaches the socket.
"""

from __future__ import annotations

START_COMMANDS = {"discharge": "START DISCHARGE", "charge": "START CHARGE"}
SLEEP_COMMANDS = {1: "SLEEP 1", 3: "SLEEP 3"}
STOP = "STOP"
HB = "HB"
CLEAR = "CLEAR"

ALLOWED = frozenset({*START_COMMANDS.values(), *SLEEP_COMMANDS.values(), STOP, HB, CLEAR})

# Word of the bridge reply (``S OK <word> ...`` / ``S ERR <word> ...``) each command is answered with.
REPLY_WORD = {"START DISCHARGE": "START", "START CHARGE": "START", "STOP": "STOP", "CLEAR": "CLEAR",
              "SLEEP 1": "SLEEP", "SLEEP 3": "SLEEP"}
REPLY_WORDS = frozenset(REPLY_WORD.values())


class NotAllowed(ValueError):
    """Raised when something other than an allowed command is about to be sent."""


def check(cmd: str) -> str:
    if cmd not in ALLOWED:
        raise NotAllowed(f"refusing to send {cmd!r}")
    return cmd
