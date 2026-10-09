# Shared notes: reverse engineering the Vanguard Li-ion battery BMS firmware

Goal: a protocol document precise enough that another agent can write a driver that controls and
interrogates this pack over its two CAN buses. Every claim must say where it came from (function
word-address) and whether it is CONFIRMED (read directly from code), or INFERRED (reasoned).
Never invent units or scaling: derive them from the code (constants, float multipliers, shifts).

## Target
Pack: recovered Vanguard Li-ion battery from a Tempo golf car. NMC, 14 series (14 modules/cell groups of
32 parallel cells each), about 50 V nominal. MCU: Microchip dsPIC33EP128GS806 (16-bit, 24-bit
instruction words, little endian). Hardware: 14-cell monitor chip (NXP MC33771B class, via SPI1, 5-byte
frames with CRC-8 poly 0x2F seed 0x42), 14 NTC thermistors, current sensor on main bus bar,
TWO contactor/solenoid outputs in series with pack positive (must be on for the pack to connect to charger
or load). Two CAN buses: CAN1 (11-bit IDs) and CAN2 (29-bit, J1939 style). Both 500 kbit/s (assumes
16 MHz crystal; application timing: BRP=2, 10 TQ per bit = sync 1 + prop 1 + phase1 4 + phase2 4).
The firmware image is the FULL flash read from the board (bootloader at the top of flash, reset vector
goes to 0x013800; application at lower addresses).

## Files (all read-only for you; write only under your own output path)
SCRATCH=/tmp/claude-1000/-home-nick-projects-nickydoes-vanguard-battery-revival/76d95066-751e-49fd-99d7-30758cfecc79/scratchpad
- $SCRATCH/out2/listing.txt  : full disassembly, "rom:ADDR<TAB>instruction", ADDR = PROGRAM WORD address
- $SCRATCH/out2/all.c        : Ghidra decompilation of all 332 functions (rough: dsPIC quirks, see below)
- $SCRATCH/NOTES.md          : this file
- $SCRATCH/dfp/xc16/support/dsPIC33E/gld/p33EP128GS806.gld : SFR name = address (e.g. C1CTRL1 = 0x480)
- $SCRATCH/dfp/xc16/support/dsPIC33E/h/p33EP128GS806.h : SFR bit-field definitions (use for decoding)
- Hex source: /home/nick/projects/nickydoes/vanguard-battery-revival/DSPIC33EP128GS806.hex
Section output goes to $SCRATCH/sections/<your-name>.md.

## Gotchas
- Ghidra "rom" address space is WORD addressed: address N holds program word N (2 hex-file bytes per
  address unit; each 24-bit instruction takes 2 address units, 4 hex bytes with a phantom byte).
  In the Ghidra Java API use getAddressFactory().getAddress("rom:00a040"); do NOT use
  getAddress(long) (it takes byte offsets, so it halves your address).
- Constant tables read through program-space visibility (PSV): data address 0x8000+X maps to program
  word X/2 in the low 16 bits of each 24-bit word. Python helper (the hex file -> lo16 stream):
  build bytes by hex address, keep only bytes i%4 in (0,1), table at data addr A is at stream offset A-0x8000.
- Data-space (RAM) variable names in all.c look like DAT_ram_1782 / _DAT_ram_1782 (16 bit when leading '_').
  RAM 0x1000-0x1FFF is the application's variable area. SFRs are < 0x1000.
- Floats are IEEE-754 single in W1:W0 (hi word:lo word). Helpers (library): FUN_rom_000754 = float multiply
  (a * b), FUN_rom_000606 = float multiply/add? (check), FUN_rom_00066e = float -> integer,
  FUN_rom_00062c / 0x000a40 = integer -> float, FUN_rom_000628 = compare, etc. Verify by reading the listing.
  0x41200000 = 10.0, 0x3e1c4000 = 0.15234.
- Decompiler often drops byte-vs-word and carry detail: when a scaling matters, read the listing.
- Do NOT run Ghidra on the shared project directories (they are locked/shared). If you need a fresh
  decompile, import into your OWN project dir under $SCRATCH (see $SCRATCH/scripts/Full2.java, DecAt.java:
  analyzeHeadless <projdir> vb -import <hex> -processor "dsPIC33E:LE:24:default" -scriptPath $SCRATCH/scripts -postScript DecAt.java <out> <wordaddr...>).
  Usually all.c + listing.txt are enough.
- Tool restrictions in this environment: avoid `rm`, `mv`, `cp -r`; write files with shell heredocs.

## Already established (CONFIRMED unless marked)
CAN1 (SFR base 0x480, DMA RAM buffers): app init FUN_rom_00de28. Filters: 0 -> SID 0x105, 1 -> 0x515, 2 -> 0x614; masks are zero
 (all standard frames reach software; software sorts by ID). TX buffers 0-7, RX FIFO starts at buffer 8.
 TX helper: caller fills a struct at RAM 0x1782: [1782]=ID (word), [1784]=0, [1786]=4 (std-ID flag), [1788]=DLC,
 [1789..1790]=data[0..7], then calls FUN_rom_00e09a(0) (queue) and FUN_rom_00e06a() (trigger). RAM 0x1664 = free-running ms tick (INFERRED).
 RX: FUN_rom_009284 reads frame (FUN_rom_00e106 -> struct at 0x1792: [1792] ID, [1794], data at 1799..17a0),
 routes 0x105 -> FUN_rom_009196, 0x515 -> FUN_rom_0091f0, (ID&0x7f)==0x14 && (ID&0x780)==0x600 (0x614) -> FUN_rom_00a99a with data copy.
 SDO-ish reply ID 0x594 is built in FUN_rom_00a918 (length byte DLC = n+4). FUN_rom_00a8b2 sends (arg | 0x14)=reply with 8 bytes.
CAN1 TX IDs seen: 0x111 0x112 0x113 0x114 0x120 0x510 0x512 0x513 0x517 0x518 0x519 0x51a 0x51b 0x51c 0x51d 0x594.
  Periodic sender chains are in FUN_rom_009fd0 (0x111 every 50 ms, 0x517.., 0x51x every 1000 ms etc.) and FUN_rom_00a040 (0x51a, 0x120, 0x113, 0x114, 0x112, 0x510, 0x512, 0x513).
CAN2 (SFR base 0x780, DMA RAM at 0x2200+16*n): init FUN_rom_00ecae. Accepts ALL extended frames in hardware.
 TX helper: struct at RAM 0x17a2: [17a2]=ID low 16, [17a4]=ID high 13 bits, [17a6]=3 (ext flag), [17a8]=DLC, [17a9..17b0]=data[0..7];
 FUN_rom_00edd8(1, &DAT_ram_17a2) queues it.
 RX: FUN_rom_009368 -> FUN_rom_00ee44 -> struct at 0x17b2: [17b2]=ID low, [17b4]=ID high, [17b8]=DLC?, [17b9..17c0]=data[0..7].
 RX IDs matched: 0x0C1BF349 (0xf349/0x0c1b), 0x0C1BFF49, 0x18DFFFF3, 0x0CFF00F3 -> FUN_rom_008c22 (command interpreter on data[0]),
 0x0CFFBC49 -> FUN_rom_009178.
CAN2 TX IDs (all source address 0xF3): 0CF090F3 0CF093F3 0CF094F3 0CF095F3 0CF096F3 0CF10AF3 0CFF02F3 0CFF06F3 0CFF20F3 0CFF21F3 0CFF31F3
 0CFF32F3 0CFF33F3 0CFF34F3 0CFF35F3 0CFF3EF3 18FF03F3 18FF04F3 18FF05F3 18FF06F3 18FF07F3 18FF08F3 18FF30F3 18FF31F3 18FF32F3 18FF50F3 18FF51F3 18FF52F3
Command 0xC3 via 0CFF00F3: data[2]=balance start spread mV, data[3]=stop spread mV, data[4..5]=balance floor mV (LE), data[6..7]=bitwise inverse of data[4..5] (checksum). (FUN_rom_008c22)
Parameter tables: six variants at PSV data addr 0xb0b4,0xb1f0,0xb32c,0xb468,0xb5a4,0xb6e0 (316 bytes each, 32-bit entries); RAM 0x1d00 holds the chosen table pointer (set by FUN_rom_010c76(index)).
 Offsets (bytes) 0x44..0x5c: max-cell-voltage thresholds in mV (4250,4200,4200,4190,4150,4050,3800), 0x60..0x74 low-cell thresholds (3000,3000,3050,2500,2250,2000).
Cell/temp data: RAM 0x1a1a[14] = cell mV (word each, index 0 = cell 1); 0x1a36 = min cell mV, 0x1a38 = max, 0x1a3c = max-min, 0x1a3e/0x1a3f = 1-based index of min/max cell.
 Temperatures: byte values = index into lookup table at PSV 0xbd5e, index = degC + 40 (INFERRED); 0x1981..0x198e raw temp bytes, 0x1990 = max temp byte, 0x198f = min temp byte.
Balancing: FUN_rom_010972 (mask of cells) -> FUN_rom_00f9da (writes monitor registers 0x0C..0x19 with 0x200/0) every 35 s; 0x1cb9 = balancing active, 0x1cb2 = mask, 0x1cb4 floor (default table[0x5c]=3800 mV), 0x1cb6 start spread (default 40), 0x1cfc stop spread (default 10).
Monitor driver (SPI1): FUN_rom_00fd0a(reg, value) write; FUN_rom_00fd44/00fd96(reg, &out) read.
Other RAM: 0x1676,0x1678,0x1679 state bytes; 0x1680/1682/1684/1686 and 0x167c/167e are fault/warning bitmask words (INFERRED); status bytes 0x1623..0x162a go out in CAN1 0x120.
Port D latch 0x0e34, port C latch 0x0e24, port B 0x0e14, port A 0x0e04 (LATA=0xe04, LATB=0xe14, LATC=0xe24, LATD=0xe34, TRIS 0xe00/e10/e20/e30).
Contactor (solenoid) outputs and current sensing: NOT yet traced.
