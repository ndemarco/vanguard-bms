"""Frame definition tables for CANBUS_PROTOCOL.md.

One definition per frame drives both the decoder and the encoder. Confidence tags
follow the document: "C" confirmed, "I" inferred, "?" unknown. Where the document
gives different tags for different aspects of one field (for example format [C],
sign [I]) the weaker tag is used and the detail is kept in ``note``.

Scaling convention: ``scaled = (raw - bias) * scale``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

CAN1 = 1
CAN2 = 2

PACK_TX = "pack_tx"  # frame the pack transmits
PACK_RX = "pack_rx"  # frame the pack receives (host traffic)


@dataclass(frozen=True)
class Field:
    name: str
    offset: int
    size: int
    order: str = "little"  # "little" or "big"
    signed: bool = False
    scale: float = 1
    bias: int = 0
    unit: str = ""
    conf: str = "C"
    mask: Optional[int] = None  # applies to single-byte fields: raw = (byte & mask) >> shift
    shift: int = 0
    invalid: frozenset = frozenset()  # raw values meaning "no data"
    always_invalid: bool = False  # raw kept, value never interpreted
    enum: Optional[dict] = None
    note: str = ""


@dataclass(frozen=True)
class Bit:
    """One boolean. ``bit`` may exceed 7: the bit then lives in a little-endian word at ``offset``."""

    name: str
    offset: int
    bit: int
    conf: str = "C"
    note: str = ""


@dataclass(frozen=True)
class Const:
    """Padding or constant bytes. Written by the encoder, ignored by the decoder."""

    offset: int
    value: bytes


@dataclass(frozen=True)
class FrameSpec:
    name: str
    bus: int
    can_id: int
    direction: str
    period_ms: Optional[int] = None
    gate: Optional[str] = None  # "d1_bit0": only sent while 0x105 D1 bit 0 was set
    dlc: int = 8
    fields: tuple = ()
    custom: Optional[str] = None  # name of a hand-written decoder in decoder.py
    note: str = ""


def _c1(name, off, size, **kw):
    return Field(name, off, size, "big", **kw)


def _c2(name, off, size, **kw):
    return Field(name, off, size, "little", **kw)


def _temp1(name, off, conf="C"):
    return _c1(name, off, 1, bias=40, unit="degC", conf=conf, invalid=frozenset({0}))


def _temp2(name, off, conf="C"):
    return _c2(name, off, 1, bias=40, unit="degC", conf=conf, invalid=frozenset({0}))


def _cur2(name, off, conf="I"):
    return _c2(name, off, 2, bias=32000, scale=0.05, unit="A", conf=conf,
               note="(raw - 32000) x 0.05 A; formula [C], sign (positive = charge) [I]")


def _pad(off, n, v=0xFF):
    return Const(off, bytes([v]) * n)


# ---------------------------------------------------------------------------
# Fault tables (section 6.4)
# ---------------------------------------------------------------------------

# (byte, bit, name, conf, blocking). Names are descriptive snake_case.
FAULT_BITS = (
    (0, 1, "cell_imbalance", "I", True),
    (1, 1, "current_sensor_offset_out_of_range", "C", True),
    (1, 2, "keepalive_timeout", "C", True),
    (1, 3, "cell_overvoltage_trip", "C", True),
    (1, 4, "cell_overvoltage_warning", "C", False),
    (1, 5, "cell_undervoltage_trip", "C", True),
    (1, 6, "cell_undervoltage_warning", "C", False),
    (2, 3, "enable_input_released", "I", True),
    (3, 1, "contactor_voltage_mismatch", "C", True),
    (3, 2, "output_voltage_mismatch_or_weld", "C", True),
    (3, 7, "overcurrent_trip", "C", True),
    (4, 0, "precharge_failed", "C", True),
    (4, 1, "monitor_chip_comm_failure", "C", True),
    (4, 6, "undertemperature_or_few_sensors", "C", True),
    (4, 7, "overtemperature", "C", True),
    (5, 1, "invalid_variant_index", "C", True),
    (5, 5, "temperature_spread_warning", "?", False),
    (5, 6, "extreme_overtemperature", "I", True),
    (5, 7, "persistent_store_fatal_event", "C", True),
)

# bit -> name for the CAN2 0x18FF08F3 words (section 6.4). Bit positions [C], names [I].
LATCHED_WORD_BITS = {
    0: "cell_undervoltage",
    1: "temperature_out_of_window",
    2: "overcurrent",
    3: "current_sensor_offset_bad",
    4: "cell_overvoltage",
    6: "cell_imbalance",
    8: "host_timeout",
    9: "contactor_precharge_voltage_fault",
    10: "enable_input_released",
    12: "monitor_comm_failure",
    15: "permanent_event_latched",
}
LIVE_WORD_BITS = {
    0: "low_cell_warning",
    1: "temperature_derate",
    2: "overcurrent_warning",
    3: "current_sensor_abnormal_idle",
    8: "config_byte_not_3_or_5",
    12: "monitor_read_stage_failed",
}

MODE_FF20 = {1: "off_idle", 2: "discharge_run", 3: "charge", 4: "service", 5: "startup"}
MODE_FF08 = {0x10: "startup", 1: "idle", 2: "discharge_run", 4: "charge", 8: "service"}
SUBSTATE_FF08 = {0: "no_fault", 1: "connecting", 2: "running", 8: "fault"}
LOG_EVENT_CODES = {
    0x0A: "bootloader_jump", 0x0C: "current_scale_set", 0x0D: "precharge_word_set",
    0x0F: "cell_limit_set", 0x10: "permanent_fault_logged", 0x11: "fault_cleared",
    0x12: "write_retry", 0x13: "serial_set", 0x14: "model_set", 0x15: "host_silence_sleep",
    0x16: "enable_dropped",
}
SLEEP_CMD = {0: "none", 1: "light_sleep", 2: "deep_shutdown", 3: "light_sleep"}

# ---------------------------------------------------------------------------
# CAN1 (11-bit, big-endian)
# ---------------------------------------------------------------------------

_CAN1_HOST_NOTE_BITS = "D0 bit-to-output order is unverified: signals are named by D0 bit"

CAN1_SPECS = (
    FrameSpec("host_keepalive", CAN1, 0x105, PACK_RX, fields=(
        _c1("mode_bits", 0, 1, mask=0x03, conf="I", enum={0: "discharge_path", 1: "charge_path",
            2: "charge_path", 3: "charge_path"}, note="0 = discharge path (mode 2), any non-zero = charge path (mode 4); naming [I]"),
        Bit("aux_out_bit2", 0, 2, "I", "drives LATD3; " + _CAN1_HOST_NOTE_BITS),
        Bit("aux_out_bit4", 0, 4, "I", "drives LATA4; " + _CAN1_HOST_NOTE_BITS),
        Bit("aux_out_bit6", 0, 6, "I", "drives LATA3; " + _CAN1_HOST_NOTE_BITS),
        Bit("request_gated_frames", 1, 0, "C", "D1 bit 0: enables 0x510, 0x512, 0x513, 0x51D"),
        Bit("clear_faults", 1, 1, "C", "D1 bit 1"),
        Bit("extend_timeout", 1, 2, "C", "D1 bit 2: timeout about 20 min instead of about 1 s"),
        Bit("enable", 1, 4, "C", "D1 bit 4: without it the pack stays idle"),
    ), note="keep-alive, send every 100 ms; timeout about 1 s"),
    FrameSpec("sleep_command", CAN1, 0x515, PACK_RX, fields=(
        _c1("sleep_request", 0, 1, mask=0x30, shift=4, conf="C", enum=SLEEP_CMD,
            note="D0 bits 5:4: 2 deep shutdown, 1 or 3 lighter sleep; sleep 500 ms later"),
    )),
    FrameSpec("sdo_request", CAN1, 0x614, PACK_RX, dlc=8, custom="sdo_request"),
    FrameSpec("sdo_reply", CAN1, 0x594, PACK_TX, dlc=8, custom="sdo_reply", note="DLC = n + 4"),

    FrameSpec("pack_current_limits", CAN1, 0x111, PACK_TX, 50, fields=(
        _c1("pack_current_ma", 0, 3, signed=True, unit="mA", conf="C",
            note="signed 24-bit, 1 mA/count; positive = into the pack (bench-confirmed 2026-10-11)"),
        _c1("discharge_limit_a", 3, 2, scale=0.1, unit="A", conf="I", note="format [C], direction [I]"),
        _c1("charge_limit_a", 5, 2, scale=0.1, unit="A", conf="I", note="format [C], direction [I]"),
        _c1("counter", 7, 1, conf="C"),
    )),
    FrameSpec("status_voltages", CAN1, 0x112, PACK_TX, 50, fields=(
        Bit("any_fault_byte_nonzero", 0, 1), Bit("warning_word_nonzero", 0, 2),
        Bit("contactor_a_driven", 0, 5, "C", "driver A, closes second"),
        Bit("contactor_b_driven", 1, 5, "C", "driver B, closes first"),
        _c1("pack_side_adc_v", 2, 2, scale=0.01, unit="V", conf="I", note="unit [I]"),
        _c1("pack_voltage_v", 4, 2, scale=0.01, unit="V", conf="C", note="sum of 14 cells, 5230 = 52.30 V"),
        _c1("output_side_adc_v", 6, 2, scale=0.01, unit="V", conf="I", note="unit [I]"),
    )),
    FrameSpec("state_soc", CAN1, 0x113, PACK_TX, 100, fields=(
        Bit("charge_mode", 0, 0, "C", "pack in charge mode (mode 4); naming [I]"),
        Bit("aux_echo_bit6", 0, 2, "C", "echo of 0x105 D0 bit 6"),
        Bit("balancing_active", 1, 0),
        Bit("aux_echo_bit2", 1, 2, "C", "echo of 0x105 D0 bit 2"),
        Bit("aux_echo_bit4", 1, 5, "C", "echo of 0x105 D0 bit 4"),
        Bit("input_rb13_asserted", 2, 0, "?", "debounced, pin low; meaning unknown"),
        Bit("input_rb14_asserted", 2, 1, "?", "debounced, pin low; meaning unknown"),
        Bit("substate_entered", 2, 2), Bit("substate_closing_or_closed", 2, 3),
        Bit("substate_blocked_by_fault", 2, 5),
        _c1("model_code", 3, 1, conf="C"),
        _c1("soh_pct", 5, 1, scale=0.5, unit="%", conf="I"),
        _c1("soc_pct", 6, 1, scale=0.5, unit="%", conf="C", note="0.5 %/count, 200 = 100 %"),
        _c1("temp_charge_limit_a", 7, 1, unit="A", conf="I"),
    )),
    FrameSpec("temp_cell_extremes", CAN1, 0x114, PACK_TX, 100, fields=(
        _temp1("hottest_temp_c", 0), _temp1("coolest_temp_c", 1),
        _c1("max_cell_mv", 2, 2, unit="mV"), _c1("min_cell_mv", 4, 2, unit="mV"),
        Const(6, b"\x00\x00"),
    )),
    FrameSpec("fault_bytes", CAN1, 0x120, PACK_TX, 100, custom="fault_bytes"),
    FrameSpec("identity", CAN1, 0x510, PACK_TX, 1000, "d1_bit0", fields=(
        _c1("identity_a", 0, 4, conf="I", note="role [I]"),
        _c1("identity_b_byte4", 4, 1, conf="?",
            note="doc lists B4-B7 but says u24 in B5-B7; byte 4 kept separately"),
        _c1("identity_b", 5, 3, conf="I", note="u24 in B5-B7"),
    )),
    FrameSpec("voltage_limits", CAN1, 0x512, PACK_TX, 1000, "d1_bit0", fields=(
        _c1("low_cell_limit_v", 0, 2, scale=0.01, unit="V"),
        _c1("high_cell_limit_v", 2, 2, scale=0.01, unit="V"),
        _c1("low_pack_limit_v", 4, 2, scale=0.01, unit="V"),
        _c1("high_pack_limit_v", 6, 2, scale=0.01, unit="V"),
    )),
    FrameSpec("capacity_ratings", CAN1, 0x513, PACK_TX, 1000, "d1_bit0", fields=(
        _c1("capacity_ah", 0, 1, unit="Ah", conf="I", note="unit [I]"),
        _c1("current_rating_1_a", 1, 2, scale=0.1, unit="A", conf="I"),
        _c1("current_rating_2_a", 3, 2, scale=0.1, unit="A", conf="I"),
        _c1("constant_5", 5, 1, conf="?"),
        _temp1("low_temp_limit_c", 6, "I"), _temp1("high_temp_limit_c", 7, "I"),
    )),
    FrameSpec("trip_ah", CAN1, 0x517, PACK_TX, 1000, fields=(
        _c1("trip_ah_discharge", 0, 2, unit="Ah", conf="I"), _c1("trip_ah_net", 2, 2, unit="Ah", conf="I"),
        _c1("trip_ah_regen", 4, 2, unit="Ah", conf="I"), _c1("trip_ah_charger", 6, 2, unit="Ah", conf="I"),
    )),
    FrameSpec("trip_wh", CAN1, 0x51A, PACK_TX, 1000, fields=(
        _c1("trip_wh_discharge", 0, 2, unit="Wh", conf="I"), _c1("trip_wh_net", 2, 2, unit="Wh", conf="I"),
        _c1("trip_wh_regen", 4, 2, unit="Wh", conf="I"), _c1("trip_wh_charger", 6, 2, unit="Wh", conf="I"),
    )),
    FrameSpec("lifetime_wh_net_regen", CAN1, 0x518, PACK_TX, 1000, fields=(
        _c1("lifetime_wh_net", 0, 4, signed=True, unit="Wh", conf="I"),
        _c1("lifetime_wh_regen", 4, 4, unit="Wh", conf="I"),
    )),
    FrameSpec("lifetime_wh_charger_discharge", CAN1, 0x519, PACK_TX, 1000, fields=(
        _c1("lifetime_wh_charger", 0, 4, unit="Wh", conf="I"),
        _c1("lifetime_wh_discharge", 4, 4, unit="Wh", conf="I"),
    )),
    FrameSpec("lifetime_ah_net_regen", CAN1, 0x51B, PACK_TX, 1000, fields=(
        _c1("lifetime_ah_net", 0, 4, signed=True, unit="Ah", conf="I"),
        _c1("lifetime_ah_regen", 4, 4, unit="Ah", conf="I"),
    )),
    FrameSpec("lifetime_ah_charger_discharge", CAN1, 0x51C, PACK_TX, 1000, fields=(
        _c1("lifetime_ah_charger", 0, 4, unit="Ah", conf="I"),
        _c1("lifetime_ah_discharge", 4, 4, unit="Ah", conf="I"),
    )),
    FrameSpec("version_target", CAN1, 0x51D, PACK_TX, 1000, "d1_bit0", fields=(
        _c1("fw_major", 0, 1, conf="I"), _c1("fw_minor", 1, 1, conf="I"), _c1("fw_patch", 2, 1, conf="I"),
        _c1("board_address", 3, 1, conf="I", note="strap value 0x28..0x2E, or 0; [C]/[I]"),
        _c1("charge_target_mv", 4, 2, unit="mV", conf="I"),
        _c1("model_constant", 6, 2, conf="?"),
    )),
)

# ---------------------------------------------------------------------------
# CAN2 (29-bit, little-endian). Listed in the firmware scheduler order (can2tx.md).
# ---------------------------------------------------------------------------

_cell = lambda n, off: _c2(f"cell{n}_mv", off, 2, unit="mV")  # noqa: E731

CAN2_SPECS = (
    FrameSpec("c2_summary", CAN2, 0x0CFF20F3, PACK_TX, 100, fields=(
        _c2("mode", 0, 1, conf="I", enum=MODE_FF20, note="mapping [C], names [I]"),
        _c2("fault_present", 1, 1, conf="C"),
        _c2("remaining_capacity_ah", 2, 2, scale=0.01, unit="Ah", conf="I", note="unit [I]"),
        _c2("cell_sum_mv", 4, 2, unit="mV"),
        _cur2("current_a", 6, "I"),
    )),
    FrameSpec("c2_extremes", CAN2, 0x0CFF21F3, PACK_TX, 100, fields=(
        _temp2("max_temp_c", 0), _temp2("min_temp_c", 1),
        _c2("max_cell_mv", 2, 2, unit="mV"), _c2("min_cell_mv", 4, 2, unit="mV"), Const(6, b"\xff\xff"),
    )),
    FrameSpec("c2_soc_x640", CAN2, 0x0CF094F3, PACK_TX, 100, fields=(
        _c2("bytes_0_3_defective", 0, 4, conf="C", always_invalid=True,
            note="firmware defect: stale bytes from the previous frame; never interpret"),
        _c2("soc_pct", 4, 2, scale=1 / 640, unit="%", conf="C", note="SOC x 640, 100 % = 64000"),
        _c2("soc_highcell_pct", 6, 2, scale=1 / 640, unit="%", conf="I", note="second SOC from highest cell [I]"),
    )),
    FrameSpec("c2_smoothed_current", CAN2, 0x0CFF31F3, PACK_TX, 100, fields=(
        _cur2("smoothed_current_a", 0, "I"),
        _c2("overcurrent_counter_a", 2, 2, conf="C"),
        _cur2("mean_current_a", 4, "I"),
        Const(6, b"\x00"),
        _c2("overcurrent_counter_b", 7, 1, conf="C", note="low byte"),
    )),
    FrameSpec("c2_adc_voltages", CAN2, 0x0CFF32F3, PACK_TX, 100, fields=(
        _c2("cell_sum_mv", 0, 2, unit="mV"),
        _c2("pack_side_adc_mv", 2, 2, unit="mV", conf="I", note="ADC scale uncertain"),
        _c2("output_side_adc_mv", 4, 2, unit="mV", conf="I", note="ADC scale uncertain"),
        Const(6, b"\x00\x00"),
    )),
    FrameSpec("c2_learned_capacity", CAN2, 0x0CFF33F3, PACK_TX, 1000, fields=(
        _c2("soh_pct", 0, 2, scale=0.1, unit="%", conf="I"),
        _c2("learned_capacity_1", 2, 2, conf="?", note="unit unknown"),
        _c2("learned_capacity_2", 4, 2, conf="?", note="unit unknown"),
        _c2("learned_capacity_3", 6, 2, conf="?", note="unit unknown"),
    )),
    FrameSpec("c2_capacity_counters", CAN2, 0x0CFF34F3, PACK_TX, 1000, fields=(
        _c2("measurements_done", 0, 2, conf="?"),
        _c2("reference_capacity", 2, 2, conf="?", note="model table 460, 487, 615, 772, 413, 441; [C]/[?]"),
        _c2("ring_index", 4, 2, conf="?"),
        _c2("latest_learned_capacity", 6, 2, conf="?"),
    )),
    FrameSpec("c2_soh_bounds", CAN2, 0x0CFF35F3, PACK_TX, 500, fields=(
        _c2("soh_upper_pct", 0, 2, scale=0.1, unit="%", conf="I"),
        _c2("soh_lower_pct", 2, 2, scale=0.1, unit="%", conf="I"),
    )),
    FrameSpec("c2_pack_config", CAN2, 0x0CFF3EF3, PACK_TX, 500, fields=(
        _c2("model_index", 0, 1, conf="I"), _c2("type_code", 1, 1, conf="?"),
        _c2("variant_id", 2, 1, conf="I"), _c2("parallel_cells_guess", 3, 1, conf="I"),
        _c2("series_cells", 4, 1, conf="I"), Const(5, b"\x01"),
        _c2("capacity_ah", 6, 2, scale=0.01, unit="Ah", conf="I"),
    )),
    FrameSpec("c2_pack_voltage_current", CAN2, 0x0CF090F3, PACK_TX, 500, fields=(
        Const(0, b"\x00\x00\x00\x00"),
        _c2("pack_voltage_v", 4, 2, scale=0.05, unit="V", conf="C"),
        _cur2("current_a", 6, "I"),
    )),
    FrameSpec("c2_soc_precharge", CAN2, 0x0CFF06F3, PACK_TX, 1000, fields=(
        _c2("soc_pct", 0, 2, unit="%"),
        Const(2, b"\xff\xff\xff"),
        _c2("precharge_threshold_pct", 5, 1, unit="%"),
        _c2("config_word", 6, 2, note="default 3000"),
    )),
    FrameSpec("c2_capacity_balancing", CAN2, 0x0CF093F3, PACK_TX, 1000, fields=(
        _c2("remaining_capacity_ah", 0, 2, scale=0.01, unit="Ah", conf="I"),
        _c2("missing_capacity_ah", 2, 2, scale=0.01, unit="Ah", conf="I", note="unit assumed same as remaining"),
        _c2("balancing_mask", 4, 2, conf="I"),
        *[Bit(f"balancing_cell{n}", 4, n - 1, "I") for n in range(1, 15)],
        _c2("rated_capacity_ah", 6, 2, scale=0.01, unit="Ah", conf="I", note="unit assumed same as remaining"),
    )),
    FrameSpec("c2_output_voltage", CAN2, 0x0CF095F3, PACK_TX, 500, fields=(
        _c2("output_voltage_v", 0, 2, scale=0.05, unit="V", conf="I"), Const(2, b"\x00" * 6),
    )),
    FrameSpec("c2_cell_stats", CAN2, 0x0CF10AF3, PACK_TX, 500, fields=(
        _c2("max_cell_number", 0, 1), Const(1, b"\xff"), _c2("min_cell_number", 2, 1), Const(3, b"\xff"),
        _c2("mean_cell_mv", 4, 2, unit="mV"), _c2("spread_mv", 6, 2, unit="mV"),
    )),
    FrameSpec("c2_temps_1_7", CAN2, 0x0CFF01F3, PACK_TX, 500, fields=(
        *[_temp2(f"temp{n}_c", n - 1) for n in range(1, 8)], Const(7, b"\xff"),
    )),
    FrameSpec("c2_temps_8_14", CAN2, 0x0CFF02F3, PACK_TX, 500, fields=(
        *[_temp2(f"temp{n}_c", n - 8) for n in range(8, 15)], Const(7, b"\xff"),
    )),
    FrameSpec("c2_cells_1_4", CAN2, 0x18FF03F3, PACK_TX, 500, fields=tuple(_cell(n, 2 * (n - 1)) for n in range(1, 5))),
    FrameSpec("c2_cells_5_8", CAN2, 0x18FF04F3, PACK_TX, 500, fields=tuple(_cell(n, 2 * (n - 5)) for n in range(5, 9))),
    FrameSpec("c2_cells_9_12", CAN2, 0x18FF05F3, PACK_TX, 500, fields=tuple(_cell(n, 2 * (n - 9)) for n in range(9, 13))),
    FrameSpec("c2_cells_13_14", CAN2, 0x18FF06F3, PACK_TX, 500, fields=(
        _cell(13, 0), _cell(14, 2), Const(4, b"\xff\xff\xff\xff"),
    )),
    FrameSpec("c2_output_state", CAN2, 0x0CF096F3, PACK_TX, 500, fields=(
        Bit("driver_b_on", 0, 0), Bit("driver_a_on", 0, 2),
        Const(1, b"\xff\xff"), Const(3, b"\x00"), Const(4, b"\xff\xff"),
        Bit("input_rb14_low", 6, 4, "?", "pin-to-label mapping unknown"),
        Bit("input_rb12_low", 6, 5, "?", "pin-to-label mapping unknown"),
        Bit("input_rb11_low", 6, 6, "?", "pin-to-label mapping unknown"),
        Bit("input_rb13_low", 6, 7, "?", "pin-to-label mapping unknown"),
        Bit("out_driver_b", 7, 0), Bit("aux_lata4", 7, 1, "I", "echo of 0x105 D0 bit 4"),
        Bit("aux_lata3", 7, 2, "I", "echo of 0x105 D0 bit 6"), Bit("aux_latd3", 7, 3, "I", "echo of 0x105 D0 bit 2"),
        Bit("board_on_outputs", 7, 4), Bit("out_driver_a", 7, 5),
    )),
    FrameSpec("c2_status_words", CAN2, 0x18FF08F3, PACK_TX, 500, fields=(
        _c2("mode_raw", 0, 1, enum=MODE_FF08),
        _c2("substate", 1, 1, enum=SUBSTATE_FF08, conf="I", note="values [C], names [I]"),
        _c2("latched_word", 2, 2),
        *[Bit(f"latched_{n}", 2, b, "I", f"latched word bit {b}") for b, n in LATCHED_WORD_BITS.items()],
        _c2("live_word", 4, 2),
        *[Bit(f"live_{n}", 4, b, "I", f"live word bit {b}") for b, n in LIVE_WORD_BITS.items()],
        Const(6, b"\x00"),
        _c2("config_byte", 7, 1, note="default 9; overwritten by data[4] of RX 0x0CFFBC49"),
    )),
    FrameSpec("c2_temp_stats", CAN2, 0x18FF07F3, PACK_TX, 500, fields=(
        _temp2("max_temp_c", 0), _temp2("min_temp_c", 1), _temp2("mean_temp_c", 2),
        Const(3, b"\x00"), Const(4, b"\xff" * 4),
    )),
    FrameSpec("c2_constant", CAN2, 0x18FF30F3, PACK_TX, 5000, fields=(
        _c2("constant_pattern", 0, 8, conf="C", note="expected CD E9 02 93 4A 00 00 00"),
    )),
    FrameSpec("c2_current_scale", CAN2, 0x18FF31F3, PACK_TX, 500, fields=(
        _c2("current_scale", 0, 2, scale=1e-4, conf="C", note="default 10000 = 1.0000"),
        _c2("charge_cell_limit_mv", 2, 2, unit="mV"),
        _c2("scale_ratio_x100", 4, 2),
        _c2("current_zero_offset_counts", 6, 2, signed=True, conf="I",
            note="doc gives no signedness; signed assumed because the captured offset is within +-199"),
    )),
    FrameSpec("c2_monitor_identity", CAN2, 0x18FF32F3, PACK_TX, 500, fields=(
        _c2("identity_word", 0, 4), Const(4, b"\x00" * 4),
    )),
    FrameSpec("c2_log_status", CAN2, 0x18FF50F3, PACK_TX, 500, fields=(
        Field("log_write_pointer", 0, 4, "big"),
        _c2("verify_status", 4, 1, note="0 = ok"),
        Field("write_count", 5, 2, "big"),
        _c2("last_event_code", 7, 1, enum=LOG_EVENT_CODES),
    ), custom="log_status", note="may instead be a CAN1 error pattern; no discriminator"),
    FrameSpec("c2_log_index", CAN2, 0x18FF51F3, PACK_TX, fields=(
        _c2("latest_record_number", 0, 4), _c2("raw_pointer", 4, 4, conf="I", note="byte order not stated; LE assumed"),
    ), note="reply to command 0xC9"),
    FrameSpec("c2_log_record", CAN2, 0x18FF52F3, PACK_TX, custom="log_record", note="reply to command 0xCB"),

    # Frames the pack receives
    FrameSpec("c2_maintenance_command", CAN2, 0x0CFF00F3, PACK_RX, custom="maintenance",
              note="any frame with this ID puts the pack in service mode"),
    FrameSpec("c2_charger_config", CAN2, 0x0CFFBC49, PACK_RX, fields=(
        _c2("config_byte", 4, 1, conf="C", note="stored as config byte; not 3 or 5 sets a transient warning"),
    ), custom="charger_config", note="sender probably a charger (node 0x49) [I]"),
    FrameSpec("c2_ignored_1bf349", CAN2, 0x0C1BF349, PACK_RX, note="accepted and ignored"),
    FrameSpec("c2_ignored_1bff49", CAN2, 0x0C1BFF49, PACK_RX, note="accepted and ignored"),
    FrameSpec("c2_ignored_dfffff3", CAN2, 0x18DFFFF3, PACK_RX, note="accepted and ignored; not a J1939 request"),
)

# 0x120 fault bytes are generated from FAULT_BITS (custom decoder adds the byte signals).
_FAULT_FIELDS = tuple(
    [_c1(f"b{i}", i, 1, conf="C") for i in range(8)]
    + [Bit(n, b, bit, c, ("blocking fault" if blk else "non-blocking warning")) for b, bit, n, c, blk in FAULT_BITS]
)

ALL_SPECS = CAN1_SPECS + CAN2_SPECS
SPEC_BY_KEY = {(s.bus, s.can_id): s for s in ALL_SPECS}
SPEC_BY_NAME = {s.name: s for s in ALL_SPECS}

# Periodic scheduling order (section 4.3 / can1.md chains / can2tx.md scheduler list).
CAN1_CHAIN_1 = (0x111, 0x51D, 0x51C, 0x51B, 0x517, 0x518, 0x519)
CAN1_CHAIN_2 = (0x51A, 0x120, 0x114, 0x113, 0x112, 0x510, 0x512, 0x513)


def fields_of(spec: FrameSpec) -> tuple:
    """Field list for a spec, resolving the generated 0x120 table."""
    if spec.name == "fault_bytes":
        return _FAULT_FIELDS
    return spec.fields
