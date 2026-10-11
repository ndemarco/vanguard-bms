# Vanguard battery bridge: decoder, encoder, pack simulator and web app

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

## Web app (step 3)

`vanguard.web` is the page the owner uses. It is a separate, unprivileged process: it connects to
the bridge's Unix socket (`../bridge/README.md` is the protocol), decodes the `R`/`T` frames with
`vanguard.decoder`, shows every signal with its raw integer and confidence tag, and sends only
`START DISCHARGE|CHARGE`, `STOP`, `HB`, `CLEAR` and `SLEEP 1|3`, each a fixed string.

Behaviour worth knowing:

* `HB` goes out every 250 ms only after Start and until Stop, a refusal, a fatal status line
  (`S TXERROR`, `S TXREFUSED`, `S STOP`) or a lost connection. The app never sends `START` by itself, and a
  reconnect never repeats one. Reconnects wait 2 s, then 4 s, 8 s ... up to 60 s; the page shows the reason and
  refuses Start during the wait.
* The page does not use the sign of any current, and labels current and limits "sign unverified".
* After a stop caused by a missed heartbeat the pack latches a fault (`keepalive_timeout`); press
  Clear faults while running, or Stop and Start again, as the pack requires.
* The server answers only the Host names it is bound to (`localhost`, `127.0.0.1`, `[::1]`, plus `--allow-host`),
  refuses cross-origin requests and non-JSON POSTs, and sends a Content-Security-Policy that allows only itself.
  Nothing is loaded from or sent to any other host; it works offline.
* Every run writes `vanguard-<date>-<time>.jsonl` (frames, the format in `src/vanguard/wire.py`) and
  `...status.jsonl` (bridge status lines and app events; `wire.py` has no record for them) to `--log-dir`.

### Install on a development machine

    python3 -m venv .venv
    .venv/bin/pip install -e '.[test]' -e ../bridge
    .venv/bin/python -m pytest tests

Run the bridge simulator and the app in two terminals (keep the socket path short, under about 100 characters):

    .venv/bin/vanguard-bridge --simulate --socket /tmp/vgb.sock
    .venv/bin/vanguard-app --socket /tmp/vgb.sock --port 8000

Open http://127.0.0.1:8000/. Replay a log without a bridge (controls disabled):

    .venv/bin/vanguard-app --replay vanguard-logs/vanguard-20260102-030405.jsonl --speed 4

Options: `--host`, `--port`, `--socket`, `--log-dir`, `--no-log`, `--allow-host`, `--backoff-initial`,
`--backoff-max`. Binding anything but a loopback address prints a warning, because the page can start the pack
and has no login.

### Install on the Pi

After the bridge is installed and checked (`../bridge/README.md`, steps 0 to 10), on the Pi:

0. `git -C /opt/vanguard-bridge/src pull`, then confirm `git -C /opt/vanguard-bridge/src log -1` shows the commit
   you expect before going on.
1. Install the app into the bridge's virtual environment with its web dependencies:

       sudo /opt/vanguard-bridge/venv/bin/pip install '/opt/vanguard-bridge/src/app[web]'

2. Create the user (the `vanguard` group comes from the bridge's sysusers file) and install the unit:

       sudo install -m 0644 /opt/vanguard-bridge/src/app/system/vanguard-app.sysusers.conf /etc/sysusers.d/vanguard-app.conf
       sudo systemd-sysusers
       sudo install -m 0644 /opt/vanguard-bridge/src/app/system/vanguard-app.service /etc/systemd/system/
       sudo systemctl daemon-reload
       sudo systemctl enable --now vanguard-app
       journalctl -u vanguard-app -n 20

3. From the PC, open a tunnel and browse to http://127.0.0.1:8000/:

       ssh -L 8000:127.0.0.1:8000 pi@<host>

Frame logs are in `/var/lib/vanguard-app/logs`. `sudo systemctl restart vanguard-app` is always safe: the bridge
stops the pack when the app disconnects.
