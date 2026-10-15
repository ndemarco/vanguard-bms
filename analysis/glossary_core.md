# glossary_core (core job): RAM variables
| Addr | Meaning | Units | Evidence |
|---|---|---|---|
| 0x1622 | contactor state machine state (1..6, 0xFE opening, 0xFF off) | state | FUN_rom_004e94 |
| 0x16B2 | passes since contactor state change | 10 ms | 0086fa |
| 0x1664 | ms tick (Timer1 ISR) | ms | 010e86 |
| 0x17EC/EE | V_A, AN2 voltage (uint32) | mV | 005534 |
| 0x17F0/F2 | V_B, AN1 voltage | mV | 005534 |
| 0x1AD6/D8 | pack voltage = sum of 14 cells | mV | 00e5a4 |
| 0x1A1A[14] | cell voltages | mV | 00e5a4 |
| 0x1A36/38/3C | min/max/spread cell | mV | 00e5a4 |
| 0x1981[14],0x1970[14] | temperature byte index (degC+40) | idx | 00d5d0 |
| 0x198F/0x1990/0x1991/0x1992 | min/max/mean/spread temp idx | idx | 00d5d0 |
| 0x13C0 (float) | pack current, + = charge | mA | 00cb6e |
| 0x13D4/6, 0x13D8/A, 0x1C52/4 | zero offset, filtered raw, raw (monitor regs 2D:2E / reg47) | counts | 00f844,00c992 |
| 0x1C6E, 0x1C70 | current gain 10000 default; float 10000/gain | | 00795e |
| 0x1CC2 | AN scaling denominator (3418) | counts per 58 V | data init |
| 0x1BF6 / 0x1BF8 | SOC from min/max cell / integrated | 0.1 % | 00f3ee,00f474 |
| 0x1BFE | pack capacity | 10 mAh | 00795e |
| 0x1CC0 | state of health | per mille | 0086bc |
| 0x1D00 | pointer to selected parameter table | | 010c76 |
| 0x1BF4 | pack variant 0..5 | idx | 00795e,008c22 |
| 0x1676 | debounced inputs/flags (bit4 RB14,bit5 RB12,bit6 RB11,bit7 RB13, bit2 cell OV) | | 0056a8 |
| 0x1678/0x1679 | mode / sub-state | | 5bc0 etc |
| 0x161B/161C/161D | CAN11 0x105 data0/1/3 host request | | 009196 |
| 0x1680/1682/1684 | charge warn/discharge warn/or | bits | section 3 |
| 0x167C/167E/1686 | charge fault/discharge fault/summary | bits | section 3 |
| 0x1623..0x162A | status bytes (CAN11 0x120) | bits | section 3.3 |
| 0x1688 | non-volatile event bits (2 over-current, 0x10 cell OV) | | 00cb6e |
| 0x16A0 | test-mode bits | | 008c22,006508 |
| 0x17F9/0x17FC | solenoid/outputs state | bits | 004dd4.., 0060c8 |
| 0x1778 | charger current request (INFERRED A) | A | 0057ec |
| 0x1C7C/7E | heartbeat timeout (passes, 32 bit) | passes | 0086fa |
| 0x1CB4/B6/FC | balance floor / start spread / stop spread | mV | 00565a |
| 0x1CFA | charge cell-voltage limit (4150 default) | mV | 00795e |
| 0x1CBE | precharge ratio threshold (70) | % | 004e94 |
| 0x1660 | board address 0x28..0x2E | | 006e6c |
| 0x16BC | log/reason code | | section 3.5 |
