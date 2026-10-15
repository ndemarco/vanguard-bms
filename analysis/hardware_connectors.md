# BMS board connectors (from owner, verbatim labels; interpretations marked INFERRED)

Current sensor: 2-pin connector.

External connector (15 pins; some functions also used internally):
| Pin | Label | Likely meaning (INFERRED) |
|---|---|---|
| 1 | reset | MCU/board reset input |
| 2 | en12v | enable of the 12 V supply (wake-up input) |
| 3 | den | discharge enable input |
| 4 | can11h | CAN11 high (11-bit bus) |
| 5 | canh | CAN high (29-bit bus) |
| 6 | fuout | output, meaning unknown (fuel gauge? fuse?) |
| 7 | fatout | fault output |
| 8 | 12vout | switched 12 V output |
| 9 | gnd | ground |
| 10 | estp | emergency stop input |
| 11 | 5v | 5 V |
| 12 | cen | charge enable input |
| 13 | can11l | CAN11 low |
| 14 | canl | CAN low |
| 15 | alout gnd | alarm output / ground |

Contactor solenoid connector: 1 Prelay, 2 12V, 3 12V, 4 srelay. Owner correction: the two contactors are redundant and wired in series (a weld in one is cleared by the other); neither is a precharge relay. Precharge is the separate firmware output RD4, not on this connector.
Power: packv2, packv1 (pack voltage sense), bat+, bat-.
Two connectors of 14 pins each: temperature sensors.
Three connectors: module voltage lines labeled C0-C14, plus one NC (not connected).
Cell monitor chip printed "MC3371BSP1" = NXP MC33771B (owner confirmed).

## Pack serial number format (owner report, 2026-10-11)
The first pack's serial is 12 digits. The first six read as a YYMMDD date code (211229, that is 2021-12-29) [I]; the last six look like a sequence number and are withheld. The BMS stores the serial in CAN11 `0x510` (bench-confirmed 2026-10-11): B5-B7 = 211229 as a u24, B0-B3 most likely the sequence number as a u32 [I, owner to confirm]; see CANBUS_PROTOCOL.md section 4.2. A second pack's serial will be added for comparison.

## Main power terminals (B+ / B-)
Owner identified the pack's high-current terminals as Rebling 250 A LFT-style lithium battery terminals: https://www.rebling.com/products/lithium-battery-terminals/250-amp-lft-style-lithium-battery-terminal
To buy, when the pack is ready for reinstallation: terminal covers that fit these terminals.

## Public information found (web search, 2026-10-09; forum posts are anecdotal)
- Vanguard 3.8 kWh pack spec sheet (search snippet): lithium-ion, 14S, nominal 51.6 V, "dual CANbus", J1939 compatible. The PDF links returned 404, so no pinout or limits were retrieved.
- Owner-supplied nameplate data (model series, part number 80090401, 67.5 Ah, charge and cutoff voltages, Delta-Q algorithm #293) is in the "Pack nameplate" section of `CANBUS_PROTOCOL.md`.
- Factory lithium Tempo carts use a vehicle control module (VCM) plus the BMS talking over CAN (cartaholics.com forum threads). The charger is enabled through the VCM. A high-voltage interlock (HVIL) fuse can stop the pack energizing its outputs. The BMS cuts its 12 V output after about 10 s when the pack is low (forum claim).
- No Club Car or Vanguard wiring diagram found online. The only pinout found is the forum list in the next section. The factory service manual probably comes only through a Club Car dealer.

## Forum-posted 20-pin Vanguard battery pinout (buggiesgonewild.com, printthread.php?t=194016; anecdotal, unverified)
Wire colors and labels as posted. Numbering is the forum's 20-pin connector and does NOT match the 15-pin board numbering above.
| Pin | Color | Label |
|---|---|---|
| 1 | orange | ESTP |
| 2 | | n/c |
| 3 | red | 12VOUT |
| 4 | yellow | CANH |
| 5 | light green | CANL |
| 6 | black | GND |
| 7 | pink | CEN |
| 8 | grey | ALOUT |
| 9 | blue | 5V |
| 10 | red/white | FATOUT |
| 11 | red | 12V |
| 12 | white/black | EN12V |
| 13 | red (brown wire) | Solenoid #2 coil and DEN |
| 14 | black | GND |
| 15 | yellow/white | CAN11H |
| 16 | green | CAN11L |
| 17 | tan | RESET |
| 18 | | n/c |
| 19 | purple | FUOUT |
| 20 | | n/c |

## Owner's vehicle-side wiring on the 20-pin connector (owner report, 2026-10-09)
Pin numbers follow the forum's 20-pin numbering. These are all the external connections to the Club Car connector except the high-current B+ and B-.
| Pin | Vehicle wire color | Label |
|---|---|---|
| 1 | tan | ESTP |
| 3 | red | 12VOUT |
| 8 | orange/black | ALOUT |
| 12 | brown | EN12V |
| 15 | white | CAN11H |
| 16 | blue | CAN11L |
| 11 and 13 | green jumper | 12V tied to DEN (solenoid #2 coil) |

Owner photo of the vehicle-side plug face (pack model 80090401; serial withheld): circular plastic connector, 20 numbered cavities, same numbering as the forum list. Cavities 1, 3, 8, 11, 12, 13, 15 and 16 hold contacts; the rest are empty. That matches the wiring table exactly. Cavity 3 and the empty cavity 6 are larger than the others. No manufacturer or part number was visible in the photo. Owner reports the Deutsch marking "26-18"; this fits the Deutsch HDP20 series plug HDP26-18-20S? (shell size 18, 20 socket cavities, size 16 and size 20 contacts; the last letter is the seal code, E or N). Not confirmed against the connector itself; datasheet: https://www.te.com.cn/chn-zh/product-HDP26-18-20PN.datasheet.pdf (pin version).

Observations (INFERRED):
- Wire colors differ from the forum's colors on ESTP, ALOUT, CAN11H and CAN11L, so the forum colors say nothing about the vehicle harness.
- Only the 11-bit CAN bus (CAN11H/L) is connected. CANH/CANL (pins 4, 5) are unused by the vehicle.
- CEN, RESET, FUOUT, FATOUT, 5V and both grounds (6, 14) are unconnected. The vehicle harness has no signal ground pin on this connector.
- The jumper ties pin 11 (12V) to pin 13 (DEN and solenoid #2 coil), so DEN is held at the 12V level.
- Owner reasoning: pin 11 is a 12 V supply input, because the contactors will not close without external 12 V and the pack has nothing that generates 12 V. The vehicle wiring above shows no wire on pin 11 other than the jumper, so the 12 V source must reach the pack through pin 3 (12VOUT) or pin 12 (EN12V), or the label direction on pin 3 is from the vehicle's side. Unresolved by this reasoning; see the bench results below, which show the pack makes its own 12 V.

Comparison with the board list (INFERRED):
- Every board label appears in the forum list (reset, en12v, den, can11h/l, canh/l, fuout, fatout, 12vout, gnd, estp, 5v, cen, alout). Labels agree, so the forum connector is probably the same signal set on a different connector.
- The forum list adds a plain `12V` (pin 11, red) separate from `12VOUT` (pin 3), likely the 12 V supply input. The board list has no such label.
- Forum pin 13 ties DEN to the coil of solenoid #2, which would mean DEN is not a pure input. Unverified.
- Forum lists two grounds (6, 14); the board list has `gnd` and `alout gnd`. The ALOUT return may be a ground pin.

## Vehicle-side "BMS 19 PIN" connector diagram (owner image; pin letters do NOT match the board's numbering)
A (blank); B signal ground; C key wake up; D 12 VDC aux; E (blank); F 58 VDC MN EN; G 58 VDC DISP EN; H 58 VDC MAIN DC-DC;
J (blank); K OFF B CHGR WK UP (off-board charger wake up); L, M, N, P (blank); R GND HV; S master key return; T master key signal;
U CAN main L; V CAN main H; plus B(+) and B(-) power studs.
Owner note: these are some of the pins on the board connector described as mostly external with some internal functions.
Owner wiring facts: D goes to a diode anode; the diode cathode goes to the DC-DC converter. F goes to the key switch input (KSI) of the motor control unit and to the instrument panel (no function label). G goes to the Visage unit (Club Car's on-board display and fleet-management computer).
Hypothesis (revised after the Club Car diagnostic screen, see Appendix C of `CANBUS_PROTOCOL.md`): F, G, H are independently switched outputs (motor control unit, vehicle control module, DC-DC converter power), driven by `0x105` D0 bits 2, 4, 6, order unknown; D = `12vout`; the screen's "Key Switch Input" and "Charger Input" are the firmware's RB13 and RB14, probably vehicle pins C or T and K; which board labels (`en12v`, `den`, `estp`, `cen`) are RB11 and RB12 is unknown.
INFERRED correspondences with board labels: gnd = B; en12v/key = C or T/S; 12vout = D; cen = K (charger wake) ; canh/canl = V/U (one bus only appears on this connector).

## Bench results (2026-10-11, bench-confirmed)
- Input mapping: tying `estp` to B- sets CAN11 `0x113` B2 bit 1 (RB14); tying `en12v` to B- sets B2 bit 0 (RB13). So estp = RB14 and en12v = RB13, active low. This corrects the earlier guess in `analysis/core.md` that RB12 = en12v. `den` and `cen` are therefore the candidates for RB11/RB12.
- `12vout` measures 12 V with respect to B- with nothing external connected except `estp` and `en12v` tied to B-: the pack generates it, so `12vout` is a true output. This resolves the open question above about the 12 V source. Pin 11 (plain `12V`) of the circular connector also measures 12 V.
- Known-good enable configuration (contactors closed under `0x105` keep-alives): `estp` and `en12v` tied to B-, `den` at 12 V through the permanent green jumper joining cavities 11 and 13, `cen` and `alout` unconnected. This does not show that each of the three is required for the enable (RB12); that is not isolated yet.
- Details of the run are in Appendix D of `CANBUS_PROTOCOL.md`.
