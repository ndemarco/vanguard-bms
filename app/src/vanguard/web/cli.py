"""``vanguard-app`` command line."""

from __future__ import annotations

import argparse
import ipaddress
import sys
import time
from typing import Callable, Optional

from .client import Backoff, BridgeLink, ClientCore
from .logger import SessionLogger
from .replay import ReplaySource, load_log
from .server import AppController, create_app
from .state import PackState, StatusLog

DEFAULT_SOCKET = "/run/vanguard-bridge/bridge.sock"


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def is_wildcard(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.strip("[]")).is_unspecified
    except ValueError:
        return False


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="vanguard-app", description="Local web app for the Vanguard battery bridge")
    p.add_argument("--socket", default=DEFAULT_SOCKET, help=f"bridge Unix socket (default {DEFAULT_SOCKET})")
    p.add_argument("--host", default="127.0.0.1", help="address to bind (default 127.0.0.1; the page can start "
                                                       "the pack, so keep it on loopback and use an SSH tunnel)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="extra Host header value to accept (repeatable)")
    p.add_argument("--log-dir", default="vanguard-logs", help="directory for per-session frame logs")
    p.add_argument("--no-log", action="store_true", help="do not write session logs")
    p.add_argument("--replay", metavar="FILE.jsonl", help="replay a frame log; controls disabled, no bridge connection")
    p.add_argument("--speed", type=float, default=1.0, help="replay speed multiplier (default 1)")
    p.add_argument("--backoff-initial", type=float, default=2.0, help="first reconnect delay in seconds")
    p.add_argument("--backoff-max", type=float, default=60.0, help="longest reconnect delay in seconds")
    a = p.parse_args(argv)
    if a.speed <= 0:
        p.error("--speed must be positive")
    if not 0 <= a.port <= 65535:
        p.error("--port out of range")
    return a


def bind_warning_text(host: str) -> Optional[str]:
    if is_loopback(host):
        return None
    return (f"WARNING: bound to {host}, which is not a loopback address. Anyone who can reach this port can start "
            f"the pack. The page has no login. Use 127.0.0.1 and an SSH tunnel instead.")


def build_controller(a: argparse.Namespace, *, clock: Callable[[], float] = time.monotonic) -> AppController:
    state, status = PackState(), StatusLog()
    warn = bind_warning_text(a.host)
    if warn:
        status.add(warn, clock(), "app")
    if a.replay:
        frames = load_log(a.replay)
        replay = ReplaySource(frames, state, status, speed=a.speed)
        return AppController(mode="replay", state=state, status=status, replay=replay, clock=clock, bind_warning=warn)
    logger = None if a.no_log else SessionLogger(a.log_dir, clock=clock)
    core = ClientCore(state, status)
    if logger:
        core.on_frame = logger.log_frame
        core.on_status = logger.log_status
        status.add(f"logging frames to {logger.frames_path}", clock(), "app")
    link = BridgeLink(core, a.socket, clock=clock, backoff=Backoff(a.backoff_initial, a.backoff_max))
    return AppController(mode="live", state=state, status=status, core=core, link=link, logger=logger, clock=clock,
                         bind_warning=warn)


def allowed_hosts_for(a: argparse.Namespace) -> Optional[set]:
    if is_wildcard(a.host):
        return None
    hosts = {"localhost", "127.0.0.1", "[::1]"}
    hosts.add(f"[{a.host.strip('[]')}]" if ":" in a.host else a.host.lower())
    hosts.update(h.lower() for h in a.allow_host)
    return hosts


def main(argv: Optional[list] = None, *, run: Optional[Callable] = None) -> int:
    a = parse_args(argv)
    warn = bind_warning_text(a.host)
    if warn:
        bar = "!" * 78
        print(f"\n{bar}\n{warn}\n{bar}\n", file=sys.stderr, flush=True)
    ctl = build_controller(a)
    app = create_app(ctl, allowed_hosts=allowed_hosts_for(a))
    if run is None:
        import uvicorn
        run = uvicorn.run
    where = f"replaying {a.replay}" if a.replay else f"bridge socket {a.socket}"
    print(f"vanguard-app: {where}; http://{a.host}:{a.port}/", file=sys.stderr, flush=True)
    run(app, host=a.host, port=a.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
