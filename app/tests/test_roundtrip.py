"""Encode/decode round trips, driven by the frame tables."""

import random

import pytest

from vanguard.decoder import decode
from vanguard.encoder import encode
from vanguard.frames import ALL_SPECS, Bit, Const, SPEC_BY_KEY, fields_of

ENCODABLE = [s for s in ALL_SPECS if s.custom in (None, "fault_bytes", "log_status", "charger_config") and fields_of(s)]


def test_ids_are_unique_and_keyed_on_bus():
    assert len(SPEC_BY_KEY) == len(ALL_SPECS)


@pytest.mark.parametrize("spec", ENCODABLE, ids=lambda s: s.name)
def test_raw_round_trip_random_payloads(spec):
    rng = random.Random(spec.can_id)
    names = {f.name for f in fields_of(spec) if not isinstance(f, Const)}
    for _ in range(25):
        data = bytearray(rng.randrange(256) for _ in range(spec.dlc))
        for f in fields_of(spec):
            if isinstance(f, Const):
                data[f.offset:f.offset + len(f.value)] = f.value
        first = decode(spec.bus, spec.can_id, bytes(data))
        raws = {s.name: s.raw for s in first.signals if s.name in names}
        rebuilt = encode(spec.name, raw=raws)
        second = decode(spec.bus, spec.can_id, rebuilt)
        pick = lambda fr: [(s.name, s.raw, s.value) for s in fr.signals if s.name in names]  # noqa: E731
        assert pick(first) == pick(second)


@pytest.mark.parametrize("spec", ENCODABLE, ids=lambda s: s.name)
def test_scaled_value_round_trip(spec):
    rng = random.Random(spec.can_id + 1)
    names = {f.name for f in fields_of(spec) if not isinstance(f, Const)}
    data = bytearray(rng.randrange(256) for _ in range(spec.dlc))
    for f in fields_of(spec):
        if isinstance(f, Const):
            data[f.offset:f.offset + len(f.value)] = f.value
    first = decode(spec.bus, spec.can_id, bytes(data))
    values = {s.name: s.value for s in first.signals if s.name in names and s.valid}
    raws = {s.name: s.raw for s in first.signals if s.name in names and not s.valid}
    # bit signals that overlap word fields are set through the word; keep both consistent
    again = decode(spec.bus, spec.can_id, encode(spec.name, values=values, raw=raws))
    enums = {f.name for f in fields_of(spec) if getattr(f, "enum", None)}  # several raws may share a name
    pick = lambda fr: {s.name: (None if s.name in enums else s.raw, s.value)  # noqa: E731
                       for s in fr.signals if s.name in names}
    assert pick(first) == pick(again)


def test_encode_documented_frames():
    assert encode(1, 0x105, {"enable": True, "request_gated_frames": True, "mode_bits": "discharge_path"}) == \
        bytes([0x00, 0x11, 0, 0, 0, 0, 0, 0])
    assert encode(1, 0x105, {"enable": True, "mode_bits": "charge_path", "clear_faults": True}) == \
        bytes([0x01, 0x12, 0, 0, 0, 0, 0, 0])
    assert encode(1, 0x515, {"sleep_request": "deep_shutdown"})[0] == 0x20
    f = encode(2, 0x0CFF20F3, {"mode": "discharge_run", "current_a": -50.0, "cell_sum_mv": 51870})
    assert f[6:8] == (31000).to_bytes(2, "little") and f[4:6] == (51870).to_bytes(2, "little") and f[0] == 2
    assert encode(2, 0x18FF06F3, {"cell13_mv": 3700, "cell14_mv": 3701}) == \
        (3700).to_bytes(2, "little") + (3701).to_bytes(2, "little") + b"\xff" * 4
    t = encode(2, 0x0CFF01F3, {f"temp{n}_c": 20 + n for n in range(1, 8)})
    assert t == bytes(60 + n for n in range(1, 8)) + b"\xff"


def test_encode_by_name_and_big_endian_current():
    d = encode("pack_current_limits", values={"pack_current_ma": -1500, "discharge_limit_a": 120.0})
    assert d[:5] == bytes.fromhex("FFFA2404B0")


def test_encode_rejects_bad_input():
    with pytest.raises(ValueError):
        encode(1, 0x105, {"no_such_signal": 1})
    with pytest.raises(ValueError):
        encode(1, 0x114, {"max_cell_mv": 70000})
    with pytest.raises(KeyError):
        encode(1, 0x7FF, {})


def test_encoder_refuses_dangerous_host_frames():
    with pytest.raises(ValueError):
        encode(2, 0x0CFF00F3, {})
    with pytest.raises(ValueError):
        encode(1, 0x614, {})
