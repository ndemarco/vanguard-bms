# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Reverse-engineered CAN protocol for a Briggs & Stratton Vanguard 48 V (14S) battery pack, plus Python code to decode it, simulate the pack, and keep it awake from a Raspberry Pi. **Nothing has been verified against a live pack.** `CANBUS_PROTOCOL.md` is the source of truth; every claim in it is tagged `[C]` confirmed, `[I]` inferred, `[?]` unknown, and code carries those tags through to the UI. Keep tags honest when changing decoding. `analysis/` holds the working notes and Ghidra scripts; the firmware image is deliberately not in the repo.

## Commands

Two independent Python 3.11+ packages (setuptools, `src/` layout). `vanguard-bridge` depends on `vanguard`, so install both into one venv, created in `app/`:

```bash
cd app
python3 -m venv .venv
.venv/bin/pip install -e '.[test]' -e ../bridge
.venv/bin/python -m pytest tests            # app tests
.venv/bin/python -m pytest ../bridge/tests  # bridge tests
.venv/bin/python -m pytest tests/test_wire.py::test_name   # single test
```

Run without hardware (two terminals):

```bash
.venv/bin/vanguard-bridge --simulate --socket /tmp/vgb.sock
.venv/bin/vanguard-app --socket /tmp/vgb.sock --port 8000     # http://127.0.0.1:8000/
.venv/bin/python -m vanguard.simulator --duration 10          # standalone simulator
```

No linter or formatter is configured.

## Architecture

Three layers, deliberately split by privilege:

1. **`app/src/vanguard/`** (package `vanguard`, no runtime deps): `frames.py` (signal specs/scalings per frame), `decoder.py` (`decode(bus, id, data)` returns scaled value plus raw integer plus confidence tag, or `None` for undocumented IDs), `encoder.py` (builds payloads; refuses maintenance/service-channel frames), `simulator.py` (pack stand-in), `wire.py` (text line protocol and JSONL log formats shared by everything).
2. **`bridge/src/vanguard_bridge/`**: the only process allowed to transmit CAN. Owns both buses on the Pi, serves exactly one client over a Unix socket using the `R`/`T`/`S` line protocol from `wire.py` plus client commands (`START`, `HB`, `STOP`, `CLEAR`, `SLEEP`; full table in `bridge/README.md`). `core.py` is the state machine (STOPPED/RUNNING, watchdog, keep-alive), `service.py` the event loop and socket, `txgate.py` the final allowlist, `canio.py`/`simlink.py` real vs. simulated sockets, `netsetup.py`/`netcheck.py` the OS-level egress filter and its verification. `system/` has systemd units.
3. **`app/src/vanguard/web/`**: unprivileged FastAPI server and static page (`static/`, no external hosts). Connects to the bridge socket (`client.py`), maintains live state (`state.py`), and sends only fixed command strings. It never sends `START` on its own, including on reconnect.

The pack has two buses that differ in everything: CAN1 is 11-bit IDs, big-endian, carries the `0x105` keep-alive (must arrive at least once a second or the contactors open); CAN2 is 29-bit J1939-style, little-endian, rich telemetry, and a maintenance channel that must never be written.

## Safety rules that constrain changes

This is high-voltage battery control; the bridge is built fail-closed. Do not weaken these without explicit instruction:

- Every CAN write goes through `TxGate.send` → `check_tx` in `txgate.py`, which validates the exact 16 bytes independent of the state machine. Only `0x105` (D0 in {0x00,0x01}, D1 in {0x11,0x13}, rest 0) and `0x515` (D0 in {0x10,0x30}, rest 0) are allowed. `0x614` (bootloader reboot) and all extended IDs must stay impossible.
- CAN2 has no send path (`Can2ReceiveOnly`). `bridge/tests/test_structure.py` AST-scans the package for write calls and fails if one appears outside the allowed places, so adding any `send`/`write` in `vanguard_bridge` will trip it.
- Bridge powers up STOPPED; any refusal, TX failure, disconnect, wiring-swap evidence or internal error goes to STOPPED (some also exit with status 3 so systemd restarts it and flushes the CAN queue).
- `START`/`SLEEP` require recent pack CAN1 traffic on the TX interface (250 ms evidence window).
- Bridge time-sensitive logic is tested with a fake microsecond clock (`Harness` in `bridge/tests/conftest.py`); use it rather than wall-clock sleeps.
- `bridge/README.md` has a table mapping each safety rule to the file and function enforcing it; update it when moving enforcement.
