# RAM variables established by the "cmd" job (receive side)

Tag: C = CONFIRMED (read in code), I = INFERRED. Addresses are data-space RAM. "wa" = program word address of evidence.

| Addr | Meaning | Units / scale | Evidence | Tag |
|---|---|---|---|---|
| 0x17b2..0x17c0 | CAN2 RX frame: ID lo 17b2, ID hi 17b4, DLC 17b8, data[0..7] = 17b9..17c0 (DLC is never tested) | - | wa 009368, 008c22 | C |
| 0x1792..0x17a0 | CAN1 RX frame: ID 1792, data[0..7] = 1799..17a0 | - | wa 009284 | C |
| 0x1678 | Operating state byte. 1 idle, 2 discharge/load, 4 charge, 8 service/CAN-command mode, 0x10 boot/latched shutdown | enum | wa 009368 (sets 8), 0086fa main loop dispatch, 005bc0 (sets 1) | C |
| 0x1679 | Secondary state byte, 0 or 8 (8 = a fault word is non-zero), echoed in 0x18FF08F3 byte 1 | - | wa 005bc0, 009dba | C |
| 0x16a0 | Service output-override bits. 0x40 set by cmd 0xDC, 0x80 set by cmd 0xDE. 0x10 and 0x20 are tested in 006508 but no code path sets them | bitmask | wa 008c58, 008c6a, 00650e..006540 | C |
| 0x1778 | cleared by 005bc0 (purpose unknown) | - | wa 005bc0 | C |
| 0x1779 | "Jump to bootloader" request flag, set by CAN1 SDO write 0x1F51:01=0 | bool | wa 00a99a, 008758 | C |
| 0x1c58..0x1c6d | CANopen identity data, loaded by C start-up data-init from template at program 0x10a8a: 1c58 count (4), 1c5a vendor (1), 1c5e product (0x7B), 1c62 revision (0x00010001), 1c66 serial (0x00BC614E = 12345678), 1c6a device type (0x55AA01A2). Never written by application code | little-endian u32 | wa 00a99a/00a918, data-init at 000248, template 0x10a8a | C |
| 0x1c6e | Current-sensor scale factor, set by cmd 0xEC. Default 10000 (= 1.0000) | 1/10000 | wa 008d34-008d60, 00795e (default 10000) | C |
| 0x1c70:1c72 | float = 10000.0 / (float)0x1c6e. Multiplies the current measurement (default 1.0 = 0x3F800000 from data-init) | float | wa 008d4c-008d5e (FUN_000582 is a divide), 009042, 009159 | C (divide order I) |
| 0x1cb4 | Balancing floor: cells are balanced only above this cell voltage. Default table[0x5c]=3800 | mV | wa 008da0, 010972 users, 00565a | C |
| 0x1cb6 | Balancing START spread (cell - min cell). Default 40 (0x28) | mV | wa 008d94, 010972 | C |
| 0x1cfc | Balancing STOP spread. Default 10 | mV | wa 008d9a, 010972 | C |
| 0x1cfa | Max-cell-voltage limit set by cmd 0xC4, clamped to [table 0x60 = 3000, table 0x44 - 5 = 4245]. Default 0x1036 = 4150. Only functional consumer is a debounce counter 0x19fa that nothing reads; also reported in 0x18FF31F3 bytes 2-3 | mV | wa 010c16, 00795e, 00e8d2 | C (consumer I) |
| 0x1cbe | Precharge completion threshold, set by cmd 0xC5 (byte). Default 0x46 = 70 | percent | wa 008e36, 004e94 state compare | C (meaning I) |
| 0x1cbc | Word set by cmd 0xC5. Default 3000. Only echoed in 0x0CFF06F3 bytes 6-7; no other reader found | probably mV | wa 008e3e, 005764 | C (units I) |
| 0x1bf4 | Pack-model / parameter-table index 0..5 (cmd 0xD3). Selects table base in 0x1d00 via 010c76 | enum | wa 008cd8-008cfa, 010c76, 010cca | C |
| 0x1d00 | Pointer (PSV address) to the active 316-byte parameter table | - | wa 010c76 | C |
| 0x1bfe:1c00 | Rated lifetime throughput = table[0xd8] (7300 / 7500 / 9800 / 13400 / 7200 / 7600 for models 0..5) | Ah (I) | wa 010cca -> 00f3ac | C (units I) |
| 0x170a:170c | Cumulative discharge counter, +1 per 1000 units of the 0x1738 roll-over counter (0x1738 rolls every 3600 units of 0x1730). Gates cmds 0xD3, 0xC6, 0xC7 | Ah (I) | wa 006d0a-006d22, 008c7c-008ca2 | C (units I) |
| 0x1688 | Latched permanent-fault word saved in the log: bit 0x04 current-limit trips (FUN_00c9ee, 00cb6e), bit 0x10 from FUN_00e6ea/00e538. Cleared by cmd 0xCF. Mirrored as 0x1686 bit 15 and 0x1628 bit 7 | bitmask | wa 009069, 0093xx, 010759, 010ba8, 010bc4 | C (bit meaning I) |
| 0x1686 | Active fault bitmask (167e OR 167c, plus 0x8000 = latched fault in 1688). Non-zero forces idle in 006616/00654c | bitmask | wa 0065c0, 00c7fc | C |
| 0x16bc | Event code of the last log write (see cmd.md event table); sent in 0x18FF50F3 byte 7 | enum | wa 00abce, 010ba8.. | C |
| 0x16be | Count of log record writes since boot (+1 each non-header write); 0x18FF50F3 bytes 5-6 | count | wa 00c10c, 00abce | C |
| 0x1b76:1b78 | Event-log write pointer: 32-bit byte address of the most recent record in the external SPI NAND (256 bytes per record, +0x100 per write). 0 = unknown, found by scanning (FUN_00bd72) | bytes | wa 00c730, 008f76 | C |
| 0x1b92 | Log write verify status: bit0 address mismatch, bit1 CRC mismatch, bit2 marker (0xAA) mismatch; 0 = OK. Also 10/20/30 added per retry | bitmask | wa 00c306, 00c730, 00abce | C |
| 0x1658:165a | 32-bit value stored by cmd 0xC6 from data[4..7] (big-endian). Sent in 0x510 bytes 0-3 | - | wa 008ef6.., 009fd0 (0x510 block) | C |
| 0x165c:165e | 24-bit value stored by cmd 0xC6 from data[1..3] (big-endian). Sent in 0x510 bytes 4-7 (byte 4 = 0) | - | wa 008e8e.. | C |
| 0x1894:1896 | Log-record dump request (cmd 0xCB): record number N (32-bit LE). Non-zero = stream 36 frames, cleared when done | record no. | wa 009060-00907e, 0089c4-008aec | C |
| 0x177c | Dump frame sequence counter (1..36) | - | wa 0089e0 | C |
| 0x1c7c:1c7e | Allowed missing-0x105 time before contactors are dropped, in main-loop steps (default 100; 120000 if 0x105 data[1] bit 2; set to 10000 by any 0x614 frame) | loop steps (~10 ticks of 0x1664) | wa 009196, 009284, 0088bc-0088f2 | C (time unit I) |
| 0x166a:166c | Steps since last 0x105 frame (cleared by CAN1 0x105 only; incremented each main-loop step). At > 1c7c: drop outputs; at > 1c7c+30000: fault event 0x15 | loop steps | wa 0088c8-0088e0 | C |
| 0x1666,1668,166e,1670 | Cleared on every CAN2 RX frame (and at 0x105), but nothing reads them (dead) | - | wa 009368, 009196, grep of listing | C |
| 0x1c78 | Byte copied from data[4] of CAN2 0x0CFFBC49; default 9; echoed in 0x18FF08F3 byte 7 | - | wa 009178, 009dde | C |
| 0x161b,161c,161d | Copies of CAN1 0x105 data[0], data[1], data[3] | bytes | wa 009196 | C |
| 0x161b bits | bit2 -> output LATD3 via 004db6; bit4 -> LATA4; bit6 -> LATA3; bits0-1 non-zero = charge/discharge request | - | wa 0060c8, 006616, 00654c | C (names I) |
| 0x161c bits | bit4 enable (clear = idle via 005bc0); bit0 enables some TX; bit1 "event" one-shot -> FUN_00609c | - | wa 0086fa tail, 009196 | C |
| 0x1610,1612,1614 | CAN1 0x515 data: 1610 = data[0]; 1612 = data[3]<<8 | data[4]; 1614 = data[1]<<8 | data[2]; 161e:1620 = 1612*1614*100 | - | wa 0091f0 | C |
| 0x17f9 | Output-stage-active bits (bit0 set by 004e72, bit2 by 004df0; cleared by 004e34/004dd4). Cmd 0xCB is ignored while non-zero | bitmask | wa 004e34, 004df0, 004e72, 009042 | C |
| 0x17fc | Output-status byte: bit1 LATA4, bit2 LATA3, bit3 LATD3, bit4 "aux power outputs on", bit5/bit0 output stages; sent in 0x0CF096F3 byte 7 | bitmask | wa 0060c8, 006124, 005c70 | C |
| LATD4 (0xE34 bit 4) | Driven high in the closing sequence 004e94 state 4 and cleared by every shutdown path: this is the main contactor drive (INFERRED) | - | wa 004f8c (bset), 005218.. (bclr) | I |
| LATD15 (0xE34 bit 15) | Cleared by 004da6 before every contactor action (precharge drive, INFERRED) | - | wa 004da8 | I |
| LATD12, LATC13, LATC0, LATA3 | Group of "aux power / hold" outputs. Set every main-loop pass by 006124 (D12, C13, C0) ; cleared by 005c70 (shutdown) and by 006508 under cmd 0xDC (D12) / 0xDE (C13, C0). Set to 1 just before the bootloader jump | - | wa 006124, 005dbe, 006534-006544, 008760 | C (function I) |
