import random
import struct

import pytest

from vanguard.frames import CAN2_SPECS
from vanguard_bridge.canframe import CAN_EFF_FLAG, CAN_ERR_FLAG, CAN_RTR_FLAG, FRAME_FMT, pack
from vanguard_bridge.txgate import (ALLOWED_105_D0, ALLOWED_105_D1, ALLOWED_515_D0, TxGate, check_tx)

KA = bytes([0x00, 0x11, 0, 0, 0, 0, 0, 0])
SL = bytes([0x10, 0, 0, 0, 0, 0, 0, 0])


def raw(word, data, dlc=None, pad=b"\0\0\0"):
    return struct.pack(FRAME_FMT, word, len(data) if dlc is None else dlc, pad, bytes(data).ljust(8, b"\0"))


def test_good_frames_pass():
    for d0 in (0x00, 0x01):
        for d1 in (0x11, 0x13):
            assert check_tx(pack(0x105, bytes([d0, d1, 0, 0, 0, 0, 0, 0]))) is None
    for d0 in (0x10, 0x30):
        assert check_tx(pack(0x515, bytes([d0, 0, 0, 0, 0, 0, 0, 0]))) is None


def test_all_standard_ids_exhaustive():
    allowed = set()
    for can_id in range(0x800):
        for payload in (KA, SL):
            if check_tx(pack(can_id, payload)) is None:
                allowed.add(can_id)
    assert allowed == {0x105, 0x515}


def test_0x614_and_service_frames_refused():
    for d in ("2351 1F01 00000000", "40181001 00000000", "00" * 8):
        assert check_tx(pack(0x614, bytes.fromhex(d.replace(" ", "")))) is not None


def test_extended_ids_refused():
    rng = random.Random(7)
    ids = list(range(0x800)) + [0x105, 0x515, 0x0CFF00F3, 0x0CFFBC49, 0x1FFFFFFF]
    ids += [s.can_id for s in CAN2_SPECS] + [rng.randrange(0x20000000) for _ in range(20000)]
    for can_id in ids:
        for payload in (KA, SL):
            assert check_tx(pack(can_id, payload, eff=True)) is not None
            assert check_tx(raw(can_id | CAN_EFF_FLAG, payload)) is not None


def test_id_word_out_of_range_without_flag_refused():
    for word in (0x905, 0x1105, 0x10000105, 0x0CFF00F3):
        assert check_tx(raw(word, KA)) is not None


@pytest.mark.parametrize("flag", [CAN_RTR_FLAG, CAN_ERR_FLAG, CAN_RTR_FLAG | CAN_ERR_FLAG])
def test_rtr_and_error_frames_refused(flag):
    assert check_tx(raw(0x105 | flag, KA)) is not None
    assert check_tx(raw(0x515 | flag, SL)) is not None


@pytest.mark.parametrize("dlc", [d for d in range(256) if d != 8])
def test_bad_dlc_refused(dlc):
    assert check_tx(raw(0x105, KA, dlc=dlc)) is not None
    assert check_tx(raw(0x515, SL, dlc=dlc)) is not None


@pytest.mark.parametrize("pad", [b"\1\0\0", b"\0\1\0", b"\0\0\x08"])
def test_nonzero_padding_refused(pad):
    assert check_tx(raw(0x105, KA, pad=pad)) is not None


@pytest.mark.parametrize("blob", [b"", pack(0x105, KA)[:15], pack(0x105, KA) + b"\0",
                                  pack(0x105, KA) + bytes(56)])  # 72 bytes = CAN FD frame size
def test_wrong_size_refused(blob):
    assert check_tx(blob) is not None


def test_non_bytes_refused():
    assert check_tx(bytearray(pack(0x105, KA))) is not None
    assert check_tx(None) is not None


def test_0x105_d0_d1_exhaustive():
    for d0 in range(256):
        for d1 in range(256):
            ok = check_tx(pack(0x105, bytes([d0, d1, 0, 0, 0, 0, 0, 0]))) is None
            assert ok == (d0 in ALLOWED_105_D0 and d1 in ALLOWED_105_D1)
            if d0 & 0x54 or d1 & 0x04:
                assert not ok


def test_0x105_trailing_bytes_every_bit():
    for i in range(2, 8):
        for bit in range(8):
            d = bytearray(KA)
            d[i] = 1 << bit
            assert check_tx(pack(0x105, bytes(d))) is not None


def test_0x515_exhaustive():
    for d0 in range(256):
        assert (check_tx(pack(0x515, bytes([d0]) + bytes(7))) is None) == (d0 in ALLOWED_515_D0)
    assert check_tx(pack(0x515, bytes([0x20]) + bytes(7))) is not None  # deep shutdown
    for i in range(1, 8):
        for bit in range(8):
            d = bytearray(SL)
            d[i] = 1 << bit
            assert check_tx(pack(0x515, bytes(d))) is not None


def test_short_payload_refused():
    assert check_tx(pack(0x105, KA[:7])) is not None
    assert check_tx(pack(0x515, b"\x10")) is not None


class Rec:
    def __init__(self, exc=None, short=False):
        self.calls, self.exc, self.short = [], exc, short

    def send(self, b):
        self.calls.append(b)
        if self.exc:
            raise self.exc
        return len(b) - 1 if self.short else len(b)


def _gate(w):
    ev = []
    g = TxGate(w, on_sent=lambda r: ev.append(("sent", r)), on_refused=lambda r, why: ev.append(("refused", why)),
               on_failed=lambda r, why: ev.append(("failed", why)))
    return g, ev


def test_gate_refuses_before_socket():
    w = Rec()
    g, ev = _gate(w)
    assert g.send(pack(0x614, bytes(8))) is False
    assert w.calls == [] and ev[0][0] == "refused"


def test_gate_sends_allowed():
    w = Rec()
    g, ev = _gate(w)
    assert g.send(pack(0x105, KA)) is True
    assert w.calls == [pack(0x105, KA)] and ev == [("sent", pack(0x105, KA))]


@pytest.mark.parametrize("w", [Rec(exc=OSError(105, "No buffer space available")), Rec(exc=BlockingIOError()),
                               Rec(short=True)])
def test_gate_reports_write_failures(w):
    g, ev = _gate(w)
    assert g.send(pack(0x105, KA)) is False
    assert ev[0][0] == "failed"
