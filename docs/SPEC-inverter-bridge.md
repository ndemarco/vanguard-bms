# Inverter bridge: spec

Status: draft v0.3. Written for an agent picking this up cold.

This is the plan for the layer that sits between the Vanguard packs and a hybrid inverter. It covers only what is not built yet. Pack protocol facts live in [`../CANBUS_PROTOCOL.md`](../CANBUS_PROTOCOL.md), the pack-side service in [`../bridge/README.md`](../bridge/README.md), and the decoder and local web page in [`../app/README.md`](../app/README.md). Do not copy their content here; link to it.

Tags match the protocol document: **[C]** confirmed, **[I]** inferred, **[?]** unknown. Untagged statements come from the owner or a cited document. Do not promote an inference to a fact without evidence. Nothing has yet been checked against a live pack.

## 1. Purpose

Make Vanguard lithium packs (Club Car fleet packs, first target Fi3.8, model 80090401) look like one ordinary lithium BMS to a hybrid inverter, and control when each pack's contactors close.

- Inputs: 1 to N Vanguard packs.
- Output to inverter: Pylontech-style BMS protocol over RS485 first. The inverter also accepts CAN, so Pylontech CAN is a later option.
- Side function: web page (Wi-Fi) and BLE for status and config.
- Safety function: a pre-connect voltage gate, so no pack's contactors close into a bank it is not matched to (section 4).

The bridge does not carry power. It reads, reports, and keeps packs awake.

Out of scope: power switching hardware, pack fusing and busbars, charger control, inverter selection. Degraded operation (one pack missing) is acceptable. The die-cast vehicle module on top of each pack is scrap and is removed; the pack's own connector is the interface.

## 2. BMS findings

- Pinouts and the unresolved 12 V path are in `analysis/hardware_connectors.md`.

- **Each pack has two CAN buses.**  
A bridge only requires CAN11. The current bridge service uses both HAT channels for one pack, so N packs need N CAN11 channels, from more HATs or another CAN interface. [I]

  * Vehicle bus: CAN11 carries current, limits, status, pack and output voltage, SOC, cell and temperature extremes, faults. (11-bit IDs)
  * CAN (CANH/CANL) adds per-cell data and a maintenance channel. (29-bit J1939-style)
  
- Multiple packs cannot coexist on a CAN bus. CAN11 frames have no source address. [I]
- The pack closes its own contactors. The host sends `0x105` at least once per second to permit it. Stopping `0x105` opens them within about 1 s. Contactors close after the BMS runs precharge and voltage checks [C]
- A hardware enable input must be asserted before the pack can be awoken. [C/I]
- Pack data is broadcast. Nothing is requested.
- Voltage is available with contactors open. [C]
- Charge and discharge are separate modes. Changing mode needs STOP then START, which opens the contactors. [C]

## 3. Platform

Raspberry Pi 4 with the Waveshare 2-CH CAN FD HAT (MCP2518FD), Python, SocketCAN. Setup is in the bridge README.

Still to choose:

- RS485 adapter: Isolated USB-to-RS485 with screw terminals. For the inverter's BMS port
- Watchdog: Enable the Pi hardware watchdog.
- More CAN channels: A third and further channel for N packs and for Pylontech CAN.

## 4. Pre-connect voltage gate

No pack receives `START` until its voltage is within `dv_close` of the bank, or of the DC bus for the first pack. The web page's Start button goes through the same check.

- Pack voltage: `0x112` B4-B5. Bank voltage: the closed packs, or inverter register 0x101 for the first pack (6.1).
- `dv_close`: config, per pack pair. Placeholder 1.0 V. Set it from measured wiring resistance and the contactor and fuse ratings; inrush is roughly dV divided by resistance. [I]
- First pack on a dead bus: start, and the pack precharges itself.
- Pack outside tolerance: stays open. Higher than the bank, allow charge and hold discharge at zero. Lower, the reverse. Owner confirms.
- A pack that opens on a fault restarts only after it passes again.
- Manual override: its own config switch, with an on-screen warning.
- Log each decision with the voltages used.

To settle on a bench:

- [?] Which `0x105` mode keeps contactors closed for a bank that both charges and discharges.
- [?] Whether output-side voltage (`0x112` B6-B7) reads the bank while the pack is open. If not, commissioning measures each pack by hand.

## 5. Software architecture

Existing pieces: `vanguard.decoder` turns frames into signals, `vanguard_bridge` is the only transmitter, `vanguard.web` is the local page.

New pieces:

- `bank`: aggregation (5.1) and the connect gate (section 4).
- `safety`: limit derating and fail-safe (5.2).
- `inverter_emulator_*`: pluggable output protocols (section 6).
- `inverter_modbus`: Modbus-RTU master for the monitoring protocol (6.1), read-only by default.
- `ble`, plus the inverter-facing parts of the web page.
- `config`: JSON on disk, schema versioned.

Open design question: the bridge serves exactly one controlling client, and that is the web page. The inverter emulator and the gate also need pack data, and the gate needs `START`. Either a single process owns the one client connection and serves both the web page and the emulator, or the bridge gains read-only observers. Decide before building. The bridge's one-writer safety rule should not change.

### 5.1 Aggregation (packs in parallel on one DC bus)

PackState fields come from the decoder: voltage, current, SOC, SOH, cell min/max, temperature min/max, charge and discharge limits, fault bytes, contactor state. Fields a pack does not report are marked absent, not zero. Current sign and limit direction are unverified [I].

- Voltage: mean of closed packs. Current: sum. SOC: capacity-weighted mean using per-pack capacity from config.
- Charge voltage limit: minimum across closed packs. Per-cell target is in `0x51D` B4-B5 and the limits in `0x512`; pack maximum is 14 times the cell value, about 58.8 V [C].
- Charge and discharge current limits: sum of per-pack limits of closed packs (`0x111`), times a configurable margin.
- Cell and temperature min/max: global across closed packs. Alarms: OR.
- Only packs with closed contactors count toward limits. Packs held open appear in the page but are not reported to the inverter.

### 5.2 Safety

- Stale data (default 3 s): that pack's limits go to zero and a protect flag is raised.
- Any pack fault: zero limits for that pack. If no valid closed pack remains, report zero bank limits.
- Startup: report zero limits until at least one pack is closed and valid.
- A crash must produce silence on RS485, not stale values. Use the software watchdog and the Pi hardware watchdog. The inverter stops on BMS comm loss only if its BMS-error-stop setting is on (register E214, section 6.1) [I]; verify on the bench.
- Pack-side safety (transmit allowlist, STOPPED default, heartbeat watchdog) stays in the bridge service and is not repeated here.

## 6. Inverter-side protocol

Start with Pylontech. The inverter takes Pylontech over RS485 or CAN.

1. Obtain the official Pylontech RS485 protocol document (and the CAN one for later). Record the version in `docs/protocols.md`. Do not reconstruct frames from memory.
2. Implement `inverter_emulator_pylon_rs485` first, then `inverter_emulator_pylon_can`. JK RS485 is optional and later, for monitoring tools.
3. Multi-pack presentation, selectable. Mode A (default): one aggregated virtual pack. Mode B: N module addresses, if the protocol supports a stack.
4. The reported battery type is 14S ternary lithium; the inverter setting is E004 = 13. [I] reading of the owner's note that the BMS reports its type; confirm.

Acceptance: the target inverter shows battery voltage, SOC, and charge and discharge limits from the bridge, and obeys a limit change.

### 6.1 The Modbus monitoring protocol (SRNE table V2.00)

Source: [`docs/MODBUS Monitoring Protocol Register Address Table-V2.00.pdf`](MODBUS%20Monitoring%20Protocol%20Register%20Address%20Table-V2.00.pdf), 15 pages, revision V2.00 of 2024-05-08. The header carries only "Solar Co.,Ltd" with the vendor name removed, so the same table probably ships under other brands. [I] Registers marked invalid in the table (grey) differ per model.

What it is: the inverter's own monitoring and settings interface, with the inverter as the Modbus slave. It does not carry BMS data in, and it does not define the Pylontech or JK frames. The bridge uses it as a Modbus master toward the inverter, never as a way to feed the inverter pack data. Registers 0x112 to 0x11B are read-only (`R`), so they cannot be written. Pack data reaches the inverter only through section 6's BMS protocol.

Bridge uses:

- Read the inverter's DC bus voltage (0x101) for the first-pack gate (section 4).
- Read back what the inverter received from the BMS (0x112 to 0x11B) to prove the bridge works on the bench.
- Read inverter state (0x210 MachineState, 0x200 to 0x207 fault codes) for the status page.
- Optionally read and set battery parameters (below), behind a config switch.

Frame format (Modbus-RTU):

- 9600 baud, 8 data bits, no parity, 1 stop bit. One master, several slaves. The slave address is set in E200 (1 to 254, default 1). Address 0 is broadcast and 0xFF is a universal address that the inverter answers from its real address when connected one to one.
- Function codes 03 (read registers), 06 (write one register) and 10 hex (write several). At most 32 registers per request; more returns error 0x0A. Anything else is invalid.
- Frame: address, function, data, CRC16. The CRC is standard Modbus CRC-16 (initial 0xFFFF, polynomial 0xA001) sent low byte first. The PDF gives three C implementations (bitwise, byte table, word table). Use a tested library, not a copy of the C.
- Read request data: start address (2 bytes), register count (2 bytes). Response: byte count, then registers high byte first.
- Error response: function code plus 0x80, then an error code: 01 illegal command, 02 illegal address, 03 illegal value, 04 operation failure, 05 password error, 06 data frame error, 07 read-only parameter, 08 cannot be changed, 09 password protection, 0A length error, 0B permission denied.
- A 32-bit value spans two registers, low word at the lower address.

Units, as a multiplier on the real value: voltage 10, current 10, power 1, temperature 10 (signed), frequency 100, power factor 1000 (signed). Current may be signed or unsigned per register.

Battery set-voltage registers use a 12 V-equivalent scale: divide the real voltage by 4 for a 48 V system, then multiply by 10. The PDF's example: 57.6 V is 14.4, register value 144. So 58.8 V is 147.

Registers that matter here:

| Address | Name | Notes |
|---|---|---|
| 0x100, 0x101, 0x102, 0x103 | BatSoc, BatVolt, ChargeCurr, DeviceBatTemper | the inverter's own battery measurements. 0x102: above 0 is discharging, below 0 is charging, the opposite of the pack's sign (`CANBUS_PROTOCOL.md`) [C] |
| 0x112 to 0x11B | BatBmsVolt, BatBmsCurr, BatBmsTemp, BatBmsChgLimitVolt, BatBmsChgLimitCurr, BatBmsDchgLimitCurr, BmsAlarmH/L, BmsProtectH/L | what the BMS sent. Scales 0.1 V, 0.1 A, 0.1 degC. Alarm and protect bit meanings are not defined in the PDF. [?] Sign of 0x113 is not stated; check it on the bench |
| 0x210 | MachineState | 0 power-on delay, 1 standby, 2 initialization, 3 soft start, 4 AC operation, 5 inverter, 6 inverter to AC, 7 AC to inverter, 8 battery activation, 9 manual shutdown, 10 fault |
| E215 | BmsCommEnable | 0 off, 1 RS485-BMS, 2 CAN-BMS |
| E21B | Rs485BmsProtocol | range 0 to 30, default 7. [?] The value-to-protocol mapping is not in the PDF. Find it in the inverter manual, or read the register while the front-panel menu changes. Do not assume 7 is Pylontech |
| E214 | BmsEnStopEnable | stop the inverter on BMS communication fault. The safety design depends on it (5.2) |
| E004, E003 | BatTypeSet, BatRateVolt | type 13 is "ternary lithium x14"; rated voltage 48. The PDF default for E004 is 6 |
| E00F, E01D, E01E, E01F, E020 | discharge cutoff SOC, stop-charge SOC, low SOC alarm, SBU switch SOCs | valid under BMS communication |
| E021 | BatDischgMaxCurSet | maximum battery discharge current |
| E005 to E00E | charge and discharge voltage thresholds | 12 V-equivalent scale; the pack's own limits are in `CANBUS_PROTOCOL.md` section 6.3 |
| E200 | Rs485AddSet | slave address |
| F04D to F051 | battery charge and discharge kWh, today and total | read-only statistics |

Peak shaving may be native. E21D MaxLinePower is peak clipping power of the grid, with 50 meaning 500 W. E42A BattForGridPowerEn, E42C ZeroExportPower and the timed charge and discharge registers (E026 to E04D, with their own SOC and voltage stops) are also present. [I] [?] Whether the SPH6548P supports any of them is unknown; verify before designing around them.

Write policy. Any write from the bridge goes through a fixed allowlist of register addresses, as the pack-side bridge does for CAN frames. The allowlist must exclude, at least: the control area (0xDF00 power on/off, 0xDF01 reset, 0xDF02 restore factory setting, 0xDF06 firmware upgrade command), the debug and factory areas (0xDF20 onward, 0xE100 to 0xE145), the online-upgrade entry at 0x0438 to 0x0439, and the grid-connection area (0xE400 onward), since a wrong value there can disconnect or damage the installation. The default is read-only.

## 7. Web and BLE

The local web page already shows live per-pack signals with confidence tags, raw frames, session logs, replay, and the pack controls. Remaining:

- Bank aggregate, gate status per pack, and comms health for the inverter link and each CAN channel.
- Config: pack count, per-pack capacity, `dv_close`, margin, stale timeout, output protocol and mode, Wi-Fi. Wi-Fi STA with AP fallback and an mDNS name.
- Preview of the exact frame sent to the inverter.
- BLE: GATT status and config. JK app compatibility is a stretch goal, only if the JK BLE protocol is documented.
- Today the page is loopback-only with no login, reached by an SSH tunnel. Any network exposure needs authentication first.

## 8. Test plan

1. Unit tests for aggregation, safety, and the voltage gate (stale, fault, mismatch, missing pack, unequal capacity, gate pass and fail).
2. Loopback: emulator output decoded by an independent script that follows the protocol document. Test the Modbus master against a fake slave that checks CRC and the error codes in 6.1.
3. Bench: bridge against the real inverter, no battery connected, with injected pack values. Confirm inverter registers 0x112 to 0x11B show the bridge values and limit changes are obeyed. Confirm the inverter stops when the bridge goes silent.
4. Real packs: first the checks in "Verify on the Pi" in the bridge README, then one pack at a time with fusing in place.

## 9. Phases

1. Pack protocol from firmware: done. Verification on a live pack: pending.
2. Pack-side service, decoder, local web page: built, not yet run against a live pack.
3. Bank layer: aggregation, safety, gate, Pylontech RS485, bank status page.
4. Bench against the inverter.
5. Real packs on the bridge, one at a time.
6. Pylontech CAN, BLE, JK RS485, enclosure.

## 10. Open questions for the owner

- Gate default for packs outside tolerance (section 4), and the `dv_close` value after measurement.
- Usable capacity per pack for SOC weighting. The nameplate says 67.5 Ah, the firmware tables say 73 Ah or more, and the Club Car diagnostic screen says 54 Ah (`CANBUS_PROTOCOL.md` appendix C).
- How many packs, and so how many CAN11 channels (section 2).
- Which structure for one client versus the emulator and the gate (section 5).
- How the wake line is driven for each pack. The bridge drives no GPIO today, so a relay or optocoupler per pack needs its own safety review.
- Does the inverter's peak clipping (E21D) work on the SPH6548P?
- Are the inverter's BMS port and Modbus monitoring port the same RS485 port or two (section 3)? What does E21B = 7 select?
