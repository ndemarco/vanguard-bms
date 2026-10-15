# RAM variables established by the "can1" job (CAN11 transmit and receive)

Tag: C = CONFIRMED (read in code), I = INFERRED. "wa" = program word address of evidence. Multiword values are little-endian
word pairs: the LOW word sits at the lower address (e.g. 0x1706 = low word, 0x1708 = high word).

| Addr | Meaning | Units / scale | Evidence | Tag |
|---|---|---|---|---|
| 0x1664 | Free-running millisecond tick, 16-bit, wraps every 65.536 s. Incremented by Timer1 interrupt (PR1=3750, prescaler 1:8, Fcy 30 MHz = 1.000 ms) | ms | wa 010e86 (inc), 010e44 (T1CON=0x8010, PR1=0xEA6, IEC0.T1IE), IVT word 0x1a -> 0x388 | C (Fcy assumes 16 MHz crystal, PLL wa 00fecc) |
| 0x16ae, 0x16b0 | Main-loop timers: 0x16ae = last slow-pass tick (slow section runs when tick-0x16ae >= 10); 0x16b0 = last transmit-scheduler call (runs when tick-0x16b0 >= 2) | ms | wa 0086fa main loop (008784, 009fe2 caller) | C |
| 0x1782..0x1790 | CAN11 TX scratch frame: ID 1782, DLC 1788, data[0..7] = 1789..1790 | - | wa 009fd0 | C |
| 0x1874,187e,187c,187a,1878,185e,1862,1864,186a,186c,186e,1870,1880,1882,1884 | Last-sent tick of frames 0x111,0x120,0x114,0x113,0x112,0x510,0x512,0x513,0x517,0x518,0x519,0x51a,0x51b,0x51c,0x51d | ms | wa 009fe4.., 00a3aa.. | C |
| 0x13c0:13c2 | Pack current, IEEE float, mA, positive = into the pack (charging / regeneration), negative = discharge. = (0x13dc as float) * 6.0 * float(0x1c70) | mA | wa 00cc00-00cc1c (6.0 = 0x40c00000), main loop 0087fc-008846 (positive branch uses charge efficiency table[0xec]), coil offset 125 mA wa 006b16 | C (mA unit I: 6 mA/LSB fits 0.6 uV LSB of the monitor chip with a 100 uOhm shunt; 125 mA contactor-coil correction; Ah/Wh counter arithmetic) |
| 0x13d8:13da | Latest current measurement from the monitor chip, signed 32-bit, average of 4 samples | raw counts | wa 00c9a6-00c9b8 | C |
| 0x13d4:13d6 | Current zero-offset captured at start-up when raw value is within +-199 (else 0, sets fault 1624 bit1 / 167c bit3) | raw counts | wa 00c860 | C |
| 0x13dc:13de | Offset-corrected current counts = 0x13d8 - 0x13d4 (in state 1 idle: 0x13d8 forced to 0 when |value| <= 349) | raw counts | wa 00cb6e (00cb78-00cbfc) | C |
| 0x1c6e | Current gain calibration word (default 10000). 0x1c70 = 10000.0/0x1c6e | 1/10000 | wa 007aaa-007ac6 | C |
| 0x171e:1720, 0x1722:1724 | Minimum and maximum of 0x13c0 seen since last reset, stored as signed 32-bit integers | mA | wa 00cc4a-00cc9a | C |
| 0x1bb4:1bb6 | Float: discharge current limit (SOC/temperature based, from 11x11 tables 0x1cd2/0x1cd4/0x1cd6). Sent in 0x111 bytes 3-4 as trunc(x*10) | A | wa 00b20c-00b21c, 00add0 | C (discharge I) |
| 0x1bb8:1bba | 32-bit form of the same limit | mA | wa 00add0 (00af14-00b20c) | C |
| 0x1bd2:1bd4 | Float: charge current limit (max-cell-voltage, SOC, temperature tables 0x1ccc/0x1cce/0x1cd0). Sent in 0x111 bytes 5-6 as trunc(x*10) | A | wa 00b6f0-00b700, 00b22a | C (charge I) |
| 0x1bd6:1bd8 | 32-bit form of that limit | mA | wa 00b22a | C |
| 0x17cc | Rolling frame counter, +1 after every 0x111 frame, cleared at start-up. 0x111 byte 7 | count | wa 00a06c-00a074, 005f30, 008afc | C |
| 0x1bf6 | State of charge. 0..1009 (clamped at 0x3f1), init from open-circuit-voltage table of the minimum cell. 0x113 byte 6 = this / 5 | 0.1 % | wa 00f3ee, 00f474 (clamp 0x3f1), 00add0 (x/10 -> percent 0..100) | C |
| 0x1bf8 | Companion SOC computed from the maximum cell (same scale) | 0.1 % | wa 00f3ee | C (role I) |
| 0x1bfe:1c00 | Pack capacity, default table[0xd8] (7300, 7500, 9800, 13400, 7200, 7600 for models 0..5). 0x1c02 = 0x1bfe*36000; one SOC step (0.1 %) = 0x1c02/1000 mA*s = capacity(Ah)*3.6 As. 0x513 byte 0 = 0x1bfe/100. NOTE: sections/glossary_cmd.md calls this "rated lifetime throughput"; the SOC arithmetic says rated capacity | 0.01 Ah | wa 00f3ac, 00f3ee, 00f474, 00a81a | C (unit I) |
| 0x1cc0 | State of health, init 1000, then bounded by wear tables from discharged Ah (0x170a). 0x113 byte 5 = this / 5 | 0.1 % | wa 0086bc (1000), 006744, 0067e0 | C (SOH name I) |
| 0x1ad6:1ad8 | Sum of the 14 cell voltages (pack voltage from cells). 0x1a3a = average cell (sum/14) | mV | wa 00e5c0-00e674 | C |
| 0x17ec:17ee | Pack-terminal voltage by ADC (path "mode 2"), 12-bit ADC, trimmed mean of 6 samples, scaled; compared against 0x1ad6 during contactor closing (must agree within 2000 mV) and must be <2000 mV with contactors open | mV | wa 005534 (store 0055c2), 004e94, 005218 | C (scale I) |
| 0x17f0:17f2 | Second high-voltage ADC channel ("mode 1"), same filtering. Compared with 0x17ec (2000/5000 mV) in 004e94 | mV | wa 005534 (store 005650) | C (role I) |
| 0x1a36 / 0x1a38 | Minimum / maximum cell voltage | mV | wa 00a45e block, notes | C |
| 0x1a3c | Max cell - min cell | mV | notes | C |
| 0x1990 / 0x198f | Hottest / coolest temperature sensor, encoded byte = degC + 40 (0 = invalid sensor). 0x114 bytes 0,1 | degC + 40 | wa 00ae02-00ae06 (sub 0x28, clamp -40..70), 00594c (!=0 test), table[0]=110 -> 70 degC | C |
| 0x1660 | Board address from three strap pins (PORTC bit9, PORTD bit13, PORTD bit7): n = 3-bit read, v = 7-n; v=1..7 -> 0x28..0x2E, v=0 stays 0. 0x51d byte 3 | enum | wa 006e6c | C |
| 0x1c80 | Per-cell charge target voltage, default table[0x54] (4150 / 4200), set to table[0xf8]=3900 or [0x54] by the charge routine. 0x51d bytes 4-5 | mV | wa 010d10-010d18, 005822, 005866 | C |
| 0x1778 | Charge current limit by temperature in whole amps (interpolated from table 0x1cd8 at temperature 0x19e8-40, divided by 10); 0 outside -11..60 degC or when charge conditions fail; computed only while state 4. 0x113 byte 7 | A | wa 00ad9e, 0057ec (00589e..0058e0) | C (name I) |
| 0x19e8:19ea | Float temperature used for the charge limit (degC + 40 encoded, converted to byte by FUN_00066e) | degC + 40 | wa 00ada0-00ada8 | C (which sensor I) |
| 0x1678 | State byte. 1 idle (contactors open), 2 discharge path (entered by 005b2c when min cell >= table[0x6c] 2500 mV), 4 charge path (entered by 00594c; uses charge target 0x1c80 and charge limit 0x1778), 8 CAN service mode, 0x10 start-up. Refines glossary_cmd.md, which has 2/4 swapped | enum | wa 005b2c, 00594c, 0057ec, 005930, 006616, 00654c, 1bf0 written 005934/0057f0 | C (charge/discharge naming from the voltage/current-limit logic) |
| 0x1679 | Sub-state: 0 none, 1 entered, 2 contactor-closing sequence running/closed, 8 blocked by fault word. Value 4 is tested but never written | enum | wa 0057ec, 005930, 005bc0, 00594c | C |
| 0x1622 | Contactor closing/opening step counter (1..6, 0xfe = failed, 0xff) used by 004e94 and 005218 | step | wa 004e94, 005218 | C |
| 0x1676 | Debounced input/status bits. bit7 = PORTB.13 low, bit6 = PORTB.11 low, bit5 = PORTB.12 low (required for contactor closing), bit4 = PORTB.14 low, bit2 = over-voltage trip latch (set by 00e6ea at cell-OV trip, cleared when max cell stays <= table[0x58] 4050 mV), bit0 cleared at close step | bitmask | wa 0056a8, 00e6ea (00e994), 0057ec | C |
| 0x1623..0x162a | Status/fault bytes sent in 0x120 bytes 0..7. Bit table in sections/can1.md. Cleared by FUN_0000609c (0x105 byte1 bit1) except 0x1628 bits 1 and 7 | bitmask | wa 00609c, 00e6ea, 004e94, 005218, 00594c, 00c860, 0086fa, 00c7fc | C |
| 0x1680, 0x1682, 0x1684 | Warning words; 0x1684 = 0x1680 | 0x1682. Non-zero sets 0x112 byte 0 bit 2 | wa 008950-008956, 00a5fc | C |
| 0x167c, 0x167e, 0x1686 | Fault words; 0x1686 = 0x167c | 0x167e | 0x1686 (kept bit 15). Non-zero (or any 0x1623.. byte except non-blocking bits) forces state 1 | wa 00605e, 00654c, 006616 | C |
| 0x1c7c:1c7e, 0x166a:166c | 0x105 watchdog threshold and counter, see sections/can1.md section 0x105 | main-loop steps (~10 ms) | wa 009196, 0088bc-0088f2 | C |
| 0x16b8 | Tick of last fault-clear (0x105 byte1 bit1 is honoured at most once per 100 ms) | ms | wa 0091d2-0091e4 | C |
| 0x1610, 0x1616, 0x1618, 0x161a, 0x176c, 0x176d | 0x515 command path: 0x1610 = data[0]; when (0x1610 & 0x30) != 0 start 500 ms hold (0x1616=1, tick in 0x1618, value in 0x161a); on expiry 0x176c = (value & 0x30) >> 4 and sleep entry FUN_00006142 with reason 0x16bc = 3 (if 0x176c==2) else 2 | - | wa 00886a-0088bc | C |
| 0x16bc | Event/sleep reason code: 2, 3 = 0x515 command; 4 = cell under-voltage (min cell <= table[0x74] for >999 counts); 8 = boot; 0x15 = no 0x105 for too long; 0x16 = PORTB.12 went inactive; others in cmd.md | enum | wa 0086fa (008738, 008866, 0088b2, 0088b8, 0088e2), 0056a8 | C |
| 0x16e6:16e8, 0x16ee:16f0, 0x16f2:16f4 | Lifetime energy counters, 32-bit: 0x16e6 = energy IN while NOT in charge state 4 (regeneration), 0x16ee = energy IN during charge state 4 (charger), 0x16f2 = energy OUT (discharge). Loaded from non-volatile block at boot. | Wh | wa 006b06 (006c1c, 006cb6, 006d50), 00795e | C (Wh unit I from 3.6e6 roll-over) |
| 0x16ea:16ec | Net energy = 0x16e6 + 0x16ee - 0x16f2 (signed 32-bit) | Wh | wa 006b06 (006d88) | C |
| 0x16fe:1700, 0x1706:1708, 0x170a:170c | Lifetime charge counters, 32-bit: 0x16fe = Ah IN outside charge state (regeneration), 0x1706 = Ah IN during charge state 4, 0x170a = Ah OUT (discharge) | Ah | wa 006b06 (006c86, 006bec, 006d20) | C (Ah unit I) |
| 0x1702:1704 | Net Ah = low words of (0x1706 + 0x16fe - 0x170a), high word = sign extension | Ah | wa 006b06 (006da6-006da8) | C |
| 0x170e, 0x1712, 0x1714, 0x1710 | Resettable ("since reset") 16-bit energy counters: 0x170e regen IN, 0x1712 charger IN, 0x1714 OUT, 0x1710 net = 0x1712+0x170e-0x1714. Zeroed by FUN_00006df2 | Wh | wa 006b06, 006df2 | C |
| 0x1716, 0x171a, 0x171c, 0x1718 | Resettable 16-bit charge counters: 0x1716 regen IN, 0x171a charger IN, 0x171c OUT, 0x1718 net = 0x171a+0x1716-0x171c | Ah | wa 006b06, 006df2 | C |
| 0x1726:1728, 0x172a:172c, 0x172e | Fractional accumulators for the state-4 branch (charge Ah, charge Wh, Ah sub-count). Others: 0x173a/173e/1742 (regen), 0x1730/1734/1738 (discharge) | internal | wa 006b06 | C |
| 0x1658:165a, 0x165c:165e | Two stored identity numbers (see glossary_cmd.md). 0x510 bytes 0-3 and 4-7 | - | wa 00a6ce-00a724 | C |
| 0x1d00 table offsets used by CAN11 frames | [0x00] max-temp byte limit 110/105; [0x1c] min-temp byte limit 20; [0x44] max cell mV; [0x6c] min cell mV; [0x8c] pack min mV (35000, 38500 for model 1); [0x90] pack max mV 58800; [0xb4] 240000/320000/403000 mA; [0xc8] 150000/200000/221000 mA; [0xd4] 2400,1500,3200,8008,4290,4576 (meaning unknown); [0xd8] capacity; [0xf4] model code 1,6,7,8,9,10 | see text | wa 00a74c-00a89c, 00a0dc, 00a55a | C |
