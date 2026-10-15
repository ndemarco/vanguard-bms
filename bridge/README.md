# Vanguard battery bridge service

Step 2 of the Vanguard battery bridge. A small Python service for a Raspberry Pi 4 with a
Waveshare 2-CH CAN FD HAT (two MCP2518FD). It owns both pack CAN buses, sends the pack's keep-alive, and serves
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
| Refused transmit forces STOPPED; failed transmit forces STOPPED and exits (status 3) so the restart flushes the CAN queue | `core.py` `Bridge._on_refused`, `Bridge._on_failed`; `service.py` `Service._fail`; `system/vanguard-bridge.service` `ExecStartPre` |
| A frame only the pack sends, seen on the wrong interface (wiring swapped): STOPPED, `START` refused until a new client connects | `service.py` `wiring_swap_evidence`, `Service.step`; `core.py` `Bridge.latch_wiring_fault`, `Bridge.connect` |
| CAN socket error (for example interface down): STOPPED, exit 3 | `canio.py` `CanReceiver.read_frames`; `service.py` `Service.step` |
| Heartbeats that waited in the socket during a loop stall cannot extend the keep-alive | `service.py` `Service._read_client` |
| Pack CAN2 has no send path | `canio.py` `Can2ReceiveOnly`, `open_pack_can2`; `simlink.py` `SimCan2Socket` |
| Keep-alive every 100 ms, 1 s watchdog fed only by `HB` / `START` | `core.py` `Bridge._tick` |
| Client disconnect, slow client, internal error, SIGTERM: STOPPED | `core.py` `Bridge.disconnect`, `Bridge._fail_closed`; `service.py` `Service._drop_client`, `Service.run` |
| `CLEAR` once per 100 ms, single-shot D1 = 0x13 | `core.py` `Bridge._clear`, `Bridge._tick` |
| `SLEEP 1` / `SLEEP 3` only while STOPPED | `core.py` `Bridge._sleep` |
| `START` and `SLEEP` need positive evidence: a pack CAN1 frame on the TX interface within 250 ms and no wiring latch | `core.py` `Bridge._transmit_permission`, `Bridge.note_pack_can1_frame`; `service.py` `is_pack_can1_evidence`, `Service.step` (receivers are read before client input) |
| OS layer: pack CAN1 egress filter (only 0x105 and 0x515), pack CAN2 listen-only plus drop-all filter | `netsetup.py` `commands` |
| Bridge refuses to start (exit 2), or stops and exits (exit 3) while running, without those guards; the periodic check runs in a thread | `netcheck.py` `check_all`; `service.py` `main`, `BackgroundChecker`, `Service.step` |
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
| `START DISCHARGE` / `START CHARGE` | STOPPED, pack CAN1 traffic seen (see below) | enter RUNNING; first `0x105` at once, then every 100 ms; D0 = `0x00` (discharge) or `0x01` (charge), D1 = `0x11`. Feeds the watchdog |
| same `START` again | RUNNING in that mode, pack CAN1 traffic seen | feeds the watchdog; refused (and not counted as a heartbeat) when no pack CAN1 traffic was seen in the last 250 ms, so clients should use `HB` as the heartbeat |
| `START` of the other mode | RUNNING | refused (`STOP` first) |
| `HB` | any time | feeds the watchdog while RUNNING; no reply |
| `STOP` | any time | enter STOPPED; `S OK STOP` |
| `CLEAR` | RUNNING, at least 100 ms after the last accepted `CLEAR` | next `0x105` carries D1 = `0x13`, then back to `0x11` |
| `SLEEP 1` / `SLEEP 3` | STOPPED, pack CAN1 traffic seen, at least 1.5 s after the last `0x105` was sent, at most once per second | one `0x515` with D0 = `0x10` / `0x30` |

Anything else, including `SLEEP 2`, gets `S ERR ...` and changes nothing. Errors and
refused commands never feed the watchdog.

Watchdog: while RUNNING, if 1 s passes without `HB` or `START`, the bridge stops sending
`0x105` at once, enters STOPPED, sends `S STOP watchdog: no HB or START for 1 s` and stays
STOPPED until a fresh `START`. A client should send `HB` every 250 ms or so.

Commands are timed by when they reached the bridge's socket, not when the bridge got round
to reading them (more exactly, by the last moment the socket was seen empty). If the bridge's
loop stalls, heartbeats queued meanwhile therefore cannot keep the pack alive for more than
1 s after the client really went silent, and a `START` that waited 1 s or more is refused.
This also covers bytes a client wrote before the bridge accepted its connection. The
arrival time is used only for the watchdog and the stale-`START` rule; keep-alive timing,
`CLEAR`/`SLEEP` intervals and all timestamps use the current time.

The bridge exits (status 3) after a CAN transmit error, a CAN socket error or a failed
periodic interface check. systemd restarts it, and every start first re-runs the interface
setup as root (`ExecStartPre`), whose down/up discards frames still queued for transmit. The
client has to reconnect and send `START`. systemd allows at most 10 starts per minute
(`StartLimitBurst=10`, `StartLimitIntervalSec=60`) and then gives up until
`sudo systemctl reset-failed vanguard-bridge`. A client must therefore back off after
`S TXERROR` or a lost connection (for example wait a few seconds, longer after repeated
failures) rather than reconnecting and sending `START` at once; the web app in step 3 will.

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
| `S TXERROR <reason>` | the CAN socket write failed (for example no ACK, queue full); state is STOPPED and the bridge exits |
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
interface **and** the SPI device of its CAN controller. Kernel names `can0`/`can1` follow driver probe
order and are not guaranteed to follow the HAT terminal labels, so both the setup and the bridge
refuse to run unless `/sys/class/net/<interface>/device` resolves to the configured SPI device.
The mapping is printed to the journal at startup and sent to each client.

`[pack_can2] guard`:

The bridge also watches the traffic for frames only the pack sends: a 29-bit frame with
source address `0xF3` on the pack CAN1 interface, or an 11-bit frame with an ID from the
pack's CAN1 transmit list (`vanguard.frames`, for example `0x111`) on the pack CAN2 interface.
Either means the wiring does not match the config: the bridge enters STOPPED and refuses
`START` until a new client connects. Remote frames and other nodes' frames are ignored. A
real swap trips again within about 50 ms of reconnecting, because the pack sends those frames
every 50 to 100 ms.

Clearing the latch is safe because `START` and `SLEEP` also need positive evidence: within
the last 250 ms the pack CAN1 interface must have received a standard frame whose ID is in
the pack's CAN1 transmit list (`0x111`, `0x112`, ... from `vanguard.frames`), and no wiring
latch may be set. Otherwise they are refused with
`S ERR START ... refused: no pack CAN1 traffic seen in the last 250 ms` or
`S ERR START ... refused: wiring latch (...)`. Each step reads the CAN sockets before the
client's input, so this step's frames count. This also covers the first moments after the
bridge starts, before any frame has been read.

Consequence: the bridge cannot wake a pack that is asleep and silent on CAN1, because it
will not send the first `0x105` without seeing pack traffic. Wake the pack by its hardware
input (key switch or charger wake-up line) first; once it broadcasts on CAN1, `START` works.

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

2. Device tree: append `bridge/system/config.txt.snippet` to `/boot/firmware/config.txt`, reboot, then check:

       dmesg | grep -i mcp251
       ls -l /sys/class/net/can0/device /sys/class/net/can1/device

   Expect two "MCP2518FD ... successfully initialized" lines and links ending in `spi1.0` and
   `spi0.0` (in either order). The `can0`/`can1` to `spi0.0`/`spi1.0` order differs between boots and between Pis, so `bridge.toml` must match what `ls -l /sys/class/net/can0/device` shows.
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

Expect `LOWER_UP` and a state other than BUS-OFF / STOPPED / SLEEPING on pack CAN1, `bitrate 500000` on both, `can <LISTEN-ONLY> state` on pack CAN2 only, `qdisc prio 1:
root` on both, three `u32` filters in chain 0 on pack CAN1 (allow the exact `0x105` and `0x515` frames to class 1:1 for prefs 1 and 2, `gact action drop` for everything else at pref 3; `netcheck.CAN1_U32_RULES`), and a
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
6. Remote frames: `cansend can0 105#R` must not appear either. The u32 rule matches the whole ID
   word including the RTR flag, so it is dropped in the kernel as well as refused by the bridge.
7. Pack CAN2 drop-all: `candump -td can0` in the second terminal, then
   `cansend can1 0CFF00F3#0000000000000000` and `cansend can1 105#0011000000000000`: neither may appear.
8. Listen-only: `sudo systemctl restart vanguard-can-setup` (restores listen-only on `can1`),
   then `cansend can0 105#0011000000000000` and `ip -details -statistics link show dev can0`:
   with no ACK from `can1`, the transmit error counter climbs and the state goes to
   ERROR-PASSIVE, which shows `can1` does not ACK. `sudo systemctl restart vanguard-can-setup`
   again to reset `can0`.
9. Remove the loopback wire.

**C2. Transmit error and restart (pack connected on the bench, no load on the pack output).**
Start the bridge, connect a client, `START DISCHARGE` with `HB` every 250 ms. Unplug the
pack CAN1 lines at the HAT.

1. The client receives `S TXERROR ...` and `S STOP transmit failed...` within about 0.1 s,
   and the connection closes.
2. `journalctl -u vanguard-bridge` shows `FATAL transmit failed`, the exit, `ExecStartPre`
   running the setup, and a fresh start in STOPPED.
3. In a second terminal start `candump -td <pack CAN1 interface>` (it shows only frames
   actually sent). Reconnect the pack CAN1 lines. No `0x105` may appear: the pack must open
   its contactors and stay open. If any `0x105` appears, the interface down/up does not clear
   the queue on this driver; stop and fix that before using the bridge.

**D. Hardware facts not confirmed from the Waveshare wiki:** which kernel name each controller
gets (handled by the SPI check), the exact SPI device names (`spi1.0`/`spi0.0`, step 2), that
the mcp2515 driver accepts `listen-only on` (step A), that `cls_u32`, `cls_basic`, `sch_prio`
and `act_gact` exist in the Raspberry Pi kernel (`sudo modprobe -a can can_raw sch_prio cls_basic cls_u32 act_gact`; the Pi kernel has no `em_canid`, which is why pack CAN1 uses u32 byte matches),
and that a qdisc added while the interface is down survives `ip link set up` (step A shows it).
The overlays come from the Waveshare 2-CH CAN FD HAT wiki: SPI0 and SPI1 each with chip select 0, interrupts on
BCM 25/24, and the overlay lines in `system/config.txt.snippet`.

## Known limits

* The tc filter checks the ID word (so EFF, RTR and error frames never match), the length and all
  eight payload bytes, but not the three padding bytes of the `can_frame`. The padding is enforced by
  `txgate.check_tx` alone.
* If pack CAN1 stops ACKing, the CAN controller retries its current frame forever (error-passive,
  not bus-off). The setup uses `txqueuelen 1` (not the wiki's 65536), and the bridge exits on
  the first transmit error so that the restart's interface down/up discards the stuck frames.
  That this down/up really empties the controller's buffer is to be confirmed on the Pi
  (bench step C2).
* Python cannot make a stored socket truly unreachable. `Can2ReceiveOnly` keeps it in a
  name-mangled attribute and has no write method; the OS-layer guards are the hard barrier.
* The periodic interface check runs in a background thread (each command has a 0.5 s time
  limit); a check that has not completed for 20 s counts as a failure.
* Any CAN write error stops the bridge and restarts it. The client has to reconnect and send `START`.

## Development

    python3 -m venv .venv
    .venv/bin/pip install -e ../app -e '.[test]'
    .venv/bin/python -m pytest

Tests use a fake clock and fake or simulated sockets; nothing sleeps. `tests/test_structure.py`
fails if any new write call appears anywhere in the package.
