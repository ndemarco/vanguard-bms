"""Structural safety properties: where CAN writes can happen at all."""

import ast
import pathlib
import socket

import pytest

import vanguard_bridge
from vanguard_bridge import canio
from vanguard_bridge.canio import Can2ReceiveOnly, CanReceiver
from vanguard_bridge.simlink import SimCan2Socket, SimLink

PKG = pathlib.Path(vanguard_bridge.__file__).parent
WRITE_NAMES = {"send", "sendall", "sendto", "sendmsg", "write", "writelines"}


def _write_calls():
    """Every call ``x.send(...)``-like in the package, as (file, enclosing function)."""
    found = set()
    for path in PKG.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                            and sub.func.attr in WRITE_NAMES:
                        found.add((path.name, node.name, ast.unparse(sub.func)))
    return found


def test_only_known_write_calls_exist():
    assert _write_calls() == {
        ("txgate.py", "send", "self._writer.send"),            # THE final CAN send
        ("core.py", "_sleep", "self.gate.send"),               # via the TxGate
        ("core.py", "_tick", "self.gate.send"),                # via the TxGate
        ("service.py", "_flush_client", "self.client.send"),   # Unix socket to the client
        ("service.py", "_refuse_second_client", "conn.send"),  # Unix socket refusal line
    }


def test_txgate_constructed_only_by_bridge():
    hits = []
    for path in PKG.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "TxGate":
                hits.append(path.name)
    assert hits == ["core.py"]


@pytest.mark.parametrize("cls", [Can2ReceiveOnly, CanReceiver, SimCan2Socket])
def test_receive_classes_have_no_send_path(cls):
    names = {n for c in cls.__mro__ for n in vars(c)}
    assert not names & (WRITE_NAMES | {"sock", "socket", "_sock", "writer"})


def test_can2_wrapper_hides_socket():
    class Probe:
        def fileno(self):
            return -1

        def recv(self, n):
            raise BlockingIOError
    r = Can2ReceiveOnly(Probe())
    assert r.bus == 2
    assert not any(isinstance(v, Probe) for k, v in vars(r).items() if not k.startswith("_CanReceiver__"))
    assert r.read_frames() == []


def test_open_pack_can2_returns_receive_only(monkeypatch):
    class FakeSock:
        def __init__(self, *a):
            pass

        def bind(self, addr):
            self.addr = addr

        def setblocking(self, flag):
            pass
    monkeypatch.setattr(canio.socket, "socket", FakeSock)
    monkeypatch.setattr(canio.socket, "AF_CAN", 29, raising=False)
    monkeypatch.setattr(canio.socket, "CAN_RAW", 1, raising=False)
    r = canio.open_pack_can2("can1")
    assert type(r) is Can2ReceiveOnly


def test_sim_can2_socket_has_no_send():
    link = SimLink(lambda: 0)
    assert not hasattr(SimCan2Socket(link), "send")


def test_real_af_can_socket_if_available():
    try:
        s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    except (OSError, AttributeError):
        pytest.skip("AF_CAN not available on this kernel")
    s.close()
