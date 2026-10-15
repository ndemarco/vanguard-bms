# Architecture

Three layers, deliberately split by privilege.

## 1. `app/src/vanguard/` (package `vanguard`, no runtime dependencies)

| Module | Role |
|---|---|
| `frames.py` | Signal specs and scalings per frame |
| `decoder.py` | `decode(bus, id, data)` returns the scaled value, the raw integer and a confidence tag, or `None` for undocumented IDs |
| `encoder.py` | Builds payloads; refuses maintenance and service-channel frames |
| `simulator.py` | Pack stand-in |
| `wire.py` | Text line protocol and JSONL log formats shared by everything |

## 2. `bridge/src/vanguard_bridge/`

The only process allowed to transmit CAN. It owns both buses on the Pi and serves exactly one client over a
Unix socket, using the `R`/`T`/`S` line protocol from `wire.py` plus client commands (`START`, `HB`, `STOP`,
`CLEAR`, `SLEEP`). The full command table is in [`bridge/README.md`](../bridge/README.md).

| Module | Role |
|---|---|
| `core.py` | State machine (STOPPED/RUNNING), watchdog, keep-alive |
| `service.py` | Event loop and socket |
| `txgate.py` | Final transmit allowlist |
| `canio.py` / `simlink.py` | Real vs. simulated CAN sockets |
| `netsetup.py` / `netcheck.py` | OS-level egress filter and its verification |
| `system/` | systemd units |

## 3. `app/src/vanguard/web/`

An unprivileged FastAPI server and static page (`static/`, no external hosts). It connects to the bridge socket
(`client.py`), maintains live state (`state.py`), and sends only fixed command strings. It never sends `START`
on its own, including on reconnect.

## The two pack buses

The pack has two buses that differ in everything. This project names them after the labels on the BMC board:
**CAN11** (`can11h`/`can11l`, 11-bit IDs) and **CAN** (`canh`/`canl`, 29-bit IDs). Code identifiers, config keys
and wire/log strings still use the older `can1`/`can2` and `CAN1`/`CAN2` names.

| | CAN11 | CAN |
|---|---|---|
| IDs | 11-bit | 29-bit, J1939-style |
| Byte order | big-endian | little-endian |
| Carries | the `0x105` keep-alive, which must arrive at least once a second or the contactors open | rich telemetry, and a maintenance channel that must never be written |
