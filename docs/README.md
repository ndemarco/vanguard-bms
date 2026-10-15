# Documentation index

| Document | Contents |
|---|---|
| [`overview.md`](overview.md) | What the project is, the pack, and the confidence tags used throughout |
| [`architecture.md`](architecture.md) | The three software layers, the two CAN buses, and the module map |
| [`safety.md`](safety.md) | The bridge safety rules, the transmit allowlist, and the file and function enforcing each rule |
| [`development.md`](development.md) | Setting up a venv, running tests, and running without hardware |

Related documents elsewhere in the repo:

- [`../CANBUS_PROTOCOL.md`](../CANBUS_PROTOCOL.md): the protocol, source of truth for every frame and scaling
- [`../bridge/README.md`](../bridge/README.md): bridge client protocol, configuration, Pi install and bench checks
- [`../app/README.md`](../app/README.md): decoder, simulator and web app usage
- [`../firmware/SPEC.md`](../firmware/SPEC.md): design for the planned ESP32-C6 pack controller
- [`../analysis/`](../analysis/): working notes and Ghidra scripts behind the protocol
