"""Bridge service between the Vanguard BMS CAN buses and one controlling client.

Safety-critical code, in the order a reviewer should read it:

* ``txgate.py``  - the transmit allowlist (``check_tx``) and the one final send function
  (``TxGate.send``) that every CAN write goes through.
* ``core.py``    - the STOPPED/RUNNING state machine, keep-alive, watchdog, client grammar.
* ``canio.py``   - SocketCAN sockets; pack CAN is wrapped in a receive-only class.
* ``netcheck.py``- startup refusal when listen-only mode or the tc filters are missing.

See ../README.md.
"""

__version__ = "0.1.0"
