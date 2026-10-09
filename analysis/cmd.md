# Receive side: what the firmware accepts, and what each message does

Tags: CONFIRMED = read directly in the code at the cited program word address (wa). INFERRED = reasoned, no direct proof.
All addresses are program word addresses as in out2/listing.txt. RAM names are in sections/glossary_cmd.md.

## 0. Summary for a driver writer

* There are two buses. CAN2 (29-bit, J1939 style IDs) carries a small command set. CAN1 (11-bit) carries two "enable" frames from the vehicle or charger (0x105, 0x515) and a CANopen-style service channel (0x614 request, 0x594 reply).
* No command on either bus closes the contactors. Closing is automatic and is driven by CAN1 0x105 (byte 0 and byte 1) plus the pack's own voltage/temperature checks. The only contactor-related command is the opposite: any frame with ID 0x0CFF00F3 puts the pack in "service mode" (state 8), which stops the normal contactor logic and drives the contactor outputs off on every main-loop pass. (CONFIRMED wa 009368/0093ba, 0086fa tail, 006508; contactor naming of the output pin is INFERRED.)
* Four of the five accepted CAN2 IDs have no effect on the pack at all (CONFIRMED, see 1.1 to 1.3).
* Writes that persist (calibration, pack model, limits, serial) are saved by the firmware into an event log held in an external SPI NAND flash chip (not into the MCU's own flash). They are re-read at boot. Balancing parameters (cmd 0xC3) are RAM only.
* Reply/acknowledge frames are on CAN2 29-bit IDs 0x18FF50F3 (log write acknowledge), 0x18FF51F3 (log index), 0x18FF52F3 (log record dump).
* Always send 8 data bytes. The receive code never tests the DLC (CONFIRMED wa 009368, 008c22: data bytes 17b9..17c0 are read straight from the buffer), so a short frame is parsed with stale bytes.
* Commands run inside the CAN receive interrupt (wa 00041c calls FUN_rom_009368; wa 0003f2 calls FUN_rom_009284). Event-log writes (SPI NAND program + verify, can take milliseconds) therefore run in interrupt context; do not send commands back to back faster than about 50 ms.

## 1. CAN2 receive (29-bit), dispatcher FUN_rom_009368

Source: wa 009368..0093d4. The function calls FUN_rom_00ee44(&0x17b2) to fetch one frame from the hardware FIFO. Frames are delivered one per interrupt. On every frame, before the ID test, it executes `clr 0x1666, 0x1668, 0x166e, 0x1670` (wa 00937a..000380). Nothing in the program reads those four words (listing grep: only `clr.w` instructions exist), so they are dead timeout counters (CONFIRMED).

ID layout used by the compare: ID low 16 bits in 0x17b2, ID high 13 bits in 0x17b4. J1939 breakdown below uses priority = bits 28..26, PF = bits 23..16, PS = bits 15..8, SA = bits 7..0.

| ID (hex) | Pri | PF | PS | SA | Action in firmware | Tag |
|---|---|---|---|---|---|---|
| 0x0C1BF349 | 3 | 0x1B | 0xF3 (destination = BMS own address) | 0x49 | Compared at wa 00938a..00938e, then branches straight to the return. No payload byte read. | CONFIRMED |
| 0x0C1BFF49 | 3 | 0x1B | 0xFF (broadcast) | 0x49 | wa 00939a..00939c, same: ignored. | CONFIRMED |
| 0x18DFFFF3 | 6 | 0xDF | 0xFF | 0xF3 | wa 0093aa, same: ignored. PF 0xDF is not the J1939 request PGN (that is PF 0xEA, ID 0x18EAxxyy). The SA 0xF3 is the BMS's own source address (every BMS transmit ID ends in F3), so this ID is either a loopback of a second BMS-class node or something the original product used; the firmware never answers it. | CONFIRMED (ignored) / INFERRED (origin) |
| 0x0CFF00F3 | 3 | 0xFF | 0x00 | 0xF3 | Sets 0x1678 = 8 (service mode) and calls FUN_rom_008c22, the command interpreter (section 2). wa 0093ba..0093be. | CONFIRMED |
| 0x0CFFBC49 | 3 | 0xFF | 0xBC | 0x49 | FUN_rom_009178: copies data[4] into 0x1c78; if data[4] is not 3 and not 5 sets bit 0x100 in 0x1680 (warning word) and zeroes 0x16ba. | CONFIRMED |
| any other ID | | | | | Ignored (the hardware accepts everything, software drops it). | CONFIRMED |

Who sends them: SA 0x49 (decimal 73) is the same sender for the three 0x...49 IDs. Charger vs vehicle controller cannot be proven from this firmware, because only 0x0CFFBC49 has any effect. INFERRED: node 0x49 is the charger (0xBC data[4] values 3 and 5 look like "charging" and "charge complete" states and any other value sets a warning), and 0x0CFF00F3 is a diagnostic or factory tool (it uses the BMS's own SA and is the only frame that can change anything).

### 1.1 What happens when 0x1B frames and 0x18DFFFF3 stop arriving or never arrive

Nothing. There is no timer on any CAN2 receive ID (CONFIRMED: the only per-frame side effect is clearing the four dead words). There is no J1939 request-for-PGN (0xEA00) handler at all (CONFIRMED: no compare against 0x18EAxxxx anywhere in the dispatcher). The BMS never answers a request; it broadcasts its status periodically (other sections).

### 1.2 0x0CFFBC49, byte layout (FUN_rom_009178, wa 009178..009194)

| Byte | Use |
|---|---|
| data[0..3] | not read |
| data[4] | stored to 0x1c78. Values 3 and 5: accepted silently. Any other value: sets 0x1680 |= 0x0100 and clears 0x16ba. |
| data[5..7] | not read |

0x1c78 is echoed back in byte 7 of the 0x18FF08F3 status frame (wa 009dde) and in 0x...: the value is initialised to 9 at start-up and in FUN_rom_00565a. The 0x0100 bit in 0x1680 is cleared again by FUN_rom_006616 (idle path, wa 006616..) and by FUN_rom_00654c after 1000 passes (wa 0065c6..0065da), so it is a transient warning that is mirrored into 0x1684 (OR of 1680 and 1682) and sent in 0x18FF08F3 bytes 4-5. Tag: CONFIRMED for the code path, INFERRED for the "charger status" interpretation.

### 1.3 0x0C1BF349, 0x0C1BFF49, 0x18DFFFF3

No payload used. A driver does not need to send them. They also give no keep-alive: the pack does not time out without them.

### 1.4 Timeouts on the vehicle side (CAN1 0x105) and how a 0x614 frame changes them

CAN2 has no timeout. The only link timeout is on CAN1 0x105 (wa 009196 resets, wa 0088bc..0088f2 and 004787..004812 test):

* 0x105 frame arrival (FUN_rom_009196): clears step counter 0x166a:166c; copies data[0]->0x161b, data[1]->0x161c, data[3]->0x161d; sets the allowed silence 0x1c7c:1c7e = 100 loop steps, or 120000 if data[1] bit 2 is set. CONFIRMED.
* Main loop steps are every >= 10 ticks of the free-running counter 0x1664 (wa 0086fa: `9 < 1664 - 16ae`). If 0x1664 is a 1 ms tick (INFERRED) one step is about 10 ms, so the default silence limit is about 1 s and the extended one about 20 min.
* When the step counter exceeds the allowed silence: 0x1c7c is set back to 100, 0x161b &= 0xAB (clears the output request bits 2, 4, 6), and in states 2 or 4 the fault bit 0x100 is set in 0x167e or 0x167c, 0x1624 bit 2 set, and the output stage is shut down by FUN_rom_005218 (contactors open). CONFIRMED (wa 0088f4..00893x).
* If the counter exceeds allowed silence + 30000 steps (about 5 minutes at 10 ms): event code 0x15 and FUN_rom_006142 (shut down / re-init loop). CONFIRMED (wa 0088bc..0088e4).
* Any 0x614 frame (CAN1 service channel) sets the allowed silence 0x1c7c:1c7e to 10000 steps (about 100 s) (wa 009284 mov 10000). So a diagnostic session on 0x614 keeps the pack from dropping contactors for 100 s of missing 0x105 frames. CONFIRMED.

### 1.5 CAN1 0x105 and 0x515 (the two "simple" ones, listed because they decide contactor action)

| ID | Handler | Bytes |
|---|---|---|
| 0x105 | FUN_rom_009196 | data[0] -> 0x161b: bits 0-1 nonzero = charge/discharge request, bit 2 -> output LATD3 (via FUN_rom_004db6), bit 4 -> LATA4, bit 6 -> LATA3 (wa 0060c8..0060ee, 006104..00611e). data[1] -> 0x161c: bit 4 = enable (clear: pack is kept idle, wa 0086fa tail `161c & 0x10` -> FUN_rom_005bc0), bit 2 = long timeout, bit 1 = one-shot that calls FUN_rom_00609c, bit 0 enables some TX frames. data[3] -> 0x161d (no consumer found). data[2], data[4..7] not read. CONFIRMED for the bit tests, names INFERRED. |
| 0x515 | FUN_rom_0091f0 | data[0] -> 0x1610 (bits 4-5 form the mode 0x176c, acted on after 500 ticks, wa 0086fa); data[1..2] -> 0x1614 (big-endian); data[3..4] -> 0x1612 (big-endian); product 0x1612*0x1614*100 -> 0x161e:1620. Units not decoded. INFERRED: charger or vehicle voltage/current request. |

## 2. Command interpreter FUN_rom_008c22 (frame 0x0CFF00F3)

Source: wa 008c22..009176. Frame: ID 0x0CFF00F3, extended, 8 data bytes. data[0] is the command byte. All commands first run after 0x1678 was forced to 8 (CONFIRMED wa 0093ba..0093bc), including unknown command bytes.

Important side effect for all bytes: because 0x1678 = 8 the main loop (wa 0087xx tail, 00897e..008984, source FUN_rom_0086fa) calls FUN_rom_006508 on every pass instead of the normal state handlers. FUN_rom_006508 clears LATD4 (0xE34 bit 4, the main-contactor drive, INFERRED) and LATD15 (via FUN_rom_004da6) every pass, then acts on 0x16a0 bits. The pack stays in this state until command 0x00 is accepted, a fault handler (FUN_rom_00c7fc, 00c82e, ...) sets 0x1678 back to 1, or the pack resets. Service mode is visible in 0x0CFF20F3 byte 0 = 4 and 0x18FF08F3 byte 0 = 8.

Checksum rule (called "inverse-byte" below): pairs of bytes must add to 0xFF, i.e. the second byte is the bitwise NOT of the first. Unless stated, a failed check silently ignores the command (the service-mode state change has already happened).

Gate G5 = "new-pack gate": the command runs only when (0x1bfe:1c00 / 100) * 5 > 0x170a:170c (wa 008c7c..008ca2, 008e60..008e86, 008f32..008f58). 0x1bfe is the model's rated lifetime throughput (table[0xd8], 7300 for model 0) and 0x170a is the cumulative discharge counter. With model 0 the commands are accepted only while the counter is below 365. INFERRED units: Ah. The counter is read back from the log at boot, so a used pack will refuse these commands (CONFIRMED logic, INFERRED meaning).

### 2.1 Command table

| Cmd | Byte layout (data[0..7]) | Validation | Gate | Effect (RAM / hardware) | Reply | Persists? | Tag |
|---|---|---|---|---|---|---|---|
| 0x00 | 00 xx xx xx xx xx xx xx | none | ignored while 0x16a0 & 0xC0 != 0 (i.e. after 0xDC or 0xDE) | Clears 0x16a0, calls FUN_rom_005bc0: 0x1778=0, FUN_rom_005218 (output-stage shutdown, 0x1622=0xFE, 0x17f9 cleared), LATD15 low, LATD4 low, 0x1678=1 (idle), 0x1679 = 8 if any fault word else 0. Normal logic then resumes and re-evaluates CAN1 0x105. | none (state shows in 0x0CFF20F3 byte0 and 0x18FF08F3) | state only | CONFIRMED wa 008c26..008c3c, 005bc0 |
| 0xC3 | C3 xx S T FL FH ~FL ~FH | FL+data[6]=0xFF, FH+data[7]=0xFF | none | 0x1cb6 = S (balance start spread, mV), 0x1cfc = T (stop spread, mV), 0x1cb4 = FH:FL (balance floor, mV). data[1] unused. Defaults are re-applied by FUN_rom_00565a and FUN_rom_005ef6 whenever the pack goes through those (charge-state path), so a value may not last. Balancing logic is FUN_rom_010972 / 010a54. | none | RAM only | CONFIRMED wa 008d66..008db2 |
| 0xC4 | C4 xx xx xx VL VH ~VL ~VH | same pairs | none | FUN_rom_010c16: value V (mV) clamped to [table 0x60 = 3000, table 0x44 - 5 = 4245 for model 0] then 0x1cfa = V; event code 0x0F -> log write. Only reported in 0x18FF31F3 bytes 2-3 plus a debounce counter nobody reads. | 0x18FF50F3 (log write ack, byte 7 = 0x0F) | yes (log record) | CONFIRMED wa 008db4..008dfa, 010c16..010c66; effect INFERRED (no functional consumer found) |
| 0xC5 | C5 xx P ~P LL LH ~LL ~LH | P+data[3]=0xFF, LL+data[6]=0xFF, LH+data[7]=0xFF | none | 0x1cbe = P (default 70), 0x1cbc = LH:LL (default 3000); event 0x0D -> log write. 0x1cbe is compared with the percentage 17f0*100/1ad6 in the contactor closing sequence FUN_rom_004e94 (wa 00203x..): precharge ratio threshold, percent (INFERRED). 0x1cbc only echoed in 0x0CFF06F3 bytes 6-7. | 0x18FF50F3 (byte 7 = 0x0D) | yes | CONFIRMED wa 008dfc..008e54, 010c68 |
| 0xC6 | C6 D1 D2 D3 D4 D5 D6 D7 | none | G5 | 0x165c:165e = D1D2D3 (24-bit big-endian), 0x1658:165a = D4D5D6D7 (32-bit big-endian), event 0x13 -> log write. CAN1 0x510 then transmits D4 D5 D6 D7 00 D1 D2 D3 (a serial/ID number, INFERRED name). | 0x18FF50F3 (byte 7 = 0x13) | yes | CONFIRMED wa 008e56..008f26, 009fd0 block for 0x510 |
| 0xC7 | C7 xx xx xx xx xx xx xx | none | G5 | Erases the first 16 blocks (page addresses 0, 0x40, ... 0x3C0) of the external SPI NAND (FUN_rom_00c0ec -> 00c098, opcode 0xD8), zeroes log pointer 0x1b76:1b78 and write counter 0x16be, FUN_rom_00c4e4 rebuilds the log header from current RAM values, FUN_rom_00abce sends the status frame. This wipes the event history and the stored cumulative counters (INFERRED: the counters are stored in the same record) but writes the current calibrations back. | 0x18FF50F3 | yes (erases) | CONFIRMED wa 008f28..008f6a |
| 0xC9 | C9 xx xx xx xx xx xx xx | none | none | Reads the log index. If pointer 0x1b76:1b78 is 0 first scans the NAND (FUN_rom_00bd72). Sends 0x18FF51F3: bytes 0-3 little-endian = latest record number N_last = ((ptr - 0x20000) >> 8) + 2 (or 1 if ptr = 0); bytes 4-7 little-endian = raw pointer. | 0x18FF51F3 | no | CONFIRMED wa 008f6c..009036 |
| 0xCB | CB N0 N1 N2 N3 xx xx xx | none | ignored if 0x17f9 != 0 (an output stage is active) | N = N3N2N1N0 little-endian record number. N = 0 does nothing. Stores N in 0x1894:1896, then sends one first frame, then the main loop streams 36 more frames (see 3). | 0x18FF52F3 x37 | no | CONFIRMED wa 009038..009164, 0089c4..008af2 |
| 0xCF | CF xx xx xx xx xx xx xx | none | none | 0x1688 = 0 (clears the latched permanent-fault word), event 0x11 -> log write, 0x1686 bit 15 and 0x1628 bit 7 cleared. Does not clear other active fault words (0x167c, 0x167e, 0x1680, 0x1682). | 0x18FF50F3 (byte 7 = 0x11) | yes | CONFIRMED wa 008c42..008c4c, 010ba8 |
| 0xD3 | D3 xx xx xx M ~M xx xx | M + data[5] = 0xFF only (data[6..7] not checked) | G5 | 0x1bf4 = M if 0..5, else 0 (M = 3 stays 3). FUN_rom_010cca selects the parameter table 0xB0B4/B1F0/B32C/B468/B5A4/B6E0, sets 0x1d00, reloads the rated throughput 0x1bfe, calls FUN_rom_00ac86 and FUN_rom_00f276 with the model, 0x1c80 = table[0x54]. FUN_rom_010bec: event 0x14 -> log write. All protection thresholds switch immediately to the new table (cell over/under-voltage, temperature, current limits). Models differ in thresholds and capacity: see section 4. | 0x18FF50F3 (byte 7 = 0x14) | yes | CONFIRMED wa 008c72..008d0c, 008cd8..008d08, 010cca, 010c76 |
| 0xDC | DC xx xx xx xx xx xx xx | none | none | 0x16a0 |= 0x40. In service mode FUN_rom_006508 then clears LATD12 (0xE34 bit 12) and runs FUN_rom_005218 on every main-loop pass. LATD12 is part of the "aux power" output group re-asserted by FUN_rom_006124 (INFERRED: a power-enable line). Blocks command 0x00 (the only way out other than a fault or reset). | none | RAM only (cleared by FUN_rom_005ef6 on re-init) | CONFIRMED wa 008c52..008c5e, 00650e..006540 |
| 0xDE | DE xx xx xx xx xx xx xx | none | none | 0x16a0 |= 0x80. FUN_rom_006508 then clears LATC13 and LATC0 (0xE24 bits 13 and 0) and 0x17fc bit 4 every pass. Same group as above; also blocks command 0x00. | none | RAM only | CONFIRMED wa 008c64..008c70, 006538..006546 |
| 0xEC | EC xx xx xx GL GH ~GL ~GH | pair sums 0xFF | none | 0x1c6e = GH:GL (current-sensor scale, 10000 = 1.0000, 16-bit, NO range check; 0 gives 10000/0 = infinite scale), 0x1c70:1c72 = 10000.0 / (float) 0x1c6e. Event 0x0C -> log write. The scale multiplies the current measurement (wa 009042, 009159). | 0x18FF50F3 (byte 7 = 0x0C) | yes | CONFIRMED wa 008d0e..008d64, 010c08 |
| any other byte | | | | only the side effects of section 2 intro (state 8, contactor outputs off) | none | | CONFIRMED |

DC and DE are not "unlock" bytes for other commands. Searching all code, 0x16a0 is read only at wa 008c2a (cmd 0x00), 00650e..006540 (FUN_rom_006508) and written at wa 005f4c (clear) and 008c58/008c6a (set). So they gate nothing except (a) command 0x00 and (b) the output-off actions. Bits 0x10 and 0x20 of 0x16a0 would call the contactor-closing routines FUN_rom_005218 / FUN_rom_004e94 but no code ever sets them, so there is no CAN command that closes the contactors. CONFIRMED.

No command has a password or sequence requirement. The only access control is the G5 gate (C6, C7, D3), the checksum pairs, and 0x17f9 (CB).

### 2.2 Acknowledge frame 0x18FF50F3 (FUN_rom_00abce, wa 00abce..00ac2e)

Sent after every event-log write and every 500 ms periodically (wa 0093d6 tail). 8 bytes.

| Byte | Content |
|---|---|
| 0..3 | Log pointer 0x1b78:1b76, big-endian (byte 0 = high byte of 0x1b78). Address of the record just written (the pointer is advanced by 0x100 before writing). |
| 4 | 0x1b92 verify status: bit0 address mismatch on read-back, bit1 CRC mismatch, bit2 marker byte mismatch. 0 = write verified. Multiples of 10 (10, 20, 30) added if a retry was needed. |
| 5..6 | 0x16be log write count since boot, big-endian |
| 7 | 0x16bc event code of the last write |

Event codes seen: 0x0A bootloader jump, 0x0C current scale (EC), 0x0D precharge/aux word (C5), 0x0F cell limit (C4), 0x10 latched permanent fault set, 0x11 fault latch cleared (CF), 0x12 write retry, 0x13 serial (C6), 0x14 pack model (D3), 0x15 0x105 silence fault, 0x16 and others from the fault handler FUN_rom_006142 callers. CONFIRMED for codes cited in the code listed in section 2.1.

### 2.3 Persistence

CONFIRMED wa 006efc (record writer), 00c10c (program), 00c306 (read-back), 00795e (boot loader of values), 00c4e4 (boot scan). The record is 256 bytes in an external SPI NAND (INFERRED part: 2 KiB pages, 64 pages per block; opcodes used: 0x06 write enable, 0x13 page read, 0x03 read cache, 0x02 program load, 0x10 program execute, 0xD8 block erase, 0x1F/0x0F feature registers). The record carries: 0xAA marker, 4 address bytes, then (from 6efc) fault word 0x1688, table[0xe8], 0x1bfe, 0x1c6e, 0x1cfa, 0x1cbe, 0x1cbc, a cumulative-usage block (0x1758, 0x16e6.., 0x170a, 0x1738 etc.), 0x165c/0x1658 serial, 14 cell voltages, 14 temperatures, flags, 0x1bf4, CRC-16 in the last two bytes. At boot the newest valid record restores 0x1c6e, 0x1cfa, 0x1cbe, 0x1cbc, 0x1bf4, 0x1688, the serial words and the usage counters (wa 00795e; defaults used when a field reads 0 or 0xFFFF: 10000, 4150, 70, 3000). The MCU's own program flash is never written by any command (no NVM key sequence 0x55/0xAA to NVMCON 0x728 is in the receive path; the bootloader at 0x013800 owns that).

## 3. Log record dump (cmd 0xCB), reply 0x18FF52F3

Record number to address (wa 008a20..008a5a, 0089f4..00901e):

* N = 1: byte address 0 (the header record).
* N >= 2: byte address (N + 0x1FE) * 256, i.e. 0x20000 + (N-2)*256.
* NAND page = address >> 11, column = address & 0x7FF; records are 256 bytes, 8 per page.

Frames, all ID 0x18FF52F3, 8 bytes:

| Frame | Byte 0 | Bytes 1-7 |
|---|---|---|
| First (sent by the command, wa 0090f0..00915c) | 0x00 | N0 N1 N2 (low three bytes of N), then 4 bytes read at record offset 0..3 (normally AA, page hi, page lo, col hi) |
| k = 1..36 (sent from the main loop, one per tick of 0x1664 while 0x17f9 == 0, wa 0089c4..008af2) | k | 7 record bytes at offsets 7k-3 .. 7k+3 |

36 frames cover offsets 4..255, so one dump gives the full 256-byte record. Each tick re-reads the NAND, so stream duration is bounded by SPI speed. After k = 36 the request word 0x1894 is cleared. A new CB restarts the stream. A frame with 0x17f9 != 0 (contactor stage active) pauses the stream. CONFIRMED.

Record content beyond the header is the layout written by FUN_rom_006efc: offset 0 = 0xAA, 1..4 = page/column, then from offset 5 fields in the order of that function with the CRC at offsets 254..255 (INFERRED offsets; read the listing of 006efc for exact positions: base 0x1500 + 3 header bytes, fields start at 0x1508 for non-header records). Cell voltages (14 x 16-bit LE, mV) are at record offset 0x68 + 5? (INFERRED, from `0x1568 + 2*i` with base 0x1503, i.e. record offset 0x1568 - 0x1503 + ... see glossary).

## 4. Model table (cmd 0xD3) values, CONFIRMED from the hex file

Tables at PSV data addresses 0xB0B4, 0xB1F0, 0xB32C, 0xB468, 0xB5A4, 0xB6E0, 316 bytes each, 32-bit entries.

| Model M | table[0x44] cell max (mV) | table[0x6c] (mV) | table[0x74] (mV) | table[0xd8] rated throughput | table[0xdc] |
|---|---|---|---|---|---|
| 0 | 4250 | 2500 | 2000 | 7300 | 24 |
| 1 | 4230 | 2750 | 2250 | 7500 | 29 |
| 2 | 4250 | 2500 | 2000 | 9800 | 32 |
| 3 | 4240 | 2500 | 2000 | 13400 | 28 |
| 4 | 4240 | 2500 | 2000 | 7200 | 15 |
| 5 | 4240 | 2500 | 2000 | 7600 | 16 |

table[0xdc] is probably nominal Ah (INFERRED). The commanded default after power-up with no valid record is 0 (wa 004582 / 008cfa).

## 5. CAN1 0x614 service channel (CANopen-style SDO)

Handler chain: CAN1 receive interrupt -> FUN_rom_009284 (wa 009284..009366) -> FUN_rom_00a99a (request decode) -> FUN_rom_00a918 (reply builder) or FUN_rom_00a8b2 (raw 8-byte reply). Condition: 11-bit ID with (ID & 0x7F) == 0x14 and (ID & 0x780) == 0x600, which is exactly 0x614 (node ID 20 = 0x14). Hardware filter 2 also passes only 0x614 (from NOTES).

Is it real CANopen? Yes, an expedited-only SDO server subset (CONFIRMED by structure): COB-ID 0x600 + node for requests, 0x580 + node for replies (0x594), byte 0 command-specifier tested as `(b0 & 0xE0) == 0x20` (initiate download request) and `== 0x40` (initiate upload request), bytes 1-2 = 16-bit index little-endian, byte 3 = sub-index, bytes 4-7 = data, the download response is the standard `60 idx_lo idx_hi sub 00 00 00 00`, and the objects served are the standard CANopen 0x1000 (device type), 0x1018 (identity) and 0x1F51 (program control, CiA 302-3). It lacks: abort frames (unknown index = no reply at all), segmented transfers, block transfer, size encoding (reply byte 0 is always 0x43 even for 1-byte data), NMT and heartbeat.

### 5.1 Request layout (0x614, 8 bytes; DLC not checked)

| Byte | Meaning |
|---|---|
| 0 | command: 0x40 = read (upload), any value with bits 7..5 = 001 (0x20..0x3F, normally 0x23 or 0x2F) = write (download); everything else ignored |
| 1 | index low |
| 2 | index high |
| 3 | sub-index |
| 4..7 | write data (little-endian) |

### 5.2 Reply layout (0x594)

`DLC = n + 4` where n is the number of data bytes of the object (CONFIRMED wa 00a918: `DAT_ram_1788 = param_5 + 4`). Bytes: [0] = 0x43 (= request command 0x40 + 3, always), [1..2] = index, [3] = sub-index, [4..3+n] = data little-endian. Object sizes: n = 4 for u32 objects, n = 1 for 0x1018:00.

### 5.3 Objects

| Index:Sub | Meaning | Size | Value (default, from start-up data template at 0x10a8a, RAM) | Reply example | Tag |
|---|---|---|---|---|---|
| 0x1000:0 | Device type | u32 (RAM 0x1c6a) | 0x55AA01A2 | 43 00 10 00 A2 01 AA 55 | CONFIRMED |
| 0x1018:0 | Number of entries | u8 (0x1c58) | 4 | DLC 5: 43 18 10 00 04 | CONFIRMED |
| 0x1018:1 | Vendor ID | u32 (0x1c5a) | 1 | 43 18 10 01 01 00 00 00 | CONFIRMED |
| 0x1018:2 | Product code | u32 (0x1c5e) | 0x0000007B (123) | 43 18 10 02 7B 00 00 00 | CONFIRMED |
| 0x1018:3 | Revision | u32 (0x1c62) | 0x00010001 | 43 18 10 03 01 00 01 00 | CONFIRMED |
| 0x1018:4 | Serial number | u32 (0x1c66) | 0x00BC614E (12345678, a placeholder; not the number set by cmd 0xC6) | 43 18 10 04 4E 61 BC 00 | CONFIRMED |
| 0x1F51:1 write 0 | Program control = stop application and enter the bootloader | write only | requires bytes 4..7 all zero | 60 51 1F 01 00 00 00 00 | CONFIRMED |

There is no object for cell voltages, pack data, parameters or firmware version. Pack data is only available from the periodic broadcast frames. Writing any other index, or writing 0x1F51:1 with non-zero data, gets no reply (CONFIRMED).

Write 0x1F51:1 = 0 (wa 00a99a first branch): copies 8 bytes `60 51 1F 01 00 00 00 00` from program memory (PSV 0xC7E0) and sends them on 0x594, writes 0 to C1FEN1 (0x494, disables every CAN1 filter) and sets flag 0x1779 = 1. The main loop (wa 008758..00877a) then runs FUN_rom_005218 (outputs off), sets LATC0 and LATC13 high, logs event 0x0A, runs the log write and FUN_rom_00c10c(0), shuts the peripherals (FUN_rom_006e1a) and jumps to program address 0x013800 (the bootloader reset entry). No password, no key. The pack stops running until the bootloader hands control back. DANGEROUS: avoid in a driver.

## 6. Worked examples

All CAN2 frames are 29-bit extended, 500 kbit/s. Data bytes in hex.

| Goal | Bus / ID | Data | Expected reply |
|---|---|---|---|
| Set balancing start 40 mV, stop 10 mV, floor 3800 mV (0x0ED8) | CAN2 0x0CFF00F3 | C3 00 28 0A D8 0E 27 F1 | none (RAM only). Pack is now in service mode. |
| Leave service mode | CAN2 0x0CFF00F3 | 00 00 00 00 00 00 00 00 | none; 0x0CFF20F3 byte 0 leaves 4 |
| Set current scale to 10000 (1.0000) | CAN2 0x0CFF00F3 | EC 00 00 00 10 27 EF D8 | 0x18FF50F3 with byte 7 = 0C |
| Set cell limit 4150 mV (0x1036) | CAN2 0x0CFF00F3 | C4 00 00 00 36 10 C9 EF | 0x18FF50F3 byte 7 = 0F |
| Set precharge threshold 70 percent, word 3000 (0x0BB8) | CAN2 0x0CFF00F3 | C5 00 46 B9 B8 0B 47 F4 | 0x18FF50F3 byte 7 = 0D |
| Select model 2 (only on a nearly new pack) | CAN2 0x0CFF00F3 | D3 00 00 00 02 FD 00 00 | 0x18FF50F3 byte 7 = 14 |
| Clear latched permanent fault | CAN2 0x0CFF00F3 | CF 00 00 00 00 00 00 00 | 0x18FF50F3 byte 7 = 11 |
| Read log index | CAN2 0x0CFF00F3 | C9 00 00 00 00 00 00 00 | 0x18FF51F3, bytes 0-3 = last record number (LE) |
| Dump record 5 | CAN2 0x0CFF00F3 | CB 05 00 00 00 00 00 00 | 0x18FF52F3 first frame, then 36 frames with byte 0 = 1..36 |
| Read vendor ID over CAN1 | CAN1 0x614 | 40 18 10 01 00 00 00 00 | CAN1 0x594, DLC 8: 43 18 10 01 01 00 00 00 |
| Read identity serial | CAN1 0x614 | 40 18 10 04 00 00 00 00 | 0x594: 43 18 10 04 4E 61 BC 00 |
| Reboot into bootloader (do not use casually) | CAN1 0x614 | 23 51 1F 01 00 00 00 00 | 0x594: 60 51 1F 01 00 00 00 00, then the pack stops responding |

There is no frame that reads cell voltages on request: use the periodic transmissions.


## 8. Interaction with the hardware inputs and relays (den, cen, estp, Prelay, srelay)

CONFIRMED: FUN_rom_005534 (called every main-loop pass, wa 005534..005658) debounces four active-low port-B inputs into byte-flags in 0x1676: RB13 -> 0x80, RB11 -> 0x40, RB12 -> 0x20 (set in state 4), RB14 -> 0x10. FUN_rom_004e94 (the closing sequence for state 4) returns to state 1 and aborts when 0x1676 bit 0x20 is clear or 0x1625 bit 3 is set (wa 004e94 first lines). Mapping of these pins to the labelled connector inputs is INFERRED only: RB12 (bit 0x20, needed for the charge-state closing sequence) is most likely cen; RB13 (0x80) most likely den; RB11/RB14 (0x40, 0x10) the estp/other lines. A pin trace is required to confirm.

What the commands do relative to these inputs:
* No CAN command reads, overrides or emulates den, cen or estp. Setting them by CAN is impossible; the pack still needs the physical inputs (and CAN1 0x105 bits) to close contactors. CONFIRMED (no write to 0x1676 anywhere in the receive path).
* Cmd 0x00 and every other 0x0CFF00F3 frame act below those inputs: any 0x0CFF00F3 frame forces state 8, which stops the closing sequences so the relay drives stay off even if den/cen are asserted; only 0x00 (or a fault reset) returns control to the input-driven logic. CONFIRMED logic. INFERRED mapping: LATD4 = srelay (main), LATD15 = Prelay (precharge), based on 4e94 state 4 raising LATD4 after LATD15 is dropped, and on the precharge-ratio test using 0x1cbe (cmd 0xC5).
* The fault/alarm outputs (fatout, alout) are not touched by the command interpreter. 0x1686 (fault word) non-zero is what the periodic frames report; cmd 0xCF clears only the latched word 0x1688. Which port pins drive fatout/alout was not traced (open question).
* Cmds 0xDC and 0xDE clear LATD12 and LATC13/LATC0, which are re-asserted every pass by FUN_rom_006124 together with the 12 V-related outputs; likely candidates are the switched 12 V output (12vout) and the contactor 12 V supply, INFERRED, unverified.

## 7. Open questions

1. Which physical pin is LATD4 / LATD12 / LATC13 / LATC0 / LATA3? Contactor and "aux power" naming is inferred from sequencing in FUN_rom_004e94, FUN_rom_006124, FUN_rom_005c70; the schematic or a pin trace is needed.
2. Units of the G5 gate counter 0x170a (Ah assumed) and whether a used pack will refuse C6, C7, D3. Read it back through a 0xCB dump (record fields near offset 0x2d..0x30) to find out.
3. Exact record layout inside the 256-byte log record (offsets of the 14 cell voltages and counters): derive from FUN_rom_006efc with base 0x1503.
4. Meaning of 0x0CFFBC49 data[4] values 3 and 5 and the identity of node 0x49. 0x0C1BF349, 0x0C1BFF49, 0x18DFFFF3 do nothing in this firmware; they may have been used by an older build or another node on the bus.
5. 0x105 and 0x515 byte semantics beyond the output bits listed (other agents).
6. Whether 1 tick of 0x1664 is 1 ms (affects all the second-valued timeouts above).
7. What FUN_rom_00ac86 and FUN_rom_00f276 do with the model index (D3 side effects beyond the table pointer).
8. Behaviour of EC with value 0 or very large (no range check; floating-point divide by zero).
