import re
from pathlib import Path

from vanguard.web import server

STATIC = Path(server.__file__).parent / "static"


def test_no_external_urls_in_static_assets():
    files = list(STATIC.iterdir())
    assert {f.name for f in files} >= {"index.html", "app.js", "style.css"}
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"https?://", text, re.I), f"{f.name} contains an http(s) URL"
        assert not re.search(r"(src|href)\s*=\s*[\"']\s*//", text), f"{f.name} has a protocol-relative URL"
        assert "@import" not in text and "fonts.googleapis" not in text


def test_html_only_references_local_files():
    html = (STATIC / "index.html").read_text()
    refs = re.findall(r"(?:src|href)=\"([^\"]+)\"", html)
    assert refs and all(r.startswith("/") and not r.startswith("//") for r in refs)


def test_served_bodies_have_no_external_urls():
    from fastapi.testclient import TestClient
    from vanguard.web.server import AppController, create_app
    from vanguard.web.state import PackState, StatusLog
    ctl = AppController(mode="live", state=PackState(), status=StatusLog())
    with TestClient(create_app(ctl), base_url="http://127.0.0.1:8000") as c:
        for p in ("/", "/app.js", "/style.css"):
            assert not re.search(r"https?://", c.get(p).text)


def test_no_python_module_makes_outbound_requests():
    import vanguard.web as pkg
    for f in Path(pkg.__file__).parent.glob("*.py"):
        t = f.read_text()
        assert not re.search(r"^\s*(import|from)\s+(requests|urllib\.request|httpx|aiohttp|http\.client)\b", t, re.M), f.name
