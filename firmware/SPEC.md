# Vanguard ESP32 pack controller: specification

> ## START HERE (for any future firmware session)
>
> This file is the design for the ESP32-C6 firmware that will sit on each Vanguard pack, and nothing under
> `firmware/` has been implemented yet. Pick up from here.
>
> 1. Read `CLAUDE.md`, then this whole spec, then `CANBUS_PROTOCOL.md` sections 3, 4 and 6 and the safety table in `bridge/README.md`.
> 2. Start at **phase 0** (section 14.1). Do not skip a phase gate. Phases 0-2 need no owner answers.
> 3. Python is the reference: `bridge/src/vanguard_bridge/core.py` and `txgate.py` define the safety behaviour the C++
>    must reproduce; `app/src/vanguard/frames.py` is the only place signal scalings and `[C]/[I]/[?]` tags live.
> 4. **Not approved yet, compiled off by default:** `VGB_ALLOW_NO_ENABLE_KEEPALIVE` (D1 = 0x01) and
>    `VGB_ENABLE_CAN2_TX`. Do not turn either on, or weaken the allowlist, evidence rule or fail-closed behaviour,
>    without the owner's explicit instruction (section 16 lists what is waiting on them).
> 5. Nothing in this spec has been verified on hardware. Bench experiments in 14.3 decide the open points
>    (mode change cycling the contactors, a second pack joining a live bus, which pin is the enable, limit
>    directions). Record results in `CANBUS_PROTOCOL.md` with a tag, then update this spec.
> 6. Pylon field layouts are marked `[verify]`: get the Pylontech RS485 protocol document and a capture from the
>    owner's inverter before relying on them.
> 7. Keep this spec and the code in agreement. Add a dated line to the status log below when you finish a phase.
>
> **Status log**
> * 2026-10-15: spec drafted, no code. Next: phase 0.

Status: draft 1, 2026-10-15. Written so that someone with the repo, a PlatformIO install and the hardware below
can implement it without the owner present. Where this spec depends on something nobody has measured, it says
so and gives a default; defaults are listed in section 16. `CANBUS_PROTOCOL.md` stays the source of truth for the
pack; this document is the source of truth for the controller.

Confidence tags carry over from the protocol document: **[C]** confirmed (bench or code), **[I]** inferred,
**[?]** unknown. Anything the controller shows (web, MQTT, link, Pylon) keeps its tag in the data model and
never presents an [I] or [?] value as confirmed. Pylon cannot carry tags; see 9.6.

## 1. What is being built

One ESP32-C6 (Seeed XIAO) is bolted to each Vanguard 14S pack. It keeps that pack awake, decides when the pack
may close its contactors, decodes everything the pack broadcasts, and exposes it. One of the two controllers
(the **master**) also talks to a solar inverter in Pylon RS485 and presents both packs to it as **one
battery**, the way a JK-BMS does. Each controller serves its own web page and publishes to MQTT for Home
Assistant, so each pack can be watched and operated on its own.

```
 pack A  <-CAN1/CAN2->  ESP32 (master) <-RS485 "link"-> ESP32 (slave)  <-CAN1/CAN2->  pack B
                           |  RS485 "inverter" (Pylon)
                        inverter           Wi-Fi: web + MQTT on both ESP32s
 B+/B- of both packs: one DC bus to the inverter, through the packs' own contactors.
```

Goals: ESS use (always awake, bus-tied, hands-off); pack safety stays fail-closed; per-pack visibility;
inverter sees one battery with limits that protect the cells.

Non-goals for v1: more than two packs in practice (the link protocol allows up to 7 slaves, nothing is
tested beyond two); master failover; cloud features; firmware patching of the pack (section 15 only prepares
for it); charging from anything other than the inverter/DC bus.

## 2. Decisions already made

| # | Decision | Source |
|---|---|---|
| D1 | Target: XIAO ESP32-C6, PlatformIO, VS Code | owner |
| D2 | Two CAN transceivers (pack CAN1 and CAN2) and two RS485 drivers per board | owner |
| D3 | Both CAN buses are bidirectional in hardware | owner |
| D4 | One pack = one controller. The master aggregates; there is no multi-pack logic in a pack controller | owner (earlier handoff) |
| D5 | Inverter protocol: Pylon over RS485 | owner |
| D6 | Web page: status, module balance view, config. MQTT with Home Assistant discovery | owner |
| D7 | The packs are kept awake permanently (a `0x105` stream), it is an ESS, not a cart | owner |
| D8 | A mechanical switch per pack interrupts a low-voltage enable signal (DEN and/or CEN, which one is a bench result) and thereby disables the pack | owner |
| D9 | Pack firmware patching (no contactor cycling on mode change) comes after comms are proven | owner |

## 3. Hardware

### 3.1 Pin map (proposed, confirm against the Seeed XIAO ESP32-C6 pinout and the owner's board)

| XIAO pin | GPIO | Function | Notes |
|---|---|---|---|
| D0 | 0 | CAN-A TX (to transceiver D) | pack CAN1 (11-bit, 500 kbit/s) |
| D1 | 1 | CAN-A RX (transceiver R) | |
| D2 | 2 | CAN-B TX | pack CAN2 (29-bit, 500 kbit/s) |
| D3 | 21 | CAN-B RX | |
| D4 | 22 | RS485-L TX (link, inter-pack) | UART1 via GPIO matrix |
| D5 | 23 | RS485-L RX | |
| D6 | 16 | RS485-I TX (inverter, Pylon) | UART0 via GPIO matrix. The USB console is USB-Serial-JTAG, so UART0 is free |
| D7 | 17 | RS485-I RX | |
| D8 | 19 | SWITCH_SENSE (input, pull-up) | reads the pack's mechanical switch, see 3.3 |
| D9 | 20 | ENABLE_DRIVE (optional output) | drives the pack enable line through an isolating stage, see 3.3 |
| D10 | 18 | spare (RS485 DE for one port if a driver turns out not to be auto-direction) | |
| onboard | 15 | user LED (active low) | status blink code |
| onboard | 9 | BOOT button | not used by firmware |

Each CAN transceiver has its own TX/RX pair so that both `dual` and `visit` modes (8.4) work without hardware changes.
All pins live in one header (`firmware/src/board_pins.h`) so a different wiring is a one-file change.

Assumptions to verify in phase 0 (section 14): the RS485 drivers switch direction automatically (no DE/RE
pins; the stated pin budget leaves none). If one does need DE, use D10 for it and say so in the header.
Avoid the C6 strapping pins (GPIO 4, 5, 8, 9, 15 are not used for external functions here, except the LED).

### 3.2 Bus and power requirements

* **CAN TXD pull-up.** Put a 10 k pull-up to 3.3 V on each transceiver D input (and use 3.3 V logic on the
  SN65HVD230; it is a 3.3 V part). While the ESP32 is in reset or booting, TXD must read recessive, so a
  resetting controller can never hold a bus dominant. The pack then simply sees silence.
* **Termination.** Pack CAN1 and CAN2 are point-to-point from the controller to the pack. 120 ohm at each end
  if the pack side is not terminated (unknown [?]; measure resistance across CANH/CANL with power off).
  The SN65HVD230 modules often carry a 120 ohm jumper; leave it fitted on the controller end.
* **RS485** link: one twisted pair between the two controllers, 120 ohm at both ends, bias resistors at one
  end only (master). The inverter port follows the inverter's manual (many inverters terminate and bias
  themselves).
* **Ground.** The packs' B- are joined by the DC bus, the CAN transceivers and RS485 drivers are not
  isolated. Keep one deliberate ground reference: both controllers and both CAN grounds on the same 0 V as
  the packs' signal ground. Do not connect the inverter RS485 ground without checking whether the inverter
  port is isolated; use an isolated RS485 module for the inverter port if there is any doubt.
* **Power.** Each controller needs a supply that stays up when its pack is off, switched off, or asleep
  (a shared 5 V rail from a small DC-DC off the DC bus, or a wall supply). Do not power from the pack's
  `12vout`: it exists only while the pack is on [C] and the master must keep serving the other pack when its
  own pack is off. Brown-out must reset cleanly (enable the brown-out detector).

### 3.3 Switch and enable signal

The owner's switch interrupts the pack's low-voltage enable (DEN or CEN, to be settled on the bench, see 14.3).
Requirements that do not depend on which pin it is:

* The switch is a **hardware** disable. Opening it must disable the pack with no firmware involvement. The
  controller only observes it (D8) so it can leave the aggregate gracefully (stop the join, drop out of the
  Pylon sums) a few tens of ms before or as the contactors open.
* Use a two-pole switch, or one pole plus a sense network: pole 1 interrupts the pack enable line, pole 2
  pulls D8 low/high for the controller. D8 is never in the pack's signal path.
* The pack's enable inputs are referenced to its own ground and are low-voltage but not 3.3 V logic [?]
  (levels not measured). D9, if used, goes through an opto-isolator or an open-collector transistor
  stage; no ESP32 pin is connected to the pack enable pins directly. D9 is **optional and off by default**
  in v1 (`enable_drive_mode = "off"`): v1 relies on the physical switch only.
* Opening the enable input does two things in the pack: contactors open **and** the pack goes to sleep
  (sleep reason `0x16`), and it latches `0x120` B2 bit 3 (needs a clear-faults, `0x105` D1 bit 1, after the
  switch is closed again) [C from code, not bench-tested with the real connector]. Booting with the enable
  already released puts the pack into a no-wake sleep until reset [C from code]. The controller therefore
  never concludes "pack lost" from a silent bus while the switch input says open; it reports
  `switch_open`. After the switch closes, the controller expects a wake: pack traffic resumes, then CLEAR is
  sent once. If the pack does not wake (no-wake sleep), after `wake_retry_s` the controller sets a `NEEDS_RESET`
  alarm and tells the owner (web/MQTT); it does not try to reset the pack.

## 4. Software platform

* **Language/tooling:** C++17, PlatformIO, VS Code. Framework: ESP-IDF 5.5 or later (needed for the newer TWAI
  driver, see below). If the stock `espressif32` platform in PlatformIO does not offer IDF 5.5+ for the XIAO
  C6, use the community `pioarduino` platform with `framework = espidf`. Arduino is not used.
* **Dual TWAI is preferred, not required.** The ESP32-C6 datasheet lists two TWAI controllers. The classic
  ESP-IDF `twai` driver exposes only one; multi-controller support arrived with the new TWAI driver
  (`esp_driver_twai`, node-based API). Phase 0 tests whether two nodes run at once at 500 kbit/s with
  independent RX/TX GPIOs. If yes: `can2_mode = dual`. If not: `can2_mode = visit` (section 8.4), which needs
  no hardware change. The firmware supports both; the HAL hides which one is in use.
* **Only classic CAN 2.0** is needed. 11-bit on CAN-A, 29-bit on CAN-B.
* **Repo layout** (all new, next to `app/` and `bridge/`):

```
firmware/
  SPEC.md                   this file
  platformio.ini            envs: xiao_c6 (target), native (host tests)
  lib/core/                 portable C++, NO ESP-IDF includes: state machine, txgate, decoder, aggregator,
                            link protocol, pylon codec, config model, JSON writer
  lib/core/generated/       signal tables generated from app/src/vanguard/frames.py (tools/gen_signals.py)
  src/                      ESP-IDF glue: main.cpp, board_pins.h, can_hal, uart_hal, wifi, web, mqtt, ota, storage
  data/                     web UI (static, gzipped at build), served from LittleFS; no external hosts
  test/                     Unity tests for the native env
  tools/                    gen_signals.py, txgate_vectors.py, pylon_master.py (inverter emulator for the Pi)
```

* **Portable core.** `lib/core` takes time as an integer microsecond argument, performs no allocation after
  startup, uses fixed-size tables, and talks to hardware only through small interfaces (`ICanTx`, `IClock`,
  `IGpio`). This mirrors `bridge/src/vanguard_bridge/core.py` + `txgate.py`; the Python versions are the
  reference and the C++ must match them line for line in behaviour.
* **Generated tables.** `tools/gen_signals.py` imports `vanguard.frames` and emits `signals.h` (id, bus,
  byte/bit position, scale, unit, confidence tag). Never hand-copy a scaling into C++.
* **Concurrency (single-core RISC-V, FreeRTOS).**
  * `safety` task (highest app priority): consumes CAN-A frames from an ISR-fed queue, runs the pack state
    machine every 10 ms, sends the keep-alive, owns TXD of CAN-A. Task watchdog 2 s (panic = reset).
  * `can2` task: receive-only in v1 builds (section 8.3).
  * `link` task (UART1), `pylon` task (UART0), `net` task group (Wi-Fi, HTTP, MQTT) at lower priority.
  * Shared state is one `Snapshot` struct updated by the safety/can tasks under a short mutex-free
    seqlock; web, MQTT, link and Pylon read snapshots and never block the safety task.
  * Requirement: keep-alive transmit jitter under 250 ms worst case (nominal 100 ms, pack timeout about
    1 s [C]) while Wi-Fi, HTTP, MQTT and OTA are all active. Measure it in phase 1 (14.1) and fail the phase
    if exceeded. The ESP-IDF Wi-Fi task can preempt user tasks; if jitter is out of budget, move the
    keep-alive onto an `esp_timer` ISR-dispatch callback.
* **Reset behaviour.** A reset stops the keep-alive; the pack opens its contactors after about 1 s [C]. A
  boot takes a few hundred ms, so a fast reset may resume before the pack opens. v1 policy: boot into
  STOPPED, require 250 ms of fresh pack frames, then proceed through the normal policy loop (so a fast reset
  can resume the same state). Do not add a special "assume still closed" path.

## 5. Pack controller

### 5.1 Inputs the controller maintains per pack

From CAN-A (always): `0x111` current/limits, `0x112` voltage + contactor drive bits, `0x113` SOC/SOH/state,
`0x114` temperatures and max/min cell, `0x120` fault bytes, and the 1 Hz counters. With keep-alive D1 bit 0:
`0x510` serial, `0x512` limits, `0x513` capacity, `0x51D` target voltage. From CAN-B (when connected):
cells 1-14 (`0x18FF03F3`..`06`), temperatures 1-14 (`0x0CFF01F3`, `0x0CFF02F3`), mode (`0x0CFF20F3` B0),
fault/warning words (`0x18FF08F3`), balance mask (`0x0CF093F3`), SOH and learned capacity (`0x0CFF33F3`).
Do not decode `0x0CF094F3` bytes 0-3 (firmware defect). Every value stores `{value, raw, tag, age_ms}`;
age saturates, and a value older than its stale limit (5x period, min 500 ms) is reported `stale`.

### 5.2 States

| State | Pack | What the controller does |
|---|---|---|
| `BOOT` | unknown | receive and ACK only (TWAI normal mode, nothing transmitted) until pack frames are seen |
| `ASLEEP` | no traffic | nothing seen for `asleep_after_ms` (default 3000). If switch closed and policy wants awake: send one wake `0x105` (section 5.4). Retry every `wake_retry_s`, rate limited |
| `STOPPED` | awake, no keep-alive from us | pack traffic seen; policy says off. Transmits nothing |
| `HOLD` | awake, contactors open | keep-alive every 100 ms with enable bit **clear** (D1 = 0x01). Requires the allowlist addition in 5.3; until the owner approves it, `HOLD` is not reachable and the controller goes `STOPPED -> JOIN` directly when it wants the pack on, and `STOPPED` when it does not |
| `JOIN` | contactors closing | keep-alive with enable (D0 per `mode_policy`, D1 = 0x11). Wait up to `join_timeout_ms` (default 5000) for `0x112` B0 bit 5 and B1 bit 5 both set and the bus voltage plausible. Timeout or fault -> `FAULT` |
| `ONLINE` | contactors closed | keep-alive continues. Leave on: policy off, switch open, fault, link loss, command |
| `LEAVE` | opening | stop enable (go `HOLD` if available, else stop `0x105`); wait for contactor bits to clear (about 0.3 s) |
| `FAULT` | any | latched; transmit nothing except a CLEAR per 5.5 once the cause has gone and `fault_retry_s` elapsed; counts retries, gives up after `fault_max_retries` and waits for an owner command |
| `LOCKOUT` | any | refusal of the txgate, TX error, wiring-swap evidence, internal error. Nothing is sent until a reboot or an explicit owner `reset_lockout` command |

Every error, refusal, link loss or watchdog expiry leads to `STOPPED`, `FAULT` or `LOCKOUT`; no path goes
toward `ONLINE` except the policy loop. Power-up state is `BOOT`.

### 5.3 Transmit allowlist (`txgate`)

Same as `bridge/src/vanguard_bridge/txgate.py`, evaluated on the exact 8 data bytes immediately before the
driver call, independent of the state machine, with a second copy of the check inside the CAN HAL
(`can_hal.cpp`) so that no code path reaches `twai_transmit` without both:

* CAN-A, standard ID, DLC 8, no RTR:
  * `0x105`: D0 in {0x00, 0x01}, D1 in {0x11, 0x13}, D2..D7 = 0. **Pending owner approval:** D1 = 0x01
    ("awake, no enable") and D1 = 0x03 (with clear). Implement it behind `#define VGB_ALLOW_NO_ENABLE_KEEPALIVE`,
    default 0; with 0 the gate rejects it. The owner flips the define after reviewing. It is a safety-rule
    change: `txgate.py`, `netcheck.CAN1_U32_RULES`, the bridge README table and the tests change together.
  * `0x515`: D0 in {0x10, 0x30}, D1..D7 = 0 (sleep). The controller never uses it by itself (D7).
* Never: `0x614`, `0x515` D0 = 0x20, `0x105` D0 bits 2/4/6, D1 bit 2, any extended ID on CAN-A.
* CAN-B: see 8.3. Default allowlist is empty.
* A refused frame: TX path stops (`twai` node disabled, queue flushed), state `LOCKOUT`, event logged.
* TX failure (bus-off, queue full, driver error): same. Bus-off recovery is only attempted from
  `STOPPED`/`ASLEEP`, never while `ONLINE`.
* **Evidence rule:** no transmit unless a pack frame (ID in the pack's CAN-A set) arrived within 250 ms,
  except the single wake frame in `ASLEEP` (which by definition has no evidence; rate limited to one per
  `wake_retry_s`, default 10 s, and only when the switch is closed).
* **Wiring-swap evidence:** a frame that only the pack sends seen on CAN-B, or an extended-ID pack frame on
  CAN-A: `LOCKOUT`.

### 5.4 Waking and staying awake

The pack wakes on any CAN-A activity, but only if SOC > 4.9 % and the cells are healthy, and on an edge of its
enable input [C from code]. It sleeps about 5 minutes after the last `0x105` [C]. ESS policy:

* Normal operation: a `0x105` every 100 ms for as long as the switch is closed and the controller is not
  `LOCKOUT`. In `STOPPED` this is deliberately not sent (stopped means silent). With the 5.3 addition the
  resting state is `HOLD` and the pack never sleeps; without it, `STOPPED` lets the pack sleep after 5 min,
  and a wake frame brings it back. This is why the owner is asked to approve D1 = 0x01.
* Wake frame: one `0x105` with D0 = 0x00, D1 = 0x11 (the only variant allowed today). That frame enables the
  pack, so waking = joining unless `HOLD` exists. In that case the controller treats a woken pack as
  `JOIN` immediately and accepts it only if the join rule (5.6) already permits it; otherwise it
  goes `LEAVE` right after the first frame. Document this prominently in the web UI as "waking a pack closes it".

### 5.5 Faults

Fault bytes `0x120` B0..B7 are decoded with the table in protocol section 6.4. Controller classes:

* `BLOCKING`: any set bit in the `0x120` bytes except the non-blocking warnings (B1 bits 4 and 6, B5 bit 5;
  protocol section 6.4). The pack leaves `ONLINE` by itself on these.
* `CLEARABLE`: latched faults whose cause is gone. A CLEAR (D1 = 0x13 once, 100 ms spacing, never sooner)
  is sent in `FAULT` after the cause has been absent for `clear_settle_ms` (default 5000), at most
  `fault_max_retries` (default 3) times per hour; the same fault returning right away counts as a retry.
  Never CLEAR over-voltage / over-temperature / over-current / weld faults automatically
  (`auto_clear_classes` in config; default only host-timeout `0x1624` bit 2, the enable latch B2 bit 3
  after the switch closes, and the contactor-mismatch faults). The rest need an owner command.
* `PERMANENT`: `0x1686` bit 15 style events are shown and need an owner; never auto-cleared.

### 5.6 Join policy (runs on the master for both packs; a standalone controller runs it for itself)

A pack may leave `STOPPED`/`HOLD` for `JOIN` only when all of the following hold:

1. switch closed, not `LOCKOUT`, no `BLOCKING` fault, no stale critical data;
2. every temperature inside the window the pack itself uses (-20..70 degC discharge, -11..60 degC charge,
   [C] for variant 0) with `join_temp_margin` (default 2 degC), min cell >= `join_min_cell_mv`
   (default 3200), max cell <= `join_max_cell_mv` (default 4100);
3. **bus rule:** if no other pack is `ONLINE` the join is allowed. Otherwise |V_pack - V_bus| <= `join_dv_mv`
   (default 500 mV) where V_bus is the voltage of an `ONLINE` pack; and the other pack's current is below
   `join_max_bus_current_a` (default 10 A), so a join never happens in the middle of a transient;
4. **one at a time:** the master owns a single `transition` token; only the holder may be in `JOIN` or
   `LEAVE`, and a token is released on completion or after `transition_timeout_ms` (default 8000);
5. master link alive (slave) / own policy tick alive (master).

Whether a second pack can close onto a live bus at all is **[?]**: the pack's contactor sequence checks V_A
against 2 V when open (`CANBUS_PROTOCOL.md` 6.2). Experiment 14.3.B decides between "join at small dV" and
"ordered start-up: both packs close within the same inverter-off window". Do not write the join rule beyond
rule 3 above until that experiment is done; implement `join_dv_mv` as a plain number the owner can tune.

### 5.7 Mode policy (`0x105` D0)

Changing D0 cycles the contactors (idle, open about 0.3 s, then the full close sequence) [C from code, not
bench-tested]. It is **forbidden** to change D0 while `ONLINE`. Config `mode_policy`:

* `discharge_hold` (v1 default): D0 = 0x00 always. Charge current is accepted as regeneration; no pack-side
  balancing or charge termination. The aggregator must therefore taper charge itself (section 9).
* `charge_hold`: D0 = 0x01 always. Balancing and the pack's termination logic run; behaviour under
  discharge is **[?]** and gated on experiment 14.3.A. Do not ship as default.
* `balance_session`: `discharge_hold`, plus scheduled sessions (section 12) where one pack at a time
  leaves the bus, rejoins in charge mode, balances, then leaves again and rejoins in discharge mode. Off by default.

When the pack firmware is patched (section 15), a single mode policy replaces this.

## 6. Inter-pack link (RS485-L)

Master/slave, polled by the master, so there are never two talkers. 115200 8N1, half-duplex with
auto-direction drivers. Role is `config.role` (`master` or `slave`, default `master`; set once per device).
A master that hears another master's POLL frame sets `LINK_DUP_MASTER`, both packs behave as if the link were
lost, and the web page says so.

### 6.1 Frame format (all multi-byte fields little-endian)

```
 0  0xA5          start
 1  0x5A          start
 2  ver           = 1
 3  dst           0 = master, 1..7 = slave, 0xFF = broadcast (master only)
 4  src
 5  type          see below
 6  seq           master increments per POLL; replies echo it
 7  len           payload length (0..64)
 8..  payload
 n  crc16 lo, hi  CRC-16/MODBUS over bytes 2..(8+len-1)
```

Receiver resynchronises on a gap of 5 ms or more, or on `A5 5A`. A frame with a bad CRC, wrong version,
wrong length or unexpected source is dropped silently and counted. The master waits `reply_timeout_ms`
(default 40 ms) for a reply; a slave replies within 15 ms of the end of a POLL addressed to it.

### 6.2 Messages

| type | Direction | Name | Payload |
|---|---|---|---|
| 0x01 | M -> S | `POLL` | 16 bytes, below |
| 0x81 | S -> M | `STATE` | 32 bytes, below |
| 0x02 | M -> S | `DETAIL_REQ` | 1 byte: 1 = cells+temps |
| 0x82 | S -> M | `DETAIL` | 45 bytes, below |
| 0x7F | S -> M | `NAK` | 2 bytes: type, code (1 = bad payload, 2 = not now, 3 = unsupported) |

`POLL` payload (master decisions for that slave):

| Off | W | Field |
|---|---|---|
| 0 | 1 | flags: bit0 `may_join`, bit1 `want_online` (master's wish; the slave still applies its own checks), bit2 `clear_faults` (one-shot, slave rate limits), bit3 `balance_grant`, bit4 `sleep_ok` (reserved, ignored in v1) |
| 1 | 1 | `mode_request`: 0 discharge_hold, 1 charge_hold |
| 2 | 2 | `bus_mv_10` bus voltage, 10 mV/count, 0xFFFF unknown |
| 4 | 4 | `master_uptime_ms` (for event correlation) |
| 8 | 2 | `join_dv_mv` the threshold the master is using |
| 10 | 6 | reserved, zero |

`STATE` payload (slave state; also used for the master's own pack by the aggregator, so both packs go through
one code path):

| Off | W | Field |
|---|---|---|
| 0 | 1 | controller state (section 5.2 enum) |
| 1 | 1 | flags: bit0 switch_closed, bit1 pack_traffic, bit2 contactors_closed (`0x112` B0 bit 5 and B1 bit 5), bit3 blocking fault, bit4 warning, bit5 balancing, bit6 can2_present, bit7 ready_to_join |
| 2 | 1 | pack mode: CAN-B mode byte if present else `0x113` B0 bit 0 mapped to 2/3; 0xFF unknown |
| 3 | 1 | data age, 10 ms/count, saturating at 255 |
| 4 | 8 | `0x120` bytes B0..B7 |
| 12 | 4 | pack current, mA, signed, positive = into the pack |
| 16 | 2 | pack voltage, 10 mV |
| 18 | 1 | SOC, 0.5 %/count |
| 19 | 1 | SOH, 0.5 %/count [I] |
| 20 | 2 | limit A (`0x111` B3-B4), 0.1 A [I direction: discharge] |
| 22 | 2 | limit B (`0x111` B5-B6), 0.1 A [I direction: charge] |
| 24 | 1 | hottest, degC + 40 |
| 25 | 1 | coolest, degC + 40 |
| 26 | 2 | max cell mV |
| 28 | 2 | min cell mV |
| 30 | 2 | capacity, Ah, 0 = unknown |

`DETAIL` payload: 14 x u16 cell mV (0xFFFF none), 14 x u8 temperature (degC + 40, 0 invalid), u16 balance
mask (bit n = cell n+1, [I] from `0x0CF093F3`), u8 CAN-B data age (100 ms/count). Total 45 bytes.

### 6.3 Timing and fail-closed rules

* Master cycle: for each slave, `POLL` every 200 ms; `DETAIL_REQ` every 1 s. A POLL reply carries a fresh `STATE`.
* A slave that has not received a valid POLL for `link_timeout_ms` (default 1000) treats the link as lost:
  `ONLINE -> LEAVE`, it stays out until POLLs resume **and** `may_join` is set again. Pack opens within
  about 1 s of keep-alive loss anyway [C]; do not wait for it.
* The master treats a slave with 3 missed replies (about 600 ms) as `offline`: it drops out of every sum and
  of Pylon at once and keeps retrying quietly.
* A slave never joins because the link is quiet. Rejoin always requires an explicit `may_join`.

## 7. Aggregation (master)

Online packs = controller state `ONLINE` and fresh `STATE` (age under 1 s). All values use the tags of their inputs.

| Composite quantity | Rule |
|---|---|
| Voltage | mean of online pack voltages (packs are on one bus, so they agree within the join dV) |
| Current | sum |
| SOC | capacity-weighted mean (capacity from `0x513`, else `config.pack_capacity_ah`) |
| SOH | min |
| Hottest/coolest | max/min over packs |
| Max/min cell | max/min over packs, with pack id and cell index when CAN-B data exists |
| Alarms/warnings | OR of per-pack decoded fault classes (section 9.5) |
| Online count | number of online packs, published to MQTT and Pylon (as module count) |
| CVL | `min(config.cvl_v, cell-protective CVL)`; cell-protective CVL = mean pack voltage + 14 x (`cell_ceiling_mv` - max cell over online packs), never below the pack's present voltage - `cvl_floor_margin_v` (default 1 V) |
| DVL | `config.dvl_v` (default 14 x 3.00 V = 42.0 V) |
| CCL | sum over online packs of `min(pack_limit_charge, config.max_charge_a_per_pack)` x derate_charge |
| DCL | sum over online packs of `min(pack_limit_discharge, config.max_discharge_a_per_pack)` x derate_discharge |
| Charge/discharge enable | CCL/DCL non-zero and no blocking alarm and at least one pack online |

Pack limits come from `0x111` B3-B6; which of the two is charge and which is discharge is **[I]**, confirmed
only by the owner's bench charge. Until the owner marks it `[C]` in `frames.py`, the master uses the more
conservative of the two for both directions and shows `limits_direction_unconfirmed` in the status. (Drop this
rule once the tag in `frames.py` becomes `[C]`.)

Derating (all configurable, defaults listed):

* `derate_charge` = taper by max cell: 1.0 below `taper_start_mv` (4000), linear to 0 at `taper_end_mv` (4120);
  also 0 when any online pack's hottest sensor is above 55 degC or coolest below 0 degC, with a linear ramp
  over 5 degC before each limit. The pack itself cuts at 60 / -11 degC [C for variant 0].
* `derate_discharge` = taper by min cell: 1.0 above 3300 mV, linear to 0 at 3050 mV; the pack warns at 3000 and
  trips at 2500 [C]. Temperature ramp as above against 65 / -15 degC.
* If a pack is `ONLINE` but its data is stale, its contribution to CCL/DCL is 0, not its last value.
* Rate limit: CVL/CCL/DCL change at most `limit_slew_per_s` (default 10 % of nominal per second) upward;
  downward changes apply immediately.

## 8. Pack bus handling details

### 8.1 CAN-A (pack CAN1)

Normal mode 500 kbit/s, standard IDs, hardware acceptance filter set to the pack's ID list (`0x111`..`0x114`,
`0x120`, `0x510`, `0x512`, `0x513`, `0x517`..`0x51D`, `0x594`). Listen-only is used only before the first
traffic if the owner wants zero ACK influence (config `ack_before_tx`, default true: ACK from the start, so
packs stop repeating their first frame [C, protocol Appendix D]). ISR pushes frames with a microsecond timestamp
into a 64-entry queue; the safety task drains it every 10 ms.

### 8.2 Wiring-swap and sanity checks

CAN-A frames with extended IDs, or CAN-B frames with an ID in the CAN1 set, indicate swapped transceivers:
`LOCKOUT` (section 5.3). A pack that reports model code different from `config.expected_model` (default any)
raises an alarm and does not join.

### 8.3 CAN-B (pack CAN2)

* Hardware: bidirectional (D3). Firmware v1: **receive only**. The TX path exists in the HAL but its allowlist
  is empty, and the build flag `VGB_ENABLE_CAN2_TX` is 0. With the flag 0, the CAN-B node is created in
  listen-only mode, so even a bug in firmware cannot write the maintenance channel.
* Why: any frame on `0x0CFF00F3`, including a bad one, drops the pack into service mode and opens the contactors
  until command `0x00` [C]. CAN2 writes are for commissioning, not for operation.
* Planned (phase 6, not before the flag is approved): a `maintenance` mode that is only reachable when the
  pack is `STOPPED`/`HOLD`-off-bus **and** the switch is closed **and** an authenticated owner command was
  received within the last 60 s. Per-command allowlist, one command per 100 ms, each followed by `0x00`
  (leave service mode) and a verification read:
  * allowed in v1 of maintenance: `0x00`, `0xC3` (balance thresholds), `0xC4` (cell charge limit, clamped 3000..4245 by the
    pack), `0xC5` (precharge threshold, clamped to 50..90 % by the controller).
  * never from firmware: `0xC6`, `0xC7` (erases the event log), `0xD3` (changes the variant), `0xDC`/`0xDE`,
    `0xEC` (zero gain divides by zero), `0xCB`/`0xC9` (log dump, unneeded), and anything not listed.
  * `0xCF` (clear permanent fault) only on an explicit owner command with a confirmation step.
* Bitrate 500 kbit/s [C for register values]. Make it configurable only in the board header, not at runtime.

### 8.4 CAN-B access modes (`can2_mode`)

| Mode | How | When |
|---|---|---|
| `dual` | second TWAI node on D2/D3, always running | phase 0 proved two nodes work |
| `visit` | one TWAI node; CAN-A normally, CAN-B for short scheduled visits | single usable TWAI |
| `off` | CAN-B never used (the balance view then shows only CAN-A data: max/min cell) | CAN-B not wired |

Both transceivers stay wired to their own pin pair in every mode. In `visit` mode the node is deleted and
recreated on the other pin pair (switch time to be measured in phase 0; budget 10 ms). The idle transceiver's
TXD is held recessive by its pull-up (3.2). Visit rules:

* Dwell `can2_dwell_ms` (default 350, hard maximum 450) every `can2_interval_s` (default 5, minimum 2). The
  hard maximum keeps the CAN-A keep-alive gap (dwell + switch time + wait for fresh evidence, about 50 ms)
  below half the pack's roughly 1 s timeout [C].
* The node is in normal mode on CAN-B (it must ACK, or the pack repeats its first frame forever, same
  behaviour as CAN-A in listen-only [C]). Receive only; the CAN-B TX allowlist is still empty (8.3).
* No visit in `JOIN`, `LEAVE`, `FAULT`, `LOCKOUT`, while the transition token is held, or within 2 s of a link
  loss. In `ONLINE` visits are allowed only if `can2_visit_online = true` (default false until phase 1/2
  jitter measurements show the margin holds; with false, CAN-B data is gathered only in `STOPPED`/`HOLD`).
* Returning: restore the CAN-A node, wait for a fresh pack frame (the 250 ms evidence rule applies again), send
  the next `0x105` at once. If CAN-A shows no pack frame within `can2_return_ms` (default 300) after the
  visit, treat it as a link problem: `STOPPED`.
* CAN-B data collected this way has age up to the interval; the 1 s frames (SOC, balance mask, SOH) arrive on
  about 35 % of visits at the default dwell, so the model must show their age and never treat a missed frame as
  zero. Stale limits are 3x the interval in this mode.
* Everything Pylon and the aggregator need comes from CAN-A, so a degraded or missing CAN-B changes no limit.
  The maintenance mode of 8.3 uses a visit with the pack off the bus, where the gap does not matter.

## 9. Pylon RS485 (master only, port RS485-I)

The master answers an inverter that polls it. It never initiates traffic. Default 9600 8N1, configurable
(1200, 2400, 4800, 9600, 19200, 115200). Pylon RS485 is an ASCII-hex protocol; the exact field layout must be
taken from the **Pylontech RS485 communication protocol document (V3.x)**, not from this section, which fixes
only what is stable across versions and what the composite must say. Anything marked [verify] must be
checked against that document and against a capture from the owner's inverter before shipping.

### 9.1 Framing [verify]

`SOI 0x7E`, `VER` (2 hex chars, typically `20`; echo the request's version), `ADR` (2 hex), `CID1` (`46` for
battery data), `CID2` (command or response code), `LENGTH` (4 hex = 12-bit LENID + 4-bit LCHKSUM, where
LCHKSUM = ((sum of the three nibbles of LENID) mod 16 inverted + 1) & 0xF), `INFO` (hex ASCII), `CHKSUM`
(4 hex = ~(sum of all characters from `VER` to the end of `INFO`) + 1, 16 bit), `EOI 0x0D`. Upper-case hex.
Bad checksum or length: answer with RTN `0x02` / `0x03`; do not answer garbage with garbage.

### 9.2 Commands to implement, in priority order [verify codes against the document]

| CID2 | Meaning | Source in the composite |
|---|---|---|
| `0x4F` | protocol version | fixed, echo version |
| `0x51` | manufacturer/device info | device name "VGB-ESS", software version from the build, manufacturer "Vanguard-ESP32" |
| `0x42` | analog values | section 9.3 |
| `0x44` | alarm info | section 9.5 |
| `0x47` | system parameters | cell/pack over/under voltage and temperature limits from config |
| `0x92` | charge/discharge management info | CVL, DVL, CCL, DCL, status flags (charge enable, discharge enable, full-charge request) |
| `0x93` | serial number | `config.composite_serial`, default `VGB` + last 3 digits of each pack's serial |
| other | | RTN `0x04` (CID2 invalid) |

If the owner's inverter issues commands outside this list, capture them with `tools/pylon_master.py --sniff`
and add them here; do not guess.

### 9.3 Presenting two packs as one module

* Present **one** module at `config.pylon_adr` (default 2, the address most inverters poll first). A second
  compatibility mode `pylon_modules = per_pack` answers ADR 2 and 3 with each pack separately, for inverters
  that handle stacks better than a single big module. Default is the composite.
* `0x42` analog data: 14 cell voltages as the per-position mean of online packs (config `cell_report`:
  `mean` | `min`), temperature fields mapped (cell temperatures: max, min, mean of valid sensors; remaining
  fields repeat the mean), total current = sum (signed, 10 mA units), total voltage = mean (mV), remaining
  capacity and full capacity = sums (10 mAh units), cycles = max of packs if known else 0.
  The user-defined item count and any sub-fields follow the document's version; [verify].
* `0x92`: CVL, DVL, CCL, DCL from section 7. Status flags: charge enable = 0 and discharge enable = 0 when no
  pack is online or there is a blocking alarm. "Charge immediately" is never set. Full-charge request is set
  when max cell is above `taper_end_mv` minus 10 mV.

### 9.4 When there is nothing to report

* No pack online: keep answering; limits 0, enables cleared, alarm info reports a module fault. Silence is
  worse than a clear refusal because many inverters then keep their last accepted limits.
* Controller crashed or `LOCKOUT`: the master process does not answer or answers with zero limits. Which one is
  chosen in config (`pylon_on_lockout`: `zero`|`silent`, default `zero`); the inverter's own comms-loss
  setting must be checked at commissioning (it should treat silence as battery lost).
* Unknown fields in a composite (for example CAN-B data absent): report the `0xFF`/zero value defined by the
  document for "not supported", never a plausible number.

### 9.5 Alarms

Map decoded fault classes (from `frames.py`, protocol 6.4) to Pylon alarm bytes: cell over/under-voltage,
pack over/under-voltage, charge/discharge over-current, high/low cell temperature, "module fault" for everything else
blocking. Warnings (non-blocking) map to the warning-level encoding. The mapping lives in one table in
`lib/core/pylon_map.cpp` with a unit test per entry.

### 9.6 Tags in Pylon

Pylon carries no confidence. Anything whose tag is [I] or [?] is still sent if the inverter needs it, but the
web page and MQTT mark it, and `status.pylon.assumptions` lists which transmitted values rest on [I] data
(today: SOH, the limit directions, the charge limit by temperature).

## 10. Web UI (each controller, port 80)

Static single-page app (vanilla JS, no external hosts, same style as `app/src/vanguard/web/static/`),
gzipped into LittleFS. Live data over a WebSocket (`/ws`, 2 Hz, falls back to polling `/api/status`).
No fetches from outside the device.

Pages:

1. **Status.** Pack voltage/current/power, SOC, SOH, temperatures, controller state with the reason it is in
   it, switch state, contactor state, active faults with plain-language text and tag, link state, Wi-Fi/MQTT
   state, uptime, keep-alive jitter. Master: composite block (sums, CVL/CCL/DCL, online count, Pylon
   last-poll age).
2. **Modules / balance.** 14 cell bars per pack, shared scale, min/max highlighted, delta, balance mask
   [I], per-sensor temperatures, difference between the two packs (voltage, SOC, cell spread). Master shows
   both packs (slave from `DETAIL`, refreshed 1 Hz); slave shows its own. Time-series sparkline of max/min cell
   (RAM ring, last 15 minutes).
3. **Config.** Edit everything in section 11; validates in the browser and again on the device; shows the
   applied value and the hard limit it was clamped to. Wi-Fi and MQTT credentials write-only.
4. **Control.** Per pack: "Take offline" / "Allow online" (soft, in addition to the physical switch), "Clear
   faults", "Start balance session" (when enabled), "Reset lockout". Destructive/maintenance actions behind
   a two-step confirmation. Nothing here can close a contactor if the switch is open or policy forbids it.
5. **Events.** Last 200 events (state changes, refusals, faults, link/Pylon/Wi-Fi/MQTT), with uptime and
   wall-clock time when NTP is available.
6. **Diagnostics.** Last 256 raw CAN-A and CAN-B frames with decode and tag, TX counters, queue depth, error
   counters, TWAI state, link counters, Pylon request log (last 32 raw requests, for commissioning), free heap.

REST: `GET /api/status`, `/api/cells`, `/api/events`, `/api/frames`, `/api/config`; `POST /api/config`,
`/api/cmd/{online,offline,clear,balance,reset-lockout}`. Responses are JSON, one object each.

Security: LAN only. First boot starts an AP (`VGB-<chipid>`) with a provisioning page that sets the Wi-Fi
credentials and an admin password. All `POST` requests and the WebSocket require the admin password (HTTP
Basic or a token obtained with it) plus a fixed custom header (blocks cross-site form posts). No default
password. Nothing in the web layer can bypass the policy loop or the txgate; commands are requests the safety
task may refuse. Wi-Fi loss changes nothing about pack state.

## 11. Configuration

Stored as JSON in NVS (or a LittleFS file with a CRC), loaded at boot, validated, **clamped to compiled-in hard
limits that config cannot change**. A config that fails validation is rejected whole; the previous config
stays. Changing a value never needs a pack to cycle, except `mode_policy` and `role`, which apply at the next
boot while the pack is off the bus.

| Key | Default | Hard limit (compiled) |
|---|---|---|
| `role` | master | master/slave |
| `device_name` | `vgb-<last3 of mac>` | 1..24 chars |
| `mode_policy` | discharge_hold | enum 5.7 |
| `cvl_v` | 57.4 | <= 14 x 4.15 |
| `dvl_v` | 42.0 | >= 14 x 2.80 |
| `cell_ceiling_mv` | 4120 | <= 4150 |
| `taper_start_mv` / `taper_end_mv` | 4000 / 4120 | end <= 4150 |
| `max_charge_a_per_pack` | 36 | <= 40 (0.5 C of 73 Ah) |
| `max_discharge_a_per_pack` | 100 | <= 150 |
| `join_dv_mv` | 500 | <= 2000 |
| `join_min_cell_mv` / `join_max_cell_mv` | 3200 / 4100 | |
| `pylon_adr` / `pylon_baud` / `pylon_modules` / `cell_report` | 2 / 9600 / composite / mean | |
| `pack_capacity_ah` | 73 | |
| `can2_mode` / `can2_dwell_ms` / `can2_interval_s` / `can2_visit_online` | dual if phase 0 passes else visit / 350 / 5 / false | dwell <= 450, interval >= 2 |
| `enable_drive_mode` | off | off/on |
| `auto_clear_classes` | host_timeout, enable_latch, contactor_mismatch | |
| `wifi_*`, `mqtt_*`, `admin_password_hash`, `ntp_server` | | |

Hard limits come from the pack's own values (`0x512`, variant 0 table) so a typo cannot push the system above
what the pack's protection would accept. Config changes are logged as events, never silently applied.

## 12. MQTT and Home Assistant

* Broker address/port/user/password in config; TLS optional (system CA bundle or pinned cert).
* Client id `vgb-<device_name>`. Last-will `<base>/availability` = `offline` (retained); `online` on connect.
* Base topic `vanguard/<device_name>`. Per pack: `/state` (JSON, 1 Hz: V, I, P, SOC, SOH, temps, state,
  switch, contactors, limits, faults, ages), `/cells` (JSON, every 5 s or on change > 5 mV: cell mV array,
  min/max/delta, temperatures, balance mask), `/events` (not retained). Master adds `<base>/composite/state`
  (CVL, CCL, DCL, online count, sums) and `/pylon` (last poll age, request counters).
* Home Assistant MQTT Discovery under `homeassistant/<component>/<device_name>/<object>/config`, retained,
  published on every connect: sensors (voltage, current, power, SOC, SOH, hottest, coolest, max cell, min cell,
  cell delta, state, each of the 14 cells and temperatures as diagnostic entities), binary sensors (switch
  closed, contactors closed, fault, link, balancing), buttons (clear faults, reset lockout), switch (soft
  online/offline), number entities for a few safe config values (`cvl_v`, `max_charge_a_per_pack`,
  `max_discharge_a_per_pack`). Entities for values tagged [I] get an `[I]` suffix in the name.
* Commands arrive on `<base>/set/<name>`; they are validated and executed like web commands and answered on
  `<base>/ack`. MQTT cannot cause any frame that the web cannot, and MQTT loss changes nothing about pack state.
* Rate limits: at most 2 messages/s sustained per device; back off on reconnect.

## 13. Observability, diagnostics, OTA

* Event log: RAM ring of 200 plus persistent counters (boots, lockouts, faults by class, last reset reason,
  last 8 fault byte snapshots) in NVS.
* USB console (USB-Serial-JTAG): line commands `STATUS`, `GET <signal|group>`, `CONFIG`, `EVENTS`, `FRAMES n`,
  `CLEAR`, `OFFLINE`, `ONLINE`. Same handlers as the web API. Read commands are always on; control commands
  require the physical switch closed and are off unless `console_control = true`.
* Time: SNTP when online, otherwise uptime. Timestamps in events carry both.
* OTA: HTTP upload at `/ota` (admin password) with image signature/hash check and rollback on failed boot.
  The controller refuses OTA while `ONLINE` unless `ota_while_online = true`, since a reboot drops that pack
  from the bus for the boot duration and, on the master, the inverter loses its battery. The web page warns
  before it starts. The new image must be marked valid only after the first 60 s of healthy operation.

## 14. Test and bring-up plan

### 14.1 Phases (each has a gate; do not start the next until the gate passes)

| Phase | Work | Gate |
|---|---|---|
| 0 Toolchain | PlatformIO env builds; two TWAI nodes at 500 kbit/s; two UARTs on the planned pins; blink; USB console | loopback frames on both CAN paths (dual nodes, or visit switching with measured switch time); UART echo through the real RS485 drivers; `can2_mode` recorded in the build |
| 1 Read-only | CAN-A and CAN-B receive only, ACK, decoder from generated tables, Snapshot, web Status + Modules, USB console. **No transmit anywhere** | all frames from the Pi pack simulator and from a real pack decode with the same values as the Python app; jitter test: 10 min with Wi-Fi + HTTP + MQTT load, max loop delay < 50 ms; in `visit` mode also the CAN-A gap per visit |
| 2 Single pack | txgate + state machine + keep-alive on CAN-A, switch input, join policy with one pack, fault handling, CLEAR | native tests pass; txgate parity vectors pass; bench: pack closes/opens on command, fails closed on every injected fault in 14.2; jitter < 250 ms with all services on |
| 3 Link | RS485 master/slave, aggregator, join arbitration | bench: two controllers (one pack and one simulator, or two packs) negotiate joins; unplug the link and both packs leave within 1.5 s |
| 4 Pylon | codec, command handlers, mapping, `tools/pylon_master.py` | the Python master gets valid responses for every implemented command; replay of a real inverter's captured polls gets valid responses; checksum/length tests |
| 5 MQTT / HA | publisher, discovery, commands | HA shows both packs and the composite; MQTT outage changes no pack state |
| 6 CAN-B maintenance | only after owner approval of `VGB_ENABLE_CAN2_TX` | per-command allowlist tests; verified against a pack on the bench; service mode left after each |
| 7 Pack patch support | see section 15 | |

### 14.2 Automated tests (native env, no hardware)

* Fake microsecond clock harness (port of `bridge/tests/conftest.py` `Harness`); no wall-clock sleeps.
* **txgate parity:** `tools/txgate_vectors.py` runs the Python `check_tx` over every ID/D0/D1 combination
  and a set of malformed frames, writes `test/txgate_vectors.json`; the C++ test must agree on every vector.
* State machine tests: every transition in 5.2, refusal, TX failure, evidence timeout (250 ms), keep-alive period
  and watchdog, CLEAR spacing, one-at-a-time token, link loss at 1.0 s, restart policy, fast reset.
* Decoder tests against the same frame vectors the Python tests use (`app/tests`), including the tags.
* Visit scheduler tests: dwell cap, no visit in forbidden states, return-without-evidence goes `STOPPED`, keep-alive
  gap never exceeds the cap on the fake clock, stale-data handling for missed 1 s frames.
* Aggregator tests: one pack leaving mid-cycle, stale data, taper curves, limit direction rule.
* Link codec: CRC, resync, garbage, truncated frames, duplicate master. Pylon codec: framing, checksum, LENID.
* Fuzz (host): random bytes into the link, Pylon and web JSON parsers must never crash or transmit.
* A structure test equivalent to `bridge/tests/test_structure.py`: scan the source for calls to the CAN driver's
  transmit function and fail unless they appear only in `can_hal.cpp`, behind the gate.

### 14.3 Bench experiments before relying on the design (Pi + both packs)

These decide open points; each result goes into `CANBUS_PROTOCOL.md` with a tag. The Pi bridge can run them.

* **A. Mode change with contactors closed.** Pack closed in `discharge_hold`; send D0 = 0x01. Record whether contactors
  open and for how long, and how long until closed again. Then, in charge mode, drive current in both
  directions (bench supply and a load) and record faults, `0x113` B7, balancing, limits on `0x111`.
* **B. Second pack onto a live bus.** Terminals joined through a fuse and a series resistor. Close pack 1; request pack 2
  at |dV| of 0.1 V, 0.5 V, 1 V, 2 V. Record `0x120` B3, `0x112` B2..B7, and whether the contactor sequence
  completes. Repeat with the resistor removed only if the earlier steps pass.
* **C. Pin matrix.** With contactors closed under keep-alive, release each of DEN, CEN, EN12V, ESTP in turn:
  contactor bits, `0x113` B2 bits, `0x120` B2 bit 3, pack sleeps or not, how long to wake after reclosing, whether a CLEAR
  is needed. Record the pin voltage levels and the load each presents (to size D9's isolation stage).
* **D. Boot with the enable released.** Confirms the no-wake sleep and what recovers it.
* **E. Awake-time and idle draw.** `0x105` D1 = 0x01 held for hours (needs the allowlist addition): does the pack
  ever sleep, what does it draw, does the contactor stay open.
* **F. Limit directions.** While charging and discharging a pack, confirm which of `0x111` B3-B4 / B5-B6 moves with which
  direction, then change the tag in `frames.py` to [C].
* **G. Inverter capture.** Sniff the inverter's RS485 requests with `pylon_master.py --sniff` against a dummy responder.

## 15. Preparing for the pack firmware patch (no code in v1)

Keep the pack firmware image out of the repo (as today). The controller should make a patched pack a drop-in:
`mode_policy` gets a third value `single` that sends a constant D0; `txgate` and the join policy do not care
what the pack does internally; the CAN1 `0x1F51:1` bootloader entry is **not** implemented on the ESP32 (the
allowlist forbids `0x614`). Patching is done from the Pi bridge or a separate tool, with the pack off the bus.
Open work for that day: trace the bootloader's programming protocol (not analysed yet), locate ICSP pads for
recovery, and decide the patch (hold the contactors across a D0 change, or fold balancing into discharge mode).

## 16. Defaults used where the owner has not decided, and open questions

Defaults chosen so implementation is not blocked. Each can be changed with a one-line edit.

| Item | Default | What would change it |
|---|---|---|
| `VGB_ALLOW_NO_ENABLE_KEEPALIVE` | 0 (rejected) | owner approval, then update `txgate.py`, `netcheck`, README table, tests |
| `VGB_ENABLE_CAN2_TX` | 0 | owner approval at phase 6 |
| Mode | `discharge_hold` | experiment 14.3.A |
| Join threshold | 500 mV | experiment 14.3.B |
| Pin names | DEN/CEN decided by experiment C | until then `enable_drive_mode = off`, switch observed on D8 |
| `can2_mode` | dual if two nodes work, else visit | phase 0 |
| Which UART, RS485 direction | as in 3.1 | phase 0 |
| Pylon address / baud / composite vs per-pack | 2 / 9600 / composite | inverter's manual and capture |
| Master failover | none; dead master takes both packs off | owner decision for a later version |
| Limit directions | conservative of both | experiment F |
| Wi-Fi credentials / MQTT broker | AP provisioning | owner |

Questions the owner still needs to answer (none blocks phases 0-2):

1. Inverter make/model and its battery-port settings (Pylon variant, address, baud, comms-loss behaviour).
2. Whether to approve `D1 = 0x01` (and `0x03`) on the CAN-A allowlist.
3. Where the broker runs (the Pi is the obvious host), and whether Home Assistant is on the same LAN.
4. Whether a soft "online/offline" button in the web UI is wanted in addition to the physical switch (the spec assumes yes).
5. Whether per-pack Wi-Fi access points / a mDNS name (`vgb-<name>.local`) is wanted.

## 17. Rules for whoever implements this

* Follow `CLAUDE.md`. Fail-closed everywhere; do not weaken the allowlist, evidence rule, or "CAN2 does not
  transmit" without explicit owner instruction; those are product rules, not style.
* Keep `[C]/[I]/[?]` tags in the data model and in every UI/MQTT field. If a value's tag changes, change it in
  `frames.py` first (the generator carries it through).
* No wall-clock sleeps in tests; use the fake clock.
* Update this spec when behaviour changes; the spec and the code must not disagree.
* Put any new bench finding into `CANBUS_PROTOCOL.md` in the same change.
