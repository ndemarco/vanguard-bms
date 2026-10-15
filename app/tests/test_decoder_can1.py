"""CAN1 decoder tests: payloads are built by hand from CANBUS_PROTOCOL.md section 4."""

import pytest

from vanguard.decoder import decode
from vanguard.frames import FAULT_BITS


def d1(can_id, data):
    f = decode(1, can_id, bytes.fromhex(data))
    assert f is not None
    return f


def test_pack_current_and_limits_big_endian():
    # -1500 mA = 0xFFFA24 (24-bit two's complement), 120.0 A, 36.0 A, counter 7
    f = d1(0x111, "FFFA24 04B0 0168 07".replace(" ", ""))
    assert f.raw("pack_current_ma") == -1500
    assert f.value("pack_current_ma") == -1500
    assert f.value("discharge_limit_a") == pytest.approx(120.0)
    assert f.value("charge_limit_a") == pytest.approx(36.0)
    assert f.value("counter") == 7
    assert f.signal("pack_current_ma").conf == "C"  # sign bench-confirmed 2026-10-11


def test_pack_current_positive_and_raw_kept():
    f = d1(0x111, "00 27 10 00 00 00 00".replace(" ", "") + "00")
    assert f.raw("pack_current_ma") == 10000


def test_0x112_bits_and_voltages():
    # B0 = 0b0010_0110: any fault, warning, contactor A driven. B1 bit 5: contactor B driven.
    f = d1(0x112, "26 20 1442 1473 1442".replace(" ", ""))
    assert f.value("any_fault_byte_nonzero") is True
    assert f.value("warning_word_nonzero") is True
    assert f.value("contactor_a_driven") is True
    assert f.value("contactor_b_driven") is True
    assert f.value("pack_side_adc_v") == pytest.approx(51.86)  # 0x1442 = 5186
    # doc example: 5230 = 52.30 V is the pack voltage (sum of cells)
    g = d1(0x112, "0000" + "0000" + "146E" + "0000")
    assert g.value("pack_voltage_v") == pytest.approx(52.30)
    assert g.value("contactor_a_driven") is False and g.value("contactor_b_driven") is False


def test_0x112_contactor_bits_independent():
    a = d1(0x112, "2000" + "0" * 12)
    assert a.value("contactor_a_driven") and not a.value("contactor_b_driven")
    b = d1(0x112, "0020" + "0" * 12)
    assert b.value("contactor_b_driven") and not b.value("contactor_a_driven")


def test_0x113_state_soc_and_echoes():
    # Appendix C: SOC 25 counts = 12.5 %, SOH 200 = 100 %
    f = d1(0x113, "01 25 0B 01 00 C8 19 24".replace(" ", ""))
    assert f.value("charge_mode") is True
    assert f.value("aux_echo_bit6") is False
    assert f.value("aux_echo_bit2") is True      # B1 = 0x25: bits 0, 2, 5
    assert f.value("aux_echo_bit4") is True      # B1 bit 5
    assert f.value("balancing_active") is True   # B1 bit 0
    assert f.value("input_rb13_asserted") is True and f.value("input_rb14_asserted") is True
    assert f.value("substate_entered") is False and f.value("substate_closing_or_closed") is True
    assert f.value("model_code") == 1
    assert f.value("soh_pct") == 100.0
    assert f.value("soc_pct") == 12.5
    assert f.value("temp_charge_limit_a") == 36


def test_0x113_aux_echo_bits():
    f = d1(0x113, "04" + "04" + "00" * 6)
    assert f.value("aux_echo_bit6") and f.value("aux_echo_bit2") and not f.value("aux_echo_bit4")


def test_0x114_temps_and_cells_big_endian():
    # hottest 58 C -> 98, coolest -5 C -> 35, max 4012 mV = 0x0FAC, min 3950 mV = 0x0F6E
    f = d1(0x114, "62 23 0FAC 0F6E 0000".replace(" ", ""))
    assert f.value("hottest_temp_c") == 58
    assert f.value("coolest_temp_c") == -5
    assert f.value("max_cell_mv") == 4012
    assert f.value("min_cell_mv") == 3950


def test_0x114_invalid_sensor():
    f = d1(0x114, "0000" + "0000" * 3)
    assert f.value("hottest_temp_c") is None
    assert not f.signal("hottest_temp_c").valid
    assert f.raw("hottest_temp_c") == 0


def test_0x120_fault_bit_table_is_complete():
    assert len(FAULT_BITS) == 19


@pytest.mark.parametrize("byte,bit,name,conf,blocking", FAULT_BITS)
def test_0x120_each_fault_bit(byte, bit, name, conf, blocking):
    payload = bytearray(8)
    payload[byte] = 1 << bit
    f = decode(1, 0x120, bytes(payload))
    assert f.value(name) is True
    assert f.signal(name).conf == conf
    others = [s.name for s in f.signals if s.name in {n for _, _, n, _, _ in FAULT_BITS} and s.name != name]
    assert not any(f.value(n) for n in others)
    assert f.raw(f"b{byte}") == 1 << bit


def test_0x120_all_zero_has_no_faults():
    f = d1(0x120, "00" * 8)
    assert not any(f.value(n) for _, _, n, _, _ in FAULT_BITS)
    assert f.notes == []


def test_0x120_keepalive_timeout_example():
    # host timeout: byte 1 bit 2
    f = d1(0x120, "00 04 00 00 00 00 00 00".replace(" ", ""))
    assert f.value("keepalive_timeout") is True


def test_gated_frame_layouts():
    f = d1(0x512, "00FA 01A9 0DAC 16F8".replace(" ", ""))  # model 0: 250, 425, 3500, 5880
    assert [f.value(n) for n in ("low_cell_limit_v", "high_cell_limit_v", "low_pack_limit_v", "high_pack_limit_v")] == \
        [pytest.approx(2.5), pytest.approx(4.25), pytest.approx(35.0), pytest.approx(58.8)]
    g = d1(0x513, "49 05DC 0960 05 14 6E".replace(" ", ""))  # 73 Ah, 150 A, 240 A, limits -20 / +70 C
    assert g.value("capacity_ah") == 73
    assert g.value("current_rating_1_a") == pytest.approx(150.0)
    assert g.value("current_rating_2_a") == pytest.approx(240.0)
    assert g.value("constant_5") == 5
    assert g.value("low_temp_limit_c") == -20 and g.value("high_temp_limit_c") == 70
    h = d1(0x51D, "02 09 07 28 1036 0000".replace(" ", ""))
    assert (h.value("fw_major"), h.value("fw_minor"), h.value("fw_patch")) == (2, 9, 7)
    assert h.value("board_address") == 0x28
    assert h.value("charge_target_mv") == 4150
    i = d1(0x510, "00BC614E" + "01" + "020304")
    assert i.value("identity_a") == 0x00BC614E
    assert i.value("identity_b") == 0x020304 and i.value("identity_b_byte4") == 1


def test_counters():
    f = d1(0x517, "000C" "0000" "0003" "0000")
    assert f.value("trip_ah_discharge") == 12 and f.value("trip_ah_regen") == 3
    g = d1(0x518, "FFFFFF9C" "00001388")  # net -100 Wh signed, regen 5000
    assert g.value("lifetime_wh_net") == -100 and g.value("lifetime_wh_regen") == 5000
    assert d1(0x519, "00013880" "00013398").value("lifetime_wh_discharge") == 0x13398
    assert d1(0x51B, "00000019" "00000064").value("lifetime_ah_regen") == 100
    assert d1(0x51C, "000005DC" "000005C3").value("lifetime_ah_charger") == 1500
    assert d1(0x51A, "026C" "0000" "0096" "0000").value("trip_wh_discharge") == 620


# Host-to-pack frames ---------------------------------------------------------

def test_0x105_keepalive_examples():
    f = d1(0x105, "00 11 00 00 00 00 00 00".replace(" ", ""))
    assert f.direction == "pack_rx"
    assert f.value("mode_bits") == "discharge_path" and f.raw("mode_bits") == 0
    assert f.value("enable") and f.value("request_gated_frames")
    assert not f.value("clear_faults") and not f.value("extend_timeout")
    assert not (f.value("aux_out_bit2") or f.value("aux_out_bit4") or f.value("aux_out_bit6"))
    c = d1(0x105, "01 13 00 00 00 00 00 00".replace(" ", ""))
    assert c.value("mode_bits") == "charge_path" and c.value("clear_faults")


def test_0x105_d0_bits_named_by_bit_not_output():
    f = d1(0x105, "54" + "10" + "00" * 6)  # bits 2, 4, 6
    assert f.value("aux_out_bit2") and f.value("aux_out_bit4") and f.value("aux_out_bit6")
    assert f.raw("mode_bits") == 0
    assert f.signal("aux_out_bit2").conf == "I"


def test_0x105_any_of_two_mode_bits_is_charge():
    for d0 in (1, 2, 3):
        assert d1(0x105, f"{d0:02X}10" + "00" * 6).value("mode_bits") == "charge_path"


def test_0x105_timeout_bit():
    assert d1(0x105, "0014" + "00" * 6).value("extend_timeout") is True


def test_0x515_sleep():
    assert d1(0x515, "20" + "00" * 7).value("sleep_request") == "deep_shutdown"
    assert d1(0x515, "10" + "00" * 7).value("sleep_request") == "light_sleep"
    assert d1(0x515, "30" + "00" * 7).value("sleep_request") == "light_sleep"
    assert d1(0x515, "0F" + "00" * 7).value("sleep_request") == "none"


def test_sdo_read_vendor_id_example():
    f = d1(0x614, "40 18 10 01 00 00 00 00".replace(" ", ""))
    assert f.value("command") == "read" and f.value("index") == 0x1018 and f.value("subindex") == 1
    assert f.value("object") == "vendor_id"
    r = decode(1, 0x594, bytes.fromhex("43 18 10 01 01 00 00 00".replace(" ", "")))
    assert r.value("value") == 1 and r.value("object") == "vendor_id"


def test_sdo_reply_variable_dlc():
    r = decode(1, 0x594, bytes.fromhex("43 00 10 00 A2 01 AA 55".replace(" ", "")))
    assert r.value("value") == 0x55AA01A2 and r.value("object") == "device_type"
    short = decode(1, 0x594, bytes.fromhex("43 18 10 02 7B"))  # DLC = 1 + 4
    assert short.value("value") == 0x7B


def test_sdo_bootloader_write_flagged():
    f = d1(0x614, "23 51 1F 01 00 00 00 00".replace(" ", ""))
    assert f.value("command") == "write"
    assert f.value("bootloader_reboot_request") is True
    assert any("DANGEROUS" in n for n in f.notes)
    safe = d1(0x614, "23 51 1F 01 01 00 00 00".replace(" ", ""))
    assert safe.value("bootloader_reboot_request") is False


def test_unknown_ids_return_none():
    assert decode(1, 0x7FF, bytes(8)) is None
    assert decode(1, 0x105 | 0x10000, bytes(8)) is None
    assert decode(2, 0x111, bytes(8)) is None  # CAN1 ID on CAN2
    assert decode(3, 0x111, bytes(8)) is None
    assert decode(2, 0x18FF99F3, bytes(8)) is None
    assert decode(1, 0x111, None) is None


def test_short_frame_does_not_raise():
    f = decode(1, 0x111, b"\x00\x01")
    assert f is not None and f.notes
    assert decode(1, 0x112, b"") is not None
    assert decode(1, 0x614, b"\x40") is not None
