"""CAN decoder tests: payloads are built by hand from CANBUS_PROTOCOL.md section 5."""

import struct

import pytest

from vanguard.decoder import decode


def d2(can_id, data):
    f = decode(2, can_id, bytes.fromhex(data.replace(" ", "")) if isinstance(data, str) else data)
    assert f is not None
    return f


def le16(*vals):
    return struct.pack("<" + "H" * len(vals), *vals)


def test_current_formula_and_sign_note():
    # raw 31000 -> (31000 - 32000) x 0.05 = -50.0 A ; raw 32800 -> +40.0 A
    f = d2(0x0CFF20F3, bytes([2, 0]) + le16(5000) + le16(51800) + le16(31000))
    assert f.raw("current_a") == 31000
    assert f.value("current_a") == pytest.approx(-50.0)
    assert f.signal("current_a").conf == "I"
    g = d2(0x0CFF20F3, bytes([2, 0]) + le16(0) + le16(0) + le16(32800))
    assert g.value("current_a") == pytest.approx(40.0)


def test_summary_fields_little_endian():
    f = d2(0x0CFF20F3, bytes([3, 1]) + le16(4000) + le16(51870) + le16(32000))
    assert f.value("mode") == "charge" and f.value("fault_present") == 1
    assert f.value("remaining_capacity_ah") == pytest.approx(40.0)
    assert f.value("cell_sum_mv") == 51870
    assert f.value("current_a") == 0


def test_endianness_differs_between_buses():
    payload = bytes([0x0E, 0x74, 0, 0, 0, 0, 0, 0])
    can2 = decode(2, 0x18FF03F3, payload)
    assert can2.value("cell1_mv") == 0x740E
    can1 = decode(1, 0x114, bytes([0x42, 0x42, 0x0E, 0x74, 0x0E, 0x74, 0, 0]))
    assert can1.value("max_cell_mv") == 0x0E74 == 3700


def test_cell_voltages_and_padding():
    f = d2(0x18FF03F3, le16(3700, 3705, 3710, 3715))
    assert [f.value(f"cell{n}_mv") for n in (1, 2, 3, 4)] == [3700, 3705, 3710, 3715]
    g = d2(0x18FF06F3, le16(3720, 3725, 0xFFFF, 0xFFFF))
    assert g.value("cell13_mv") == 3720 and g.value("cell14_mv") == 3725
    assert not any(s.name.startswith("cell1") and s.name[4:6] in ("5_", "6_") for s in g.signals)
    assert [decode(2, i, le16(1, 2, 3, 4)).value(f"cell{n}_mv")
            for i, n in ((0x18FF04F3, 5), (0x18FF05F3, 9))] == [1, 1]


def test_temperatures_byte_minus_40_and_invalid():
    f = d2(0x0CFF01F3, bytes([62, 63, 0, 20, 110, 65, 66, 0xFF]))
    assert f.value("temp1_c") == 22 and f.value("temp2_c") == 23
    assert f.value("temp3_c") is None and not f.signal("temp3_c").valid
    assert f.value("temp4_c") == -20 and f.value("temp5_c") == 70
    g = d2(0x0CFF02F3, bytes([61, 62, 63, 64, 65, 66, 67, 0xFF]))
    assert [g.value(f"temp{n}_c") for n in range(8, 15)] == [21, 22, 23, 24, 25, 26, 27]


def test_extremes_0cff21f3():
    f = d2(0x0CFF21F3, bytes([66, 61]) + le16(3720) + le16(3690) + b"\xff\xff")
    assert (f.value("max_temp_c"), f.value("min_temp_c")) == (26, 21)
    assert (f.value("max_cell_mv"), f.value("min_cell_mv")) == (3720, 3690)


def test_0cf094f3_defect_bytes_never_decoded():
    # Stale copy of 0x0CFF21F3 bytes 0-3 in B0-B3; SOC x 640 = 64000 -> 100 %, 32000 -> 50 %
    f = d2(0x0CF094F3, bytes([66, 61, 0x88, 0x0E]) + le16(64000) + le16(32000))
    s = f.signal("bytes_0_3_defective")
    assert not s.valid and s.value is None
    assert s.raw == int.from_bytes(bytes([66, 61, 0x88, 0x0E]), "little")
    assert f.value("soc_pct") == pytest.approx(100.0)
    assert f.value("soc_highcell_pct") == pytest.approx(50.0)
    # whatever the bytes hold, no other signal depends on them
    g = d2(0x0CF094F3, bytes([0xAA] * 4) + le16(64000) + le16(32000))
    assert [(x.name, x.value) for x in g.signals if x.name != "bytes_0_3_defective"] == \
        [(x.name, x.value) for x in f.signals if x.name != "bytes_0_3_defective"]
    assert {x.name for x in f.signals} == {"bytes_0_3_defective", "soc_pct", "soc_highcell_pct"}


def test_priority_keying_18ff06f3_vs_0cff06f3():
    a = decode(2, 0x18FF06F3, le16(3700, 3700, 0xFFFF, 0xFFFF))
    b = decode(2, 0x0CFF06F3, le16(45) + b"\xff\xff\xff" + bytes([70]) + le16(3000))
    assert a.name == "c2_cells_13_14" and b.name == "c2_soc_precharge"
    assert b.value("soc_pct") == 45 and b.value("precharge_threshold_pct") == 70
    assert b.value("config_word") == 3000
    assert a.can_id != b.can_id


def test_extended_flag_is_stripped():
    assert decode(2, 0x18FF03F3 | 0x80000000, bytes(8)).name == "c2_cells_1_4"


def test_pack_voltage_current():
    f = d2(0x0CF090F3, bytes(4) + le16(1037) + le16(32000))  # 1037 x 0.05 = 51.85 V
    assert f.value("pack_voltage_v") == pytest.approx(51.85)
    assert f.value("current_a") == 0


def test_output_voltage():
    assert d2(0x0CF095F3, le16(1037) + bytes(6)).value("output_voltage_v") == pytest.approx(51.85)


def test_cell_stats():
    f = d2(0x0CF10AF3, bytes([5, 0xFF, 9, 0xFF]) + le16(3705) + le16(30))
    assert (f.value("max_cell_number"), f.value("min_cell_number")) == (5, 9)
    assert (f.value("mean_cell_mv"), f.value("spread_mv")) == (3705, 30)


def test_capacity_and_balancing_mask():
    f = d2(0x0CF093F3, le16(3285) + le16(4015) + le16(0b101) + le16(7300))
    assert f.value("remaining_capacity_ah") == pytest.approx(32.85)
    assert f.value("rated_capacity_ah") == pytest.approx(73.0)
    assert f.value("balancing_mask") == 5
    assert f.value("balancing_cell1") and not f.value("balancing_cell2") and f.value("balancing_cell3")
    assert sum(f.value(f"balancing_cell{n}") for n in range(1, 15)) == 2


def test_smoothed_current_and_counters():
    f = d2(0x0CFF31F3, le16(31000) + le16(3) + le16(32400) + bytes([0, 9]))
    assert f.value("smoothed_current_a") == pytest.approx(-50.0)
    assert f.value("overcurrent_counter_a") == 3
    assert f.value("mean_current_a") == pytest.approx(20.0)
    assert f.value("overcurrent_counter_b") == 9


def test_adc_and_health_and_config_frames():
    f = d2(0x0CFF32F3, le16(51870, 51000, 50900) + b"\0\0")
    assert f.value("pack_side_adc_mv") == 51000 and f.signal("pack_side_adc_mv").conf == "I"
    g = d2(0x0CFF33F3, le16(1000, 7300, 7200, 7100))
    assert g.value("soh_pct") == pytest.approx(100.0)
    h = d2(0x0CFF3EF3, bytes([0, 0, 1, 24, 14, 1]) + le16(7300))
    assert h.value("series_cells") == 14 and h.value("variant_id") == 1
    assert h.value("capacity_ah") == pytest.approx(73.0)
    i = d2(0x0CFF35F3, le16(1000, 900, 0, 0))
    assert i.value("soh_lower_pct") == pytest.approx(90.0)
    j = d2(0x0CFF34F3, le16(0, 460, 1, 7000))
    assert j.value("reference_capacity") == 460


def test_output_state_and_inputs():
    # B0: driver B (bit0) and driver A (bit2). B6: bit7 RB13 + bit5 RB12. B7: driver B, LATA4, LATD3, board on, driver A
    f = d2(0x0CF096F3, bytes([0x05, 0xFF, 0xFF, 0, 0xFF, 0xFF, 0xA0, 0b0011_1011]))
    assert f.value("driver_b_on") and f.value("driver_a_on")
    assert f.value("input_rb13_low") and f.value("input_rb12_low")
    assert not f.value("input_rb11_low") and not f.value("input_rb14_low")
    assert f.value("out_driver_b") and f.value("aux_lata4") and not f.value("aux_lata3") and f.value("aux_latd3")
    assert f.value("board_on_outputs") and f.value("out_driver_a")


def test_status_words_and_fault_bit_names():
    # mode 2, sub-state 8 (fault), latched: host timeout (bit 8) + overcurrent (bit 2), live: bit 0, config byte 9
    f = d2(0x18FF08F3, bytes([2, 8]) + le16((1 << 8) | (1 << 2)) + le16(1) + bytes([0, 9]))
    assert f.value("mode_raw") == "discharge_run" and f.value("substate") == "fault"
    assert f.value("latched_host_timeout") and f.value("latched_overcurrent")
    assert not f.value("latched_cell_undervoltage")
    assert f.value("live_low_cell_warning") and f.value("config_byte") == 9
    assert f.raw("latched_word") == 0x104


@pytest.mark.parametrize("bit,name", [(0, "cell_undervoltage"), (1, "temperature_out_of_window"), (2, "overcurrent"),
                                      (3, "current_sensor_offset_bad"), (4, "cell_overvoltage"),
                                      (6, "cell_imbalance"), (8, "host_timeout"),
                                      (9, "contactor_precharge_voltage_fault"), (10, "enable_input_released"),
                                      (12, "monitor_comm_failure"), (15, "permanent_event_latched")])
def test_latched_word_bits(bit, name):
    f = d2(0x18FF08F3, bytes([1, 0]) + le16(1 << bit) + bytes(4))
    assert f.value("latched_" + name) is True
    assert sum(1 for s in f.signals if s.name.startswith("latched_") and s.value is True) == 1


def test_mode_values():
    assert d2(0x18FF08F3, bytes([0x10]) + bytes(7)).value("mode_raw") == "startup"
    assert d2(0x18FF08F3, bytes([8]) + bytes(7)).value("mode_raw") == "service"
    assert d2(0x0CFF20F3, bytes([5]) + bytes(7)).value("mode") == "startup"
    assert d2(0x0CFF20F3, bytes([4]) + bytes(7)).value("mode") == "service"


def test_temp_stats_and_constants():
    f = d2(0x18FF07F3, bytes([66, 61, 63, 0, 255, 255, 255, 255]))
    assert (f.value("max_temp_c"), f.value("min_temp_c"), f.value("mean_temp_c")) == (26, 21, 23)
    c = d2(0x18FF30F3, bytes.fromhex("CDE902934A000000"))
    assert c.raw("constant_pattern") == int.from_bytes(bytes.fromhex("CDE902934A000000"), "little")
    s = d2(0x18FF31F3, le16(10000, 4150, 100, 0xFFFB))
    assert s.value("current_scale") == pytest.approx(1.0) and s.value("charge_cell_limit_mv") == 4150
    assert s.value("current_zero_offset_counts") == -5
    assert d2(0x18FF32F3, bytes([1, 2, 3, 4, 0, 0, 0, 0])).raw("identity_word") == 0x04030201


def test_log_status_and_can1_error_patterns():
    f = d2(0x18FF50F3, bytes([0, 0, 1, 0x40, 0, 0, 5, 0x11]))
    assert f.value("log_write_pointer") == 0x140 and f.value("write_count") == 5
    assert f.value("last_event_code") == "fault_cleared"
    for payload, label in ((bytes([1, 0] * 4), "bus_off"), (bytes([2] * 8), "transmit_bus_passive"),
                           (bytes([1, 2] * 4), "receive_bus_passive"), (bytes([1] * 8), "receive_buffer_overflow")):
        assert d2(0x18FF50F3, payload).value("can1_error_pattern") == label


def test_log_replies():
    f = d2(0x18FF51F3, le16(5, 0) + struct.pack("<I", 0x1000))
    assert f.value("latest_record_number") == 5
    r0 = d2(0x18FF52F3, bytes([0, 5, 0, 0, 1, 2, 3, 4]))
    assert r0.value("frame_index") == 0 and r0.value("record_number_low_bytes") == 5
    r1 = d2(0x18FF52F3, bytes([3, 1, 2, 3, 4, 5, 6, 7]))
    assert r1.value("frame_index") == 3 and r1.raw("record_bytes") == int.from_bytes(bytes(range(1, 8)), "little")


# Frames the pack receives ----------------------------------------------------------------

@pytest.mark.parametrize("data,cmd,checks", [
    ("C3 00 28 0A D8 0E 27 F1", "set_balance_parameters",
     {"balance_start_spread_mv": 40, "balance_stop_spread_mv": 10, "balance_floor_mv": 3800, "inverse_check_ok": True}),
    ("00 00 00 00 00 00 00 00", "leave_service_mode", {}),
    ("EC 00 00 00 10 27 EF D8", "set_current_gain", {"current_gain_x1e4": 10000, "inverse_check_ok": True}),
    ("C4 00 00 00 36 10 C9 EF", "set_cell_charge_limit", {"cell_charge_limit_mv": 4150, "inverse_check_ok": True}),
    ("D3 00 00 00 02 FD 00 00", "select_variant", {"variant": 2, "inverse_check_ok": True}),
    ("CF 00 00 00 00 00 00 00", "clear_permanent_fault", {}),
    ("CB 05 00 00 00 00 00 00", "dump_log_record", {"record_number": 5}),
    ("C7 00 00 00 00 00 00 00", "erase_event_log", {}),
    ("C9 00 00 00 00 00 00 00", "read_log_index", {}),
    ("C5 00 46 B9 B8 0B 47 F4", "set_precharge_threshold",
     {"precharge_threshold_pct": 70, "config_word": 3000, "inverse_check_ok": True}),
])
def test_maintenance_command_examples(data, cmd, checks):
    f = d2(0x0CFF00F3, data)
    assert f.direction == "pack_rx"
    assert f.value("command") == cmd
    for k, v in checks.items():
        assert f.value(k) == v, k
    assert any("service mode" in n for n in f.notes)


def test_maintenance_bad_inverse_and_unknown_command():
    bad = d2(0x0CFF00F3, "D3 00 00 00 02 FE 00 00")
    assert bad.value("inverse_check_ok") is False
    unk = d2(0x0CFF00F3, "77 00 00 00 00 00 00 00")
    assert unk.value("command") == "unknown" and any("service mode" in n for n in unk.notes)


def test_destructive_commands_noted():
    for c in ("C7", "D3", "EC", "C6"):
        assert any("DESTRUCTIVE" in n for n in d2(0x0CFF00F3, c + " 00 00 00 00 00 00 00").notes)
    assert not any("DESTRUCTIVE" in n for n in d2(0x0CFF00F3, "CF 00 00 00 00 00 00 00").notes)


def test_charger_config_frame():
    ok = d2(0x0CFFBC49, bytes([0, 0, 0, 0, 3, 0, 0, 0]))
    assert ok.value("config_byte") == 3 and ok.value("raises_config_warning") is False
    bad = d2(0x0CFFBC49, bytes([0, 0, 0, 0, 4, 0, 0, 0]))
    assert bad.value("raises_config_warning") is True
    assert d2(0x0CFFBC49, bytes([0, 0, 0, 0, 5, 0, 0, 0])).value("raises_config_warning") is False


@pytest.mark.parametrize("cid", [0x0C1BF349, 0x0C1BFF49, 0x18DFFFF3])
def test_ignored_receive_ids_decode_as_empty_frames(cid):
    f = d2(cid, bytes(8))
    assert f.signals == [] and f.direction == "pack_rx"
