# BMS core behaviour (contactors, current, faults, modes)

Tags: CONFIRMED = read directly from code/constants; INFERRED = reasoned. Evidence = function word-address (FUN_rom_xxxxxx) in out2/all.c + listing.txt.
Conventions: "tick" = 1 ms counter RAM 0x1664 (Timer1 ISR FUN_rom_010e86; PR1=0xEA6, 1:8, FCY 30 MHz assumed from 16 MHz xtal, PLL 60 MHz; INFERRED). "Pass" = one slow main-loop iteration, runs when tick delta >= 10 ms (FUN_rom_0086fa), so 1 pass ~ 10 ms. Most debounce counters count passes.
Direction: positive current (RAM 0x13c0, float, mA) = CHARGING into pack, negative = discharge (CONFIRMED by SOC integrator FUN_rom_00f474 adding signed current and by charge counters in FUN_rom_006b06 accumulating only when mode 4 and I>=0).

## 0. Pin map (CONFIRMED from init FUN_rom_010492, FUN_rom_014af8 and PPS tables; connector names INFERRED)
| MCU pin / function | Firmware use | Connector name (INFERRED) |
|---|---|---|
| RC12 (RP60) = OC2 PWM out, TRIS out | solenoid "A" (PWM pull-in/hold) | one of Prelay/srelay |
| RA0 (RP16) = OC3 PWM out | solenoid "B" | the other of Prelay/srelay |
| RD4 (LATD bit4 0x10) plain out | precharge enable, on only in contactor states 3 and 4 | internal precharge path (not on the 4-pin solenoid connector per owner) |
| RA1 = AN1 (ADCBUF1 0x40E), RA2 = AN2 (ADCBUF2 0x410) | two pack-voltage sense inputs, 12 bit, mV = raw * 58000 / 3418 (see 2.5) | packv1 / packv2 |
| RB12 in (PORTB 0xE12 bit12 0x1000), CN source in sleep | "enable" (low = asserted); gates the contactor machine; release of it forces sleep | en12v (INFERRED, strongest candidate) |
| RB6 in (bit6), CN source in sleep | wake pin read in FUN_rom_006142 | unknown (en12v/reset derived) |
| RB11 in (0x800) -> RAM 0x1676 bit6 | debounced 4-9 passes; selects which fault word is used for imbalance fault (FUN_rom_00e6ea 10785: bit6 clear -> 0x167e, set -> 0x167c) | cen (charge enable) INFERRED |
| RB13 in (0x2000) -> 0x1676 bit7 ; RB14 in (0x4000) -> 0x1676 bit4 | only reported (CAN1 0x113 byte2 bit0=bit7, bit1=bit4; CAN2 0x0CF096F3 byte6 high nibble = 0x1676&0xF0) | den and estp in unknown order |
| RC9, RD13, RD7 in (PORTC 0xE22 bit9, PORTD 0xE32 bits13,7) | 3-bit board address -> RAM 0x1660 = 7-s +0x27 (s=sum 4*RC9+2*RD13+RD7; s=7 gives 0): values 0x28..0x2E; sent in CAN1 0x51D byte4 (FUN_rom_009fd0, FUN_rom_006e6c) | internal strap |
| RA4 (PWM1H), RA3 (PWM1L), RD3 plain outs | host-controlled aux outputs (161b bits 4,6,2) | fatout / alout / fuout in unknown order |
| RC13 (0x2000) + RC0 (0x0001), RD12 (0x1000) outs | set together at wake-up "board on" (FUN_rom_006124 17fc bit4); cleared at sleep and by test cmd | 12vout (INFERRED) |
| RB7?? not used. RB8 SCK1, RD11 SDO1, RB15 SDI1, RD0 CS, RD8 (pulse sequence FUN_rom_00fb98) | SPI1 to MC33771B | |
| RC2 SCK2, RC7 SDO2, RC8 SDI2, RD9 CS | SPI2 to external NV memory (opcodes 06/02/03/D8; log of 0x100-byte records) | |
| RB0 (LATB bit0) | analog mux select for NTC bank (toggled every temperature pass, FUN_rom_00d5d0) | |
| RB9? RB7 (0x80) / RC3,RC6 (0x48) / RB5? | set at init as outputs high/low (LATB |=0x80, LATC |=0x48); function unknown | |
| RD6 C1TX, RD5 C1RX, RC5 C2TX, RC4 C2RX, RC9->no | CAN | can11, can2 |
Other: RB14 dual use none. Reset pin = MCLR, not software visible.

## 1. Contactor / solenoid control

### 1.1 Hardware drive (CONFIRMED FUN_rom_010832, FUN_rom_0105ce, FUN_rom_010700, FUN_rom_004dd4..004e72)
- Timer2 PR2=0x780 (1920 counts, ~15.6 kHz at 30 MHz FCY, INFERRED). OC2 and OC3 run OCxCON1=6 (edge-aligned PWM, Timer2), OCxCON2=0x0C, OCxRS=0x780.
- Pull-in: OCxR=0x780 (100 % duty, always high). Hold: OCxR=0x3C0 (50 %). Off: OCxCON1.OCM=0 (module disabled, pin low) and OCxR reset.
- Helper functions: A off = FUN_rom_004dd4 (clears RAM 0x17FC bit5, 0x17F9 bit2); A pull-in = 004df0; A hold = 004e12 (both set 0x17FC bit5, 0x17F9 bit2). B off = 004e34 (clears 0x17FC bit0, 0x17F9 bit0); B pull-in = 004e50; B hold = 004e72 (set 0x17FC bit0, 0x17F9 bit0).
- OC1 (0x900) is programmed OCxR=0x18 on Timer2 period but has no pin mapped; INFERRED ADC/internal trigger. Shut down in FUN_rom_004dbe before sleep.
- Coil current is not measured; FUN_rom_006b06 subtracts 125 mA from the measured current when any solenoid output is on (0x17F9 != 0) (CONFIRMED 0x42FA0000), i.e. coil draw passes through the shunt.

### 1.2 State flags (CONFIRMED)
- 0x1622 contactor state (below). 0x16B2 pass counter since last state change (saturates at 250).
- 0x17F9 (sent CAN2 0x0CF096F3 byte0): bit0 = B energised, bit2 = A energised.
- 0x17FC (sent CAN2 0x0CF096F3 byte7): bit0 B on, bit1 RA4 aux, bit2 RA3 aux, bit3 RD3 aux, bit4 board-on outputs (RC13/RC0/RD12), bit5 A on. CAN1 0x112 byte0 bit5 = A on, byte1 bit5 = B on.
- Measurements used: V_A = RAM 0x17EC/0x17EE (32 bit mV, from AN2), V_B = 0x17F0/0x17F2 (mV, from AN1), V_pack = 0x1AD6/0x1AD8 (sum of the 14 cell mV, section 2.5).

### 1.3 Preconditions to run the sequence (FUN_rom_004e94 entry, CONFIRMED)
State forced to 1 and 0x16B2 cleared whenever: RAM 0x1676 bit5 clear (enable input RB12 not asserted, FUN_rom_0056a8) OR 0x1625 bit3 set (enable input released latch). The sequence is only called from: mode 2 run (FUN_rom_005930), mode 4 run (FUN_rom_0057ec), test mode (FUN_rom_006508 when 0x16A0 bit5).
Requests to enter modes come from the host on CAN1 0x105 (data0 -> 0x161B, data1 -> 0x161C, data3 -> 0x161D; FUN_rom_009196) and are evaluated every pass at the end of FUN_rom_0086fa:
1. 0x1678==0x10 (just booted) -> idle (FUN_rom_005bc0).
2. 0x1678==8 (test mode) -> FUN_rom_006508.
3. 0x161C bit4 (0x10) clear -> idle (FUN_rom_005bc0): contactors opened.
4. 0x161C bit4 set and (0x161B & 3)==0 -> "drive/discharge" request: FUN_rom_006616 -> FUN_rom_005690 -> FUN_rom_005b2c (enter mode 2) or run FUN_rom_005930.
5. 0x161C bit4 set and (0x161B & 3)!=0 -> "charge" request: FUN_rom_00654c -> FUN_rom_00594c (enter mode 4) or run FUN_rom_0057ec; sets 0x17FD=2.
Entry checks (FUN_rom_005b2c mode 2): no fault bytes 0x1623..0x162A (FUN_rom_006616: any nonzero except masked bits 0x40 of 1624, 0x20 of 1628... and 0x1686 must be 0 else idle); temperature window: max temp idx (0x1990) < table[0x00] AND min temp idx (0x198F) > table[0x1C] i.e. <70 C and >-20 C (discharge window; else sets 0x167E bit1 + 0x1627 bit7/6 when sensors valid); min cell (0x1A36) >= table[0x6C] (2500 mV; T1 2750) else just stays open (FUN_rom_005218). Mode 4 (FUN_rom_00594c): same temperature test using table[0x00] and table[0x1C] hi/lo words (code compares [0],[1] and [0xE],[0xF]); resets charge counters (FUN_rom_006df2) and calls 4E94.
While in mode 4, FUN_rom_0057ec computes charger-current request 0x1778 (below) and runs 4E94.

### 1.4 Contactor state machine (FUN_rom_004e94, state byte 0x1622; each row executed once per pass)
| State | Outputs | Exit / checks (ms = ~10 ms per 16B2 count) | Faults |
|---|---|---|---|
| 0 / unknown | (default branch sets state 1) | | |
| 1 | RD15 off, RD4 off, A off, B off | if V_A < 2000 mV -> state 2 else fault | V_A >= 2000 mV (voltage on output side with everything open, welded contact or external source): 0x1686|=0x200, 0x1626|=0x04, state 0xFE |
| 2 | RD4 off, A off, B pull-in | after 16B2 >= 8 (~80 ms): |V_A - V_B| <= 2000 mV -> state 3 | mismatch: 0x1686|=0x200, 0x1626|=0x02, state 0xFE |
| 3 (precharge) | RD4 ON, A off, B hold | each pass: if V_B >= V_pack, or 100*V_B/V_pack >= RAM 0x1CBE (default 70 %) -> state 4 (16B2=0) | if 16B2 > 29 and ratio <= 29 %, or 16B2 >= 100 (1 s): 0x1627|=0x01 (precharge timeout), state 0xFE |
| 4 | RD4 ON, A pull-in, B hold | after 16B2 >= 8: |V_A - V_pack| <= 2000 mV -> state 5 | else 0x1686|=0x200, 0x1626|=0x04, 0xFE |
| 5 | RD4 off, A hold, B hold | after 16B2 >= 8: same check -> state 6 | same |
| 6 (closed) | A hold, B hold, RD4 off | every pass: |V_A - V_B| <= 5000 mV else 0x1626|=0x02; |V_pack - V_A| <= 5000 mV else 0x1626|=0x04; both set 0x1686|=0x200 and state 0xFE | |
| 0xFE (opening, FUN_rom_005218) | pass 0-7: B off, RD4 off (A unchanged); 8-12: B off, if A still on and |V_A-V_pack|>2000 -> 0x1626|=2,0x1686|=0x200; 13-30: A off, B off; >=31: A off, B off, if V_A>=2000 mV -> 0x1626|=4 (output still live: welded); then state 0 | | |
| 0xFF | all off | | |
Notes: CONFIRMED sequence: B (OC3/RA0) closes first, then RD4 precharge, then A (OC2/RC12) pulls in at full duty, then RD4 drops and both hold at 50 %. "Precharge then main" is therefore: B, precharge path, A. INFERRED topology consistent with all voltage checks: pack+ -> A -> node X (AN2, V_A) -> B -> output node Y (AN1, V_B); RD4 feeds a precharge resistor around A. Which of A/B is the connector's "Prelay" vs "srelay" is NOT determinable from firmware (open question); owner says both end up on.
Opening commands: FUN_rom_005218 is called by every fault handler, by FUN_rom_005bc0 (idle), host-timeout, sleep prep, over-current etc. Because the output is held by PWM, opening takes ~0.3 s and the B contactor opens first.

### 1.5 Conditions that open the contactors (all CONFIRMED, call FUN_rom_005218 then mode 1, sub-state 8)
- 0x161C bit4 cleared by host; 0x161B&3 changes mode (goes via idle).
- Any fault byte/word nonzero (FUN_rom_005bc0 chosen by FUN_rom_006616/00654c).
- Enable input RB12 released (state forced to 1).
- Host heartbeat loss: no CAN1 0x105 for RAM 0x1C7C passes (default 100 = ~1 s; 54464+65536 passes = ~20 min if the last 0x105 had 0x161C bit2 set). On expiry: 0x161B &= 0xAB (aux bits 2,4,6 cleared), in mode 2 -> 0x167E|=0x100,0x1624|=4; in mode 4 -> 0x167C|=0x100, 0x1624|=4; FUN_rom_005218 (FUN_rom_0086fa 4806-4819).
- Silent 30000+0x1C7C passes: sleep, reason 0x15.
- Over-temperature, under-temperature, cell OV/UV, over-current, imbalance, current-sensor, monitor-comm faults (section 3).
- Contactor voltage plausibility faults (1.4).
- Over-current "severe" events also write NV flag (0x1688) and latch 0x1686 bit15.
- CAN2 test commands (1.7).

### 1.6 Charge request output 0x1778 (INFERRED meaning: charger current request in A, CAN1 0x113 byte7)
FUN_rom_0057ec (mode 4, sub-state 2) sets 0x1778=FUN_rom_00ad9e() = interpolated limit table (FUN_rom_00b70e on table 0x1CD8, ptr per variant FUN_rom_00ac86, x = avg temperature float 0x19E8) /10; forced 0 when: avg temp below table[0x104] (40 idx = 0 C) or above table[0xFC] (90 idx = 50 C), min temp < table[0x108]? , max cell > RAM 0x1C80 + 10 (0x1C80 = table[0x54] 4150 mV, or table[0xF8] 3900 mV when avg temp in [table[0x104], table[0x108]) ie cold), or taper-done flag 0x13F8==1 when table[0xE8]==2. Also 0 in sub-state !=2.

### 1.7 Test/override mode (CONFIRMED FUN_rom_009368, FUN_rom_008c22, FUN_rom_006508)
Any CAN2 frame with ID 0x0CFF00F3 sets 0x1678=8 and runs FUN_rom_008c22. In mode 8 the main loop only executes FUN_rom_006508 using RAM 0x16A0: bit4 (0x10) -> FUN_rom_005218 (open); bit5 (0x20) -> FUN_rom_004E94 (run closing sequence; never set by any command seen, INFERRED unreachable); bit6 (0x40, set by command 0xDC) -> RD12 off + open; bit7 (0x80, command 0xDE) -> RC13/RC0 off (17FC bit4 cleared). Command 0x00 clears 0x16A0 and goes idle (open). NOTE: entering mode 8 does not itself open contactors, but it disables the host-timeout and fault paths that test 0x1678==2/4.

## 2. Current, voltage, SOC
### 2.1 Current path (CONFIRMED FUN_rom_00f844, FUN_rom_00c992, FUN_rom_00cb6e, FUN_rom_00c9ee)
- Sensor: shunt connected to the NXP MC33771B (SPI1, FUN_rom_00fd96 read). Per pass FUN_rom_00f844 reads monitor registers 0x2F (dummy), 0x2E, 0x2D, 0x47 and forms raw = int32{reg2D:reg2E} / uint16(reg47) (FUN_rom_000606 signed 32/16 divide) -> RAM 0x1C52/0x1C54. Skipped if reg47 >= 0xFFEF. Register names INFERRED: 0x2D/0x2E coulomb counter MSW/LSW (MC33771B COULOMB_CNT1/2), 0x47 = number of samples (CC_NB_SAMPLES-like), so raw = average ISENSE ADC counts. (Register map is offset +1 vs the generic datasheet table; cells 0x3F..0x32, NTC AN0..AN6 0x46..0x40, TH_ALL_CT 0x4B all consistent with that offset.)
- Average of last 4 raws minus min and max -> /2 : FUN_rom_00f762 -> RAM 0x13D8/0x13DA (int32 counts).
- Offset: RAM 0x13D4/0x13D6. Measured once in FUN_rom_005ef6 -> FUN_rom_00c860: if -199 <= counts <= 199 offset=counts, clear 0x167C/0x1680 bit3; else 0x167E|=8, 0x167C|=8, 0x1624|=2 and offset=0 (current sensor offset fault).
- Scaling: I[mA] (RAM 0x13C0 float) = (counts - offset) * 6.0 * RAM 0x1C70, with 0x1C70 = 10000.0 / RAM 0x1C6E (gain, default 10000 -> 1.0; changeable by CAN2 cmd 0xEC, stored in NV). 6 mA per count matches a 0.1 mOhm shunt with MC33771B 0.6 uV LSB (INFERRED). Sign: positive = charge.
- Idle dead-band: when mode 1 (idle/open) counts within +-349 are forced to 0 and larger reading sets warning bit3 (0x1680/0x1682) (FUN_rom_00cb6e 9113-9134); in other modes offset is subtracted only if the stored offset is within +-199.
- Coil compensation: -125 mA when any solenoid on (FUN_rom_006b06).
- Derived: 0x1022 |I| , 0x102E ring (signed, 10), 0x1056 ring (|I|), averaged to 0x107E/0x1082 (10-sample means), ring of 100 at 0x1088/0x1218, means 0x13A8 (signed), 0x13AC (|I|), 0x1026, 0x102A (shorter-window sums). Exact windows INFERRED (~100 ms, ~2 s, ~10 s).
### 2.2 Over-current thresholds (table offsets are byte offsets into the pointer at RAM 0x1D00; values are signed 32-bit mA; see 2.6)
All counters are per pass; "n>k" means k+1 consecutive passes.
| Offset | Value T0 (mA) | Compared with | Count | Action |
|---|---|---|---|---|
| 0x98 | -900000 | I (0x13C0) below | 10 | FUN_rom_00c7fc: 0x167E|=4, 0x1626|=0x80, mode 1, open; first time also NV log: 0x1688|=4, 0x1686|=0x8000, 0x1628|=0x80 (FUN_rom_010bc4) |
| 0x9C | -500000 (T3 -600000) | I below | 10 | FUN_rom_00c7fc (no NV flag) |
| 0xB0 | 300000 | 0x102A avg | 10 | FUN_rom_00c7fc |
| 0xB4 | 240000 | 0x13AC avg | 200 | FUN_rom_00c7fc |
| 0xA8 / 0xAC | -75000 / -74000 | I below / hysteresis | 11 / clear after 0 | warning 0x1682 bit2 set / cleared |
| 0xC0 | +500000 | I above | 10 | FUN_rom_00c82e: 0x167C|=4, 0x1626|=0x80, open; plus NV flag as above |
| 0xC4 | 240000 | 0x1026 avg | 10 | FUN_rom_00c82e |
| 0xC8 | 150000 | 0x13A8 avg | 200 | FUN_rom_00c82e |
| 0xCC / 0xD0 | 37500 / 36500 | I above / below | 10 | warning 0x1680 bit2 set / cleared |
Evidence: FUN_rom_00cb6e 9281-9477, FUN_rom_00d010 listing. Polarity of the 0xB0/0xB4 pair is INFERRED (direction-neutral averages feeding the discharge fault word).
Also: 0x1CC0 state-of-health (per mille) = capacity learned / table[0x118]; table[0x11C..0x138] feed SOH limit calc FUN_rom_006744 (INFERRED).
### 2.3 Pack voltage (CONFIRMED FUN_rom_00e5a4)
14 cell voltages read from MC33771B regs 0x3F..0x32 (cell1..cell14) every pass, average of 4 minus min/max (FUN_rom_00f694), mV = (raw & 0x7FFF) * 0.152344 (float 0x3E1C4000; ~152.6 uV/LSB). RAM 0x1A1A[14] (cell1..14). V_pack = sum -> 0x1AD6/0x1AD8 (uint32 mV). min 0x1A36, max 0x1A38, spread 0x1A3C, index of min/max 0x1A3E/0x1A3F (1-based). Separately AN1/AN2 (2.5).
### 2.4 Temperatures (CONFIRMED FUN_rom_00d5d0, 00d4b4)
7 monitor inputs AN0..AN6 (regs 0x46..0x40) read twice per two passes with analog mux RB0 high/low = 14 NTCs; 4-sample trimmed mean -> 0x1952[14]; look up in PSV table 0xBD5E (251 entries, 15-bit ADC descending) -> byte index 0x1981[14] (0 = open/invalid, 0xFA = below table). Index = degC+40 (CONFIRMED by divider math: index 65 -> ADC 10813/32768 = 0.33 = 10k NTC with 20k pull-up at 25 C). Reordered into 0x1970[14]. Max 0x1990 (position 0x1994), min 0x198F (0x1993), spread 0x1992, mean 0x1991 (valid count 0x19ED; fewer than 12 valid sensors -> 0x1627|=0x40 and 0x1686|=2 and open).
### 2.5 AN1/AN2 voltage sense (CONFIRMED FUN_rom_005534, FUN_rom_0052d4)
Each ADC channel: 6 samples, drop min and max, sum/4; ring of 4 -> trimmed mean/2 (FUN_rom_005368/00543c). mV = raw * 58000.0 / RAM 0x1CC2 (initialised 3418, 0x0D5A from data-init table) -> 32 bit. AN2 -> 0x17EC/0x17EE (V_A), AN1 -> 0x17F0/0x17F2 (V_B). Raw 12 bit (values >0xFFF forced 0). 1640/1652: stability counters (<=100).
### 2.6 Parameter tables (six, selected by pack variant, 316 bytes, 32-bit entries; extracted from hex)
Rows are byte offsets; T0..T5 where they differ. Temperatures are byte indices (degC = idx - 40).
- 0x00..0x3C discharge/charge temperature limits: 0x00 110(70C) cut-off max discharge (T1 105); 0x04 105; 0x08 98; 0x0C 96; 0x10 24; 0x14 21; 0x18 22; 0x1C 20 (-20C); 0x20 105 (T1,T3-5: 95) charge max cut; 0x24 93; 0x28 95 (85); 0x2C 93 (83); 0x30 37; 0x34 34; 0x38 37; 0x3C 29 (-11C); 0x40 140 (100C, extreme).
- 0x44..0x5C cell max mV: 4250,4200,4200,4190,4150,4050,3800 (T1 4230; T3-5 4240/4210/4210/4200/4200/4050/3800).
- 0x60..0x74 cell min mV: 3000,3000,3050,2500(T1 2750),2250(2500),2000(2250). 0x78 2500, 0x7C 0.
- 0x80 500 (T3-5 250) imbalance limit charging; 0x84 5000 (250) imbalance limit not charging; 0x88 6000 (T3-5 4250) hard cell-OV; 0x8C 35000/38500, 0x90 58800, 0x94 61000 (pack-level mV limits, use not traced).
- 0x98..0xD0 current thresholds, 2.2. 0xD4 2400/1500/3200/8008/4290/4576 (use at FUN_rom_00a0e0 only); 0xD8 capacity in 10 mAh: 7300,7500,9800,13400,7200,7600 (73..134 Ah); 0xDC 24/29/32/28/15/16; 0xE0 = 14 (series cells); 0xE4 = 1; 0xE8 = 0/1/0/2/2/2 (charge-termination style, 2 = taper stop uses flag 0x13F8); 0xEC 9800/9900; 0xF0 sleep monitor cell-threshold register value 55209.. (written to monitor reg 0x4B); 0xF4 variant id 1,6,7,8,9,10 (sent CAN1 0x113 byte3); 0xF8 3900; 0xFC 90; 0x100 50; 0x104 40; 0x108 30; 0x10C 3650; 0x110..0x138 SOH/limit parameters.
### 2.7 SOC and capacity (CONFIRMED structure, scaling INFERRED)
- Open-circuit lookup: FUN_rom_00f2ca interpolates a 100-point table (ptr 0x1C1E: 0xC16A/C234/C2FE/C3C8 by variant) -> SOC from cell voltage; FUN_rom_00f3ee(min cell) sets 0x1BF6 = SOC(min cell) and 0x1BF8 = SOC(max cell) in 0.1 % units (cap 0x3F1), remaining charge 0x1C16/0x1C18 (mA*s) = SOC*capacity, capacity 0x1BFE (10 mAh, default table[0xD8], NV overridable via CAN2 0xC? record) -> 0x1C02 (= cap*36000 mA*s), 0x1C06 (cap*10 mAh).
- Initialised at boot in FUN_rom_005ef6/FUN_rom_00f3ee from min cell voltage after settling and again at sleep prep.
- Integration: FUN_rom_00f474 each pass adds I to a 100-pass accumulator; each 100 passes (~1 s) averages and, if |avg| > 1000 mA, adds avg*1 s to remaining charge, then nudges SOC (0x1BF6) by at most +-1 toward the integral result and clamps 0x3F1. If |avg| <= 1000 mA for 900 s (0x384) the SOC is re-derived from the cell voltage (FUN_rom_00f3ee) (rest correction) and 0x1C24 counts those.
- Counters: FUN_rom_006b06 accumulates charge/discharge Ah, Wh in 0x16E6.. (mAh rollover 3600 units per mAh at 10 ms/pass, Ah at 1000 mAh).
- Output: CAN1 0x113 byte5 = SOH/5 (0x1CC0/5), byte6 = SOC/5 (0x1BF6/5), i.e. 0.5 % per count.

## 3. Fault, warning and status words
Flow per pass (FUN_rom_0086fa 4830): 0x1684 = 0x1682|0x1680 (warning summary); 0x1686 |= 0x167E|0x167C (fault summary, latched). Words: 0x1680 = charge-side warnings (live), 0x1682 = discharge-side warnings (live), 0x167C = charge-side faults (latched), 0x167E = discharge-side faults (latched). Many conditions set both sides' bits. Fault clear: CAN1 0x105 data1 bit1 (0x161C&2) with >100 ms since last clear, or CAN2 cmd 0x00: FUN_rom_00609c zeroes 1623..1627, 1629, 162A, keeps 1628&0x82, zeroes 167C/167E, keeps only bit15 of 1686. Transmitted: CAN2 0x18FF08F3 data0=0x1678 data1=0x1679 data2..3=0x1686 data4..5=0x1684 data6=0x17D6 data7=0x1C78 (FUN_rom_009fd0/0093d6); CAN1 0x120 data0..7 = 0x1623..0x162A.

### 3.1 0x167C / 0x167E / 0x1686 shared bit meanings
| Bit | Name | Set | Clear | Evidence |
|---|---|---|---|---|
| 0 (0x0001) | cell under-voltage | 167E: min cell < tbl[0x6C] for 300 passes while discharging (FUN_rom_00e560 via mode 2); also at sleep check min < tbl[0x74] (167E,1682 bit0). 167C: min cell < tbl[0x70] 300 passes with I<0 requested charge | FUN_rom_00609c; 654c clears 167E bit0 at charge entry | e6ea 10851/10865, 5c70 2984, e560 |
| 1 (0x0002) | temperature out of window | FUN_rom_005be0/005c06 (167C: charge over/under temp), 005c2c/005c4e (167E: discharge over/under temp, 0x200 counters >=200), 00594c/005b2c at mode entry; d5d0 (<12 sensors) sets 0x1686 | 609c | d7a2, 5c2c |
| 2 (0x0004) | over-current | 167E FUN_rom_00c7fc (discharge side/any-direction averages), 167C FUN_rom_00c82e (charge) ; 0x1626 bit7 set; mode 1 | 609c | c7fc, c82e |
| 3 (0x0008) | current sensor offset bad at init | FUN_rom_00c860 | 609c, re-measured at init | c860 |
| 4 (0x0010) | cell over-voltage | FUN_rom_00e538: max cell >= tbl[0x44] (4250 mV) for 300 passes, or >= tbl[0x88] for 500 passes (also NV flag 0x1688|=0x10) ; mode 1 | 609c | e6ea 10642/10758 |
| 6 (0x0040) | cell imbalance | max >= RAM 0x1CB4 (3800 default) and spread >= tbl[0x80] (charging 161B&3!=0) or tbl[0x84] (not charging) for 61 passes; goes to 0x167E if 0x1676 bit6 clear else 0x167C; 0x1623 bit1 | 609c | e6ea 10766-10791 |
| 8 (0x0100) | host communication / mode timeout | 0x167E/0x167C: no CAN1 0x105 within 0x1C7C passes while mode 2/4 (0x1624 bit2 too); 0x167C|=0x100 also by 57ec when mode 4 request lasts >=1008 passes before... see 4.3 | 609c (also 654c/6616 clear the live bit in 0x1680) | 86fa 4806-4817, 57ec 2620 |
| 9 (0x0200) | contactor/precharge/output-voltage fault (0x1686 only, not in 167x) | FUN_rom_004e94, 005218 (1.4) | 609c clears (not kept); live-cleared to 0 and bit10 set when enable input released (56a8) | 4e94 |
| 10 (0x0400) | enable input RB12 not asserted (0x1686 only) | FUN_rom_0056a8 2562 | FUN_rom_00609c | |
| 12 (0x1000) | monitor chip (MC33771B) SPI communication fault | FUN_rom_00fafc after 5*20 failed polls: 0x167C|=,0x167E|=,0x1627|=2 | 609c; live bit 0x1680/0x1682 bit12 set when any read stage fails (62ec) and cleared on next success | fafc 11968, 62ec |
| 15 (0x8000) | permanent (non-volatile) event latched (0x1686 only) | FUN_rom_010bc4 when 0x1688 !=0: severe over-current (0x1688 bit2), severe cell OV (bit4); also loaded from NV at boot if 0x1688 !=0 | CAN2 cmd 0xCF (FUN_rom_010ba8) | 795e 4361, 10bc4 |
Bits 5,7,11,13,14: never set in code (reserved).
### 3.2 0x1680 / 0x1682 warnings
| Bit | Name | 0x1682 (discharge) | 0x1680 (charge) |
|---|---|---|---|
| 0 | low cell voltage warning | min < tbl[0x64] (3000) 300 passes while discharge/idle; cleared when min >= tbl[0x68] (3050) (counter 1A04==0); also set at sleep prep | cleared with 1682 bit0 when charging |
| 1 | temperature derate | max >= tbl[0x08] (98) or min <= tbl[0x14] (21) 200 passes; clears at < tbl[0x0C] (96) and > tbl[0x10] (24) | charge: max >= tbl[0x28] (95) or min <= tbl[0x34] (34); clears < tbl[0x2C] and > tbl[0x30] |
| 2 | over-current warning | I < tbl[0xA8] (-75 A) 12 passes; cleared < tbl[0xAC] | I > tbl[0xCC] (+37.5 A) 12 passes; cleared < tbl[0xD0] |
| 3 | current sensor reading abnormal while idle (|counts|>349) | same | same |
| 4 | cell over-voltage warning | n/a | max >= tbl[0x4C] (4200) 300 passes; cleared max < tbl[0x50] (4190) after 100 passes (also 0x1624 bit4) |
| 8 | host state | not set | set by CAN2 0x0CFFBC49 when data4 not 3 or 5 (FUN_rom_009178); cleared 654c after 1000 passes in charge, 57ec, 6616 |
| 12 | monitor read stage failed | per stage | per stage |
Bit 11 (0x0800) of 0x1682 and bit 5 (0x20) of 0x1680 only ever cleared (dead). 0x1684 = OR of both.
### 3.3 Status bytes 0x1623..0x162A (CAN1 0x120 data0..7). Any nonzero => "fault present" (0x1674=1, CAN1 0x112 byte0 bit1)
0x1623: bit1 (0x02) imbalance (e6ea 10784); bit3 (0x08) balancing flag, only ever cleared (dead).
0x1624: bit1 current offset bad (c860); bit2 host timeout (86fa); bit3 cell OV 3 s (e6ea 10642); bit4 cell OV warning (set/clear e6ea); bit5 (0x20) severe low cell (min<tbl[0x6C]) or charge-side UV or sleep-time UV (e6ea, 5c70); bit6 (0x40) low cell warning (cleared 3050) / UV fault (e560); bit7 unused. 654c clears bit6 on charge entry; FUN_rom_00654c/6616 treat 0xAF-masked.
0x1625: bit3 (0x08) enable input RB12 released (56a8) - cleared by 609c.
0x1626: bit1 (0x02) contactor voltage mismatch A vs B (4e94/5218); bit2 (0x04) output voltage mismatch / welded (4e94/5218); bit7 (0x80) over-current (c7fc, c82e, cb6e 9292). Bits 56a8 clears 0x06 on enable.
0x1627: bit0 precharge timeout (4e94); bit1 monitor comm fault (fafc); bit6 (0x40) under-temp cut (5c06/5c4e/594c/5b2c) or <12 NTC (d5d0); bit7 (0x80) over-temp cut (5be0/5c2c/594c/5b2c).
0x1628: bit1 (0x02) pack variant index invalid >5 (010cca); bit5 (0x20) temperature spread >=20 for 200 passes (cleared <18); bit6 (0x40) extreme over-temp >=100 C 10 passes (also opens); bit7 (0x80) non-volatile fatal event (10bc4/795e). 609c keeps bits 1 and 7.
0x1629, 0x162A: never written (reserved, always 0).
### 3.4 Other status
0x1676 (debounced inputs & flags): bit2 cell OV active (set e6ea 10861, cleared when max < tbl[0x58] (4050) after 300 passes), bit0 charge sub-state flag (cleared 57ec), bit4 RB14 asserted, bit5 RB12 asserted, bit6 RB11 asserted, bit7 RB13 asserted (asserted = pin low; set after 4..9 low passes, cleared when counter decays to 0). Sent CAN2 0x0CF096F3 byte6 high nibble.
0x1678 mode: 0 post-sleep/uninitialised, 1 idle (contactors open), 2 discharge/drive running, 4 charge running, 8 test mode (any 0x0CFF00F3 frame), 0x10 just booted (acts as idle until a request). 0x1679: 0 ok/idle, 1 entering, 2 running, 8 stopped by fault (set by 5bc0 when 0x1686 !=0 else 0); 4 is tested but never assigned.
0x161B host request (CAN1 0x105 data0): bits0-1 charge request (0 = none/drive, !=0 = charge), bit2 -> RD3 aux out, bit4 -> RA4, bit6 -> RA3 (FUN_rom_0060c8; mirrored in 0x17FC bits 3,1,2). 0x161C (data1): bit1 clear-faults, bit2 extend heartbeat timeout to ~20 min, bit4 enable contactors. 0x161D data3 unused.
0x16A0 test bits (1.7). 0x17FC/0x17F9 solenoid flags (1.2). 0x1622 contactor state (1.4). 0x1A62/0x13F8/0x13F6 charge taper flags. 0x1688 NV event bits (bit2 over-current, bit4 cell OV).
### 3.5 Reason codes written to log/RAM 0x16BC (sent in CAN2 0x50F3 frames and stored in NV records, FUN_rom_00abce, 006efc)
2 sleep request by host (CAN1 0x515 data0 bits5:4 ==1), 3 same with bits==2 (deep, no CAN wake), 4 deep discharge (min cell < tbl[0x74] for >999 passes), 6 charge started, 7 capacity learned, 8 boot, 0xA, 0xC gain changed, 0xD precharge params, 0xF charge-voltage limit command, 0x10 permanent fault logged, 0x11 cleared, 0x12 NV write retry, 0x13 totals set, 0x14 variant changed, 0x15 host silent sleep, 0x16 enable input dropped, 0x17 SOH limit update.

## 4. Main loop, modes, sleep, variants
### 4.1 Reset path (CONFIRMED)
Reset vector -> goto 0x013800 (FUN_rom_013800 bootloader): sets DSRPAG, runs C-init FUN_rom_013848 (data-init table), then bootloader main FUN_rom_0141b0 which brings up CAN1 (filter for 0x6xx/0x14 SDO frames, FUN_rom_014192), SPI-free; it polls CAN and if the bootloader timeout (0x1024:0x1026 = 200 x 1.0) expires with flag DAT_1000 set, calls FUN_rom_014c14/01492a and jumps to application entry FUN_rom_000200 (SP=0x2300, runs data-init at table 0x10A8A, calls FUN_rom_0086fa). Bootloader also has its own pwrsavOp sleep after 0x5F8F+ ticks of CAN silence (FUN_rom_0141b0 14376-14385).
Application FUN_rom_0086fa: FUN_rom_00fe82 init order: 010492 (ports, PPS, oscillator unlock), 00fecc (PLL: CLKDIV 0x3102, PLLFBD 0x1C -> 60 MHz), 010f7c (interrupt priorities), 01032c (SPI), 010f2a (UART1), 010832/0105ce/010700 (OC1/2/3), 010d1e/010db2 (Timer2/5), 0101c8, 01001e (ADC), 010db2, 010ed0 (DMA), 010e44 (Timer1 1 ms), 00ecae (CAN2), 00de28 (CAN1), then FUN_rom_00fe64, FUN_rom_00fe70(0x20). Then FUN_rom_00c4e4 (load NV record), FUN_rom_010cca (select variant tables), FUN_rom_0086bc (SOH), FUN_rom_005ef6 (re-init, wait for settled cell data, offset, SOC), set 0x16BC=8, FUN_rom_006142 (sleep/wake gate), then loop.
### 4.2 Loop schedule
Fast part (every iteration): CAN1 RX FUN_rom_009284, CAN2 RX FUN_rom_009368, periodic senders; the 0x105-heartbeat bookkeeping; CAN2 0x18FF52F3 sender. Slow part when tick delta >= 10 ms (a "pass"): FUN_rom_0056a8 (debounce inputs), 16B2++, FUN_rom_005534 (AN1/AN2), FUN_rom_0062ec monitor sequence (state 1 current -> 2 temperatures -> 3 cell voltages and cell checks -> 4 balancing), SOC integrate FUN_rom_00f474, accumulate FUN_rom_006b06, FUN_rom_0067e0 (capacity learning), sleep conditions, FUN_rom_00605e (0x1674 fault summary), FUN_rom_006124 (board outputs), FUN_rom_00add0/00b22a (current limits, every 20 ms), then the mode selector (1.3).
Sleep triggers: 0x1C7C+30000 passes with no 0x105 (reason 0x15); CAN1 0x515 data0 bits5:4 !=0 held 500 ms (reasons 2/3); deep discharge (reason 4); en12v drop (RB12 rising edge compare 0x16D2<0x16D4 with timers, reason 0x16); boot with RB12 high sets 0x176C=2 (no-wake sleep).
FUN_rom_005c70 (sleep): final CAN frames (2 x 0x18FF08F3-style status, CAN1 0x120, 0x0CF096F3), writes monitor mask regs, sleeps monitor (reg 0x03 / 0x02), outputs off (RD15,RD4,A,B,RA3,RC13,RC0,RD12), if SOC (0x1BF6) > 4.9 % and 0x176C<2: arms wake sources: monitor TH_ALL_CT = tbl[0xF0] (reg 0x4B), CN interrupt on RB12 and RB6 (CNENB |= 0x1040), CAN1 wake interrupt (C1INTE bit6), and RD5 (CAN1 RX) change notification if min cell >= tbl[0x74]; otherwise TH_ALL_CT=0xD766 and wake only by reset. Then pwrsavOp (sleep). On wake FUN_rom_006142 loops: re-run FUN_rom_005ef6 and sleeps again while min cell < tbl[0x74] or RB6 rose with wake armed.
Wake via CAN: YES, CAN1 activity (WAKIE and RD5 CN) when SOC>4.9 % and cells healthy.
### 4.3 Pack variant selection (CONFIRMED)
FUN_rom_010c76(index) maps 0..5 -> table pointer 0xB0B4,B1F0,B32C,B468,B5A4,B6E0 (anything else = 0). Index = RAM 0x1BF4, loaded in FUN_rom_00795e from the non-volatile record byte at offset 0xAC (RAM image 0x14A6; 0xFF -> 0) read from the external SPI NV memory (FUN_rom_00c4e4/00c306). It is set over CAN2 0x0CFF00F3 command 0xD3: data[4]=index (0..5), data[5]=~data[4]; allowed only while lifetime discharge Ah counter 0x170A < 5*(capacity/100)... (guard in FUN_rom_008c22 5161); it then calls 010cca/010bec to apply and log (reason 0x14). Not a pin strap. Index > 5 sets 0x1628 bit1 but still uses table 0.
### 4.4 Charge termination and balancing (CONFIRMED structure)
- Charge allowed/current request 0x1778 (1.6); termination: max cell reaching RAM 0x1CFA (default 0x1036 = 4150 mV, settable via CAN2 cmd 0xC4 and clipped by table[0x44]-5 / table[0x60]) for 300 passes (counter 0x19FA); 0x1A62 set when max >= 0x1C80 - 50 for 300 passes (taper), cleared at < 0x1C80 - 100; 0x13F8 set after 0x9C5 passes of I<=0 (charger ended) with taper flag; 1778 zeroed accordingly when table[0xE8]==2.
- Balancing (FUN_rom_010972, FUN_rom_010a54, FUN_rom_00f9da): active only in mode where (0x161C bit4) and (0x161B&3)!=0 (charging). Start when max >= 0x1CB4 (3800 mV default) for 300 passes and spread >= 0x1CB6 (40 mV default) for 60 passes (flag 0x1CB9); stop when spread <= 0x1CFC (10 mV) ; cells above (min + threshold) are bled via monitor regs 0x0C..0x19 bit 0x200 refreshed every 35 s. CAN2 cmd 0xC3 changes 0x1CB4/1CB6/1CFC. Balancing flag 0x1CB9 -> CAN1 0x113 byte1 bit0.

## 5. Open questions
1. Which solenoid output (OC2/RC12 vs OC3/RA0) is the connector's Prelay vs srelay; what RD4 actually switches (precharge path/resistor?). Physical topology assumed from voltage checks only.
2. Which of RB11/RB13/RB14 are den/cen/estp (RB11 = cen is best guess); which of RA3/RA4/RD3 are fatout/alout/fuout; RB6 function; RB12 = en12v assumed.
3. Exact meaning of monitor register 0x47 as divisor, and window lengths of the averaging variables 0x1026/0x102A/0x13A8/0x13AC.
4. Units of table[0xD4], [0xDC], [0x110..0x138], [0x8C..0x94]; reason for 0x1679==4 never assigned.
5. Polarity (0xB0/0xB4 group) and whether 0x161D / CAN1 0x515 data1..4 (0x1612/0x1614/0x161E) are a charger voltage/current command (computed 0x161E/0x1620 = 100*x*y).
6. Whether bootloader timeout path (0x141B0) is entered by CAN 0x6xx SDO frames only; not traced in detail.
