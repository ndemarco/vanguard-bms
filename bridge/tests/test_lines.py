from vanguard_bridge.core import LineAssembler


def test_split_across_chunks():
    a = LineAssembler()
    assert a.feed(b"ST") == []
    assert a.feed(b"OP\nHB\nSTART CH") == [("STOP", None), ("HB", None)]
    assert a.feed(b"ARGE\r\n") == [("START CHARGE", None)]


def test_max_length_boundary():
    a = LineAssembler(64)
    assert a.feed(b"A" * 64 + b"\n") == [("A" * 64, None)]
    out = a.feed(b"A" * 65 + b"\n")
    assert len(out) == 1 and out[0][0] is None and "longer" in out[0][1]


def test_overlong_discarded_until_newline_and_reported_once():
    a = LineAssembler(64)
    out = a.feed(b"X" * 100)
    out += a.feed(b"Y" * 1000)
    out += a.feed(b"Z\nHB\n")
    assert [o for o in out if o[1]] == [(None, "ERR line longer than 64 bytes discarded")]
    assert out[-1] == ("HB", None)


def test_non_ascii_and_control_bytes():
    a = LineAssembler()
    for bad in (b"H\xc3\xa9\n", b"HB\x00\n", b"\tHB\n", b"HB\x7f\n", b"\x1b[A\n"):
        out = a.feed(bad)
        assert out[0][0] is None and "non-ASCII" in out[0][1]


def test_empty_line_is_a_line():
    assert LineAssembler().feed(b"\n") == [("", None)]
