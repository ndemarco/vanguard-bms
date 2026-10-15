"""FastAPI application: JSON snapshot, WebSocket feed and the four control endpoints."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, StrictInt, field_validator

from .client import BridgeLink, ClientCore, CommandResult
from .logger import SessionLogger
from .replay import ReplaySource
from .state import PackState, StatusLog
from .view import build_view

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
REPLY_WAIT_S = 1.5
SNAPSHOT_PERIOD_S = 0.25
MAX_WS_CLIENTS = 8


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["discharge", "charge"]


class SleepBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    level: StrictInt

    @field_validator("level")
    @classmethod
    def _level(cls, v: int) -> int:
        if v not in (1, 3):
            raise ValueError("level must be 1 or 3")
        return v


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppController:
    """Owns the state model and, in live mode, the bridge client; in replay mode, the replay source."""

    def __init__(self, *, mode: str, state: PackState, status: StatusLog, core: Optional[ClientCore] = None,
                 link: Optional[BridgeLink] = None, replay: Optional[ReplaySource] = None,
                 logger: Optional[SessionLogger] = None, clock: Callable[[], float] = time.monotonic,
                 bind_warning: Optional[str] = None):
        assert mode in ("live", "replay")
        self.mode = mode
        self.state = state
        self.status = status
        self.core = core
        self.link = link
        self.replay = replay
        self.logger = logger
        self.clock = clock
        self.bind_warning = bind_warning
        self.waiters: Dict[str, List[asyncio.Future]] = {}
        self.ws_clients = 0
        if core is not None:
            core.on_reply = self._on_reply

    def now(self) -> float:
        return self.replay.now() if self.mode == "replay" and self.replay else self.clock()

    def _on_reply(self, word: str, text: str) -> None:
        for fut in self.waiters.pop(word, []):
            if not fut.done():
                fut.set_result(text)

    def controls(self) -> dict:
        if self.mode == "replay" or self.core is None:
            return {"enabled": False, "reason": "replay mode: controls are disabled"}
        if not self.core.connected:
            return {"enabled": False, "reason": "not connected to the bridge"}
        return {"enabled": True, "reason": ""}

    def snapshot(self) -> dict:
        now = self.now()
        if self.core is not None:
            bridge = self.core.summary(self.clock())
            replies = {w: {"ok": r["ok"], "text": r["text"], "age": round(self.clock() - r["t"], 1)}
                       for w, r in self.core.replies.items()}
        else:
            bridge = {"connected": False, "mode": "replay", "state": "replay", "state_age": None, "config": [],
                      "app_wants_running": False, "app_mode": None, "halt_reason": None, "cooldown_s": 0.0}
            replies = {}
        bridge["bind_warning"] = self.bind_warning
        lg = {}
        if self.logger is not None:
            lg = {"file": self.logger.frames_path, "status_file": self.logger.status_path,
                  "frames": self.logger.frames_written, "error": self.logger.error}
        return build_view(state=self.state, status=self.status, now=now, mode=self.mode, bridge=bridge,
                          controls=self.controls(), replies=replies, logging=lg,
                          replay=self.replay.summary() if self.replay else None)

    async def command(self, word: str, fn: Callable[[float], CommandResult]) -> tuple:
        if self.mode == "replay" or self.core is None:
            return CommandResult(False, None, "replay mode: controls are disabled"), None
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.waiters.setdefault(word, []).append(fut)
        res = fn(self.clock())
        reply = None
        if res.accepted and res.command is not None:
            try:
                reply = await asyncio.wait_for(fut, REPLY_WAIT_S)
            except asyncio.TimeoutError:
                reply = None
        if not fut.done():
            fut.cancel()
        lst = self.waiters.get(word, [])
        if fut in lst:
            lst.remove(fut)
        return res, reply

    async def startup(self) -> None:
        if self.link:
            self.link.start()
        if self.replay:
            self.replay.start()
        self._flusher = asyncio.get_running_loop().create_task(self._flush_loop())

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self.logger:
                self.logger.flush()

    async def shutdown(self) -> None:
        self._flusher.cancel()
        if self.link:
            await self.link.close()
        if self.replay:
            await self.replay.close()
        if self.logger:
            self.logger.close()


def _host_only(netloc: str) -> str:
    netloc = netloc.strip().lower()
    if netloc.startswith("["):
        return netloc.split("]")[0] + "]"
    return netloc.rsplit(":", 1)[0] if ":" in netloc else netloc


class GuardMiddleware:
    """Host and Origin checks (DNS rebinding, cross-site requests), JSON-only POST, security headers."""

    def __init__(self, app, allowed_hosts: Optional[set]):
        self.app = app
        self.allowed_hosts = allowed_hosts  # None = any Host (explicit non-loopback bind)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        problem = None
        if self.allowed_hosts is not None and _host_only(host) not in self.allowed_hosts:
            problem = (421, "host not allowed")
        origin = headers.get("origin")
        if problem is None and origin is not None:
            if origin == "null" or urlsplit(origin).netloc.lower() != host.lower():
                problem = (403, "cross-origin request refused")
        if problem is None and scope["type"] == "http" and scope["method"] == "POST":
            if not headers.get("content-type", "").lower().startswith("application/json"):
                problem = (415, "POST needs Content-Type: application/json")
        if problem is not None:
            if scope["type"] == "websocket":
                await receive()
                await send({"type": "websocket.close", "code": 1008})
                return
            body = json.dumps({"detail": problem[1]}).encode()
            await send({"type": "http.response.start", "status": problem[0],
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                hs = list(message.get("headers", []))
                hs += [(b"content-security-policy", CSP.encode()), (b"x-content-type-options", b"nosniff"),
                       (b"referrer-policy", b"no-referrer"), (b"cache-control", b"no-store")]
                message = {**message, "headers": hs}
            await send(message)
        await self.app(scope, receive, send_wrapped)


def create_app(ctl: AppController, *, allowed_hosts: Optional[set] = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await ctl.startup()
        try:
            yield
        finally:
            await ctl.shutdown()

    app = FastAPI(title="Vanguard battery app", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def static_route(path: str, name: str, ctype: str) -> None:
        data = (STATIC_DIR / name).read_bytes()

        async def handler() -> Response:
            return Response(data, media_type=ctype)
        app.add_api_route(path, handler, methods=["GET"], include_in_schema=False)

    for path, (name, ctype) in STATIC_FILES.items():
        static_route(path, name, ctype)

    @app.get("/api/state")
    async def api_state() -> JSONResponse:
        return JSONResponse(ctl.snapshot())

    def respond(res: CommandResult, reply: Optional[str]) -> JSONResponse:
        body = {"accepted": res.accepted, "command": res.command, "message": res.message, "reply": reply}
        return JSONResponse(body, status_code=200 if res.accepted else 409)

    @app.post("/api/start")
    async def api_start(body: StartBody) -> JSONResponse:
        core = ctl.core
        return respond(*await ctl.command("START", lambda now: core.request_start(body.mode, now)))

    @app.post("/api/stop")
    async def api_stop(body: Optional[EmptyBody] = None) -> JSONResponse:
        core = ctl.core
        return respond(*await ctl.command("STOP", lambda now: core.request_stop(now)))

    @app.post("/api/clear")
    async def api_clear(body: Optional[EmptyBody] = None) -> JSONResponse:
        core = ctl.core
        return respond(*await ctl.command("CLEAR", lambda now: core.request_clear(now)))

    @app.post("/api/sleep")
    async def api_sleep(body: SleepBody) -> JSONResponse:
        core = ctl.core
        return respond(*await ctl.command("SLEEP", lambda now: core.request_sleep(body.level, now)))

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        if ctl.ws_clients >= MAX_WS_CLIENTS:
            await sock.close(code=1013)
            return
        await sock.accept()
        ctl.ws_clients += 1
        try:
            while True:
                await sock.send_text(json.dumps(ctl.snapshot(), separators=(",", ":")))
                try:
                    await asyncio.wait_for(sock.receive_text(), SNAPSHOT_PERIOD_S)
                except asyncio.TimeoutError:
                    pass
        except Exception:  # client went away
            pass
        finally:
            ctl.ws_clients -= 1

    app.add_middleware(GuardMiddleware, allowed_hosts=allowed_hosts)
    return app
