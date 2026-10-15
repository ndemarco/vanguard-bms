# Vanguard BMS: teaching an orphaned battery to talk again

A lithium battery pack from a Club Car Tempo golf car came out of service with plenty of life left in its
448 cells. It also came out with no documentation, no wiring diagram, and a battery management system (BMS)
that refuses to connect its terminals unless something on the other end of a CAN bus says the right words,
at least once a second, forever.

This repository is the attempt to learn those words and say them carefully.

The pack is a Briggs & Stratton Vanguard 48 V unit: 14 series groups of 32 NMC cells, about 51.6 V nominal,
run by a Microchip dsPIC33 microcontroller and an NXP cell-monitor chip. Nobody publishes its protocol. So
the flash was read off the board, disassembled in Ghidra, and walked through function by function until the
CAN traffic made sense. What came out is a protocol document, a decoder, a pack simulator, and a small,
deliberately paranoid service that lets a Raspberry Pi keep the pack awake.

## What is here

| Path | What it is |
|---|---|
| [`CANBUS_PROTOCOL.md`](CANBUS_PROTOCOL.md) | The reverse-engineered protocol for both CAN buses: every frame, every scaling, every command, each tagged with how sure we are. Start here. |
| [`analysis/`](analysis/) | The working notes behind the protocol: per-bus evidence, RAM glossaries, connector pinouts, and the Ghidra scripts used. |
| [`app/`](app/) | The `vanguard` Python package: decoder, encoder, pack simulator, and a local web page that shows live pack data and drives the bridge. |
| [`bridge/`](bridge/) | The `vanguard_bridge` service for a Raspberry Pi 4 with a two-channel CAN HAT. It owns the CAN buses and is the only thing allowed to transmit to the pack. |

## How confident is any of this?

Every claim in the protocol document carries a tag:

- **[C]** confirmed: read straight from the code or its constant tables.
- **[I]** inferred: reasoned from context or arithmetic. Check it on a bench before trusting it.
- **[?]** unknown.

**Only CAN1 idle telemetry, the keep-alive and contactor closing have been checked, on a bench on 2026-10-11; the rest is unchecked against a live pack.** The decoder keeps the raw integer next to every scaled
value, and the web page shows the confidence tag next to every signal, so that a wrong guess is visible rather
than quietly wrong.

## The interesting parts

- **The pack has two CAN buses that speak differently.** CAN1 uses 11-bit IDs and big-endian payloads; it
  carries the keep-alive and is the one the golf car talks to. CAN2 uses 29-bit J1939-style IDs and
  little-endian payloads, with far richer telemetry and a maintenance channel the car never uses.
- **The pack will not stay connected without a heartbeat.** Frame `0x105` must arrive at least once a second
  or the contactors open. No CAN command closes them by itself; a hardware enable line must also be held.
- **Some commands are dangerous.** A few CAN2 frames rewrite persistent calibration, and one CAN1 object
  reboots the BMS into its bootloader with no password. The bridge makes those impossible to send.
- **The nameplate and the firmware disagree.** The label says 67.5 Ah; every capacity table in the firmware
  assumes cells of about 3 Ah, which does not fit 32 cells in parallel. The protocol document lays out the
  arithmetic and what reading would settle it.

## Safety: what the bridge will and will not do

A battery this size can start fires. The bridge is built on the assumption that every other part of the
system, including the person at the keyboard, will eventually make a mistake.

- It powers up stopped and sends nothing until told to start.
- It can transmit exactly two frame IDs, with a fixed set of byte patterns, checked on the exact bytes written
  to the socket. The Linux traffic-control layer enforces the same allowlist a second time.
- CAN2 is listen-only, with a drop-all filter, and has no send path in the code at all.
- It refuses to start without fresh evidence that the pack is actually on the wire it expects, and stops if
  the wiring looks swapped.
- If it crashes, the heartbeat stops and the pack disconnects itself within about a second.

The full list, with the file and function enforcing each rule, is in [`bridge/README.md`](bridge/README.md).

## Trying it without a battery

The simulator stands in for the pack, so everything except the CAN hardware runs on an ordinary computer
(Python 3.11 or newer):

```bash
cd app
python3 -m venv .venv
.venv/bin/pip install -e '.[test]' -e ../bridge
.venv/bin/python -m pytest tests
.venv/bin/vanguard-bridge --simulate --socket /tmp/vgb.sock
```

In a second terminal:

```bash
cd app
.venv/bin/vanguard-app --socket /tmp/vgb.sock --port 8000
```

Then open http://127.0.0.1:8000/. The page works offline and loads nothing from any other host.

Installing on the Pi, wiring, and the bench checks to run before connecting a real pack are in
[`bridge/README.md`](bridge/README.md) and [`app/README.md`](app/README.md).

## The firmware image is not included

The analysis was done on a full flash read of the BMS (`DSPIC33EP128GS806.hex`). That firmware belongs to
its manufacturer, so it is not in this repository. The protocol document and notes give word addresses, so
anyone with their own read of the same board can follow along in Ghidra.

## Why bother

Because a pack like this is worth more as a battery than as recycling, and the only thing standing between
the two is a few bytes nobody wrote down. Writing them down, carefully and honestly about what is still a
guess, seemed worth the effort.

## License

The code and documentation in this repository are released under the [MIT License](LICENSE). Vanguard,
Briggs & Stratton and Club Car are trademarks of their owners; this project is not affiliated with or
endorsed by them. Working on a high-voltage battery is at your own risk.
