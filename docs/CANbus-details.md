# CAN bus details for a pack-powered bridge

Status: draft. What a bridge microcontroller needs to know about the pack's two CAN buses, their connector pins, and safe transmitting. Planned bridge: ESP32-C6, two CAN transceivers, one RS485 transceiver, powered from the pack's `12vout`.

Frame layouts: [`../CANBUS_PROTOCOL.md`](../CANBUS_PROTOCOL.md). Pinouts: [`../analysis/hardware_connectors.md`](../analysis/hardware_connectors.md). Inverter side: [`SPEC-inverter-bridge.md`](SPEC-inverter-bridge.md).

Tags: [C] confirmed in firmware, [I] inferred, [?] unknown.

## 1. The two buses

| Bus | Pins (20-pin pack connector) | IDs | Carries | Bridge role |
|---|---|---|---|---|
| CAN1, vehicle | 15 CAN11H, 16 CAN11L | 11-bit | `0x105` keep-alive and control (host to pack); current, limits, status, voltages, SOC, cell and temperature extremes, faults | transmit and receive |
| CAN2, maintenance | 4 CANH, 5 CANL | 29-bit J1939-style, source address `0xF3` | per-cell voltages, temperatures, input and output state, status words; `0x0CFF00F3` maintenance commands | listen; transmit only in maintenance mode (section 5) |

- Classic CAN, 500 kbit/s [I: assumes a 16 MHz crystal; not measured].
- The pack broadcasts all data unrequested [C].
- Only CAN1 `0x105` has a timeout [C].
- CAN11 frames carry no source address, so one pack per bus [I].
- The ESP32-C6 has two classic-CAN (TWAI) controllers. Use 3.3 V transceivers, such as SN65HVD230 or TJA1051T/3.
- Measure CANH to CANL with everything off before fitting 120 Ω termination. The pack may already terminate [?].
- The vehicle harness has no signal ground on this connector. Reference the transceivers to pack ground, pin 6 or 14.

## 2. Keep-alive and contactors (CAN1)

- The host sends `0x105` every 100 ms. The pack closes its own contactors after precharge and voltage checks [C].
- About 1 s without `0x105` drops the three auxiliary outputs and opens the contactors [C]. This is the fail-safe: a crashed or rebooting bridge opens the contactors. Never send `0x105` from an interrupt or timer that outlives a hung main loop.
- `0x105` D1 bit 2 stretches the timeout to about 20 minutes [C]. Leave it clear.
- About 5 minutes without `0x105` puts the pack to sleep [C/I].

## 3. Hardware inputs and outputs seen on CAN

No CAN command reads, overrides or emulates inputs RB11 to RB14 [C]. The physical enable input stays required.

| Signal | Pins | On CAN | Notes |
|---|---|---|---|
| Inputs RB11 to RB14 | four of 1 ESTP, 7 CEN, 12 EN12V, 13 DEN [?] | CAN2 `0x0CF096F3` B6 high nibble: bit 7 RB13, bit 6 RB11, bit 5 RB12, bit 4 RB14; set = pin low [C]. CAN1 `0x113` B2 bits 0-1: RB13, RB14 [C] | RB12 is the master enable: active low, gates contactor closing, release forces sleep [C]. Owner: grounding the enable pin wakes and runs the pack. RB11 selects which fault word records imbalance [C]. RB13 and RB14 are only reported [C] |
| Auxiliary outputs RA3, RA4, RD3 | three of 8 ALOUT, 10 FATOUT, 19 FUOUT [?] | set by `0x105` D0 bit 6 (RA3), bit 4 (RA4), bit 2 (RD3); echoed in `0x113` and `0x0CF096F3` B7 bits 1-3 [C] | Club Car's diagnostic screen calls them MCU, VCM and DC-DC power [I]. Leave unwired; keep the bits 0 |
| Board-on outputs RC13, RC0, RD12 | 3 12VOUT [I] | `0x0CF096F3` B7 bit 4 [C] | off while the pack sleeps; cleared by maintenance commands `0xDC`, `0xDE` [C] |

Input mapping test:

1. Pack awake, nothing connected: meter pins 1, 7, 12, 13 against pack ground. Near 5 V or 12 V is a pull-up; assert by grounding. Near 0 V: assert with 12 V through about 1 kΩ.
2. Assert one pin at a time; note which `0x0CF096F3` B6 bit changes. The pin whose release sleeps the pack is RB12.

## 4. Powering the bridge from 12VOUT

The bridge loses power whenever the pack sleeps and cannot wake it. Therefore:

- Never send `0x515` with D0 bits 5:4 non-zero; the pack sleeps 500 ms later [C]. Drop the Linux bridge's `SLEEP 1` and `SLEEP 3`.
- Send `0x105` whenever the bridge runs (section 2).
- Refuse maintenance commands `0xDC` and `0xDE`; they switch the board-on outputs off [C].
- A maintained switch from the enable pin to pack ground turns pack and bridge on and off. Switching off cuts bridge power instantly: keep no persistent writes pending, or add a hold-up capacitor with power-loss detection.
- Confirm 12VOUT stays on with the contactors open, and measure its current capacity. Buck converter plus ESP32-C6 with Wi-Fi peaks near 1.5 W.

### Wake failures

Releasing the enable input arms wake sources before sleep, so closing the switch wakes the pack. Two cases skip arming [C, untested on hardware]:

1. The board boots (battery reconnected, board reset) with the enable input released. Only a reset ends that sleep.
2. State of charge is at or below 4.9 % at sleep. Only a reset wakes it. This is a protection.

Recover with a reset. Pin 17 (RESET) is probably the microcontroller reset, active low [I]. Measure it first; expect 3.3 to 5 V with the pack awake. If confirmed, fit a momentary push button from pin 17 to ground. Never apply 12 V. A reset shows on CAN1 as the `0x111` B7 rolling counter restarting at 0 [C].

## 5. Transmitting on the maintenance bus (CAN2)

Configuration needs CAN2 transmit, but:

- Any `0x0CFF00F3` frame, even an unknown command, enters service mode and drops the contactors until command `0x00` or a fault reset [C]. Under inverter load, that is a hard disconnect.
- Lasting changes: `0xC7` erases the event log; `0xD3` switches all protection thresholds to another variant; `0xEC` with a wrong gain corrupts current readings and the protections using them; `0xC6` changes stored identity [C].

Design:

1. A GPIO drives the CAN2 transceiver's silent pin, with a pull-up holding it silent through boot, crashes and normal operation.
2. Only maintenance mode releases the silent pin. It refuses entry while the contactors are closed or current flows (CAN1 `0x112`). Exit always sends command `0x00`, then re-silences.
3. In maintenance mode, read and dump commands pass. `0xC7`, `0xD3`, `0xEC`, `0xC6`, `0xDC` and `0xDE` need confirmation with the value shown.
4. Viewing over Wi-Fi is open; maintenance mode requires authentication. A physical maintenance jumper is optional.

## 6. CAN1 transmit allowlist

Port the Linux bridge's allowlist (`bridge/txgate.py`; see [`../bridge/README.md`](../bridge/README.md)): standard 11-bit frames, DLC 8, no remote frames, `0x105` only. Remove `0x515` (section 4). Keep `0x614`, the password-free bootloader entry, refused.

The Linux bridge has three layers: OS egress filter, listen-only CAN2, allowlist. The microcontroller has firmware plus the silent pin.

## 7. Open items

- Pin mapping and active levels for RB11 to RB14 (section 3).
- Auxiliary output mapping: set one `0x105` D0 bit at a time and meter pins 8, 10, 19; use a 12 V test lamp or 1 kΩ pull-up if open-drain.
- Pin 11 (12V): function unknown; not the contactor supply (owner). Meter it asleep and awake; check continuity to the board's `5v`, `12vout` and contactor 12 V pins.
- 12VOUT current capacity; RESET pin level (section 4).
- Bit rate and termination (section 1).
