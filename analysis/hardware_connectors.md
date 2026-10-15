# BMS board connectors (from owner, verbatim labels; interpretations marked INFERRED)

Current sensor: 2-pin connector.

External connector (15 pins; some functions also used internally):
| Pin | Label | Likely meaning (INFERRED) |
|---|---|---|
| 1 | reset | MCU/board reset input |
| 2 | en12v | enable of the 12 V supply (wake-up input) |
| 3 | den | discharge enable input |
| 4 | can11h | CAN1 high (11-bit bus) |
| 5 | canh | CAN2 high (29-bit bus) |
| 6 | fuout | output, meaning unknown (fuel gauge? fuse?) |
| 7 | fatout | fault output |
| 8 | 12vout | switched 12 V output |
| 9 | gnd | ground |
| 10 | estp | emergency stop input |
| 11 | 5v | 5 V |
| 12 | cen | charge enable input |
| 13 | can11l | CAN1 low |
| 14 | canl | CAN2 low |
| 15 | alout gnd | alarm output / ground |

Contactor solenoid connector: 1 Prelay, 2 12V, 3 12V, 4 srelay. Owner correction: the two contactors are redundant and wired in series (a weld in one is cleared by the other); neither is a precharge relay. Precharge is the separate firmware output RD4, not on this connector.
Power: packv2, packv1 (pack voltage sense), bat+, bat-.
Two connectors of 14 pins each: temperature sensors.
Three connectors: module voltage lines labeled C0-C14, plus one NC (not connected).
Cell monitor chip printed "MC3371BSP1" = NXP MC33771B (owner confirmed).

## Public information found (web search, 2026-10-09; forum posts are anecdotal)
- Vanguard 3.8 kWh pack spec sheet (search snippet): lithium-ion, 14S, nominal 51.6 V, "dual CANbus", J1939 compatible. The PDF links returned 404, so no pinout or limits were retrieved.
- Factory lithium Tempo carts use a vehicle control module (VCM) plus the BMS talking over CAN (cartaholics.com forum threads). The charger is enabled through the VCM. A high-voltage interlock (HVIL) fuse can stop the pack energizing its outputs. The BMS cuts its 12 V output after about 10 s when the pack is low (forum claim).
- No Club Car or Vanguard wiring diagram or pinout found online. The factory service manual probably comes only through a Club Car dealer.

## Vehicle-side "BMS 19 PIN" connector diagram (owner image; pin letters do NOT match the board's numbering)
A (blank); B signal ground; C key wake up; D 12 VDC aux; E (blank); F 58 VDC MN EN; G 58 VDC DISP EN; H 58 VDC MAIN DC-DC;
J (blank); K OFF B CHGR WK UP (off-board charger wake up); L, M, N, P (blank); R GND HV; S master key return; T master key signal;
U CAN main L; V CAN main H; plus B(+) and B(-) power studs.
Owner note: these are some of the pins on the board connector described as mostly external with some internal functions.
Owner wiring facts: D goes to a diode anode; the diode cathode goes to the DC-DC converter. F goes to the key switch input (KSI) of the motor control unit and to the instrument panel (no function label). G goes to the Visage unit (Club Car's on-board display and fleet-management computer).
Hypothesis (revised after the Club Car diagnostic screen, see Appendix C of `CANBUS_PROTOCOL.md`): F, G, H are independently switched outputs (motor control unit, vehicle control module, DC-DC converter power), driven by `0x105` D0 bits 2, 4, 6, order unknown; D = `12vout`; the screen's "Key Switch Input" and "Charger Input" are the firmware's RB13 and RB14, probably vehicle pins C or T and K; which board labels (`en12v`, `den`, `estp`, `cen`) are RB11 and RB12 is unknown.
INFERRED correspondences with board labels: gnd = B; en12v/key = C or T/S; 12vout = D; cen = K (charger wake) ; canh/canl = V/U (one bus only appears on this connector).
