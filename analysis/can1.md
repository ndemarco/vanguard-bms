# CAN11 (11-bit ID) interface, decoded from the firmware

Scope: every frame the BMS transmits on CAN11, the two receive handlers (0x105, 0x515), and the RAM variables behind each field.
Tags: CONFIRMED = read directly from code at the quoted program word address ("wa"). INFERRED = reasoned from code
context, tables or arithmetic; the reasoning is given. Variable addresses are data-space RAM. Companion file:
`sections/glossary_can1.md` (variable glossary). Some command-channel facts (0x614 / 0x594 / CAN) are in `sections/cmd.md`.

## 0. Conventions used by every frame

* Standard 11-bit identifier, always 8 data bytes (DLC 8), except the 0x594 service reply (DLC = n+4). CONFIRMED: DLC byte 0x1788 = 8 in every block, wa 009fd0.
* Multi-byte values are **big-endian** on the wire (high byte first). Where the firmware variable is a 32-bit pair, the low word is at the lower RAM address, so the wire order is `[hi word high byte][hi word low byte][lo word high byte][lo word low byte]`. CONFIRMED by the listing (e.g. 0x51c wa 00a132-00a184).
* All integer fields are unsigned unless stated. The only signed field is the 24-bit current in 0x111.
* Transmit mechanism: the scheduler FUN_rom_009fd0 fills a scratch frame at RAM 0x1782 and queues it into the first free hardware TX buffer (wa 00e09a, looks at 8 buffers). If all 8 are busy the frame is silently dropped (wa 00e09a returns 0, caller ignores it). CONFIRMED.
* **Time base.** RAM 0x1664 is a 16-bit millisecond tick incremented by the Timer1 interrupt: T1CON=0x8010 (on, prescaler 1:8, internal clock), PR1=0xEA6=3750, so one tick = 3750 x 8 / Fcy. With the oscillator set-up in wa 00fecc (PLLFBD=28, PLLPRE=2 -> /4, PLLPOST=0 -> /2: Fosc = 16 MHz x 30 / 8 = 60 MHz, Fcy = 30 MHz) this is exactly 1.000 ms. The interrupt vector word 0x1a (T1) points to wa 000388, which calls wa 010e86 (increments 0x1664). CONFIRMED for the arithmetic; the 16 MHz crystal is an assumption shared with the CAN bit-timing (NOTES.md).
* **Scheduler.** The main loop (wa 0086fa) calls FUN_rom_009fd0 whenever `tick - last_call >= 2` (test wa 0089ac-0089b4, calls wa 0093d6 then wa 009fd0 at 0089ba-0089be); the slow section test is `tick - 0x16ae > 9` at wa 008782, i.e. every 2 ms or slower, and the slow main-loop section runs when `tick - 0x16ae >= 10`. Each call evaluates two independent priority chains and sends **at most one frame from each chain**. A frame is due when `(uint16)(tick - frame_last_sent) >= period` (the test is `> period-1`). The last-sent stamps are all initialised to the boot tick (wa 008af4), so the first frames go out one period after boot. CONFIRMED. Real periods are therefore the nominal period rounded up to the 2 ms call spacing plus jitter from the slow loop (a few ms).
  * Chain 1, in priority order: 0x111 (50 ms) > 0x51d (1000 ms, only if 0x105 byte1 bit0) > 0x51c > 0x51b > 0x517 > 0x518 > 0x519 (all 1000 ms).
  * Chain 2, in priority order: 0x51a (1000 ms) > 0x120 (100 ms) > 0x114 (100 ms) > 0x113 (100 ms) > 0x112 (50 ms) > 0x510 (1000 ms, gated) > 0x512 (1000 ms, gated) > 0x513 (1000 ms, gated).
  * "gated" = only sent while bit0 of the last received 0x105 byte1 is 1 (RAM 0x161c bit0): wa 00a09a, 00a6b0-00a6b8, 00a740-00a74a, 00a7fa-00a804. After boot, or after a wake from sleep that clears 0x161c (wa 005c76), these four frames are not sent until the host sets that bit.
* Sleep: before the BMS enters power-save (wa 005c70) it sends **one extra 0x120 frame** (wa 005d28-005d5a builds ID 0x120 from 0x1623..0x162a) and stops all CAN11 traffic. CONFIRMED. Any CAN11 activity can wake it (C1INTE.WAKIE is enabled, `0x48c |= 0x40`, wa 005eb0-005eb2, cleared again after wake wa 005eda-005edc); INFERRED that the wake comes from CAN11 traffic.

## 1. Summary table of transmitted frames

| ID | DLC | Period (nominal) | Gate | Content |
|---|---|---|---|---|
| 0x111 | 8 | 50 ms | none | pack current (signed 24-bit, mA), discharge current limit, charge current limit (0.1 A), rolling counter |
| 0x112 | 8 | 50 ms | none | status bits (any fault byte, warning, output stages on), three voltages in 10 mV |
| 0x113 | 8 | 100 ms | none | state/IO bits, balancing flag, pack-model code, SOH, SOC (0.5 % steps), temperature charge limit |
| 0x114 | 8 | 100 ms | none | hottest and coolest sensor (degC + 40), max and min cell voltage (mV) |
| 0x120 | 8 | 100 ms (+1 at sleep entry) | none | eight raw fault/status bytes |
| 0x510 | 8 | 1000 ms | 0x105 B1 bit0 | two stored identity numbers (32-bit, 24-bit) |
| 0x512 | 8 | 1000 ms | 0x105 B1 bit0 | four configured voltage limits (10 mV): min cell, max cell, min pack, max pack |
| 0x513 | 8 | 1000 ms | 0x105 B1 bit0 | capacity (Ah), two current ratings (0.1 A), constant 5, temperature limit bytes |
| 0x517 | 8 | 1000 ms | none | resettable ("trip") charge counters, Ah, four 16-bit |
| 0x518 | 8 | 1000 ms | none | lifetime net energy and regeneration energy, Wh, two 32-bit |
| 0x519 | 8 | 1000 ms | none | lifetime charger energy and discharge energy, Wh, two 32-bit |
| 0x51a | 8 | 1000 ms | none | resettable ("trip") energy counters, Wh, four 16-bit |
| 0x51b | 8 | 1000 ms | none | lifetime net charge and regeneration charge, Ah, two 32-bit |
| 0x51c | 8 | 1000 ms | none | lifetime charger charge and discharge charge, Ah, two 32-bit |
| 0x51d | 8 | 1000 ms | 0x105 B1 bit0 | version 2.9.7, board address, charge target voltage, pack-model constant |
| 0x594 | n+4 | on request | - | CANopen-style SDO reply to a 0x614 request (covered in cmd.md) |

0x594 is also produced by wa 00a8b2 (called as `00a8b2(0x580, ...)`: ID = 0x580 | 0x14 = 0x594, 8 data bytes copied from program memory 0xC7E0, then the BMS sets RAM 0x1779 = 1, the jump-to-bootloader flag) and by wa 00a918 (identity-object uploads, ID built as the constant 0x594, byte 0 = request command + 3, bytes 1-2 = index low/high, byte 3 = sub-index). CONFIRMED.

## 2. Frame layouts

Notation: B0..B7 = data bytes in transmit order. "BE" = big endian. RAM names as in glossary_can1.md.

### 2.1 ID 0x111, every 50 ms (builder wa 009fe4-00a082)

| Bytes | Field | Encoding / scale | Source RAM | Tag and evidence |
|---|---|---|---|---|
| B0-B2 | Pack current | signed 24-bit two's complement, BE, **1 mA per count**, positive = into the pack (charging or regeneration), negative = discharge | float 0x13c0 -> int32 via FUN_rom_00062c (truncate toward zero), low 3 bytes sent (wa 009ffc-00a026) | CONFIRMED for the format. Unit INFERRED: 0x13c0 = counts x 6.0 x gain, 6 mA per count matches a 100 micro-ohm shunt with the monitor chip's 0.6 uV current LSB, thresholds table[0xcc]=37500 and table[0xd0]=36500 are compared directly with it (37.5 A and 36.5 A), and the Ah/Wh counters only reach 1 Ah / 1 Wh with mA (wa 006b4a-006b5e divides |I|+50 by 100) |
| B3-B4 | Discharge current limit | unsigned 16-bit BE, **0.1 A per count** (= trunc(float x 10)); negative values clamp to 0 | float 0x1bb4 = 0x1bb8 mA / 1000 (wa 00b20c-00b21c) | Format CONFIRMED (wa 00a028-00a042). Direction INFERRED: tables 0x1cd2/0x1cd4/0x1cd6 hold 75 / 240 / 150 A (model 0) = continuous / peak / mid discharge levels; the limit is derated by an over-current "heat" accumulator 0x1bc8 (wa 00afdc-00b0e0) |
| B5-B6 | Charge current limit | unsigned 16-bit BE, 0.1 A per count | float 0x1bd2 = 0x1bd6 mA / 1000 (wa 00b6f0-00b700) | Format CONFIRMED (wa 00a04a-00a068). Direction INFERRED: tables 0x1cd0/0x1ccc/0x1cce hold 37 / 112 / 74 A (model 0), max 37 A is about 0.5 C of 73 Ah and matches table[0xcc] 37.5 A, the charge over-current warning level; inputs are max cell voltage, SOC, temperature (wa 00b23c-00b300) |
| B7 | Rolling counter | unsigned 8-bit, +1 per 0x111 sent, wraps 255 -> 0; reset to 0 at boot | RAM 0x17cc | CONFIRMED wa 00a06c-00a074, 005f30 |

Example (model 0, idle, 73 Ah): current 0 mA -> B0-B2 = 00 00 00.

### 2.2 ID 0x112, every 50 ms (wa 00a59c-00a69c)

| Byte / bits | Field | Source | Tag and evidence |
|---|---|---|---|
| B0 bit 1 (0x02) | Any of the eight 0x120 bytes is non-zero | OR of 0x1623..0x162a | CONFIRMED wa 00a5b2-00a5f0 |
| B0 bit 2 (0x04) | Warning word non-zero | 0x1684 = 0x1680 OR 0x1682 | CONFIRMED wa 00a5fc-00a606 |
| B0 bit 5 (0x20) | Output stage "A" driven (output-compare 2 PWM at pull-in/hold duty); in the closing sequence this one is energised second, so it is the series (main) relay | RAM 0x17fc bit5 | CONFIRMED bit source (wa 004df0, 004e12 set; 004dd4 clear). Relay naming INFERRED from the closing order in wa 004e94 and the connector pin names (Prelay, srelay) |
| B0 other bits | 0 | | CONFIRMED |
| B1 bit 5 (0x20) | Output stage "B" driven (output-compare 3 PWM); energised first with output A off, so INFERRED to be the precharge relay | RAM 0x17fc bit0 | CONFIRMED source (wa 004e50/004e72 set, 004e34 clear). Naming INFERRED |
| B1 other bits | 0 | | CONFIRMED |
| B2-B3 | Voltage on ADC channel "mode 2" (pack terminal side) | u16 BE, **10 mV per count** (= mV / 10, 32-bit unsigned divide, low 16 bits) | RAM 0x17ec:17ee, wa 005534 | Format CONFIRMED (wa 00a63a-00a656). Unit INFERRED: compared against the cell sum 0x1ad6 in mV with +-2000 mV windows (wa 004e94, 005218) |
| B4-B5 | Pack voltage = sum of the 14 cell voltages | u16 BE, 10 mV per count | RAM 0x1ad6:1ad8 (mV), wa 00e5c0-00e674 | CONFIRMED |
| B6-B7 | Voltage on ADC channel "mode 1" (second high-voltage sense) | u16 BE, 10 mV per count | RAM 0x17f0:17f2, wa 005534 | Format CONFIRMED, meaning INFERRED (compared with 0x17ec to detect a relay that did not close, wa 004e94) |

Example: a 52.30 V pack reads 5230 = 0x146E in B4-B5.

### 2.3 ID 0x113, every 100 ms (wa 00a4b4-00a58e)

| Byte / bits | Field | Source | Tag and evidence |
|---|---|---|---|
| B0 bit 0 (0x01) | BMS is in state 4 (the charge path) | RAM 0x1678 == 4 | CONFIRMED value; "charge" naming INFERRED: state 4 is entered by wa 00594c and run by wa 0057ec, which uses the per-cell charge target 0x1c80 and a temperature based charge current limit; state 2 (wa 005b2c, 005930) requires min cell >= 2500 mV and has no charge logic |
| B0 bit 2 (0x04) | Echo of output pin LATA3 (0x105 byte0 bit 6) | RAM 0x17fc bit2 | CONFIRMED wa 0060e6-0060f8 |
| B1 bit 0 (0x01) | Cell balancing active | RAM 0x1cb9 != 0 | CONFIRMED wa 00a4ee-00a4f8 |
| B1 bit 2 (0x04) | Echo of output pin LATD3 (0x105 byte0 bit 2) | RAM 0x17fc bit3 | CONFIRMED wa 0060ca-0060d8, 00a4fa-00a508 |
| B1 bit 5 (0x20) | Echo of output pin LATA4 (0x105 byte0 bit 4) | RAM 0x17fc bit1 | CONFIRMED wa 0060ca, 00a50a-00a518 |
| B2 bit 0 (0x01) | Digital input PORTB.13 is low (debounced) | RAM 0x1676 bit7 | CONFIRMED source (wa 0056a8); physical meaning unknown |
| B2 bit 1 (0x02) | Digital input PORTB.14 is low (debounced) | RAM 0x1676 bit4 | CONFIRMED source (wa 0056a8, 00a52a-00a53e); meaning unknown |
| B2 bits 2..5 | Sub-state 0x1679 shifted left by 2, **only while state 4**: value 1 -> bit 2 (state entered), 2 -> bit 3 (closing sequence running / contactors closed), 8 -> bit 5 (blocked, a fault word is non-zero). Value 4 (bit 4) is never written | RAM 0x1679 | CONFIRMED wa 00a540-00a554, writers wa 00594c, 0057ec, 005bc0 |
| B3 | Pack-model code | table[0xf4] low byte: models 0..5 -> 1, 6, 7, 8, 9, 10 | CONFIRMED wa 00a55a-00a562; meaning of the code INFERRED (it follows the table index chosen by 0x1bf4) |
| B4 | 0 | | CONFIRMED wa 00a564 |
| B5 | State of health | RAM 0x1cc0 / 5, i.e. **0.5 % per count** (200 = 100 %), integer division | CONFIRMED arithmetic (wa 00a566-00a570, multiply 0xCCCD then >>18). Meaning INFERRED: starts at 1000 (wa 0086bc) and is lowered by wear tables driven by discharged Ah (wa 006744) |
| B6 | State of charge | RAM 0x1bf6 / 5, **0.5 % per count** (200 = 100 %) | CONFIRMED arithmetic; 0x1bf6 is 0..1009 in 0.1 % (clamp 0x3f1 wa 00f474; wa 00add0 divides by 10 to get percent) |
| B7 | Charge current limit by temperature | whole amps (interpolated table / 10); 0 when not in state 4, out of the -11..60 degC window, or charging is not allowed (e.g. max cell above target + 10 mV) | RAM 0x1778 from FUN_rom_00ad9e | Format CONFIRMED (wa 00a57e). Meaning INFERRED (table 0x8e58: 0, 72, 72, 144, 360, 360, 0 at -11, -10, 0, 5, 10, 50, 60 degC, divided by 10 = 36 A max = 0.5 C of 73 Ah) |

### 2.4 ID 0x114, every 100 ms (wa 00a45e-00a4a6)

| Bytes | Field | Encoding | Source | Tag and evidence |
|---|---|---|---|---|
| B0 | Hottest temperature sensor | byte = degC + 40 (so 0x41 = 25 degC, 0 = no valid sensor) | RAM 0x1990 | CONFIRMED copy. Offset INFERRED: wa 00ae02-00ae1e subtracts 0x28 and clamps to -40..+70; table[0] = 110 is the over-temperature limit (= 70 degC) |
| B1 | Coolest temperature sensor | same | RAM 0x198f | same |
| B2-B3 | Maximum cell voltage | u16 BE, mV | RAM 0x1a38 | CONFIRMED (thresholds 4250 etc. are compared directly, wa 00e6ea) |
| B4-B5 | Minimum cell voltage | u16 BE, mV | RAM 0x1a36 | CONFIRMED |
| B6-B7 | 0 | | | CONFIRMED wa 00a496-00a498 |

### 2.5 ID 0x120, every 100 ms, plus once before sleep (wa 00a410-00a450; sleep copy wa 005cf0)

Eight raw status bytes, B0..B7 = RAM 0x1623..0x162a. Any bit set also makes 0x112 B0 bit1 = 1. A **fault** is a bit that forces the BMS to idle (state 1, contactors opened): the main loop treats the pack as faulted when 0x1623 != 0, (0x1624 & 0xAF) != 0, 0x1625 != 0, (0x1626 & 0xAF) != 0, 0x1627 != 0, (0x1628 & 0xDF) != 0, 0x1629 or 0x162a != 0 (wa 006570-0065c0, 006630-006678). Bits 0x1624.4, 0x1624.6, 0x1626.4, 0x1626.6 and 0x1628.5 are warnings that do not force idle. All bytes are cleared by the clear-faults command (0x105 B1 bit1, FUN_rom_0000609c) except 0x1628 bits 1 and 7 (mask 0x82 kept); bits re-appear at once if the condition persists. CONFIRMED structure; the bit meanings below are CONFIRMED for where they are set and INFERRED for the physical wording.

| Byte | Bit | Set when | Tag, evidence |
|---|---|---|---|
| B0 (0x1623) | 1 (0x02) | cell imbalance: max cell >= balancing floor 0x1cb4 (3800 mV) and (max - min) >= limit for 62 evaluations; limit = table[0x80] in charge path (500 mV model 0), table[0x84] in the discharge path (5000 mV model 0, 250 mV models 3-5). Also sets fault bit 0x40 and opens contactors | wa 00e6ea (00ea1c); INFERRED wording |
| B0 | 3 (0x08) | never set in this image (only cleared by the balancing code, wa 0063b8, 0109d4, 010a2a, 010a48). Balancing is reported by 0x113 B1 bit0 instead | CONFIRMED by search |
| B1 (0x1624) | 1 (0x02) | current-sensor zero offset out of range at start-up (raw offset outside +-199 counts) | wa 00c860 (00c862-00c8aa) |
| B1 | 2 (0x04) | 0x105 watchdog expired while in state 2 or 4 (see section 3) | wa 0086fa (008906-00892a) |
| B1 | 3 (0x08) | cell over-voltage trip: max cell >= table[0x44] (4250 mV model 0) for 301 evaluations; opens contactors | wa 00e6ea (store at 00e814), 00e538 |
| B1 | 4 (0x10) | cell over-voltage warning: max cell >= table[0x4c] (4200 mV) for 301 evaluations; clears when below table[0x50] (4190 mV) | wa 00e6ea (store at 00e87c); non-blocking |
| B1 | 5 (0x20) | cell under-voltage trip: min cell below table[0x6c] (2500 mV, 2750 for model 1) for 300 evaluations in the discharge path, or below table[0x70] (2250 mV) while discharging, or set just before sleep when min cell <= table[0x74] (2000 mV) | wa 00e6ea, 005c70 |
| B1 | 6 (0x40) | cell under-voltage warning: min cell below table[0x64] (3000 mV) for 300 evaluations; cleared when above table[0x68] (3050 mV). Also set by wa 00e560 | wa 00e6ea; non-blocking, cleared every pass by wa 00654c |
| B1 | 0, 7 | never set | |
| B2 (0x1625) | 3 (0x08) | PORTB.12 input went inactive (high, debounced): contactor closing is refused and the BMS may go to sleep (event code 0x16) | wa 0056a8 (005762), 004e94 (0x1625&8 test) |
| B3 (0x1626) | 1 (0x02) | contactor sequence: the two high-voltage sense channels (0x17ec, 0x17f0) disagree by more than 2000 mV (5000 mV in step 6) | wa 004e94, 005218 |
| B3 | 2 (0x04) | contactor sequence: sense voltage does not match the cell sum within 2000/5000 mV, or voltage still above 2000 mV after everything was opened (0x17ec) | wa 004e94, 005218 |
| B3 | 7 (0x80) | over-current protection trip (several current thresholds table[0xb4], [0xc0], [0xc4], [0xc8] compared with filtered current for 10 or 200 evaluations); also sets fault bit 0x04 and event counters 0x1000/0x1002 | wa 00c7fc, 00c82e, 00cb6e, 00d0f2-00d2a2; INFERRED wording |
| B4 (0x1627) | 0 (0x01) | precharge failed: bus not reaching enough of the pack voltage within 100 steps (ratio test 0x1d = 29 %) | wa 004e94 (stores at 005004, 005026) |
| B4 | 1 (0x02) | cell-monitor chip communication failure (5 consecutive failed bursts) | wa 00fafc (store at 00fb32) |
| B4 | 6 (0x40) | under-temperature (coolest sensor byte < table[0x1c] = 20, i.e. below -20 degC) while trying to enter charge or discharge, or fewer than 12 valid temperature sensors | wa 00594c, 005b2c, 005c06, 005c4e, 00d5d0 |
| B4 | 7 (0x80) | over-temperature (hottest sensor byte >= table[0] = 110, i.e. >= 70 degC; 105 for model 1 = 65 degC) | wa 00594c, 005b2c, 005be0, 005c2c |
| B5 (0x1628) | 1 (0x02) | invalid pack-model index (0x1bf4 > 5) | wa 010cca |
| B5 | 5 (0x20) | secondary temperature-sensor condition (RAM 0x1992 >= 20 for 200 evaluations); non-blocking; meaning unknown | wa 00d7a2 |
| B5 | 6 (0x40) | counter 0x18e0 > 9; opens contactors; meaning unknown | wa 00d7a2 |
| B5 | 7 (0x80) | non-volatile parameter block invalid / error latched (also mirrored in fault word 0x1686 bit 15) | wa 00795e, 010bc4 |
| B6, B7 (0x1629, 0x162a) | all | never set by any writer found; always 0 | CONFIRMED by search |

Evaluation interval: the counters in wa 00e6ea advance once per run of FUN_rom_0000e5a4, which is called from FUN_rom_000062ec in the main loop; about once per 10 ms main-loop pass is expected, so 300 evaluations is roughly 3 s. INFERRED.

### 2.6 ID 0x510, every 1000 ms (gated) (wa 00a6ba-00a732)

| Bytes | Field | Encoding | Source | Tag |
|---|---|---|---|---|
| B0-B3 | Stored identity number 1 | u32 BE | RAM 0x165a:1658 (loaded from non-volatile block at boot, wa 00795e (008388); written by CAN command 0xC6 data[4..7], wa 008ee6-008f1c) | CONFIRMED copy; role "serial / production number" INFERRED (command is only accepted while discharged Ah < 5 x capacity, wa 008e60-008e86) |
| B4-B7 | Stored identity number 2 | u32 BE, top byte always 0 (24-bit value) | RAM 0x165e:165c (CAN command 0xC6 data[1..3]) | same |

### 2.7 ID 0x512, every 1000 ms (gated) (wa 00a74c-00a7ec)

Four u16 BE values, each = a 32-bit entry of the active parameter table divided by 10 (signed divide, FUN_rom_00000606). Unit = **10 mV per count**.

| Bytes | Table entry | Model 0 / 1 / 2 / 3-5 values | Meaning | Tag |
|---|---|---|---|---|
| B0-B1 | [0x6c] / 10 | 250 / 275 / 250 / 250 (2.50 V, 2.75 V) | low cell-voltage limit (under-voltage trip level) | CONFIRMED copy; meaning from wa 00e6ea use of [0x6c] |
| B2-B3 | [0x44] / 10 | 425 / 423 / 425 / 424 | high cell-voltage limit (over-voltage trip) | CONFIRMED |
| B4-B5 | [0x8c] / 10 | 3500 / 3850 / 3500 / 3500 | low pack-voltage limit (35.00 V) | CONFIRMED copy; meaning INFERRED (range 35 V ... 58.8 V = 14 x 2.5 V ... 14 x 4.2 V) |
| B6-B7 | [0x90] / 10 | 5880 (all models) | high pack-voltage limit (58.80 V) | same |

### 2.8 ID 0x513, every 1000 ms (gated) (wa 00a806-00a8a4)

| Byte | Field | Encoding | Source | Tag |
|---|---|---|---|---|
| B0 | Pack capacity in whole Ah | (RAM 0x1bfe / 100) truncated to 8 bits | 0x1bfe default table[0xd8] = 7300 / 7500 / 9800 / 13400 / 7200 / 7600 (0.01 Ah) -> 73, 75, 98, 134, 72, 76 | CONFIRMED arithmetic; Ah unit INFERRED from the SOC arithmetic (wa 00f3ac, 00f3ee: one 0.1 % SOC step = 0x1bfe x 36 mA-seconds) |
| B1-B2 | Current rating A | u16 BE, 0.1 A (table[0xc8] / 100) | 150000, 150000, 200000, 221000, 150000, 150000 mA -> 1500, 1500, 2000, 2210, 1500, 1500 | CONFIRMED copy. Meaning INFERRED: table[0xc8] is one of the discharge over-current thresholds (wa 00d262, compared with a filtered current) |
| B3-B4 | Current rating B | u16 BE, 0.1 A (table[0xb4] / 100) | 240000, 240000, 320000, 403000, 240000, 240000 mA -> 2400, 2400, 3200, 4030, 2400, 2400 | same (wa 00d0f2) |
| B5 | constant 5 | | | CONFIRMED value; meaning unknown |
| B6 | Low temperature limit byte | table[0x1c] = 20 -> degC + 40 encoding gives -20 degC | all models | CONFIRMED value, wording INFERRED |
| B7 | High temperature limit byte | table[0] = 110 (105 for model 1) -> +70 degC (+65 degC) | | same |

### 2.9 ID 0x517 and 0x51a, every 1000 ms, no gate (resettable "trip" counters)

0x517 (wa 00a228-00a280, four u16 BE, **Ah**, counters since the last reset):

| Bytes | RAM | Meaning | Tag |
|---|---|---|---|
| B0-B1 | 0x171c | Ah delivered (discharge), since reset | CONFIRMED copy (writer wa 006b06 branch for current < 0); unit INFERRED (see section 2.10) |
| B2-B3 | 0x1718 | net Ah = charger + regen - discharge, since reset (16-bit, can wrap negative) | CONFIRMED formula wa 006b06 (006dea) |
| B4-B5 | 0x1716 | Ah of positive current while NOT in charge state 4 (regeneration), since reset | CONFIRMED formula; naming INFERRED |
| B6-B7 | 0x171a | Ah of positive current while in charge state 4 (charger), since reset | same |

0x51a (wa 00a3aa-00a3fe, four u16 BE, **Wh**, since reset):

| Bytes | RAM | Meaning |
|---|---|---|
| B0-B1 | 0x1714 | Wh delivered (discharge) |
| B2-B3 | 0x1710 | net Wh (= 0x1712 + 0x170e - 0x1714) |
| B4-B5 | 0x170e | Wh regeneration |
| B6-B7 | 0x1712 | Wh from the charger |

Counters are zeroed by FUN_rom_00006df2, called from the state-4 entry routine wa 00594c when the stored current minimum (0x171e) is not below -5 A (condition at wa 005990-0059b0); this reset is INFERRED to start a new "trip". The counters are 16-bit and wrap at 65535.

### 2.10 Lifetime counters: 0x518, 0x519, 0x51b, 0x51c (1000 ms, no gate)

Each carries two big-endian 32-bit values. They are loaded from the non-volatile block at boot (wa 00795e) and updated 100 times a second by wa 006b06; INFERRED to be written back to non-volatile storage (the log/NV writer 006efc copies them into the 0x1500 block).

How the counters work (CONFIRMED arithmetic, wa 006b06): every main-loop pass the current |I| (mA, minus 125 mA coil correction if an output stage is on) is converted to `q = trunc((|I| + 50) / 100)` (units of 0.1 A). Charge counters: every 3600 q add 1 to a sub-counter, every 1000 sub-counts add 1 to the Ah counter, so 1 Ah = 3.6 million q; with q = 10 x amps and 100 passes per second that is exactly 3600 A-seconds = 1 Ah (assumes one pass per 10 ms). Energy: `q x pack_mV(0x1ad6) / 1000` added to a counter that rolls over at 3 600 000 (wa 006c06 compares against 0x36EE80 split as 0x36:0xEE80), = 1 Wh per roll-over under the same assumption. Which counter gets the sample: positive current in state 4 -> "charger" counters; positive current in any other state -> "regeneration" counters; negative current -> "discharge" counters.

| ID | Bytes | RAM (hi:lo word) | Meaning | Unit |
|---|---|---|---|---|
| 0x51b (wa 00a1a0-00a21a) | B0-B3 | 0x1704:1702 | net Ah = (0x1706 + 0x16fe - 0x170a) low 16 bits, sign-extended (0x1704 = bit15 replicated, wa 006da6) | Ah, signed 32-bit |
| 0x51b | B4-B7 | 0x1700:16fe | lifetime regeneration Ah (positive current outside state 4) | Ah |
| 0x51c (wa 00a11a-00a192) | B0-B3 | 0x1708:1706 | lifetime charger Ah (positive current in state 4) | Ah |
| 0x51c | B4-B7 | 0x170c:170a | lifetime discharge Ah (also drives the SOH wear tables and gates the identity command 0xC6) | Ah |
| 0x518 (wa 00a28e-00a318) | B0-B3 | 0x16ec:16ea | net Wh = 0x16e6 + 0x16ee - 0x16f2 (32-bit, signed) | Wh |
| 0x518 | B4-B7 | 0x16e8:16e6 | lifetime regeneration Wh | Wh |
| 0x519 (wa 00a326-00a394) | B0-B3 | 0x16f0:16ee | lifetime charger Wh | Wh |
| 0x519 | B4-B7 | 0x16f4:16f2 | lifetime discharge Wh | Wh |

Tags: byte layout CONFIRMED; the words "charger" (state 4) and "regeneration" (not state 4) are INFERRED from the state numbering (state 4 = charge path, see 0x113 B0 bit0); units INFERRED from the 3.6 x 10^6 constant.

### 2.11 ID 0x51d, every 1000 ms (gated) (wa 00a09e-00a10c)

| Bytes | Value | Source | Tag |
|---|---|---|---|
| B0, B1, B2 | constants 0x02, 0x09, 0x07 | immediates (stored through RAM 0x1808) | CONFIRMED values; INFERRED to be a firmware version 2.9.7 |
| B3 | board address strap value: 0x28..0x2E for strap value 1..7, 0 for 0 | RAM 0x1660 = 0x27 + v, v = 7 - (3-bit read of PORTC.9 << 2 | PORTD.13 << 1 | PORTD.7) | CONFIRMED wa 006e6c; meaning INFERRED (position on a shared bus) |
| B4-B5 | per-cell charge target voltage, u16 BE, mV | RAM 0x1c80, default table[0x54] = 4150 (models 0-2) or 4200 (models 3-5); the charge routine may swap it for table[0xf8] = 3900 | CONFIRMED copy (wa 00a0e4-00a0f0, 010d10-010d18, 0057ec); meaning INFERRED |
| B6-B7 | model constant, u16 BE | table[0xd4] low word = 2400, 1500, 3200, 8008, 4290, 4576 for models 0..5 | CONFIRMED copy; meaning unknown |

## 3. Receive handlers

Dispatch (CONFIRMED, wa 009284): the CAN11 receive FIFO is read by FUN_rom_00e106 into RAM 0x1792 (ID) and data bytes D0..D7 = 0x1799..0x17a0. The three hardware filters have zero masks, so every standard frame reaches software; ID == 0x105 calls FUN_rom_009196, ID == 0x515 calls FUN_rom_0091f0, an ID with (id & 0x7f) == 0x14 and (id & 0x780) == 0x600 (0x614 and other function codes for node 0x14) is the service channel (wa 00a99a). Everything else is dropped. **Neither handler tests the DLC**, so a short frame leaves the missing bytes holding stale data from earlier frames. A receive-buffer overflow flag (C1INTF bit 2) causes a CAN11 re-initialisation (wa 00e106 -> wa 00aac4).

### 3.1 ID 0x105: host command (the "keep-alive and mode request"), handler wa 009196

Send it to the pack **at least every 1 s** (every 100 ms is a safe rate). Layout:

| Byte | Bits | Effect | Tag and evidence |
|---|---|---|---|
| D0 (copied to RAM 0x161b) | bits 0-1 | Mode select. 0 = discharge path (state 2); non-zero (any of the two bits) = charge path (state 4). Also selects which current/limit table entries are used in the fault logic (wa 00e6ea, 00dbe8, 00e9e2, 00eb3c) and allows balancing only when non-zero | CONFIRMED use (wa 008988-0089a4 main-loop dispatch, 0060c8, 006398); "charge" for non-zero INFERRED from the charge logic in state 4 |
| | bit 2 (0x04) | Drives output pin LATD3 high (via wa 004db6), echo in 0x113 B1 bit2 | CONFIRMED wa 0060e6-006104 |
| | bit 4 (0x10) | Drives LATA4 high, echo in 0x113 B1 bit5 | CONFIRMED wa 0060ca-0060e4 |
| | bit 6 (0x40) | Drives LATA3 high, echo in 0x113 B0 bit2 | CONFIRMED wa 0060ca-0060fc |
| | bits 3, 5, 7 | stored, never read | CONFIRMED by search of 0x161b readers |
| D1 (copied to RAM 0x161c) | bit 0 (0x01) | Enables the gated frames 0x510, 0x512, 0x513, 0x51d | CONFIRMED wa 00a09a, 00a6b0, 00a740, 00a7fa |
| | bit 1 (0x02) | Clear-faults request: runs FUN_rom_0000609c (zero all fault bytes except 0x1628 bits 1,7, and fault words) at most once per 100 ms (stamp RAM 0x16b8); a request made inside that 100 ms is discarded | CONFIRMED wa 0091cc-0091ea, 008930-00894c |
| | bit 2 (0x04) | Extended watchdog: timeout 120 000 main-loop steps instead of 100 (see below) | CONFIRMED wa 0091ac-0091c6 |
| | bit 4 (0x10) | **Enable / run.** Clear = the main loop forces idle (state 1, contactors open) every pass (wa 008988-008990 -> 005bc0). Set = the BMS may enter the charge or discharge path | CONFIRMED |
| | bits 3, 5, 6, 7 | stored, never read | CONFIRMED |
| D2 | - | not used | CONFIRMED |
| D3 (copied to RAM 0x161d) | - | stored, never read | CONFIRMED (only wa 0091a8 touches 0x161d) |
| D4-D7 | - | not used | CONFIRMED |

**Watchdog.** Every 0x105 frame zeroes the 32-bit step counter 0x166a:166c and sets the threshold T (0x1c7c:1c7e) to 100 steps, or to 0x1D4C0 = 120 000 steps if D1 bit2 is set (wa 009196-0091c6). The main loop adds 1 to the counter once per slow pass (every >= 10 ms, wa 0086fa 0088c8-0088f2), so a step is about 10 ms:
* When the counter exceeds T (default about 1.0 s): every pass, T is reset to 100 and RAM 0x161b is ANDed with 0xAB, which clears D0 bits 2, 4, 6 (the three output pins fall); in state 2 the BMS sets fault word 0x167e |= 0x100 and 0x1624 bit2, in state 4 it sets 0x167c |= 0x100 and 0x1624 bit2, and in both cases calls FUN_rom_00005218 (open the contactors). The mode bits D0 bits 0-1 and the enable D1 bit4 are **not** cleared, but the non-zero fault word keeps the BMS idle (wa 00654c / 006616 test 0x1686 != 0) until the fault is cleared. CONFIRMED code path; "stays idle until cleared" INFERRED (no other clear of 0x167e bit8 found; 0x167c bit8 is cleared in wa 006616).
* If the counter exceeds T + 30000 (about 301 s with T = 100, about 25 minutes with D1 bit2 set): event code 0x15 and the BMS goes to sleep (FUN_rom_00006142, reason 0x16bc = 0x15). CONFIRMED wa 0088c8-0088e2.
* A 0x614 service frame sets T = 10000 (wa 009284, the 0x614 branch) but does not reset the counter; CAN traffic does not reset it.
* After boot the counter starts at 0, T = 100: with no 0x105 at all the pack goes to sleep after about 5 minutes. INFERRED from the same code.

### 3.2 ID 0x515: sleep command, handler wa 0091f0

| Byte | Effect | Tag |
|---|---|---|
| D0 | Copied to RAM 0x1610. If bits 5:4 (mask 0x30) are non-zero, the main loop starts a 500 ms hold (flag 0x1616 = 1, stamp 0x1618, value 0x161a, 0x1610 cleared). After 500 ms: 0x176c = (value & 0x30) >> 4, reason code 0x16bc = 3 if 0x176c == 2 else 2, and FUN_rom_00006142 is called, which runs the sleep routine FUN_rom_00005c70 (one last 0x120 frame, monitor chip and supplies powered down, `pwrsavOp`). Value 2 = full shut-down (monitor chip and outputs off); values 1 and 3 (with SOC > 4.9 %) take a lighter branch that keeps the monitor chip alive for wake-up. Waking re-runs the full initialisation (wa 005ef6) | CONFIRMED path (wa 00886a-0088bc, 005c70, 006142); the light/deep difference is INFERRED from wa 005dae-005dba (`1bf6 > 0x31 && 176c < 2`) |
| D1-D4 | Assembled into a 32-bit big-endian number at 0x1612:1614 (D1 most significant) and multiplied by 100 into 0x161e:1620; nothing reads either | CONFIRMED wa 0091fa-00927e; payload unused |
| D5-D7 | not used | CONFIRMED |

There is no timeout for 0x515; it is a one-shot command. Because the hold re-reads RAM 0x1610 only when it is non-zero, a sleep request needs one 0x515 frame with bits 5:4 set; the BMS acts 500 ms later.

## 4. Minimal driver recipe (INFERRED from sections 2-3)

1. Listen on CAN11 at 500 kbit/s (assumed). Frames 0x111-0x114, 0x120, 0x517-0x51c arrive without any request.
2. To make the pack connect, send 0x105 every 100 ms: D0 = 0x00 (discharge/drive) or 0x01 (charge), D1 = 0x11 (enable plus extra frames) or 0x10. Keep D0 bits 2, 4, 6 at 0 unless you want the three aux outputs.
3. To clear a latched fault, send one 0x105 with D1 bit1 set (D1 = 0x12 or 0x13), then drop the bit.
4. To power the pack down, stop sending 0x105 (outputs drop in about 1 s) or send 0x515 with D0 = 0x20.
5. The contactor state is visible in 0x112 B0 bit5 (series relay) and B1 bit5 (precharge relay).

## 5. Open questions

1. Direction of the two limits in 0x111 (B3-B4 = discharge, B5-B6 = charge) is inferred from table magnitudes (75/240/150 A versus 37/112/74 A), because neither variable is used by any other code; a bench test (change SOC or temperature and watch which limit moves) would settle it.
2. Unit of 0x13c0 (mA) rests on the 6 mA per count arithmetic and the table thresholds; a bench current reading would confirm. The sign convention (positive = charge) rests on the charge-efficiency branch in the main loop (wa 0087fc) and the over-current warning at +37.5 A.
3. The charge/discharge naming of states 4/2 (opposite of glossary_cmd.md) rests on the logic present in each state. A bench test will confirm.
4. What 0x105 D0 bits 2, 4, 6 drive physically (aux outputs on the external connector?) and what PORTB.11/12/13/14 are wired to (PORTB.12 is required for contactor closing).
5. 0x51d B0-B2 as a version, B6-B7 (table[0xd4]) and 0x513 B5 (constant 5) meanings.
6. Which temperature sensor feeds the 0x113 B7 limit (RAM 0x19e8) and what table 0x1cd8 axis units are (degC assumed from the -11..60 axis).
7. 0x120 bits B3.1/B3.2, B4.0, B5.5, B5.6 are described from the code that sets them, not from known fault names.
8. Whether the 0x105 timeout fault latches in charge state 4 (fault word 0x167c bit8 is cleared in the discharge-path function only).
9. Scale of the two high-voltage ADC channels (0x17ec, 0x17f0): float constants in wa 005510 were not decoded; mV is assumed from the comparison with the cell sum.
10. Real transmit periods have not been measured; the code guarantees "not earlier than the nominal period".
11. Hardware clock: a 16 MHz crystal is assumed, so 1 tick = 1.000 ms is exact only if that holds.
12. Exact rules for resetting the trip (16-bit) counters (wa 005990-0059b0 condition).
