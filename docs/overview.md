# Overview

This repository holds a reverse-engineered CAN protocol for a Briggs & Stratton Vanguard 48 V (14S) battery
pack, plus Python code to decode it, simulate the pack, and keep it awake from a Raspberry Pi.

## Status

[`CANBUS_PROTOCOL.md`](../CANBUS_PROTOCOL.md) is the source of truth. Every claim in it is tagged:

- **[C]** confirmed
- **[I]** inferred
- **[?]** unknown

Code carries these tags through to the UI, so the decoder returns a confidence tag next to each value. Keep the
tags honest when changing decoding.

The decoder and bridge have been run against a live pack. See the top-level [`README.md`](../README.md) and
Appendix D of the protocol document for what has been checked on a bench.

## Where things live

- `analysis/` holds the working notes and Ghidra scripts. The firmware image is deliberately not in the repo.
- `app/` is the `vanguard` package (decoder, encoder, simulator, web app).
- `bridge/` is the `vanguard_bridge` service for the Raspberry Pi.
- `firmware/SPEC.md` is the design for a planned ESP32 pack controller. See below.

## Planned: ESP32 pack controller (not started)

[`firmware/SPEC.md`](../firmware/SPEC.md) is the complete design for one ESP32-C6 per pack: keep-alive, join
policy, an RS485 link between two packs, a Pylon RS485 composite for an inverter, a web page, and MQTT. It
starts with a "START HERE" note and a status log. Nothing is implemented yet. Read the spec before writing any
firmware, and keep it updated.
