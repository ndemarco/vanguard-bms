# Vanguard battery bridge service

Step 2 of the Vanguard battery bridge. A small Python service for a Raspberry Pi 4 with a
Waveshare 2-CH CAN HAT+. It owns both pack CAN buses, sends the pack's keep-alive, and serves
exactly one controlling client (the web app, step 3) over a Unix socket with a line-based
ASCII protocol. It is the safety core of the project: it decides what reaches the pack.

The pack protocol is in `../CANBUS_PROTOCOL.md`. **Nothing here has been run against a live
pack or on the Pi yet.** Everything marked "verify on the Pi" below must be checked before the
pack is connected.

## Safety design

| Rule | Where it is enforced |
|---|---|
| Power-on state is STOPPED; nothing is sent until `START` | `core.py` `Bridge.__init__`, `Bridge._start` |
| Transmit allowlist, checked on the exact 16 bytes written, independent of the state machine | `txgate.py` `check_tx`, called by `TxGate.send` (the only CAN write in the package) |
| Refused or failed transmit forces STOPPED | `core.py` `Bridge._on_refused`, `Bridge._on_failed` |
| Pack CAN2 has no send path | `canio.py` `Can2ReceiveOnly`, `open_pack_can2`; `simlink.py` `SimCan2Socket` |
| Keep-alive every 100 ms, 1 s watchdog fed only by `HB` / `START` | `core.py` `Bridge._tick` |
| Client disconnect, slow client, internal error, SIGTERM: STOPPED | `core.py` `Bridge.disconnect`, `Bridge._fail_closed`; `service.py` `Service._drop_client`, `Service.run` |
| `CLEAR` once per 100 ms, single-shot D1 = 0x13 | `core.py` `Bridge._clear`, `Bridge._tick` |
| `SLEEP 1` / `SLEEP 3` only while STOPPED | `core.py` `Bridge._sleep` |
| OS layer: pack CAN1 egress filter (only 0x105 and 0x515), pack CAN2 listen-only plus drop-all filter | `netsetup.py` `commands` |
| Bridge refuses to start (exit 2), or stops and exits (exit 3) while running, without those guards | `netcheck.py` `check_all`; `service.py` `main`, `Service.step` |
| No GPIO: the hardware enable input is not driven | no GPIO code exists |

The transmit allowlist: standard 11-bit frames only, DLC 8, no remote (RTR) or error flag,
padding bytes zero, and either

* `0x105` with D0 in {`0x00`, `0x01`}, D1 in {`0x11`, `0x13`}, D2..D7 = 0, or
* `0x515` with D0 in {`0x10`, `0x30`}, D1..D7 = 0.

So `0x105` D0 bits 2/4/6 (vehicle load outputs) and D1 bit 2 (20-minute timeout) can never
be set, `0x515` deep shutdown (D0 = `0x20`) is impossible, and so are `0x614` (bootloader
reboot) and every extended ID (the CAN2 maintenance channel). A refused frame is reported
with `S TXREFUSED` and forces STOPPED.

If the bridge crashes or is killed, it stops sending `0x105` and the pack opens its contactors
within about 1 s on its own. systemd restarts the bridge, which comes back STOPPED.

## Client protocol

Unix stream socket, default `/run/vanguard-bridge/bridge.sock`, mode `0660`, group `vanguard`.
One client at a time: a second connection receives `S ERR busy: another client is connected`
and is closed. Lines are ASCII, terminated by `\n`.

### Client to bridge

Exact match, upper case, single spaces, no leading or trailing spaces. One `\r` before the
`\n` is tolerated. At most 64 bytes per line; longer input is discarded up to the next `\n`
and reported once. Bytes outside 0x20..0x7E are rejected.

| Line | Accepted when | Effect |
|---|---|---|
| `START DISCHARGE` / `START CHARGE` | STOPPED | enter RUNNING; first `0x105` at once, then every 100 ms; D0 = `0x00` (discharge) or `0x01` (charge), D1 = `0x11`. Feeds the watchdog |
| same `START` again | RUNNING in that mode | feeds the watchdog |
| `START` of the other mode | RUNNING | refused (`STOP` first) |
| `HB` | any time | feeds the watchdog while RUNNING; no reply |
| `STOP` | any time | enter STOPPED; `S OK STOP` |
| `CLEAR` | RUNNING, at least 100 ms after the last accepted `CLEAR` | next `0x105` carries D1 = `0x13`, then back to `0x11` |
| `SLEEP 1` / `SLEEP 3` | STOPPED, at most once per second | one `0x515` with D0 = `0x10` / `0x30` |

Anything else, including `SLEEP 2`, gets `S ERR ...` and changes nothing. Errors and
refused commands never feed the watchdog.

Watchdog: while RUNNING, if 1 s passes without `HB` or `START`, the bridge stops sending
`0x105` at once, enters STOPPED, sends `S STOP watchdog: no HB or START for 1 s` and stays
STOPPED until a fresh `START`. A client should send `HB` every 250 ms or so.

### Bridge to client

| Line | Meaning |
|---|---|
| `R <bus> <id hex> <dlc> <data hex> <timestamp us>` | frame received on pack bus 1 or 2 |
| `T <bus> <id hex> <dlc> <data hex> <timestamp us>` | frame the bridge transmitted (always bus 1) |
| `S MODE simulate (no CAN hardware)` / `S MODE hardware` | first line after connect |
| `S CONFIG pack-can1 ...` / `S CONFIG pack-can2 ...` | the bus-to-interface mapping, after connect |
| `S STATE STOPPED` / `S STATE RUNNING DISCHARGE` / `S STATE RUNNING CHARGE` | on connect, on every change, and once per second |
| `S OK <command>` | command accepted (not sent for `HB`) |
| `S ERR <reason>` | command refused or malformed input |
| `S STOP <reason>` | the bridge entered (or confirmed) STOPPED and why |
| `S TXREFUSED <frame hex> <reason>` | the allowlist refused a frame (a bridge bug); state is STOPPED |
| `S TXERROR <reason>` | the CAN socket write failed (for example no ACK, queue full); state is STOPPED |
| `S RX <bus> remote/error id=... dlc=...` | a remote or error frame was received |
| `S SIM <text>` | simulator event (`--simulate` only) |

`R`/`T` formats come from `vanguard.wire` (`app/src/vanguard/wire.py`); `parse_line` there
reads all three line types. Timestamps are microseconds on the bridge's monotonic clock.
`id hex` is 3 digits on bus 1 and 8 digits on bus 2.

If the client stops reading and more than 256 KiB of output piles up, the bridge drops the
client, which also means STOPPED.

## Simulation mode

    vanguard-bridge --simulate --socket /tmp/vanguard-bridge.sock

Both CAN sockets are replaced by `simlink.py`, an in-process link to `vanguard.simulator`.
The state machine, allowlist and `TxGate.send` are the same code as on hardware; only the
object behind `send`/`recv` changes. The simulated pack closes its contactors in response to
the keep-alive and opens them about 1 s after it stops. No interface checks run in this mode.
Without `--config`, it uses the default mapping and no socket group.

Quick manual client (needs `socat`):

    (echo "START DISCHARGE"; while sleep 0.25; do echo HB; done) | socat - UNIX-CONNECT:/tmp/vanguard-bridge.sock

Press Ctrl-C to disconnect; the bridge enters STOPPED.

## Configuration

`system/bridge.toml`, installed as `/etc/vanguard-bridge/bridge.toml`, holds the only copy of
the mapping between pack buses and HAT channels. For each pack bus it names the kernel
interface **and** the SPI device of its MCP2515. Kernel names `can0`/`can1` follow driver probe
order and are not guaranteed to follow the HAT terminal labels, so both the setup and the bridge
refuse to run unless `/sys/class/net/<interface>/device` resolves to the configured SPI device.
The mapping is printed to the journal at startup and sent to each client.

`[pack_can2] guard`:

* `"listen-only"` (active): the CAN2 controller never transmits and never ACKs.
* `"tc-drop-all"` (fallback, commented out): normal mode, so the controller ACKs pack frames;
  the drop-all tc filter is what stops it from sending frames. Use this only if listen-only
  leaves the pack retransmitting one frame forever (with only the pack and the HAT on CAN2,
  nobody ACKs). The drop-all filter is installed in both modes.

## Install on the Pi

Raspberry Pi OS bookworm, 64-bit. Run every command on the Pi.

0. Put this repository at `/opt/vanguard-bridge/src` (git clone or rsync from your
   development machine). Confirm `git -C /opt/vanguard-bridge/src log -1` shows the commit you
   expect before going further. Do not continue on a stale tree.
1. Packages:

       sudo apt install python3-venv iproute2 can-utils socat

2. Device tree: append `bridge/system/config.txt.snippet` to `/boot/firmware/config.txt`, set
   the HAT's logic-voltage jumper to 3.3 V (Waveshare wiki), reboot, then check:

       dmesg | grep -i mcp251
       ls -l /sys/class/net/can0/device /sys/class/net/can1/device

   Expect two "MCP2515 successfully initialized" lines and links ending in `spi1.1` and
   `spi1.2`.
3. Termination: CAN1 and CAN2 are separate buses; fit the HAT's 120 ohm jumper on a channel
   only if the HAT is at one end of that bus (with only the pack and the HAT, it is).
4. Decide the wiring (which HAT terminal goes to pack CAN1, which to pack CAN2), then install
   the config and edit it to match:

       sudo install -d -m 0755 /etc/vanguard-bridge
       sudo install -m 0644 /opt/vanguard-bridge/src/bridge/system/bridge.toml /etc/vanguard-bridge/bridge.toml
       sudo nano /etc/vanguard-bridge/bridge.toml

5. Users and group:

       sudo install -m 0644 /opt/vanguard-bridge/src/bridge/system/vanguard-bridge.sysusers.conf /etc/sysusers.d/vanguard-bridge.conf
       sudo systemd-sysusers

   Add the web app's user (later) with `sudo usermod -aG vanguard <user>`.
6. Python environment:

       sudo python3 -m venv /opt/vanguard-bridge/venv
       sudo /opt/vanguard-bridge/venv/bin/pip install /opt/vanguard-bridge/src/app /opt/vanguard-bridge/src/bridge
       sudo /opt/vanguard-bridge/venv/bin/python -m compileall -q /opt/vanguard-bridge/venv

7. Kernel modules:

       sudo install -m 0644 /opt/vanguard-bridge/src/bridge/system/vanguard-can.modules-load.conf /etc/modules-load.d/vanguard-can.conf
       sudo systemctl restart systemd-modules-load

8. systemd units:

       sudo install -m 0644 /opt/vanguard-bridge/src/bridge/system/vanguard-can-setup.service /etc/systemd/system/
       sudo install -m 0644 /opt/vanguard-bridge/src/bridge/system/vanguard-bridge.service /etc/systemd/system/
       sudo systemctl daemon-reload
       sudo systemctl start vanguard-can-setup
       systemctl status vanguard-can-setup

   The setup must end with no `CHECK FAILED` lines.
9. Run every check in "Verify on the Pi" below, including the bench test, **with the pack
   disconnected**.
10. Start the bridge:

        sudo systemctl enable vanguard-can-setup vanguard-bridge
        sudo systemctl start vanguard-bridge
        journalctl -u vanguard-bridge -n 20

    Expect the two `CONFIG` lines, `interface checks passed` and `state STOPPED`.
11. Only now connect the pack, and first use `candump -td can0` / `can1` (the right interface
    per your mapping) to confirm the pack's frames arrive on the expected bus.

To change the CAN2 guard later: edit `bridge.toml`, then
`sudo systemctl restart vanguard-can-setup vanguard-bridge`.

## Verify on the Pi

None of this could be run on the development machine (WSL2, no root, no vcan). The parser
tests use sample text in `tests/samples/` that was written from the iproute2 source, not
captured; replace it with real output after step A.

**A. Interface state and filters** (replace `can0`/`can1` per your mapping):

    ip -details link show dev can0
    ip -details link show dev can1
    tc qdisc show dev can0
    tc filter show dev can0 parent 1:
    tc qdisc show dev can1
    tc filter show dev can1 parent 1:

Expect `bitrate 500000` on both, `can <LISTEN-ONLY> state` on pack CAN2 only, `qdisc prio 1:
root` on both, `NOT canid(sff 0x105 sff 0x515)` with `gact action drop` on pack CAN1, and a
`basic` filter with no match and `gact action drop` on pack CAN2. Then copy the outputs over
`tests/samples/ip_can1_ok.txt`, `ip_can2_listen_only_ok.txt`, `qdisc_prio_ok.txt`,
`filter_can1_ok.txt` and `filter_can2_dropall_ok.txt` and rerun the tests. If the text differs,
fix `netcheck.py` before going on.

**B. The bridge user can run the checks without privileges:**

    sudo -u vanguard-bridge /opt/vanguard-bridge/venv/bin/vanguard-bridge --config /etc/vanguard-bridge/bridge.toml --check
    systemd-analyze security vanguard-bridge

Expect `interface checks passed` and exit status 0. Install step 10 then shows the same line
from inside the systemd sandbox; if it fails only there, a hardening option in
`vanguard-bridge.service` is blocking `ip` or `tc`.

**C. Bench test of the tc filters, pack disconnected.** Wire HAT terminal CAN_0 H/L to CAN_1
H/L, fit both termination jumpers. Pack CAN1 is assumed to be `can0` here.

1. `sudo systemctl stop vanguard-bridge`
2. Let `can1` ACK for this test: `sudo ip link set can1 down`, then
   `sudo ip link set can1 type can bitrate 500000 listen-only off`, then `sudo ip link set can1 up`
   (the drop-all filter stays in place).
3. In a second terminal: `candump -td can1`
4. Allowed, must appear: `cansend can0 105#0011000000000000` and `cansend can0 515#1000000000000000`.
5. Must not appear: `cansend can0 614#4018100100000000`, `cansend can0 106#0011000000000000`,
   `cansend can0 00000105#0011000000000000` (extended ID 0x105) and
   `cansend can0 0CFF00F3#0000000000000000`. The `dropped` figure in
   `tc -s qdisc show dev can0` rises by one per refused frame.
6. Check how the kernel filter treats remote frames: `cansend can0 105#R`. The tc filter looks
   at the ID only, so this is expected to appear; the bridge's own allowlist refuses RTR.
7. Pack CAN2 drop-all: `candump -td can0` in the second terminal, then
   `cansend can1 0CFF00F3#0000000000000000` and `cansend can1 105#0011000000000000`: neither may appear.
8. Listen-only: `sudo systemctl restart vanguard-can-setup` (restores listen-only on `can1`),
   then `cansend can0 105#0011000000000000` and `ip -details -statistics link show dev can0`:
   with no ACK from `can1`, the transmit error counter climbs and the state goes to
   ERROR-PASSIVE, which shows `can1` does not ACK. `sudo systemctl restart vanguard-can-setup`
   again to reset `can0`.
9. Remove the loopback wire.

**D. Hardware facts not confirmed from the Waveshare wiki:** which kernel name each controller
gets (handled by the SPI check), the exact SPI device names (`spi1.1`/`spi1.2`, step 2), that
the mcp2515 driver accepts `listen-only on` (step A), that `em_canid`, `cls_basic`, `sch_prio`
and `act_gact` exist in the Raspberry Pi kernel (`sudo modprobe -a can can_raw sch_prio cls_basic em_canid act_gact`),
and that a qdisc added while the interface is down survives `ip link set up` (step A shows it).
The wiki does confirm the 16 MHz crystal, SPI1 with chip selects on BCM 17/16, interrupts on
BCM 22/13 and the overlay lines in `system/config.txt.snippet`.

## Known limits

* The tc filter checks the CAN ID only. DLC, payload bytes and the RTR flag are enforced by
  `txgate.check_tx` alone.
* If pack CAN1 stops ACKing, up to 10 queued keep-alives (`txqueuelen 10`, set by the setup
  instead of the wiki's 65536) can go out when the bus recovers, after the bridge has already
  entered STOPPED. That keeps the pack alive for at most about 1 s longer.
* Python cannot make a stored socket truly unreachable. `Can2ReceiveOnly` keeps it in a
  name-mangled attribute and has no write method; the OS-layer guards are the hard barrier.
* The periodic interface check runs `ip` and `tc` in the event loop every 5 s; that can delay
  one keep-alive by a few tens of milliseconds (the pack tolerates 1 s).
* Any CAN write error stops the bridge. The client has to send `START` again.

## Development

    python3 -m venv .venv
    .venv/bin/pip install -e ../app -e '.[test]'
    .venv/bin/python -m pytest

Tests use a fake clock and fake or simulated sockets; nothing sleeps. `tests/test_structure.py`
fails if any new write call appears anywhere in the package.
