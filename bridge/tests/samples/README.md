Sample command output for the netcheck parser tests.

`filter_can1_ok.txt` was captured with `tc filter show dev can1 parent 1:` on the owner's Pi 4
(kernel 6.18.50+rpt-rpi-v8) after installing the three u32 rules from `netcheck.CAN1_U32_RULES`.

The others were written by hand from the iproute2 print code (ip/iplink_can.c, tc/f_basic.c,
tc/m_gact.c) and typical published output; they were NOT captured on the Pi. Capture the real output
with the commands in bridge/README.md ("Verify on the Pi"), replace these files, and rerun the tests.
