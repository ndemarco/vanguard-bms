# Vanguard 14S Li-ion BMS: CAN bus protocol (reverse engineered from firmware)

Status: derived by disassembling the full flash image `DSPIC33EP128GS806.hex`, **obtained from the internet, not extracted from the owner's own packs** (firmware dumping from the physical units has not been done yet). It is assumed to match a Vanguard lithium pack of this family (Club Car Tempo golf car, NMC, 14 series cells, about 51.6 V nominal), but this is unconfirmed: the actual firmware revision burned into the owner's packs could differ, including in ways that silently change the meaning of a byte (e.g. the variant/model-code table in Appendix A) without changing the frame layout around it.
The image itself is not in this repository. First CAN1 bench results (idle telemetry, keep-alive, contactor closing) were obtained on 2026-10-11 and are listed in Appendix D; CAN2 and most commands are still unchecked against a live pack. Raw bytes read off the live bus in Appendix D are real, bench-measured values; it is the *decode table* applied to them (frame layouts, scalings, and especially the variant table in Appendix A) that rests on the internet-sourced image and is unverified against this pack's actual onboard firmware. Every claim carries a confidence tag:

| Tag | Meaning |
|---|---|
| **[C]** | Confirmed: read directly from the code or constant tables, or (where stated) bench-confirmed on a live pack. |
| **[I]** | Inferred: reasoned from code context, arithmetic or table magnitudes. Verify on a bench before relying on it. |
| **[?]** | Unknown. |

Addresses written as `wa 00a040` are program word addresses (the same numbers as in the Ghidra listing).
Supporting notes (per-frame evidence, RAM glossaries, scripts) are in `analysis/`.

## Pack nameplate (owner-reported)

| Item | Value |
|---|---|
| Model series | Vanguard 48V 3.5 kWh Commercial Battery (Fi3.5), or the legacy 3.8 kWh 8010 series |
| Part number | Briggs & Stratton / Vanguard 80090401 |
| Nominal capacity | 67.5 Ah / 3.8 kWh at 51.6 V nominal |
| Full-charge voltage | 58.3 V to 58.8 V |
| Discharge cutoff voltage | 35.0 V |
| Charger profile | Delta-Q algorithm #293 (v1.00) |

Cross-checks against the firmware:

* The 58.8 V upper limit and 35.0 V cutoff match the firmware pack-voltage limits (`analysis/can1.md`, bytes B4-B7 of the limits frame) [C].
* Nameplate capacity (67.5 Ah) is below every firmware default capacity (73, 75, 98, 134, 72, 76 Ah). The nearest is variant 0 (73 Ah, 24 parallel cells), which suggests the firmware default carries margin over the rated value [I].
* 67.5 Ah x 51.6 V is 3.48 kWh, which matches the "3.5 kWh" series name. The "3.8 kWh" figure matches the 73 Ah default (3.77 kWh).
* Cell count: 14 modules of 32 cells each (4x8 layout), 448 cells, 32 in parallel per series group. **Confirmed by direct visual count of this pack's internals, 2026-10-14** (opened the modules, counted 4x8 = 32 cells per module across all 14 modules) [C], superseding the earlier owner-reported figure. In the firmware tables 32 parallel matches variant 2 (98 Ah, about 3.06 Ah per cell), not the 67.5 Ah/3.8 kWh nameplate above.
* **New source, 2026-10-14**: a related Vanguard product, the **Vanguard 48V 5 kWh Commercial Battery Pack**, per its archived datasheet (Vanguard Power, "5kWh Commercial Battery Pack", captured by the Wayback Machine 2020-08-08: <https://web.archive.org/web/20200808055743/https://www.vanguardpower.com/na/en_us/product-catalog/lithium-ion-battery/5kwh-commercial-battery-pack.html>), lists: Lithium-Ion 14S, 51.6 V nominal (matches this pack exactly), Nominal Capacity per IEC 61960 98.7 Ah / 5.09 kWh, Capacity at Max Discharge (EV) 97.3 Ah / 4.75 kWh, top voltage 58.8 V (IEC 61960) / 58.1 V (EV), cutoff voltage 35.0 V, CAN J1939 (29-bit) plus a secondary CAN interface. 98.7 Ah matches firmware variant 2's 98 Ah default within 0.7%, and 98.7 Ah x 51.6 V = 5094.9 Wh ties out to the datasheet's own 5.09 kWh figure.
* This settles the chemistry question, not the variant question: over the directly-counted 32 parallel 18650 NMC cells (14S32P, 448 cells total, pack built circa 2021), 98 Ah is about 3.08 Ah per cell — an ordinary mainstream 18650 NMC capacity for that era (comparable to Samsung 30Q/29E, Panasonic NCR18650GA, LG HG2). So a 32-parallel pack reporting variant 2/98 Ah would be chemically unremarkable, unlike the 67.5 Ah nameplate figure's implied 2.1 Ah/cell. But this same unit self-reports **variant 0 / 73 Ah** (`0x113` B3 = 1, `0x513` B0 = 73 Ah, bench-confirmed 2026-10-11, Appendix D), and the disassembled firmware's table pairs variant 0 with 24 parallel cells, not 32. With the cell count now confirmed by direct teardown count on this exact unit (not a different pack, not a counting error), the most likely explanation is **firmware provenance, not the pack**: the disassembled image was obtained from the internet, not extracted from this owner's own packs (see Status note at the top of this document), so the variant table in Appendix A is unverified against whatever firmware revision actually runs on this hardware. The real pack genuinely transmitted model code 1 and capacity byte 73 on the bus (that reading is solid); what's unconfirmed is whether "model code 1" means the same thing (24 parallel, 73 Ah) in this pack's actual onboard firmware as it does in the internet-sourced image used to build the decode tables. A firmware dump taken directly from one of the owner's own packs, compared against the internet-sourced image, would settle this and should be treated as the priority next step before trusting Appendix A's variant table for this hardware.

## 1. Read this first

1. The BMS has **two CAN buses**, both configured at **500 kbit/s** [C for the register values]. The
   bit rate assumes a 16 MHz crystal. The owner reports the crystal is marked "ECS 16" (ECS is a crystal maker),
   which reads as 16 MHz, though the full part number was not captured [I]. The bootloader and application PLL
   settings both give exactly 500 kbit/s with 16 MHz, which supports it.
   * **CAN1**: 11-bit IDs, **big-endian** payloads. Carries the vehicle/charger keep-alive (`0x105`),
     a sleep command (`0x515`), pack telemetry (`0x111`...`0x51D`) and a CANopen-style service channel
     (`0x614` request, `0x594` reply).
   * **CAN2**: 29-bit J1939-style IDs, source address `0xF3`, **little-endian** payloads. Carries rich
     telemetry (cell voltages, temperatures, SOC, limits) plus a maintenance command channel (`0x0CFF00F3`).
     The firmware needs nothing from CAN2 to operate (no keep-alive, no timeout). The owner's working theory is
     that CAN2 is a configuration/diagnostic bus that is not connected while the pack is in the vehicle, and the
     firmware is consistent with that [I]. The vehicle-side "CAN main" pins (U/V) are therefore expected to be
     CAN1, but this has not been checked by continuity.
2. **The pack will not keep its contactors closed unless it receives CAN1 `0x105` at least once per second**
   (section 3.1). It also needs its hardware enable input asserted (section 8). No CAN command closes the contactors.
3. Pack data (cell voltages, temperatures, SOC, current) is **broadcast periodically**. Nothing is ever
   requested. A listener only needs to receive. The only request/response protocol is the CANopen
   subset on CAN1 `0x614`/`0x594`, and it serves identity objects only.
4. Several CAN2 commands write persistent calibration and one CAN1 object reboots into the bootloader without a
   password (section 7). Do not send commands you do not understand to a pack in service.
5. Byte order differs between buses: CAN1 is big-endian, CAN2 is little-endian. CAN2 `0x18FF06F3` and
   `0x0CFF06F3` share the PGN `0xFF06` and differ only in priority bits: key on the full 29-bit ID.

## 2. Hardware context

| Item | Detail |
|---|---|
| MCU | Microchip dsPIC33EP128GS806, two ECAN modules (C1 = CAN1, C2 = CAN2) [C] |
| Cell monitor | NXP MC33771B (owner confirmed; printed "MC3371BSP1"), 14 cells, on SPI1 with 5-byte frames, CRC-8 poly 0x2F seed 0x42 [C] |
| Cells | 14 series groups of 32 parallel NMC cells (owner) |
| Temperatures | 14 NTC thermistors, read through the monitor chip analog inputs AN0..AN6 and a 2:1 multiplexer [C] |
| Current | Shunt measured by the MC33771B current channel; 6 mA per count, 0.1 mOhm shunt assumed [I]; positive current = into the pack (charge) [I] |
| Contactors | Two redundant contactors wired in series with pack positive (owner confirmed), so a weld in one can be cleared by the other. Each is a solenoid output driven by PWM (100 % duty to pull in, 50 % to hold) [C]. A separate plain precharge output (RD4) is not on the contactor connector. Its switched path most likely bypasses contactor A [I]. The connector labels "Prelay" and "srelay" are only names for contactor 1 and contactor 2; which one is firmware driver A or B is [?] |
| Persistent store | External SPI memory (NAND-like) holds an event log with calibration and counters. The MCU flash is never written by commands [C] |
| Bootloader | Top of flash (`0x013800`), reset vector jumps there [C] |

Board connectors (owner supplied labels; interpretations are [I]): external 15-pin (reset, en12v, den, can11h/l,
canh/l, fuout, fatout, 12vout, gnd, estp, 5v, cen, alout gnd), 4-pin contactor (Prelay, 12 V, 12 V, srelay),
pack sense (packv1, packv2, bat+, bat-), two 14-pin temperature connectors, three cell-tap connectors (C0..C14, one NC),
2-pin current sensor. Vehicle-side 19-pin: B signal ground, C key wake up, D 12 VDC aux, F 58 VDC MN EN,
G 58 VDC DISP EN, H 58 VDC MAIN DC-DC, K OFF B CHGR WK UP, R GND HV, S/T master key return/signal,
U/V CAN main L/H, B(+)/B(-) studs. Only one CAN bus appears on the vehicle connector.

Vehicle-side wiring reported by the owner [C as reported, not traced by the author]:
* D (12 VDC aux) goes to a diode anode; the diode cathode goes to the DC-DC converter (direct current to direct current converter). D is therefore the pack's switched 12 V output (`12vout`) waking or enabling the converter, [I].
* F (58 VDC MN EN) goes to the key switch input (KSI) of the motor control unit and to the instrument panel. F is an output from the pack to loads, not an input [I].
* G (58 VDC DISP EN) goes to the Visage unit, Club Car's on-board display and fleet-management computer [I]. G is a feed to the display.
* F, G and H are independently switched outputs, not taps behind the contactors: the Club Car diagnostic screen shows "Battery Output VCM Power: On" while "Battery Main Contactor State: Open" (Appendix C). They are the three outputs the host drives with `0x105` D0 bits 2, 4, 6 (motor control unit, vehicle control module, DC-DC converter power) [I, medium-high]. Bit-to-pin order is [?]; a meter on F, G, H while toggling each bit settles it (section 10).
* Input mapping (hypothesis): the diagnostic screen reports exactly two inputs, "Key Switch Input" and "Charger Input", matching the two inputs the firmware reports on CAN1 (RB13, RB14) [I, medium-high]. They are probably vehicle pins C or T (key) and K (charger wake up); which is which is [?]. Bench-confirmed 2026-10-11: RB13 = `en12v` and RB14 = `estp`, so the enable input RB12 and the input RB11 are `den` and `cen` in unknown order [?]. Confidence in section 8.
See `analysis/hardware_connectors.md`.

## 3. Quick start for a driver

### 3.1 Bring the pack online and keep it online (CAN1)

1. Open CAN1 at 500 kbit/s. Listen for `0x111`, `0x112`, `0x113`, `0x114`, `0x120` (they arrive without any request).
2. Make sure the hardware enable input is asserted [I] (section 8). Without it the contactor sequence never starts. Known-good bench configuration: `estp` and `en12v` tied to B-, `den` at 12 V, `cen` and `alout` unconnected (bench-confirmed 2026-10-11; which of them is required is not isolated). `0x120` B2 bit 3 is a latch, not a live view of the input: it stays set until a clear-faults request (step 5) clears it, so send one before the first enable (bench-confirmed 2026-10-11).
3. Send `0x105` every 100 ms (8 data bytes, always send DLC 8):

| Byte | Value | Meaning |
|---|---|---|
| D0 | `0x00` | Discharge / drive path (mode 2) [C] |
| D0 | `0x01` (any of bits 0-1) | Charge path (mode 4), also the only time balancing runs [C] |
| D1 | `0x10` | Enable. Without bit 4 the pack stays idle with contactors open [C] |
| D1 | `0x11` | Enable + also request the gated frames `0x510`, `0x512`, `0x513`, `0x51D` [C] |

4. The pack then runs its checks (no faults, temperatures in window, min cell above the low limit) and closes the
   contactors itself: first contactor B, then the precharge output until the output side reaches 70 % of pack voltage,
   then contactor A, then precharge off (section 6.2). Watch contactor state in `0x112` (B0 bit 5, B1 bit 5).
5. To clear a latched fault: send one `0x105` with D1 bit 1 set (`D1 = 0x12`/`0x13`), then clear it. Faults come back at
   once if the cause persists. Clear requests closer than 100 ms are ignored.
6. To power down: stop sending `0x105` (contactors open after about 1 s; the pack sleeps after about 5 minutes)
   or send `0x515` with D0 bits 5:4 non-zero (sleep 500 ms later).

D0 bits 2, 4, 6 drive three auxiliary output pins on the board (`LATD3`, `LATA4`, `LATA3`). The Club Car diagnostic screen
labels them "Battery Output MCU Power", "VCM Power" and "DC-DC Power" (switched supplies to the motor control unit, vehicle control module and
DC-DC converter) [I]; bit order unknown. Keep them 0 on a bench bridge so no vehicle load is powered. D1 bit 2 stretches the keep-alive timeout from about 1 s to about 20 minutes [C].

### 3.2 Read pack data (CAN2 preferred)

| Quantity | Frame | Decode |
|---|---|---|
| Cell voltages 1-14 | CAN2 `0x18FF03F3`, `04`, `05`, `06` | u16 LE millivolts, four per frame; `0x18FF06F3` has cells 13-14 then `0xFFFF 0xFFFF` |
| Pack voltage | CAN2 `0x0CF090F3` bytes 4-5 | u16 LE x 0.05 V (or sum the cells) |
| Pack current | CAN2 `0x0CF090F3` bytes 6-7 | (u16 LE - 32000) x 0.05 A, positive = charge [I sign] |
| Temperatures 1-14 | CAN2 `0x0CFF01F3` (1-7), `0x0CFF02F3` (8-14) | byte - 40 = degC, 0 = invalid sensor |
| SOC | CAN2 `0x0CFF06F3` bytes 0-1 | integer percent (u16 LE) |
| Max/min cell, indexes | CAN2 `0x0CFF21F3`, `0x0CF10AF3` | see section 5 |
| Faults and warnings | CAN2 `0x18FF08F3`, CAN1 `0x120` | bit tables in section 6.4 |
| Contactor state | CAN1 `0x112`, CAN2 `0x0CF096F3` | see sections 4 and 5 |

Never interpret `0x0CF094F3` bytes 0-3: a firmware defect leaves stale bytes there (section 5.3).

## 4. CAN1 (11-bit IDs, big-endian)

All CAN1 frames are DLC 8 except the `0x594` reply. Multi-byte values are big-endian. The firmware does not check
the received DLC, so always send 8 bytes. Scheduler granularity is 2 ms, so real periods are the nominal ones plus
a few ms. The tick assumes the 16 MHz crystal [I].

### 4.1 Frames the pack receives

| ID | Role | Notes |
|---|---|---|
| `0x105` | Keep-alive and mode request | Section 3.1. Timeout about 1 s (about 20 min with D1 bit 2) then contactors open and a "host timeout" fault latches (`0x1624` bit 2, fault word bit 8). If still silent after about 5 minutes the pack sleeps. [C] |
| `0x515` | Sleep command | D0 bits 5:4 non-zero: after 500 ms the pack sleeps. Value 2 = deep shutdown, 1 or 3 = lighter sleep that keeps wake sources. D1..D4 are read but unused. One-shot, no timeout. [C] |
| `0x614` | CANopen SDO request | Section 4.4. Any `0x614` frame stretches the `0x105` timeout to about 100 s. [C] |

A pack in sleep wakes on CAN1 activity only if SOC is above 4.9 % and the cells are healthy [C].

### 4.2 Frames the pack transmits

| ID | Period | Gate | Content |
|---|---|---|---|
| `0x111` | 50 ms | none | pack current (signed 24-bit mA), discharge limit, charge limit, counter |
| `0x112` | 50 ms | none | status bits, relay-drive bits, three voltages |
| `0x113` | 100 ms | none | state/IO bits, balancing flag, model, SOH, SOC, temperature charge limit |
| `0x114` | 100 ms | none | hottest/coolest sensor, max/min cell voltage |
| `0x120` | 100 ms (+1 at sleep entry) | none | eight raw fault/status bytes |
| `0x510` | 1000 ms | `0x105` D1 bit 0 | pack serial number (date code + sequence) |
| `0x512` | 1000 ms | `0x105` D1 bit 0 | configured voltage limits |
| `0x513` | 1000 ms | `0x105` D1 bit 0 | capacity, current ratings, temperature limits |
| `0x517` | 1000 ms | none | trip charge counters (Ah) |
| `0x51A` | 1000 ms | none | trip energy counters (Wh) |
| `0x518`, `0x519` | 1000 ms | none | lifetime energy counters (Wh) |
| `0x51B`, `0x51C` | 1000 ms | none | lifetime charge counters (Ah) |
| `0x51D` | 1000 ms | `0x105` D1 bit 0 | version, board address, charge target voltage |
| `0x594` | on request | | SDO reply (DLC = n + 4) |

#### 0x111 (50 ms)
| Bytes | Field | Decode | Conf |
|---|---|---|---|
| B0-B2 | Pack current | signed 24-bit two's complement, **1 mA/count**, positive = into the pack | [C] format; unit and sign bench-confirmed 2026-10-11 (read +17.96 A while an 18 A bench supply charged the pack, Appendix D) |
| B3-B4 | Discharge current limit | u16, 0.1 A/count | format [C], direction [I] |
| B5-B6 | Charge current limit | u16, 0.1 A/count | format [C], direction [I] |
| B7 | Counter | +1 per frame, wraps | [C] |

#### 0x112 (50 ms)
| Byte/bit | Field | Conf |
|---|---|---|
| B0 bit 1 | any `0x120` byte non-zero | [C] |
| B0 bit 2 | warning word non-zero | [C] |
| B0 bit 5 | contactor driver "A" driven (closes second) | bit [C] |
| B1 bit 5 | contactor driver "B" driven (closes first) | bit [C] |
| B2-B3 | voltage on pack-side ADC channel, u16, 10 mV/count (bench: 50.5 V against a 49.9 V cell sum, so the scale is approximate) | [I] unit |
| B4-B5 | pack voltage = sum of 14 cells, u16, 10 mV/count (5230 = 52.30 V); bench-confirmed 2026-10-11 (0x137E = 49.90 V equals the 14-cell sum within resolution) | [C] |
| B6-B7 | voltage on output-side ADC channel, u16, 10 mV/count (bench: 49.8-50.0 V as contactor A engaged, so precharge works) | [I] unit |

#### 0x113 (100 ms)
| Byte/bit | Field | Conf |
|---|---|---|
| B0 bit 0 | pack is in charge mode (mode 4) | [C]/[I] naming |
| B0 bit 2, B1 bit 2, B1 bit 5 | echoes of the host-driven aux outputs (`0x105` D0 bits 6, 2, 4) | [C] |
| B1 bit 0 | cell balancing active | [C] |
| B2 bit 0, bit 1 | debounced inputs PORTB.13, PORTB.14 asserted (low): bit 0 = RB13 = board pin `en12v`, bit 1 = RB14 = board pin `estp` (each set by tying that pin to B-; bench-confirmed 2026-10-11) | [C] |
| B2 bits 2-5 | sub-state x4 while charging: bit 2 entered, bit 3 closing/closed, bit 5 blocked by a fault | [C] |
| B3 | pack model code: 1, 6, 7, 8, 9, 10 for variants 0-5 (bench read 1 = variant 0) | [C] |
| B5 | state of health, 0.5 %/count (200 = 100 %); bench read 0xC8 = 100 % | [I] |
| B6 | state of charge, 0.5 %/count (200 = 100 %) | [C] arithmetic |
| B7 | charge current limit by temperature, whole amps (0 when not charging or temperature outside -11..60 degC) | [I] |

#### 0x114 (100 ms)
| Bytes | Field |
|---|---|
| B0 | hottest sensor, degC + 40 (0 = none) |
| B1 | coolest sensor, degC + 40 |
| B2-B3 | max cell voltage, u16 BE, mV |
| B4-B5 | min cell voltage, u16 BE, mV |
| B6-B7 | 0 |

#### 0x120 (100 ms): raw status bytes B0..B7 (full table in section 6.4)
Any non-zero byte also sets `0x112` B0 bit 1.

#### 0x510 / 0x512 / 0x513 / 0x51D (1000 ms, only while `0x105` D1 bit 0 was set)
| ID | Bytes | Content | Conf |
|---|---|---|---|
| `0x510` | B0-B3, B4, B5-B7 | 12-digit pack serial, stored split: B5-B7 = u24 BE decimal date code YYMMDD (bench read `03 39 1D` = 211229, equals the first six digits of the pack label serial, bench-confirmed 2026-10-11); B0-B3 = u32 BE, most likely the remaining six digits (sequence number) [I, owner to confirm against the label]; B4 = 0. Written as `211229` + `<sequence, withheld>`; see analysis/hardware_connectors.md "Pack serial number format". Set by CAN2 command `0xC6` | [C] layout and date code; [I] sequence |
| `0x512` | B0-B1, B2-B3, B4-B5, B6-B7 | low cell limit, high cell limit, low pack limit, high pack limit; u16, 10 mV/count. Model 0: 250, 425, 3500, 5880 (bench-confirmed 2026-10-11) | [C] |
| `0x513` | B0 | capacity, whole Ah (73 for model 0; the pack reported 73 on the bench, 2026-10-11) | [C]/[I] unit |
| | B1-B2, B3-B4 | two current ratings, 0.1 A/count (model 0: 1500, 2400 = 150 A, 240 A; bench-confirmed values 2026-10-11, role of each still [I]) | [C] values / [I] role |
| | B5 | constant 5 (bench read 5) | [?] |
| | B6, B7 | low and high temperature limit bytes (degC + 40; 0x14 = -20 C, 0x6E = +70 C on the bench) | [I] |
| `0x51D` | B0-B2 | bench read `02 04 1F` on 2026-10-11 (the earlier reading `02 09 07` as firmware version 2.9.7 was wrong or not a version); meaning unknown | [C] value / [?] meaning |
| | B3 | board address strap value (0x28..0x2E, or 0; bench read 0x28) | [C]/[I] |
| | B4-B5 | per-cell charge target voltage, u16 BE mV (4150 or 4200; bench read 4150) | [I] |
| | B6-B7 | model constant (meaning unknown) | [?] |

#### Counters (1000 ms, no gate)
Trip counters reset when a new charge starts and wrap at 65535 [I]. Lifetime counters are 32-bit, stored in the persistent log.
| ID | Bytes | Content | Unit |
|---|---|---|---|
| `0x517` | B0-B1, B2-B3, B4-B5, B6-B7 | trip Ah: discharge, net, regeneration, charger (u16 BE) | Ah [I] |
| `0x51A` | same layout | trip Wh: discharge, net, regeneration, charger (u16 BE) | Wh [I] |
| `0x51B` | B0-B3, B4-B7 | lifetime net Ah (signed), lifetime regeneration Ah (u32 BE) | Ah [I] |
| `0x51C` | B0-B3, B4-B7 | lifetime charger Ah, lifetime discharge Ah | Ah [I] |
| `0x518` | B0-B3, B4-B7 | lifetime net Wh (signed), lifetime regeneration Wh | Wh [I] |
| `0x519` | B0-B3, B4-B7 | lifetime charger Wh, lifetime discharge Wh | Wh [I] |

"Charger" means positive current while in charge mode; "regeneration" is positive current outside charge mode [I].
Counter units assume one accumulation pass per 10 ms [I].

### 4.3 CAN1 timing summary
`0x111`, `0x112`: 50 ms. `0x113`, `0x114`, `0x120`: 100 ms. `0x510`..`0x51D`: 1000 ms (gated ones need `0x105` D1 bit 0).
The pack sends one extra `0x120` just before sleeping. After boot the first copy of each frame appears one period later.

### 4.4 CANopen service channel (`0x614` request, `0x594` reply)
A genuine expedited-transfer-only SDO server for node ID 20 [C]. It has no abort frames: a request for an unknown
object gets no reply. It serves identity only: **no pack data or parameters**.

Request `0x614` (8 bytes): B0 = `0x40` read, or `0x20..0x3F` (normally `0x23`/`0x2F`) write; B1-B2 = index (LE); B3 = sub-index; B4-B7 = write data (LE).
Reply `0x594`: DLC = n + 4; B0 always `0x43` for reads; B1-B3 echo index/sub-index; data little-endian.

| Index:sub | Meaning | Value |
|---|---|---|
| `0x1000:0` | device type | `0x55AA01A2` |
| `0x1018:0` | entry count | 4 |
| `0x1018:1` | vendor ID | 1 |
| `0x1018:2` | product code | `0x7B` |
| `0x1018:3` | revision | `0x00010001` |
| `0x1018:4` | serial | `0x00BC614E` (12345678, placeholder) |
| `0x1F51:1` | program control | **write 0: reboot into the bootloader, no password** (reply `60 51 1F 01 00 00 00 00`) |

Example: read vendor ID: send `0x614` data `40 18 10 01 00 00 00 00`; reply `0x594` data `43 18 10 01 01 00 00 00`.

## 5. CAN2 (29-bit J1939-style IDs, source address 0xF3, little-endian)

ID layout: priority (bits 28-26), PF/PS = PGN, SA = `0xF3`. `0x0Cxxxxxx` = priority 3, `0x18xxxxxx` = priority 6.
All frames are DLC 8, sent unsolicited, no multiplexing and no request handling. Periods assume the 16 MHz crystal.
Temperature byte = degC + 40. Cell voltage in mV. Current = (raw16 - 32000) x 0.05 A, positive = charge [I].

### 5.1 Summary

| ID | Period | Content |
|---|---|---|
| `0x0CFF20F3` | 100 ms | mode, fault flag, remaining capacity, cell sum, current |
| `0x0CFF21F3` | 100 ms | max/min temperature, max/min cell voltage |
| `0x0CF094F3` | 100 ms | SOC x640 (two values). **Bytes 0-3 are corrupt (firmware defect)** |
| `0x0CFF31F3` | 100 ms | smoothed current, over-current counters, mean current |
| `0x0CFF32F3` | 100 ms | cell sum, two ADC voltages |
| `0x0CFF33F3` | 1000 ms | state of health and last three learned capacities |
| `0x0CFF34F3` | 1000 ms | capacity-learning counters |
| `0x0CFF35F3` | 500 ms | state-of-health bounds |
| `0x0CFF3EF3` | 500 ms | pack configuration |
| `0x0CF090F3` | 500 ms | pack voltage and current |
| `0x0CFF06F3` | 1000 ms | SOC percent, precharge settings |
| `0x0CF093F3` | 1000 ms | remaining/missing capacity, balancing mask, capacity |
| `0x0CF095F3` | 500 ms | output-side voltage |
| `0x0CF10AF3` | 500 ms | max/min cell index, mean cell mV, spread |
| `0x0CFF01F3`, `0x0CFF02F3` | 500 ms | temperatures 1-7, 8-14 |
| `0x18FF03F3`..`0x18FF06F3` | 500 ms | cell voltages 1-14 |
| `0x0CF096F3` | 500 ms + one-shots | output state, input pins |
| `0x18FF08F3` | 500 ms + one-shot | mode, sub-state, warning word, fault word, config byte |
| `0x18FF07F3` | 500 ms | max/min/mean temperature |
| `0x18FF30F3` | 5000 ms | constant bytes `CD E9 02 93 4A 00 00 00` |
| `0x18FF31F3` | 500 ms | current-sensor scale/offset, cell limit |
| `0x18FF32F3` | 500 ms | monitor-chip identity word |
| `0x18FF50F3` | about 500 ms + events | log status or CAN1 error pattern |
| `0x18FF51F3`, `0x18FF52F3` | reply only | event-log index, record dump |

One scheduler sends at most one frame per 2 ms pass, in the order above, so right after boot some frames can be
tens of ms late [C]. The two one-shot frames `0x18FF08F3` and `0x0CF096F3` also go out once as the pack goes to sleep.

### 5.2 Layouts (all multi-byte fields little-endian)

#### 0x0CFF20F3
| B | W | Field | Conf |
|---|---|---|---|
| 0 | 1 | mode: 1 off/idle, 2 discharge/run, 3 charge, 4 service (maintenance command received), 5 start-up | mapping [C], names [I] |
| 1 | 1 | fault/warning present (1 if the latched word is non-zero) | [C] |
| 2-3 | 2 | remaining capacity, 0.01 Ah | [I] unit |
| 4-5 | 2 | cell sum, mV | [C] |
| 6-7 | 2 | current, (raw - 32000) x 0.05 A | [C] formula |

#### 0x0CFF21F3
B0 max temp byte, B1 min temp byte, B2-B3 max cell mV, B4-B5 min cell mV, B6-B7 `FF FF`. [C]

#### 0x0CF094F3 (defect)
Bytes 0-3 were meant to carry the discharge and charge current limits but the firmware writes them into the CAN1
buffer, so the wire carries stale bytes from the previous frame (normally a copy of `0x0CFF21F3` bytes 0-3). **Ignore B0-B3.** [C]
B4-B5 = SOC x 640 (100 % = 64000, 1 % steps). B6-B7 = a second SOC x 640 derived from the highest cell [I].
The real current limits are on CAN1 `0x111`.

#### 0x0CFF31F3
B0-B1 smoothed current (same encoding as above). B2-B3 over-current event counter A. B4-B5 mean current of last samples. B6 = 0. B7 over-current event counter B (low byte). [C]

#### 0x0CFF32F3
B0-B1 cell sum mV. B2-B3 pack-side ADC voltage mV. B4-B5 output-side ADC voltage mV. B6-B7 = 0.
The ADC scale divisor (`RAM 0x1CC2`, default 3418 from the data-init table) is uncertain, so treat B2-B5 as approximate [I].

#### 0x0CFF33F3, 0x0CFF34F3, 0x0CFF35F3 (learned capacity, health)
| ID | Bytes | Content | Conf |
|---|---|---|---|
| `0x0CFF33F3` | B0-B1 | state of health, 0.1 % (1000 = 100 %) | [I] |
| | B2-B3, B4-B5, B6-B7 | last three learned capacities (unit unknown) | value [C], unit [?] |
| `0x0CFF34F3` | B0-B1 / B2-B3 / B4-B5 / B6-B7 | measurements done / reference capacity of the model table (460, 487, 615, 772, 413, 441) / ring index 0-2 / latest learned capacity | [C]/[?] |
| `0x0CFF35F3` | B0-B1, B2-B3 | SOH upper and lower bound, 0.1 % | [I] |

#### 0x0CFF3EF3 (pack configuration)
B0 model index 0-5; B1 type code; B2 variant id (1, 6, 7, 8, 9, 10); B3 parallel-cell count guess (24, 29, 32, 28, 15, 16); B4 = 14 (series cells); B5 = 1; B6-B7 capacity 7300, 7500, 9800, 13400, 7200, 7600 (0.01 Ah). [C] values, [I] meanings.

#### 0x0CF090F3 / 0x0CF095F3 / 0x0CF093F3 / 0x0CFF06F3
| ID | Bytes | Content |
|---|---|---|
| `0x0CF090F3` | B0-B3 = 0; B4-B5 | pack voltage, 0.05 V/bit; B6-B7 current |
| `0x0CF095F3` | B0-B1 | voltage after the contactors, 0.05 V/bit [I role]; B2-B7 = 0 |
| `0x0CF093F3` | B0-B1 | remaining capacity, 0.01 Ah [I] |
| | B2-B3 | missing capacity to full |
| | B4-B5 | **balancing mask**: bit n set = cell n+1 is being bled [I] |
| | B6-B7 | rated capacity |
| `0x0CFF06F3` | B0-B1 | SOC, whole percent |
| | B2-B4 | `FF FF FF` |
| | B5 | precharge completion threshold, percent (default 70) |
| | B6-B7 | configuration word (default 3000) |

#### 0x0CF10AF3 (cell statistics)
B0 cell number (1-14) of the max cell; B2 cell number of the min cell; B1 and B3 = `FF`; B4-B5 mean cell mV; B6-B7 max - min mV. [C]

#### Temperatures, cell voltages
* `0x0CFF01F3` B0-B6 = sensors 1-7, B7 = FF. `0x0CFF02F3` B0-B6 = sensors 8-14, B7 = FF. Byte = degC + 40, 0 = invalid. [C]
  Sensor numbering follows the firmware's own order (ADC channel permutation recorded in `analysis/can2tx.md` section 2.15); physical placement unknown [?].
* `0x18FF03F3`: cells 1,2,3,4. `0x18FF04F3`: 5-8. `0x18FF05F3`: 9-12. `0x18FF06F3`: 13, 14, `FFFF`, `FFFF`. u16 mV each. [C]
* `0x18FF07F3`: B0 max temp, B1 min temp, B2 mean temp (valid sensors), B3 = 0, B4-B7 = `FF`. [C]

#### 0x0CF096F3 (output stage and inputs)
| B | Content |
|---|---|
| 0 | bit 0 = solenoid driver B on, bit 2 = driver A on |
| 1-2, 4-5 | `FF FF` |
| 3 | 0 |
| 6 | high nibble = debounced inputs, bit 7 = PORTB.13, bit 6 = PORTB.11, bit 5 = PORTB.12, bit 4 = PORTB.14 (bit set = pin low). Pin-to-label mapping unknown [?] |
| 7 | bit 0 driver B, bit 1 aux LATA4, bit 2 aux LATA3, bit 3 aux LATD3, bit 4 board-on outputs, bit 5 driver A |

#### 0x18FF08F3 (status words)
| B | Field | Conf |
|---|---|---|
| 0 | mode raw: `0x10` start-up, 1 idle, 2 discharge/run, 4 charge, 8 service | [C] |
| 1 | sub-state: 0 no fault, 1 connecting, 2 running, 8 fault | values [C], names [I] |
| 2-3 | latched fault/warning summary word (section 6.4) | [C] |
| 4-5 | live fault word | [C] |
| 6 | 0 | [C] |
| 7 | configuration byte, default 9; overwritten by data[4] of RX `0x0CFFBC49` | [C] |

#### 0x18FF31F3 / 0x18FF32F3 / 0x18FF30F3
`0x18FF31F3`: B0-B1 current scale x 1e-4 (default 10000), B2-B3 charge cell-voltage limit mV (default 4150), B4-B5 100 x (10000/scale) = 100, B6-B7 current zero offset counts.
`0x18FF32F3`: B0-B3 monitor chip identity word, B4-B7 = 0. `0x18FF30F3`: constant `CD E9 02 93 4A 00 00 00`. [C]

#### 0x18FF50F3 / 51 / 52 (event log)
* `0x18FF50F3` (a) log status: B0-B3 log write pointer (big-endian), B4 verify status (0 = ok), B5-B6 write count (BE), B7 last event code. Sent after each log write and about every 500 ms when idle.
* `0x18FF50F3` (b) CAN1 error report from the CAN1 interrupt: all-same-byte patterns: `01 00 01 00 01 00 01 00` bus off, `02 02 02 02 02 02 02 02` transmit bus passive, `01 02 01 02 01 02 01 02` receive bus passive, `01 01 01 01 01 01 01 01` receive buffer overflow. No discriminator byte separates (a) from (b).
* `0x18FF51F3` (reply to command `0xC9`): B0-B3 latest record number (LE), B4-B7 raw pointer.
* `0x18FF52F3` (reply to command `0xCB`): first frame B0 = 0, B1-B3 record number low bytes, B4-B7 first record bytes; then frames k = 1..36 with B0 = k and 7 record bytes each (record offsets 7k-3..7k+3). Record layout is not fully mapped.
Event codes in B7 of `0x18FF50F3` (a): `0x0A` bootloader jump, `0x0C` current scale set, `0x0D` precharge word set, `0x0F` cell limit set, `0x10` permanent fault logged, `0x11` fault cleared, `0x12` write retry, `0x13` serial set, `0x14` model set, `0x15` host-silence sleep, `0x16` enable dropped.

### 5.3 CAN2 frames the pack receives

| ID | Effect |
|---|---|
| `0x0CFF00F3` | Maintenance command channel (section 7). **Any** frame with this ID, even an unknown command, puts the pack in service mode (mode 8) and drops the contactor outputs until command `0x00` or a fault resets the mode. [C] |
| `0x0CFFBC49` | Only data[4] is read, stored as the config byte (reported in `0x18FF08F3` B7). If data[4] is not 3 or 5 a transient warning bit (word bit 8) is set. Sender is probably a charger (node 0x49) [I]. |
| `0x0C1BF349`, `0x0C1BFF49`, `0x18DFFFF3` | Accepted and ignored [C]. `0x18DFFFF3` is not a J1939 request (that would be PF `0xEA`). |

There is no timeout on any CAN2 receive ID, and no J1939 request handling [C].

## 6. Operating model

### 6.1 Modes (RAM `0x1678`, visible in CAN2 `0x18FF08F3` B0 and `0x0CFF20F3` B0)
| Raw | Meaning |
|---|---|
| `0x10` | just booted; behaves as idle until a request arrives |
| 1 | idle, contactors open |
| 2 | discharge/drive path (entered when `0x105` D0 bits 0-1 are 0 and D1 bit 4 is set) |
| 4 | charge path (D0 bits 0-1 non-zero, D1 bit 4 set); only mode where balancing runs |
| 8 | service mode, entered by any CAN2 `0x0CFF00F3` frame |
Names "charge" and "discharge" are inferred from the logic present in each mode [I]. The `0x105` timeout, fault checks and sleep logic all key on modes 2 and 4.

Entry checks for modes 2 and 4: no fault bytes set, no non-zero latched word, max temperature below the over-temperature limit and
min temperature above the under-temperature limit, min cell voltage at or above the low limit (mode 2: 2500 mV model 0), hardware enable input asserted. [C]

### 6.2 Contactor sequence (state byte `0x1622`, one step per about 10 ms pass) [C]
Voltages: V_A = pack-side ADC (`0x17EC`), V_B = output-side ADC (`0x17F0`), V_pack = sum of cells.
| State | Outputs | Exit condition | Failure |
|---|---|---|---|
| 1 | all off | V_A below 2000 mV, go to 2 | V_A at or above 2000 mV with everything open: contact welded or external source, fault `0x0200`/`0x1626` bit 2 |
| 2 | driver B pulls in (100 % duty) | after about 80 ms, V_A and V_B within 2000 mV | mismatch fault (`0x1626` bit 1) |
| 3 (precharge) | precharge output on, B holds | V_B at least V_pack, or 100 x V_B / V_pack at least 70 % (value set by command `0xC5`) | ratio under 29 % after 290 ms, or 1 s total: precharge timeout (`0x1627` bit 0) |
| 4 | precharge on, driver A pulls in | after 80 ms, V_A within 2000 mV of V_pack | mismatch fault |
| 5 | precharge off, A holds | after 80 ms same check | mismatch fault |
| 6 (closed) | A and B hold (50 % duty) | continuous check: V_A vs V_B within 5000 mV, V_pack vs V_A within 5000 mV | mismatch fault |
| `0xFE` opening | B off first, then A, checks for a welded contact | | |
Hold duty is 50 % at about 15.6 kHz. Opening takes about 0.3 s. A and B are two redundant series contactors (owner confirmed), not a main and a precharge relay.
The precharge output (RD4) is a separate path that is on only in states 3 and 4; it most likely bypasses contactor A, because the output side
rises while B is closed and A is open [I]. Which firmware driver is the connector's "Prelay" and which is "srelay" is [?].
The opening order (B first, then A, then a weld check) fits the redundancy: if one contactor welds, the check reports it and the other still opens the circuit.

Contactors open on: `0x105` D1 bit 4 cleared, `0x105` timeout, any latched fault, enable input released, over-current, cell/temperature limits,
monitor-chip communication failure, a CAN2 `0x0CFF00F3` frame, sleep.

### 6.3 Protection thresholds (variant 0 values; six variants in the appendix)
| Quantity | Threshold | Action |
|---|---|---|
| Max cell voltage | 4250 mV for about 3 s | over-voltage trip (`0x1624` bit 3), contactors open |
| Max cell voltage | 4200 mV, clears at 4190 mV | over-voltage warning (non-blocking) |
| Min cell voltage | 2500 mV (discharge path, about 3 s) | under-voltage trip (`0x1624` bit 5) |
| Min cell voltage | 2250 mV while discharging; 2000 mV before sleep | under-voltage trip / deep-discharge shutdown |
| Min cell voltage | 3000 mV, clears at 3050 mV | low-cell warning (non-blocking) |
| Max temperature | 70 degC (65 degC variant 1) | over-temperature trip (`0x1627` bit 7); warning derate at 58 degC |
| Min temperature | -20 degC | under-temperature (`0x1627` bit 6) |
| Fewer than 12 valid temperature sensors | | fault `0x1627` bit 6 |
| Cell spread (max - min) while charging | 500 mV (250 mV variants 3-5), only above the 3800 mV balance floor | imbalance fault (`0x1623` bit 1, word bit 6) |
| Over-current | -900 A, -500 A, +500 A and averaged thresholds (table offsets `0x98`..`0xD0`) | over-current trip (`0x1626` bit 7), logged in the persistent store |
| Charge current limit by temperature | 0 outside -11..60 degC; 36 A maximum (0.5 C of 73 Ah) | reported in `0x113` B7 |
Time constants are counted in main-loop passes of about 10 ms, so "about 3 s" assumes 300 passes at 10 ms [I].
Temperatures are degC + 40 internally. Thresholds are read from the active variant table (appendix).

### 6.4 Fault and status bits

#### CAN1 `0x120` bytes (`0x1623`..`0x162A`); any non-zero = fault present
| Byte | Bit | Meaning | Conf |
|---|---|---|---|
| B0 | 1 | cell imbalance (max at or above 3800 mV and spread above limit) | [I] naming |
| B1 | 1 | current-sensor zero offset out of range at start-up | [C] |
| B1 | 2 | `0x105` keep-alive timeout | [C] |
| B1 | 3 | cell over-voltage trip | [C] |
| B1 | 4 | cell over-voltage warning (non-blocking) | [C] |
| B1 | 5 | cell under-voltage trip | [C] |
| B1 | 6 | cell under-voltage warning (non-blocking) | [C] |
| B2 | 3 | hardware enable input released (contactor closing refused); a latch, cleared only by the clear-faults request (`0x105` D1 bit 1), it does not drop when the input is satisfied again | [C] latch (bench-confirmed 2026-10-11); [I] naming |
| B3 | 1 | contactor voltage mismatch, pack side vs output side | [C] |
| B3 | 2 | output voltage mismatch or welded contact | [C] |
| B3 | 7 | over-current trip | [C] |
| B4 | 0 | precharge failed | [C] |
| B4 | 1 | cell monitor chip communication failure | [C] |
| B4 | 6 | under-temperature, or fewer than 12 valid temperature sensors | [C] |
| B4 | 7 | over-temperature | [C] |
| B5 | 1 | invalid pack-variant index | [C] |
| B5 | 5 | temperature spread of 20 or more (non-blocking) | [?] meaning |
| B5 | 6 | extreme over-temperature (100 degC) | [I] |
| B5 | 7 | persistent-store fatal event latched | [C] |
| B6, B7 | all | never set | [C] |
Non-blocking warnings (they do not force idle or open the contactors): B1 bits 4 and 6, and B5 bit 5 [C]. Every other bit above is a fault that forces idle.
Clear-faults (`0x105` D1 bit 1) clears all bytes except `0x1628` bits 1 and 7.

#### CAN2 `0x18FF08F3` words (B2-B3 latched summary, B4-B5 live warnings)
| Bit | Latched summary word (B2-B3) | Live word (B4-B5) |
|---|---|---|
| 0 | cell under-voltage | low-cell warning |
| 1 | temperature out of window / fewer than 12 sensors | temperature derate |
| 2 | over-current | over-current warning |
| 3 | current sensor offset bad | current sensor reading abnormal while idle |
| 4 | cell over-voltage | cell over-voltage warning |
| 6 | cell imbalance | |
| 8 | host timeout (`0x105`) | set by `0x0CFFBC49` data[4] not 3 or 5 |
| 9 | contactor/precharge voltage fault | |
| 10 | enable input released | |
| 12 | cell monitor communication failure | monitor read stage failed |
| 15 | permanent (stored) event latched | |
Bits 5, 7, 11, 13, 14 are never set [C]. Names are inferred from the conditions that set them [I].

### 6.5 Cell balancing (passive, MC33771B bleed switches) [C]
* Runs only while `0x105` has D1 bit 4 set and D0 bits 0-1 non-zero (charge request).
* Needs the max cell at or above the floor (default 3800 mV) for 300 passes, and a cell at least the start spread (default 40 mV) above the lowest for 60 passes.
  A cell already balancing stays selected until within the stop spread (default 10 mV).
* The firmware writes `0x0200` (balance on) or `0` into monitor registers `0x0C`..`0x19` (one per cell) and refreshes them every 35 s.
  The mask of bled cells is in CAN2 `0x0CF093F3` B4-B5 and a flag in CAN1 `0x113` B1 bit 0.
* Change thresholds with CAN2 command `0xC3` (RAM only; reverts to defaults on the next charge-state init) (section 7).
* Bleed resistor and current are hardware [?].

### 6.6 Current, SOC and capacity
* Current: monitor chip reads the shunt; `mA = (counts - offset) x 6 x (10000 / gain)`, gain default 10000 (command `0xEC` changes it). A 125 mA coil-current correction is
  subtracted while a solenoid is driven. Idle dead-band: while idle, readings within 349 counts are forced to zero.
* SOC: looked up from cell voltage at start-up and after 900 s of rest (current under 1 A), integrated by coulomb counting once a second otherwise. Range 0..100.9 %.
* Capacity default per variant: 73, 75, 98, 134, 72, 76 Ah (variant 0 = 73 Ah) [I unit]. SOH starts at 100 % and is reduced by wear tables driven by discharged Ah [I].

## 7. Maintenance commands: CAN2 `0x0CFF00F3`

Always send 8 data bytes. data[0] is the command. Pairs marked "inverse" must sum to `0xFF` (second byte = bitwise NOT of the first) or the command is silently ignored.
Every frame with this ID, including bad or unknown commands, first forces **service mode (mode 8)**: the normal contactor logic stops and the contactor outputs are driven off every pass.
Only command `0x00` (or a fault reset or reboot) returns the pack to normal control. After sending any of these commands, send `0x00`, then resume `0x105`.
Commands execute in the CAN receive interrupt; leave at least 50 ms between commands [C].

| Cmd | Data bytes | Effect | Persists | Reply |
|---|---|---|---|---|
| `0x00` | `00 xx xx xx xx xx xx xx` | leave service mode, return to idle, re-evaluate `0x105`. Ignored if `0xDC` or `0xDE` was sent. | state only | none |
| `0xC3` | `C3 xx S T FL FH ~FL ~FH` | balance start spread S (mV), stop spread T (mV), floor FH:FL (mV) | RAM only | none |
| `0xC4` | `C4 xx xx xx VL VH ~VL ~VH` | cell charge-voltage limit V mV (clamped 3000..4245), reported in `0x18FF31F3` B2-B3 | yes (log) | `0x18FF50F3`, B7 = `0x0F` |
| `0xC5` | `C5 xx P ~P LL LH ~LL ~LH` | precharge completion threshold P percent (default 70) and configuration word | yes | B7 = `0x0D` |
| `0xC6` | `C6 D1 D2 D3 D4 D5 D6 D7` | set stored identity numbers (shown in CAN1 `0x510`). Refused on a pack with more than 5x capacity of lifetime discharge. | yes | B7 = `0x13` |
| `0xC7` | `C7 xx xx xx xx xx xx xx` | **erases the event log** (first 16 blocks of the external memory) and rewrites current values. Same age gate. | yes | `0x18FF50F3` |
| `0xC9` | `C9 xx xx xx xx xx xx xx` | read log index | no | `0x18FF51F3` |
| `0xCB` | `CB N0 N1 N2 N3 xx xx xx` | dump log record N (LE); 1 + 36 frames on `0x18FF52F3` | no | `0x18FF52F3` |
| `0xCF` | `CF xx xx xx xx xx xx xx` | clear latched permanent-fault word | yes | B7 = `0x11` |
| `0xD3` | `D3 xx xx xx M ~M xx xx` | **select pack variant M (0..5)**; all thresholds change immediately. Same age gate. | yes | B7 = `0x14` |
| `0xDC` | `DC xx ...` | in service mode: turns off a 12 V-related output group, blocks command `0x00` | RAM | none |
| `0xDE` | `DE xx ...` | in service mode: turns off another aux output group, blocks command `0x00` | RAM | none |
| `0xEC` | `EC xx xx xx GL GH ~GL ~GH` | current-sensor gain G (10000 = 1.0000); **no range check, zero causes a divide fault** | yes | B7 = `0x0C` |

The age gate for `0xC6`, `0xC7`, `0xD3` compares a lifetime discharge counter to 5 x capacity/100; units are assumed Ah, so a heavily used pack may refuse them [I].
`0xDC` and `0xDE` do not unlock other commands [C]. There is no command that closes contactors, reads cell data on request, or changes the `0x105` timeout [C].

Example frames (29-bit ID `0x0CFF00F3`, DLC 8):
* Set balance 40 mV start, 10 mV stop, 3800 mV floor: `C3 00 28 0A D8 0E 27 F1`
* Leave service mode: `00 00 00 00 00 00 00 00`
* Set current gain 1.0000: `EC 00 00 00 10 27 EF D8`
* Set cell charge limit 4150 mV: `C4 00 00 00 36 10 C9 EF`
* Select variant 2: `D3 00 00 00 02 FD 00 00`
* Clear permanent fault: `CF 00 00 00 00 00 00 00`
* Dump record 5: `CB 05 00 00 00 00 00 00`

## 8. Hardware inputs and outputs the firmware uses

| Pin (MCU) | Function | Likely connector label | Conf |
|---|---|---|---|
| RC12 (OC2) | contactor driver A, PWM | Prelay or srelay (redundant series contactors) | [C]/[?] |
| RA0 (OC3) | contactor driver B, PWM | srelay or Prelay (redundant series contactors) | [C]/[?] |
| RD4 | precharge output, on only in contactor states 3 and 4; not on the contactor connector | internal precharge path, probably across contactor A | [I] |
| RA1, RA2 (AN1, AN2) | voltage sense after/before the contactors | packv1 / packv2 | [I] |
| RB12 | **enable input, active low; gates the contactor machine; release forces sleep** | one of den, cen (not en12v or estp, which are RB13 and RB14); unknown | [?] |
| RB11 | debounced input, selects which fault word imbalance uses | the other of den, cen; unknown | [?] |
| RB13 | debounced input, reported in CAN1 `0x113` B2 bit 0 | board pin `en12v` | [C] (bench-confirmed 2026-10-11) |
| RB14 | debounced input, reported in CAN1 `0x113` B2 bit 1 | board pin `estp` | [C] (bench-confirmed 2026-10-11) |
| RB6 | wake pin in the sleep loop | unknown | [?] |
| RA3, RA4, RD3 | aux outputs controlled by `0x105` D0 bits 6, 4, 2; shown on the diagnostic screen as "Battery Output MCU / VCM / DC-DC Power" | vehicle pins F, G, H in unknown order; board labels fatout / alout / fuout in unknown order | [I] medium-high / [?] order |
| RC13, RC0, RD12 | "board on" outputs (12 V related) | 12vout = vehicle pin D, through a diode to the DC-DC converter | [I] medium-high |
| RC9, RD13, RD7 | 3-bit board address strap (reported in `0x51D` B3) | internal | [C] |
| RB8/RD11/RB15, RD0 | SPI1 to the MC33771B (clock, out, in, chip select) | | [C] |
| RC2/RC7/RC8, RD9 | SPI2 to the external persistent memory | | [C] |
| RB0 | temperature multiplexer select | | [C] |
| RD6/RD5, RC5/RC4 | CAN1 TX/RX, CAN2 TX/RX | can11h/l, canh/l | [C]/[I] |
No CAN command reads or overrides RB11..RB14. RB13 and RB14 assert when their pin is tied to B- (active low, bench-confirmed 2026-10-11); this corrects the earlier guess in `analysis/core.md` that RB12 = en12v. The diagnostic screen's "Key Switch Input" and "Charger Input" labels therefore may not map to RB13/RB14 as guessed in section 2; unresolved. Known-good enable configuration: `estp` and `en12v` tied to B-, `den` at 12 V (permanent jumper to the 12V cavity), `cen` and `alout` unconnected; which of these RB12 needs is not isolated. `12vout` is a true output: the pack generates 12 V there by itself.

## 9. Dangerous and destructive operations
* CAN1 `0x614` write `23 51 1F 01 00 00 00 00`: reboots into the bootloader with no password; the pack stops running until the bootloader hands back control.
* CAN2 `0x0CFF00F3` command `0xC7` erases the event log; `0xD3` switches all protection thresholds to another variant; `0xEC` with a wrong gain corrupts all current readings and protections that use current; `0xC6` changes stored identity.
* Any `0x0CFF00F3` frame opens the contactors (service mode). Do not send one to a pack that is carrying load.
* Do not change balancing parameters, limits or variants unless the values are checked against the cell datasheet. This is a lithium pack: protections must stay armed.

## 10. Open questions and suggested bench tests
1. **Crystal and bit rate.** CAN1 500 kbit/s is bench-confirmed (2026-10-11). The crystal is marked "ECS 16", probably 16 MHz. Confirm the full part number and measure 500 kbit/s with a scope or CAN analyzer on CAN1/CAN2. All periods and time-outs in this document assume it.
2. **Which CAN is which.** Continuity from the vehicle "CAN main" pins to the board; confirm CAN1 is the vehicle bus.
3. **Enable inputs.** Partly answered 2026-10-11: `en12v` = RB13 and `estp` = RB14, active low; `den`/`cen` = RB11/RB12 still unmapped and the input that RB12 needs is not isolated (Appendix D). Remaining: measure the active level of the board's `den`, `cen`, `estp`, `en12v` pins and map them to RB11..RB14 (section 8).
   Check continuity from vehicle pins C, K, S, T, F, G, H, D to the 15-pin connector, then measure C, S, T with the key off and on, and watch CAN1 `0x113` B2 bits 0-1 and CAN2 `0x0CF096F3` B6 when K is asserted.
   Meter F, G, H while toggling `0x105` D0 bits 2, 4, 6 one at a time to learn which bit powers which output, and D with the pack awake and asleep.
   Assert C, then T/S, then K one at a time and see which of `0x113` B2 bits 0-1 changes (key switch versus charger input).
4. **Contactor identity.** Which of driver A and driver B is the connector's Prelay and which is srelay, and what path the precharge output switches (probably across contactor A).
5. **Current sign and unit.** Answered for CAN1, bench-confirmed 2026-10-11: `0x111` B0-B2 is 1 mA/count, positive = into the pack (+17.96 A while an 18 A bench supply charged the pack; owner confirmed the direction). CAN2 `0x0CF090F3` current uses the same stated convention (positive = charge) and is expected to match, not checked [I].
16. **Delta-Q IC650 charger needs a CAN master.** Bench 2026-10-11: the Tempo-OEM IC650 speaks CANopen at 125 kbit/s as node 10 (heartbeat `0x70A`, state `0x7F` pre-operational, once per second when ACKed) and did not charge from pack voltage alone (contactors closed for 35 s, current 0). It needs a CAN master (the cart's VCM). Charger-side work lives in the separate `../deltaq-charger` repo.
6. **Limit direction.** Confirm `0x111` B3-B4 is the discharge limit and B5-B6 the charge limit (change SOC or temperature and watch which moves).
7. **Voltage ADC scale.** Partly answered: on the bench `0x112` B4-B5 matches the cell sum, B2-B3 read 50.5 V against 49.9 V and B6-B7 about 50 V after precharge, so the ADC scale is only approximate. The two high-voltage ADC channels (CAN2 `0x0CFF32F3` B2-B5, CAN1 `0x112` B2-B3, B6-B7) use a constant that has no writer in the image; compare with a meter.
8. **Pin function of aux outputs** (`fatout`, `alout`, `fuout`) and what `0x105` D0 bits 2, 4, 6 do on the board.
9. **Pack variant.** Answered: variant 0 (`0x113` B3 = 1, `0x513` B0 = 73 Ah), bench-confirmed 2026-10-11. The variant index comes from the external memory (byte offset `0xAC`), not a pin.
10. **Counter units, SOH and capacity-learning fields** and the meaning of constants (`0x18FF30F3`, `0x51D` B6-B7, `0x513` B5).
11. **Event-log record layout** (256-byte records dumped with command `0xCB`): not mapped; decode `FUN_rom_006efc` if the stored cell and temperature snapshot is needed.
12. **Real transmit periods.** Partly measured 2026-10-11 on CAN1: `0x111`/`0x112` about 50 ms, `0x113`/`0x114`/`0x120` about 100 ms, `0x517`..`0x51C` 1000 ms. CAN2 not measured.
13. **Capacity mismatch.** Still open: the pack itself reports 73 Ah in `0x513` B0 (bench, 2026-10-11, variant 0), against 67.5 Ah on the nameplate and 54 Ah on the screen. The diagnostic screen shows "Battery Ratings 54 Ah"; no variant table holds 54 Ah (73, 75, 98, 134, 72, 76). Read `0x513` B0 and CAN2 `0x0CFF3EF3` B0 and B6-B7 from the pack. A fourth number is now in play: the archived Vanguard "5kWh Commercial Battery Pack" datasheet (see "Pack nameplate" section above) lists 98.7 Ah, matching firmware variant 2 and this pack's directly-counted 32-parallel cells chemically (~3.08 Ah/cell), but not matching this unit's bench-read variant 0 (which the disassembled firmware's table pairs with 24 parallel, not the 32 actually counted inside it on 2026-10-14). Leading suspect, per the Status note at the top of this document: the disassembled firmware image was sourced from the internet, not extracted from this owner's own pack, so Appendix A's variant table may not describe the firmware revision actually running on this hardware. See open question 17.
17. **Verify the variant table against this owner's own firmware.** The disassembled `DSPIC33EP128GS806.hex` was obtained from the internet, not dumped from any of the owner's own packs. Extract firmware directly from one of the owner's units (bootloader dump, in-circuit read, or similar) and diff it against the internet-sourced image, focusing on Appendix A's variant table (PSV addresses `0xB0B4`-`0xB6E0`) and the model-code-to-variant mapping in `0x113` B3. This would explain, or rule out, the variant-0/24-parallel vs. directly-counted-32-parallel conflict in open question 13.
14. **Isolation resistance.** The diagnostic screen shows 10000 kOhm; no source for it was found in the firmware.
15. **CAN transmit errors.** During the 6 s of `0x105` transmission on the bench the controller went error-passive once (TX error counter 67 at the end, RX errors 0). Not diagnosed; candidate causes: termination, bit timing. The owner chose to ignore it for now.

## Appendix A. Variant tables (selected by variant index 0..5; PSV addresses `0xB0B4`, `0xB1F0`, `0xB32C`, `0xB468`, `0xB5A4`, `0xB6E0`)
| Variant | Cell OV trip (mV) | Cell UV trip (mV) | Deep-discharge cut (mV) | Over-temp (degC) | Capacity (0.01 Ah) | `0x113` B3 model code |
|---|---|---|---|---|---|---|
| 0 | 4250 | 2500 | 2000 | 70 | 7300 | 1 |
| 1 | 4230 | 2750 | 2250 | 65 | 7500 | 6 |
| 2 | 4250 | 2500 | 2000 | 70 | 9800 | 7 |
| 3 | 4240 | 2500 | 2000 | 70 | 13400 | 8 |
| 4 | 4240 | 2500 | 2000 | 70 | 7200 | 9 |
| 5 | 4240 | 2500 | 2000 | 70 | 7600 | 10 |
Other table fields: offset `0x44`..`0x5C` max-cell levels (4250, 4200, 4200, 4190, 4150, 4050, 3800 for variant 0), `0x60`..`0x74` min-cell levels (3000, 3000, 3050, 2500, 2250, 2000),
`0x80`/`0x84` imbalance limits (500/5000 mV, variants 3-5: 250/250 mV), `0xE0` = 14 series cells, `0xF8` 3900 mV cold charge target. Full table dumps: `analysis/core.md` section 2.6 and `analysis/cmd.md` section 4.
Which variant this pack runs is determined by reading `0x113` B3 or `0x0CFF3EF3` B0 [C].

## Appendix B. Method and provenance
* Disassembly: Ghidra 12.1.4 headless, language `dsPIC33E:LE:24:default`, full flash from the hex file. Scripts in `analysis/scripts/`; regenerate listing and decompiled C with `Full2.java`.
* Register definitions: Microchip dsPIC33E-GS device pack (`p33EP128GS806.gld`).
* Analysis notes with per-field evidence and RAM glossaries: `analysis/can1.md`, `analysis/can2tx.md`, `analysis/cmd.md`, `analysis/core.md`, `analysis/glossary_*.md`, `analysis/hardware_connectors.md`.

## Appendix C. Club Car diagnostic screen against the firmware
Owner photo of the Club Car diagnostic tool, "Battery Details" tab (pack state: SOC 12.50 %, main contactor Open). "MCU" there means motor control unit.
| Screen field (value) | Firmware source | Match |
|---|---|---|
| Battery Ratings Ah (54) | CAN1 `0x513` B0 | **Conflict**: variant tables hold 73, 75, 98, 134, 72, 76 Ah; also doesn't match the 98.7 Ah archived "5kWh" datasheet figure (see "Pack nameplate" section) |
| SOC (12.50 %) | CAN1 `0x113` B6, 0.5 % per count | confirms the scale (25 counts) |
| Battery SOH (100.00 %) | `0x113` B5, or CAN2 `0x0CFF33F3` B0-B1 | consistent |
| Output MCU / VCM / DC-DC Power (Off / On / Off) | `0x105` D0 bits 2, 4, 6, echoed in `0x113` B0 bit 2, B1 bits 2 and 5 and `0x0CF096F3` B7 bits 1-3 | strong; VCM On while contactor Open shows they are not contactor-gated |
| Safety Level (Normal) | fault bytes `0x120` | consistent |
| Balancing State (Inactive) | `0x113` B1 bit 0 | consistent |
| Key Switch Input, Charger Input (Off, Off) | `0x113` B2 bits 0-1 (RB13, RB14) | strong, order unknown |
| Isolation Resistance (10000 kOhm) | none found | placeholder or ceiling |
| Main Contactor State (Open) | `0x112` B0 bit 5, B1 bit 5 | consistent |
| Module Max Temperature (18 C) | `0x114` B0 minus 40 | consistent |
The tool's ABOUT and USAGE tabs probably map to `0x51D`/the `0x1018` identity objects and the `0x517`..`0x51C` counters [?].

## Appendix D. First live bench results (2026-10-11)
Raspberry Pi with a Waveshare 2-CH CAN FD HAT; HAT can0 on the pack's 11-bit bus (`can11h`/`can11l`). Pack on the bench with only a voltmeter across B+/B-. All items below are bench-confirmed 2026-10-11 unless marked otherwise. CAN2 is not yet wired to the pack.
* **Bus.** 500 kbit/s confirmed (ERROR-ACTIVE, zero RX errors). In listen-only mode nothing ACKs, so the pack retransmits its first frame (`0x113`) forever; with the controller in normal mode (it ACKs, sends nothing) all frames appear.
* **Idle CAN1 frames and periods.** `0x111`, `0x112` about 50 ms; `0x113`, `0x114`, `0x120` about 100 ms; `0x517`, `0x518`, `0x519`, `0x51A`, `0x51B`, `0x51C` 1000 ms.
* **Idle values.**
| Item | Value |
|---|---|
| Pack voltage (`0x112` B4-B5 = 0x137E) | 49.90 V, equals the sum of 14 cells within resolution |
| Cell min / max (`0x114`) | 3548 mV / 3577 mV |
| Temperatures | 23-24 C |
| Current | 0 |
| SOC (`0x113` B6 = 0x30) | 24 % |
| SOH (`0x113` B5 = 0xC8) | 100 % |
| Model code (`0x113` B3) | 1 (variant 0) |
| `0x111` limits | B3-4 = 0x0960 (240 A), B5-6 = 0x0460 (112 A); directions unverified |
* **Inputs.** Tying `estp` to B- sets `0x113` B2 bit 1 (RB14). Tying `en12v` to B- sets `0x113` B2 bit 0 (RB13). `den`/`cen` remain candidates for RB11/RB12.
* **12vout.** Measures 12 V with respect to B- with only `estp` and `en12v` tied to B-: the pack generates it, so it is a true output. Pin 11 (plain `12V`) also measures 12 V. On the Club Car cart the circular connector populates only `estp`, `12vout`, `alout`, `en12v`, `can11h`, `can11l` plus a permanent jumper joining cavities 11 and 13 (12V to DEN), so DEN was at 12 V during the successful test.
* **Latch.** `0x120` B2 bit 3 read 0x08 before the test and stayed 0x08 with `estp` and `en12v` tied; one clear-faults frame cleared it and it stayed 0.
* **Bring-up.** One `0x105` D0 = 00, D1 = 0x13 (clear faults + enable), then D0 = 00, D1 = 0x11 every 100 ms for 6 s. `0x120` stayed all zero. Contactor driver B (`0x112` B1 bit 5) came on about 0.1 s after the clear, driver A (B0 bit 5) about 0.1 s later (documented B-then-A order). Output-side voltage (B6-B7) was about 49.8-50.0 V as A engaged; pack-side (B2-B3) about 50.5 V against a 49.9 V cell sum. When keep-alives stopped, the contactors opened within about 1 s and the keep-alive timeout fault latched (`0x120` B1 bit 2 = 0x04); the next start needs a clear-faults first.
* **Known-good enable configuration** (not proof each part is needed): `estp` and `en12v` tied to B-, `den` at 12 V via the permanent jumper, `cen` and `alout` unconnected.
* **Gated frames** (only while `0x105` D1 bit 0 is set): `0x510` = `xx xx xx xx 00 03 39 1D` (B0-B3 = serial sequence, withheld); `0x512` = 250, 425, 3500, 5880 (2.50/4.25/35.00/58.80 V, matches model 0); `0x513` = `49 05DC 0960 05 14 6E` (73 Ah, 150 A, 240 A, B5 = 5, limits -20 C and +70 C); `0x51D` = `02 04 1F 28 1036 0960` (B0-B2 `02 04 1F`, not `02 09 07`; board address 0x28; charge target 4150 mV).
* **Capacity conflict** (nameplate 67.5 Ah, Club Car screen 54 Ah, pack reports 73 Ah) remains open.
* **Transmit errors.** The controller went error-passive once during the 6 s of transmitting (TX error counter 67 at the end, RX errors 0); not diagnosed (open question 15).
* **Bench-supply charge** (pack 1). Pack put in charge mode via the bridge (`0x105` D0 = 0x01, D1 = 0x11), then charged from a Riden RD6018 bench supply set to 57.40 V / 18 A. The BMS accepted bench-supply current with no CAN charger present. Readings during the charge:
| Item | Value |
|---|---|
| Current (`0x111` B0-B2) | +17.96 A (power going into the pack): sign and 1 mA/count confirmed, owner confirmed the sign |
| Pack voltage (`0x112` B4-B5) | 51.66 V; output side 51.85 V |
| Cells | 3.683-3.696 V (13 mV spread) |
| Temperatures | 26-27 C |
| Faults | none |
| `0x113` B0 | bit 0 = 1 (charge mode) |
| `0x113` B2 | 0x0B (bits 0, 1 inputs asserted; bit 3 closing/closed) |
| `0x113` B6 | 0x3E = 31 % SOC |
| `0x113` B7 | 0x25 = 37 A temperature charge limit; matches the documented 36 A max for variant 0 within one count |
* **Pack serial in `0x510`.** B5-B7 `03 39 1D` = 211229 = the first six digits of the pack label serial (date code 2021-12-29); B0-B3 most likely the remaining six digits (sequence) [I, owner to confirm]; B4 = 0. Recorded here as `211229` + `<sequence, withheld>`.
* **Delta-Q IC650 charger** (Tempo OEM). Speaks CANopen at 125 kbit/s as node 10: heartbeat `0x70A` with state `0x7F` (pre-operational), once per second when ACKed. It did not charge from pack voltage alone (contactors closed for 35 s, current 0); it needs a CAN master (the cart's VCM). See the separate `../deltaq-charger` repo and open question 16.
