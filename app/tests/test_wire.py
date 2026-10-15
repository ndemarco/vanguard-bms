import json

import pytest

from vanguard.wire import (Frame, Status, WireError, format_frame, format_status, frame_from_json, frame_to_json,
                           parse_line, read_jsonl)


def test_format_exact():
    assert format_frame(Frame(1500000, 1, 0x111, bytes.fromhex("0102030405060708"))) == \
        "R 1 111 8 0102030405060708 1500000"
    assert format_frame(Frame(42, 2, 0x18FF03F3, bytes(8))) == "R 2 18FF03F3 8 0000000000000000 42"
    assert format_frame(Frame(5, 2, 0x0CFF06F3, b"\xab")) == "R 2 0CFF06F3 1 AB 5"
    assert format_frame(Frame(0, 1, 0x7FF, b"")) == "R 1 7FF 0 - 0"


@pytest.mark.parametrize("f", [Frame(1, 1, 0x105, bytes(8)), Frame(10**12, 2, 0x0CF094F3, bytes(range(8))),
                               Frame(0, 1, 0x594, bytes([0x43, 0x18, 0x10, 0x02, 0x7B])), Frame(7, 1, 0x001, b"")])
def test_line_round_trip(f):
    assert parse_line(format_frame(f)) == f


def test_parse_accepts_lowercase_and_prefix():
    f = parse_line("R 2 0x18ff03f3 8 0a0b0c0d0e0f1011 99\n")
    assert f.can_id == 0x18FF03F3 and f.data[0] == 0x0A and f.ts_us == 99 and f.bus == 2


def test_status_lines():
    assert format_status("hello world") == "S hello world"
    assert parse_line("S hello world") == Status("hello world")
    assert parse_line(format_status("x")) == Status("x")
    with pytest.raises(WireError):
        format_status("two\nlines")


@pytest.mark.parametrize("line", ["", "X 1 111 8 00 1", "R 3 111 1 00 1", "R 1 111 2 00 1", "R 1 ZZ 1 00 1",
                                  "R 1 111 1 0 1", "R 1 111 9 " + "00" * 9 + " 1", "R 1 111 1 00", "R 1 111 1 00 -5",
                                  "R 1 111 1 00 1 extra", "R 1 111 0 00 1"])
def test_malformed_lines(line):
    with pytest.raises(WireError):
        parse_line(line)


def test_json_round_trip_and_fields():
    f = Frame(1500000, 2, 0x18FF03F3, bytes.fromhex("740E7A0E800E860E"), "tx")
    obj = json.loads(frame_to_json(f))
    assert obj == {"ts_us": 1500000, "bus": 2, "id": 0x18FF03F3, "dlc": 8, "data": "740e7a0e800e860e",
                   "direction": "tx"}
    assert frame_from_json(frame_to_json(f)) == f


def test_json_accepts_hex_id_and_default_direction():
    f = frame_from_json('{"ts_us": 5, "bus": 1, "id": "0x111", "dlc": 0, "data": ""}')
    assert f == Frame(5, 1, 0x111, b"", "rx")


@pytest.mark.parametrize("line", ["not json", "{}", '{"ts_us":1,"bus":3,"id":1,"dlc":0,"data":""}',
                                  '{"ts_us":1,"bus":1,"id":1,"dlc":2,"data":"00"}',
                                  '{"ts_us":1,"bus":1,"id":1,"dlc":0,"data":"","direction":"up"}'])
def test_json_errors(line):
    with pytest.raises(WireError):
        frame_from_json(line)


def test_read_jsonl_skips_blank_and_comments():
    lines = ["# header", "", frame_to_json(Frame(1, 1, 0x105, bytes(8))), "   ", frame_to_json(Frame(2, 1, 0x111, b""))]
    assert [f.ts_us for f in read_jsonl(lines)] == [1, 2]
