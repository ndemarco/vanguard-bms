# Glossary: RAM variables established by the CAN TX analysis (job can2tx)

Format: address | meaning | units/encoding | evidence (function word-address) | CONFIRMED/INFERRED.
All multi-byte values little endian. "u16" = unsigned 16 bit.

## Time and scheduling
- 0x1664 | free-running millisecond tick (u16, wraps at 65536) | 1 ms | ISR helper FUN_rom_010e86 increments it; Timer1 PR1=0xEA6 (3750), T1CON=0x8010 (prescale 1:8), CLKDIV=0x3102 / PLLFBD=0x1C gives Fcy = 30 MHz with a 16 MHz crystal, so 3750*8/30e6 = 1.000 ms (timer set-up FUN_rom_010e44; clock set-up writes CLKDIV 0x3102 / PLLFBD 0x1C near all.c line 12380) | CONFIRMED (assuming 16 MHz crystal)
- 0x1810..0x183e, 0x1856, 0x185a | per-frame "last sent" tick stamps for the CAN periodic frames (one word per frame) | ms tick | FUN_rom_0093d6; all set to 0x1664 by init FUN_rom_008af4 | CONFIRMED
- 0x16b0 | tick stamp of last call to the TX scheduler FUN_rom_0093d6 (called from main loop FUN_rom_0086fa when >= 2 ms elapsed) | ms | FUN_rom_0086fa | CONFIRMED
- 0x17d0 | hold-off counter in front of the CAN scheduler; never set non-zero anywhere (only cleared in FUN_rom_008af4) | count | FUN_rom_0093d6, FUN_rom_008af4 | CONFIRMED (dead feature)

## Cell voltages and temperatures
- 0x1a1a..0x1a34 | cell 1..14 voltage, u16 each | mV = trunc((raw16 & 0x7FFF) * 0.15234375); constant 0x3E1C4000 | FUN_rom_00e5a4 | CONFIRMED
- 0x1ad6/0x1ad8 | sum of the 14 cell voltages (32 bit, low word 0x1ad6) | mV (max ~59500, fits in u16) | FUN_rom_00e5a4 | CONFIRMED
- 0x1a36 / 0x1a38 | minimum / maximum cell voltage | mV | FUN_rom_00e5a4 | CONFIRMED
- 0x1a3a | mean cell voltage = sum/14 | mV | FUN_rom_00e5a4 (rom:00e66e divide by 14, store rom:00e674) | CONFIRMED
- 0x1a3c | spread = max - min | mV | FUN_rom_00e5a4 | CONFIRMED
- 0x1a3e / 0x1a3f | 1-based index of the minimum / maximum cell (default 14) | cell number | FUN_rom_00e5a4 | CONFIRMED
- 0x1981..0x198e | 14 temperature bytes in ADC-channel order | byte = degC + 40, 0 = sensor invalid / not read | FUN_rom_00d5d0; offset proven by clamp "byte - 0x28" in FUN_rom_00add0 / FUN_rom_00b22a (range clamped to -40..+70 degC) | CONFIRMED
- 0x1970..0x197d | same 14 temperature bytes re-ordered into sensor-number order for CAN: 1970<-198a, 1971<-1983, 1972<-1988, 1973<-1981, 1974<-1989, 1975<-1982, 1976<-198d, 1977<-1986, 1978<-198b, 1979<-1984, 197a<-198c, 197b<-1985, 197c<-198e, 197d<-1987 | degC+40 | FUN_rom_00d5d0 | CONFIRMED
- 0x198f / 0x1990 / 0x1991 / 0x1992 | min / max / mean (integer mean of valid) temperature byte / max-min | degC+40 (spread in degC) | FUN_rom_00d5d0; zero entries are ignored | CONFIRMED
- 0x1993 / 0x1994 | 0-based index (in 0x1970 order) of coldest / hottest sensor | index | FUN_rom_00d5d0 | CONFIRMED
- 0x19ed | number of valid temperature sensors; fewer than 12 sets warning bit 0x0002 in 0x1686 and 0x1627 bit 6 | count | FUN_rom_00d5d0 | CONFIRMED
- 0x1980 | read by CAN frame 18FF07F3 byte 3; no writer exists in the image, area 0x1800-0x19FF is zeroed at start-up (data-init template at program word 0x14E8C), so always 0 | - | listing rom:009e1c, init template | CONFIRMED

## Pack electrical
- 0x17ec/0x17ee | ADC voltage measurement "2" (32 bit, low word 0x17ec). Cross-checked against the cell sum 0x1ad6 with 2000 mV / 5000 mV tolerances in the contactor sequence, so same unit | mV = 58000.0 * adc_avg / cal(0x1cc2) | FUN_rom_005534 (rom:00555e..0x555c constant 0x47629000 = 58000.0, division by float(0x1cc2)); channel selected by FUN_rom_0052d4(2); role "pack (battery side) voltage" INFERRED from FUN_rom_004e94 | scaling CONFIRMED, role INFERRED
- 0x17f0/0x17f2 | ADC voltage measurement "1" (output / load side of the contactors), same formula, FUN_rom_0052d4(1). Pre-charge completes when 100*0x17f0/0x1ad6 >= 0x1cbe (FUN_rom_004e94 line ~2050) | mV | FUN_rom_005534 | scaling CONFIRMED, role INFERRED
- 0x1cc2 | ADC calibration divisor used by FUN_rom_005534; NO writer found in the image and not covered by the start-up data-init template | counts | only reads at rom:005558 and rom:0055e6 | value UNKNOWN (open question)
- 0x13c0/0x13c2 | pack current as 32-bit float | mA; positive = charge (INFERRED from threshold signs, see 0x13dc) | FUN_rom_00cb6e rom:00cc00..00cc1c: float(0x13dc:0x13de) * 6.0 * float(0x1c70). 6 mA per count matches a 0.4 mohm shunt with a 2.4 uV LSB monitor-chip current ADC | formula CONFIRMED, unit/sign INFERRED
- 0x13dc/0x13de | signed 32-bit current counts = 0x13d8 (averaged ADC counts, FUN_rom_00c992 / FUN_rom_00f762) minus zero offset 0x13d4 | counts (6 mA nominal) | FUN_rom_00cb6e, FUN_rom_00c9ee | CONFIRMED
- 0x13d4 | current-sensor zero offset captured when |0x13d8| is small and contactors are open | counts | FUN_rom_00c860 | CONFIRMED
- 0x1c6e | current-sensor calibration, default 10000, persisted in the EEPROM-image config | 1e-4 | FUN_rom_00795e | CONFIRMED
- 0x1c70/0x1c72 | float gain = 10000.0 / float(0x1c6e) | - | rom:007abc..007ac6 (constant 0x461C4000 = 10000.0) | CONFIRMED
- 0x1026/0x1028 | smoothed current (float, mA) over 10 sample blocks of 9 | mA | FUN_rom_00cb6e | formula CONFIRMED, naming INFERRED
- 0x107e/0x1080 | mean current (float, mA) of the last 9 samples | mA | FUN_rom_00cb6e | INFERRED
- 0x1000 / 0x1002 | event counters incremented each time FUN_rom_00c7fc (0x1000) / FUN_rom_00c82e (0x1002) latches an over-current type fault (0x1000 counter feeds 167e bit 2, 0x1002 feeds 167c bit 2); cb6e compares current against table[0x98], [0x9c], [0xb0], [0xb4], [0xc0] | count | FUN_rom_00c7fc, FUN_rom_00c82e | CONFIRMED
- 0x1bb4/0x1bb6 | float, discharge current limit (A, positive magnitude) | A; CAN encoding A*20+32000 | FUN_rom_00add0 (uses current only when 0x13c0 <= 0, max/min temperature, lookup FUN_rom_00b802) | formula CONFIRMED, role INFERRED
- 0x1bd2/0x1bd4 | float, charge current limit (A) | A | FUN_rom_00b22a (uses current only when 0x13c0 > 0 and clamps the max cell voltage 2000..4250 mV) | INFERRED

## State of charge, capacity, state of health
- 0x1bf6 | state of charge derived from the MINIMUM cell voltage then corrected by coulomb counting | 0.1 % (clamped to 1009); CAN sends 0x1bf6/10 | FUN_rom_00f3ee, FUN_rom_00f474 | INFERRED
- 0x1bf8 | state of charge derived from the MAXIMUM cell voltage (0x1a38) | 0.1 % | FUN_rom_00f3ee | INFERRED
- 0x1bfe/0x1c00 | pack nominal capacity, from EEPROM config 0x13fe or parameter table [0xd8] | 0.01 Ah (table values 7300,7500,9800,13400,7200,7600 for the six variants; cell capacity 3.0-4.8 Ah x parallel count matches) | FUN_rom_00795e | scaling INFERRED
- 0x1c06 | capacity * 10 (full) | - | FUN_rom_00f3ac | CONFIRMED
- 0x1c0a | remaining capacity *10 (capped at 0x1c06); CAN sends 0x1c0a/10 | 0.01 Ah (INFERRED) | FUN_rom_00f3ee, FUN_rom_00f474 | INFERRED
- 0x1c0e | 0x1c06 - 0x1c0a = capacity missing to full *10; CAN sends /10 | 0.01 Ah | FUN_rom_00f3ee | INFERRED
- 0x1cc0 | state of health, default 1000 | 0.1 % (1000 = 100 %) = learned capacity 0x1748 * 1000 / table[0x118], clamped between 0x160e and 0x160c | FUN_rom_0086bc, FUN_rom_006744 | INFERRED
- 0x160c / 0x160e | upper / lower state-of-health bound computed from the cycle counter 0x170a and table entries 0x11c..0x138 | 0.1 % | FUN_rom_006744 | INFERRED
- 0x1748 | learned (measured) capacity | same unit as table[0x118] (unit unknown) | FUN_rom_0067e0 | INFERRED
- 0x174c / 0x174e / 0x1750 | last three learned-capacity deltas (ring buffer indexed by 0x1756 modulo 3) | as 0x1748 | FUN_rom_0067e0 | INFERRED
- 0x1756 | ring index (0..2) of the learned-capacity buffer | - | FUN_rom_0067e0 | CONFIRMED
- 0x1758 | number of completed capacity-learning measurements | count | FUN_rom_0067e0 | INFERRED
- 0x170a | cycle counter loaded from EEPROM config 0x1426, incremented in FUN_rom_006b06 | count | FUN_rom_00795e, FUN_rom_006b06 | INFERRED
- 0x1cfa | full-charge cell voltage threshold, default 0x1036 = 4150 | mV | FUN_rom_00795e (default), FUN_rom_00e6ea (compares 0x1a38 < 0x1cfa), FUN_rom_0067e0 (compares 0x1a38 with 0x1036) | INFERRED
- 0x1cbe | pre-charge completion threshold, default 0x46 = 70 | percent of 0x1ad6 | FUN_rom_004e94 (100*0x17f0/0x1ad6 compared with 0x1cbe) | INFERRED
- 0x1cbc | configuration word, default 3000, persisted at config 0x150c; no consumer found in logic | unknown (3000 suggests ms) | FUN_rom_00795e, FUN_rom_008c22 | UNKNOWN

## Mode, faults, outputs
- 0x1678 | operating mode: 0x10 = start-up/initial, 1 = off (contactors open), 2 = discharge/run, 4 = charge, 8 = service/test (set when any frame 0x0CFF00F3 is received) | enum | FUN_rom_005ef6, FUN_rom_005bc0, FUN_rom_005b2c, FUN_rom_00594c, FUN_rom_009368; charge vs discharge from FUN_rom_00565a (balance parameters set in mode 4) and FUN_rom_00654c / FUN_rom_006616 | values CONFIRMED, charge/discharge names INFERRED
- 0x1679 | sub-state: 0 = idle OK, 1 = connecting (pre-charge), 2 = running, 4 = (set only compared in FUN_rom_0057ec), 8 = fault/open | enum | FUN_rom_005bc0, FUN_rom_00594c, FUN_rom_005930 | INFERRED
- 0x167c / 0x167e | warning words (active / latched) | bit mask | setters listed in sections/can2tx.md section "Fault and warning words" | INFERRED
- 0x1680 / 0x1682 | fault words (active / latched) | bit mask | same | INFERRED
- 0x1684 | = 0x1682 | 0x1680 (fault word sent on CAN) | FUN_rom_0086fa rom around line 4830, FUN_rom_005c70 | CONFIRMED
- 0x1686 | = 0x1686 | 0x167e | 0x167c (warning word sent on CAN) | same | CONFIRMED
- 0x1688 | latched permanent-fault word restored from the event log at boot (config 0x13fa); when non-zero 0x8000 is set in 0x1686 and 0x1628 bit 7; cleared by RX cmd 0xCF | bit mask | FUN_rom_00795e; see glossary_cmd.md | CONFIRMED
- 0x1622 | contactor / pre-charge sequence step: 1.., 6 = both closed, 0xFE = sequence fault | enum | FUN_rom_004e94 | INFERRED
- 0x1623..0x162a | eight status bytes sent in CAN11 0x120 (not CAN) | - | see CAN11 section | -
- 0x17f9 | driver-output state for the two solenoid (contactor) drivers: bit 0 = driver B on (FUN_rom_004e50/004e72 set, FUN_rom_004e34 clear), bit 2 = driver A on (FUN_rom_004df0/004e12 set, FUN_rom_004dd4 clear) | bits | those functions | CONFIRMED
- 0x17fc | readback of outputs: bit 0 driver B on, bit 1 = LATA4 follows 0x161b bit 4, bit 2 = LATA3 follows 0x161b bit 6, bit 3 = LATD3 follows 0x161b bit 2, bit 4 = power stage enabled (FUN_rom_006124 sets, FUN_rom_005c70 and FUN_rom_006508 clear), bit 5 driver A on | bits | FUN_rom_0060c8, FUN_rom_006124, FUN_rom_004dd4..004e72 | CONFIRMED
- 0x17fa, 0x17d3, 0x17d6 | never written by application code; zero after start-up. 0x17fa is compared with 'P' in FUN_rom_0093d6 but both branches are identical (dead code); 0x17d3 is an array index added to 0x188c | - | grep of listing | CONFIRMED
- 0x1676 | high nibble = debounced digital inputs (active when the pin is LOW): bit 7 <- RB13, bit 6 <- RB11, bit 5 <- RB12, bit 4 <- RB14; low nibble: bit 0 and bits 1-2 cleared by FUN_rom_004d30 | PORTB 0x0E12 | FUN_rom_0056a8 | CONFIRMED (pin function unknown)
- 0x161b / 0x161c | control bytes received on CAN11 0x105 (data[0], data[1]); 161c bit 4 = enable, 161b bits 0-1 select charge vs run | - | FUN_rom_009196 | INFERRED
- 0x1c78 | configuration byte, default 9, overwritten by data[4] of CAN RX 0x0CFFBC49 | - | FUN_rom_00565a, FUN_rom_005ef6, FUN_rom_009178 | CONFIRMED (meaning unknown)
- 0x176a | cell-balancing mask currently driven (bit k = cell k+1) | bit mask | FUN_rom_00f9da (= 0x1cb2), cleared in FUN_rom_010972 / FUN_rom_0062ec | INFERRED
- 0x1654/0x1656 | 32-bit value read twice from monitor-chip register 0x6F with selector 0x18 and 0x19 during start-up: 0x1654 = reg 0x19 value, 0x1656 = reg 0x18 value (a chip identity / serial number) | - | FUN_rom_00f976, FUN_rom_005ef6 | read path CONFIRMED, meaning INFERRED
- 0x1bf4 | battery profile selector 0..5, from EEPROM config 0x14a6 or RX command 0xD3 (value in data[4], complement in data[5]) | enum | FUN_rom_00795e, FUN_rom_008c22 (see glossary_cmd.md) | CONFIRMED
- 0x1d00 | pointer to the active parameter table (six variants) | - | FUN_rom_010c76 | CONFIRMED
- Parameter table entries (32-bit, byte offset): [0xd8] capacity (0.01 Ah?), [0xdc] parallel cells per group (24,29,32,28,15,16), [0xe0] series cells = 14, [0xe4] = 1, [0xe8] = 0/1/2 (type code), [0xf4] = variant id (1,6,7,8,9,10), [0x118] reference capacity 460/487/615/772/413/441 | - | python dump of PSV tables 0xB0B4.. | values CONFIRMED, meanings INFERRED
- 0x188c + index | 16 byte array stored by FUN_rom_0093d6 (SOC percent) | - | rom:0093d6 | CONFIRMED

## Event log (SPI flash)
- 0x1b76/0x1b78 | event-log write position, 32-bit byte address, record = 256 bytes (+0x100 per record), wraps to 0 when above 0x07D6xxxx | bytes | FUN_rom_00c730, FUN_rom_00c10c | INFERRED
- 0x1b92 | log status byte = (record-check flags) | - | FUN_rom_00c730 | INFERRED
- 0x16be | number of log records written (also loaded from config 0x148c) | count | FUN_rom_00c10c | INFERRED
- 0x16bc | code of the most recent logged event (8 = power-up, 0x12/0x13/0x15/0x16/0x17 = other causes) | enum | many writers | INFERRED
- 0x1894/0x1896 | requested log page number for the dump stream started by RX command 0xCB; 0x177c = frame counter of the stream | - | FUN_rom_008c22, FUN_rom_0086fa | CONFIRMED
