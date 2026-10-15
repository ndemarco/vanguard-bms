# Safety rules that constrain changes

This is high-voltage battery control, and the bridge is built fail-closed. Do not weaken these without
explicit instruction. This document is the single source of truth for the bridge's safety rules; update it when
moving enforcement.

## Rules for changes

- **Single write path.** Every CAN write goes through `TxGate.send` → `check_tx` in `txgate.py`, which validates
  the exact 16 bytes independent of the state machine. Only `0x105` (D0 in {0x00,0x01}, D1 in {0x11,0x13}, rest
  0) and `0x515` (D0 in {0x10,0x30}, rest 0) are allowed. `0x614` (bootloader reboot) and all extended IDs must
  stay impossible.
- **CAN is receive-only.** It has no send path (`Can2ReceiveOnly`). `bridge/tests/test_structure.py` AST-scans
  the package for write calls and fails if one appears outside the allowed places, so adding any `send`/`write`
  in `vanguard_bridge` will trip it.
- **Fail closed.** The bridge powers up STOPPED. Any refusal, TX failure, disconnect, wiring-swap evidence or
  internal error goes to STOPPED; some also exit with status 3 so systemd restarts it and flushes the CAN queue.
- **Evidence before transmit.** `START` and `SLEEP` require recent pack CAN11 traffic on the TX interface (250 ms
  evidence window).
- **Testing time-sensitive logic.** Use the fake microsecond clock (`Harness` in `bridge/tests/conftest.py`)
  rather than wall-clock sleeps.

## Where each rule is enforced

| Rule | Where it is enforced |
|---|---|
| Power-on state is STOPPED; nothing is sent until `START` | `core.py` `Bridge.__init__`, `Bridge._start` |
| Transmit allowlist, checked on the exact 16 bytes written, independent of the state machine | `txgate.py` `check_tx`, called by `TxGate.send` (the only CAN write in the package) |
| Refused transmit forces STOPPED; failed transmit forces STOPPED and exits (status 3) so the restart flushes the CAN queue | `core.py` `Bridge._on_refused`, `Bridge._on_failed`; `service.py` `Service._fail`; `system/vanguard-bridge.service` `ExecStartPre` |
| A frame only the pack sends, seen on the wrong interface (wiring swapped): STOPPED, `START` refused until a new client connects | `service.py` `wiring_swap_evidence`, `Service.step`; `core.py` `Bridge.latch_wiring_fault`, `Bridge.connect` |
| CAN socket error (for example interface down): STOPPED, exit 3 | `canio.py` `CanReceiver.read_frames`; `service.py` `Service.step` |
| Heartbeats that waited in the socket during a loop stall cannot extend the keep-alive | `service.py` `Service._read_client` |
| Pack CAN has no send path | `canio.py` `Can2ReceiveOnly`, `open_pack_can2`; `simlink.py` `SimCan2Socket` |
| Keep-alive every 100 ms, 1 s watchdog fed only by `HB` / `START` | `core.py` `Bridge._tick` |
| Client disconnect, slow client, internal error, SIGTERM: STOPPED | `core.py` `Bridge.disconnect`, `Bridge._fail_closed`; `service.py` `Service._drop_client`, `Service.run` |
| `CLEAR` once per 100 ms, single-shot D1 = 0x13 | `core.py` `Bridge._clear`, `Bridge._tick` |
| `SLEEP 1` / `SLEEP 3` only while STOPPED | `core.py` `Bridge._sleep` |
| `START` and `SLEEP` need positive evidence: a pack CAN11 frame on the TX interface within 250 ms and no wiring latch | `core.py` `Bridge._transmit_permission`, `Bridge.note_pack_can1_frame`; `service.py` `is_pack_can1_evidence`, `Service.step` (receivers are read before client input) |
| OS layer: pack CAN11 egress filter (only 0x105 and 0x515), pack CAN listen-only plus drop-all filter | `netsetup.py` `commands` |
| Bridge refuses to start (exit 2), or stops and exits (exit 3) while running, without those guards; the periodic check runs in a thread | `netcheck.py` `check_all`; `service.py` `main`, `BackgroundChecker`, `Service.step` |
| No GPIO: the hardware enable input is not driven | no GPIO code exists |

The transmit allowlist: standard 11-bit frames only, DLC 8, no remote (RTR) or error flag,
padding bytes zero, and either

* `0x105` with D0 in {`0x00`, `0x01`}, D1 in {`0x11`, `0x13`}, D2..D7 = 0, or
* `0x515` with D0 in {`0x10`, `0x30`}, D1..D7 = 0.

So `0x105` D0 bits 2/4/6 (vehicle load outputs) and D1 bit 2 (20-minute timeout) can never
be set, `0x515` deep shutdown (D0 = `0x20`) is impossible, and so are `0x614` (bootloader
reboot) and every extended ID (the CAN maintenance channel). A refused frame is reported
with `S TXREFUSED` and forces STOPPED.

If the bridge crashes or is killed, it stops sending `0x105` and the pack opens its contactors
within about 1 s on its own. systemd restarts the bridge, which comes back STOPPED.
