# rdsup: Riden charge supervisor

Standalone, stdlib-only (Python 3.11+) watchdog for charging the 14S pack from a Riden RD6018.
It reads the pack on `can0` (CAN1 frames `0x111`, `0x112`, `0x114`, `0x120`, receive only) and
polls the Riden over Modbus RTU (`/dev/ttyUSB0`, 115200 8N1, slave 1) every 0.5 s. When anything
looks wrong it switches the Riden output **OFF**.

It never switches the output on and never transmits on CAN. The only write it can make is
Modbus function 0x06, register 18, value 0, in `riden_output_off()`. That function refuses any
other register or value. `SerialPort.transact` also refuses every non-read frame except that
exact OFF frame, and refuses even that one in `--dry-run`. AST tests in `tests/test_rdsup.py`
fail if a CAN send or a second write call site is added.

```bash
python3 supervisor/rdsup.py                    # supervise
python3 supervisor/rdsup.py --dry-run          # never writes; prints WOULD SWITCH OFF
python3 supervisor/rdsup.py --dry-run --run-seconds 6
```

Ctrl-C/SIGTERM just exits **without touching the output**. If the output was on, it stays on and nothing supervises it.

## Trips (all latched: after a trip the tool keeps the output off and never re-arms; restart it to re-arm)

| Condition | Default |
|---|---|
| max cell > `--max-cell-mv` | 4150 mV |
| min cell < `--min-cell-mv` (only while output on) | 3000 mV |
| hottest > `--max-temp-c` / coolest < `--min-temp-c` (byte 0 = no sensor, ignored) | 45 / 0 degC |
| any blocking bit in `0x120` (all bits except B1.4, B1.6, B5.5) | |
| pack voltage (`0x112` B4-B5) > `--max-pack-v` | 58.1 V |
| Riden V_OUT > max-pack-v + 0.3, I_OUT > I_SET + 0.5, I_OUT > `--max-current-a`, r16 protection != 0 | 10.5 A |
| output on while contactor drivers A and B are not both set, for > 1.0 s | |
| output on and no `0x112` or `0x114` for > `--can-timeout-s` (output off: report only) | 1.0 s |
| 3 consecutive failed Riden polls: try OFF anyway, loud banner | |
| charge complete: output on, I_OUT < `--done-current-a` (CV or not) for `--done-seconds` | 1.0 A / 120 s |

After any trip it writes the OFF register and reads r18 back to confirm. It tries up to 3 times per poll.
If it cannot confirm, it prints a FAILED banner every second and keeps retrying. If the output turns
on again after a trip, it switches it off again.

Every 2 s it prints a status line and appends a JSONL record to `--log` (default `~/rdsup-<date>.jsonl`).
Trip and switch-off events are also logged.

Tests: `python -m pytest -q supervisor/tests` (needs pytest only).
