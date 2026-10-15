# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Project documentation lives in [`docs/`](docs/README.md):

- [`docs/overview.md`](docs/overview.md): what this is, confidence tags, planned ESP32 controller
- [`docs/architecture.md`](docs/architecture.md): layers, modules, the two CAN buses
- [`docs/safety.md`](docs/safety.md): safety rules that constrain changes
- [`docs/development.md`](docs/development.md): venv setup, tests, running without hardware

`CANBUS_PROTOCOL.md` is the source of truth for the protocol. Keep its `[C]`/`[I]`/`[?]` tags honest when
changing decoding. Read `firmware/SPEC.md` before writing any firmware.

## Safety (read `docs/safety.md` before touching `bridge/`)

This is high-voltage battery control and the bridge is fail-closed. Do not weaken the transmit allowlist
(`txgate.py`), the CAN receive-only rule, or the STOPPED-by-default behavior without explicit instruction.
