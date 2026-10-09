# Vanguard battery bridge: decoder, encoder and pack simulator

This package is step 1 of the Vanguard battery bridge. It turns raw CAN frames from the
reverse-engineered 14-cell Li-ion BMS into named, scaled signals, and it simulates a pack so a
host can be developed without hardware. The protocol is described in `../CANBUS_PROTOCOL.md`.
Nothing here has been checked against a live pack, so every decoded signal carries the
document's confidence tag (C confirmed, I inferred, ? unknown) and keeps its raw integer
next to the scaled value.

## Install

From this directory, with Python 3.11 or newer:

    python3 -m venv .venv
    .venv/bin/pip install -e '.[test]'

## Run the tests

    .venv/bin/python -m pytest tests

## Run the simulator

    .venv/bin/python -m vanguard.simulator --duration 10

The simulator prints one line per frame the pack sends, in the format `R <bus> <id hex> <dlc> <data hex>
<timestamp us>`, plus `S <text>` status lines. By default it runs as fast as it can and
includes a simulated host sending the `0x105` keep-alive every 100 ms. Useful options:
`--realtime` paces output to the wall clock, `--charge` requests the charge path,
`--no-keepalive` shows the host-timeout behaviour, and `--duration 0` runs until interrupted.

To replay a JSON lines log (format documented in `src/vanguard/wire.py`):

    .venv/bin/python -m vanguard.simulator --replay capture.jsonl

## Use as a library

    from vanguard.decoder import decode
    frame = decode(2, 0x0CFF20F3, bytes.fromhex("0200D50C94CA007D"))
    frame.value("current_a"), frame.raw("current_a")

`decode` returns `None` for IDs the document does not describe. `vanguard.encoder.encode` builds
payloads for tests and the simulator; it refuses the maintenance and service-channel frames.
