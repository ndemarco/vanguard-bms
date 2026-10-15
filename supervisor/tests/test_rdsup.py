import ast
import pathlib
import struct
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "rdsup.py"
sys.path.insert(0, str(HERE.parent))
import rdsup  # noqa: E402

OFF = rdsup.off_frame(1)


# ---------------------------------------------------------------- fakes
class FakeRiden:
    """Fake serial transport emulating an RD6018 at slave 1."""

    def __init__(self):
        self.regs = [0] * 20
        self.regs[0] = 60181
        self.regs[5] = 25
        self.regs[8] = 5740
        self.regs[9] = 1000
        self.regs[14] = 6794
        self.frames = []          # every frame sent
        self.dead = False         # no replies at all
        self.ignore_writes = False

    def set(self, v=None, i=None, out=None, cvcc=None, prot=None, iset=None):
        if v is not None: self.regs[10] = round(v * 100)
        if i is not None: self.regs[11] = round(i * 100)
        if out is not None: self.regs[18] = out
        if cvcc is not None: self.regs[17] = cvcc
        if prot is not None: self.regs[16] = prot
        if iset is not None: self.regs[9] = round(iset * 100)

    def transact(self, frame, reply_len, timeout=0.25):
        self.frames.append(bytes(frame))
        assert rdsup.check_crc(frame)
        if self.dead:
            return b""
        slave, func, a, b = struct.unpack(">BBHH", frame[:6])
        if func == 3:
            data = b"".join(struct.pack(">H", self.regs[x]) for x in range(a, a + b))
            return rdsup.add_crc(bytes([slave, 3, len(data)]) + data)
        if func == 6:
            if not self.ignore_writes:
                self.regs[a] = b
            return bytes(frame)
        raise AssertionError("unexpected function")

    def writes(self):
        return [f for f in self.frames if f[1] != 3]


class Clock:
    def __init__(self):
        self.t = 1000.0


def mk(dry=False, **lim):
    fr = FakeRiden()
    clk = Clock()
    out = []
    events = []
    sup = rdsup.Supervisor(rdsup.RidenLink(fr, 1), rdsup.Limits(**lim), clk.t, dry_run=dry,
                           out=out.append, log=events.append)
    return sup, fr, clk, out, events


def f112(a=True, b=True, pack_v=52.0, out_v=52.0):
    return bytes([0x20 if a else 0, 0x20 if b else 0, 0, 0]) + struct.pack(">HH", round(pack_v * 100), round(out_v * 100))


def f114(hot=25, cold=20, maxmv=3900, minmv=3880):
    return bytes([hot + 40, cold + 40]) + struct.pack(">HH", maxmv, minmv) + b"\0\0"


def healthy_pack(sup, t, **kw):
    sup.on_can(0x112, f112(**{k: v for k, v in kw.items() if k in ("a", "b", "pack_v", "out_v")}), t)
    sup.on_can(0x114, f114(**{k: v for k, v in kw.items() if k in ("hot", "cold", "maxmv", "minmv")}), t)
    sup.on_can(0x120, bytes(8), t)
    sup.on_can(0x111, (5000).to_bytes(3, "big", signed=True) + bytes(5), t)


def run(sup, fr, clk, seconds, pack_kw=None, can=True):
    for _ in range(int(seconds / 0.5)):
        clk.t += 0.5
        if can:
            healthy_pack(sup, clk.t, **(pack_kw or {}))
        sup.poll_riden(clk.t)


def charging(fr, v=54.0, i=8.0):
    fr.set(v=v, i=i, out=1, cvcc=1)


# ---------------------------------------------------------------- modbus
def test_crc_known_vector():
    # Classic example: 01 03 00 00 00 0A -> CRC C5 CD
    assert rdsup.frame_read(1, 0, 10) == bytes.fromhex("01030000000AC5CD")


def test_off_frame_bytes():
    assert OFF == bytes.fromhex("010600120000") + struct.pack("<H", rdsup.crc16(bytes.fromhex("010600120000")))


def test_parse_read_ok():
    fr = FakeRiden()
    reply = fr.transact(rdsup.frame_read(1, 8, 2), 9)
    assert rdsup.parse_response(reply, 1, 3, 2) == [5740, 1000]


@pytest.mark.parametrize("bad", [
    b"",
    bytes.fromhex("0103"),
    rdsup.add_crc(bytes([2, 3, 2, 0, 1])),               # wrong slave
    rdsup.add_crc(bytes([1, 4, 2, 0, 1])),               # wrong function
    rdsup.add_crc(bytes([1, 3, 4, 0, 1])),               # bad count
    bytes([1, 3, 2, 0, 1, 0, 0]),                        # bad crc
])
def test_parse_read_rejects(bad):
    with pytest.raises(rdsup.ModbusError):
        rdsup.parse_response(bad, 1, 3, 1)


def test_parse_exception_response():
    exc = rdsup.add_crc(bytes([1, 0x83, 0x02]))
    with pytest.raises(rdsup.ModbusError, match="exception code 2"):
        rdsup.parse_response(exc, 1, 3, 1)
    exc6 = rdsup.add_crc(bytes([1, 0x86, 0x04]))
    with pytest.raises(rdsup.ModbusError, match="exception code 4"):
        rdsup.parse_response(exc6, 1, 6)
    with pytest.raises(rdsup.ModbusError, match="CRC"):
        rdsup.parse_response(bytes([1, 0x83, 2, 0, 0]), 1, 3, 1)


def test_write_echo_parse():
    assert rdsup.parse_response(OFF, 1, 6) == (18, 0)


# ---------------------------------------------------------------- write gate
@pytest.mark.parametrize("reg,val", [(18, 1), (8, 0), (9, 0), (17, 0), (18, True), (18.0, 0), (0, 0), (19, 0)])
def test_output_off_refuses_anything_else(reg, val):
    fr = FakeRiden()
    with pytest.raises(ValueError):
        rdsup.riden_output_off(rdsup.RidenLink(fr, 1), reg, val)
    assert fr.frames == []


def test_output_off_sends_exact_frame():
    fr = FakeRiden()
    fr.set(out=1)
    rdsup.riden_output_off(rdsup.RidenLink(fr, 1))
    assert fr.frames == [OFF]
    assert fr.regs[18] == 0


def test_serial_transport_gate_refuses_other_writes(tmp_path):
    # SerialPort.transact gate runs before any I/O; build one without opening a device.
    sp = object.__new__(rdsup.SerialPort)
    sp.slave = 1
    sp.allow_off_write = True
    with pytest.raises(rdsup.ModbusError):
        sp.transact(rdsup.add_crc(bytes([1, 6, 0, 18, 0, 1])), 8)
    with pytest.raises(rdsup.ModbusError):
        sp.transact(rdsup.add_crc(bytes([1, 0x10, 0, 18, 0, 1, 2, 0, 0])), 8)
    sp.allow_off_write = False   # dry run: even the OFF frame is refused
    with pytest.raises(rdsup.ModbusError):
        sp.transact(OFF, 8)


# ---------------------------------------------------------------- CAN decode
def test_parse_can_frame_and_pack_decode():
    raw = struct.pack("=IB3x8s", 0x114, 6, f114(30, 5, 4100, 3950))
    cid, d = rdsup.parse_can_frame(raw)
    p = rdsup.PackState()
    p.update(cid, d, 1.0)
    assert (p.hot_c, p.cold_c, p.max_mv, p.min_mv) == (30, 5, 4100, 3950)
    assert rdsup.parse_can_frame(struct.pack("=IB3x8s", 0x114 | rdsup.CAN_EFF_FLAG, 8, bytes(8))) is None
    p.update(0x111, (-1500).to_bytes(3, "big", signed=True) + bytes(5), 1.0)
    assert p.current_ma == -1500
    p.update(0x112, f112(True, False, 52.34, 50.0), 1.0)
    assert (p.drv_a, p.drv_b, p.pack_v, p.out_v) == (True, False, 52.34, 50.0)


def test_blocking_fault_mask():
    assert rdsup.blocking_faults(bytes([0, 0x50, 0, 0, 0, 0x20, 0, 0])) == []
    assert rdsup.blocking_faults(bytes([0, 0x58, 0, 0, 0, 0x20, 0, 0])) == ["B1.3"]
    assert rdsup.blocking_faults(bytes([0, 0, 0, 0, 0, 0, 0, 1])) == ["B7.0"]


# ---------------------------------------------------------------- behaviour
def test_healthy_charge_no_trip():
    sup, fr, clk, out, ev = mk()
    charging(fr)
    run(sup, fr, clk, 300)
    assert sup.trip_reason is None
    assert fr.writes() == []


def assert_tripped_only_off(sup, fr, substr, kind="fault"):
    assert sup.trip_reason is not None and substr in sup.trip_reason, sup.trip_reason
    assert sup.trip_kind == kind
    assert fr.writes() and all(w == OFF for w in fr.writes())
    assert fr.regs[18] == 0
    assert sup.off_confirmed


@pytest.mark.parametrize("pack_kw,substr", [
    ({"maxmv": 4151}, "max cell"),
    ({"minmv": 2999}, "min cell"),
    ({"hot": 46}, "hottest"),
    ({"cold": -1}, "coolest"),
    ({"pack_v": 58.2}, "pack voltage"),
    ({"a": False}, "contactors"),
    ({"b": False}, "contactors"),
])
def test_pack_trips(pack_kw, substr):
    sup, fr, clk, out, ev = mk()
    charging(fr)
    run(sup, fr, clk, 1)
    assert sup.trip_reason is None
    run(sup, fr, clk, 3, pack_kw)
    assert_tripped_only_off(sup, fr, substr)


def test_contactor_grace_period():
    sup, fr, clk, out, ev = mk()
    charging(fr)
    run(sup, fr, clk, 1, {"a": False})   # 1.0 s not > 1.0 s grace
    assert sup.trip_reason is None
    run(sup, fr, clk, 1, {"a": False})
    assert "contactors" in sup.trip_reason


def test_min_cell_only_while_output_on():
    sup, fr, clk, out, ev = mk()
    fr.set(out=0)
    run(sup, fr, clk, 5, {"minmv": 2900})
    assert sup.trip_reason is None and fr.writes() == []


@pytest.mark.parametrize("byte,bit,trips", [
    (1, 3, True), (1, 4, False), (1, 6, False), (5, 5, False), (5, 6, True), (2, 3, True), (0, 1, True), (7, 0, True),
])
def test_fault_bits(byte, bit, trips):
    sup, fr, clk, out, ev = mk()
    charging(fr)
    for _ in range(4):
        clk.t += 0.5
        healthy_pack(sup, clk.t)
        f = bytearray(8)
        f[byte] = 1 << bit
        sup.on_can(0x120, bytes(f), clk.t)
        sup.poll_riden(clk.t)
    if trips:
        assert_tripped_only_off(sup, fr, f"B{byte}.{bit}")
    else:
        assert sup.trip_reason is None and fr.writes() == []


@pytest.mark.parametrize("riden_kw,substr", [
    ({"v": 58.41}, "V_OUT"),
    ({"i": 10.51, "iset": 10.0}, "I_SET"),
    ({"i": 10.6, "iset": 12.0}, "> 10.5"),
    ({"prot": 1}, "protection"),
])
def test_riden_trips(riden_kw, substr):
    sup, fr, clk, out, ev = mk()
    charging(fr)
    run(sup, fr, clk, 1)
    fr.set(**riden_kw)
    run(sup, fr, clk, 1)
    assert_tripped_only_off(sup, fr, substr)


def test_stale_can_trips_only_when_on():
    sup, fr, clk, out, ev = mk()
    fr.set(out=0)
    run(sup, fr, clk, 5, can=False)
    assert sup.trip_reason is None and fr.writes() == []
    assert any("stale" in line for line in out)
    charging(fr)
    run(sup, fr, clk, 0.5, can=False)
    assert_tripped_only_off(sup, fr, "stale")


def test_stale_one_frame_type():
    sup, fr, clk, out, ev = mk()
    charging(fr)
    run(sup, fr, clk, 1)
    for _ in range(4):           # only 0x112 keeps coming
        clk.t += 0.5
        sup.on_can(0x112, f112(), clk.t)
        sup.poll_riden(clk.t)
    assert_tripped_only_off(sup, fr, "0x114 stale")


def test_riden_comms_loss():
    sup, fr, clk, out, ev = mk()
    charging(fr)
    run(sup, fr, clk, 1)
    fr.dead = True
    run(sup, fr, clk, 1)
    assert sup.trip_reason is None
    run(sup, fr, clk, 0.5)
    assert "comms lost" in sup.trip_reason
    assert fr.writes() and all(w == OFF for w in fr.writes())
    assert not sup.off_confirmed
    assert any("FAILED" in line for line in out)
    # keeps trying, does not exit; recovers when the Riden answers again
    fr.dead = False
    run(sup, fr, clk, 0.5)
    assert sup.off_confirmed and fr.regs[18] == 0


def test_off_not_confirmed_banner_every_second_and_retries():
    sup, fr, clk, out, ev = mk()
    charging(fr)
    fr.ignore_writes = True
    run(sup, fr, clk, 0.5, {"maxmv": 4200})
    n_writes = len(fr.writes())
    assert n_writes == 3
    run(sup, fr, clk, 3, {"maxmv": 4200})
    banners = [line for line in out if "FAILED TO CONFIRM" in line]
    assert len(banners) >= 3
    assert len(fr.writes()) > n_writes and all(w == OFF for w in fr.writes())


def test_charge_complete():
    sup, fr, clk, out, ev = mk()
    charging(fr)
    run(sup, fr, clk, 10)
    fr.set(i=0.8, cvcc=0)
    run(sup, fr, clk, 119)
    assert sup.trip_reason is None
    run(sup, fr, clk, 2)
    assert_tripped_only_off(sup, fr, "charge complete", kind="complete")


def test_charge_complete_timer_resets():
    sup, fr, clk, out, ev = mk()
    charging(fr)
    fr.set(i=0.8, cvcc=0)
    run(sup, fr, clk, 100)
    fr.set(i=2.0)
    run(sup, fr, clk, 1)
    fr.set(i=0.8)
    run(sup, fr, clk, 100)
    assert sup.trip_reason is None


def test_latched_and_rearms_never():
    sup, fr, clk, out, ev = mk()
    charging(fr)
    run(sup, fr, clk, 2, {"hot": 50})
    assert sup.trip_reason
    reason = sup.trip_reason
    # conditions clear, someone switches the output back on: we switch it off again, never on
    fr.set(out=1)
    run(sup, fr, clk, 2)
    assert sup.trip_reason == reason
    assert fr.regs[18] == 0
    assert all(w == OFF for w in fr.writes())


def test_dry_run_never_writes():
    sup, fr, clk, out, ev = mk(dry=True)
    charging(fr)
    run(sup, fr, clk, 3, {"maxmv": 4300})
    assert sup.trip_reason
    assert fr.writes() == []
    assert any("WOULD SWITCH OFF" in line for line in out)
    assert fr.regs[18] == 1


def test_status_line_and_json():
    import json
    sup, fr, clk, out, ev = mk()
    charging(fr)
    run(sup, fr, clk, 1)
    assert "Riden 54.00V 8.00A out=ON CC" in sup.status_line(clk.t)
    json.dumps(sup.status(clk.t))


# ---------------------------------------------------------------- structure
TREE = ast.parse(SRC.read_text())


def calls():
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call):
            yield node


def func_of(target):
    for fn in ast.walk(TREE):
        if isinstance(fn, ast.FunctionDef):
            for n in ast.walk(fn):
                if n is target:
                    yield fn.name


def test_no_can_send_anywhere():
    banned = {"send", "sendto", "sendall", "sendmsg", "sendfile", "write", "writelines"}
    offenders = []
    for c in calls():
        f = c.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name in banned:
            is_os_write = isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "os" and name == "write"
            if not is_os_write:
                offenders.append((name, c.lineno))
    assert offenders == []


def test_single_serial_write_site():
    sites = [c for c in calls() if isinstance(c.func, ast.Attribute) and c.func.attr == "write"]
    assert len(sites) == 1
    assert list(func_of(sites[0]))[-1] == "transact"


def test_single_modbus_write_call_site():
    sites = [c for c in calls() if isinstance(c.func, ast.Name) and c.func.id == "_frame_write_single"]
    assert len(sites) == 1
    assert "riden_output_off" in list(func_of(sites[0]))
    # riden_output_off itself is called from exactly one place (Supervisor.switch_off)
    offs = [c for c in calls() if isinstance(c.func, ast.Name) and c.func.id == "riden_output_off"]
    assert len(offs) == 1 and "switch_off" in list(func_of(offs[0]))

