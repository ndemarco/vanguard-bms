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


def test_top_status_banner_ids_and_order():
    html = (STATIC / "index.html").read_text()
    for i in ("topstatus", "ts-contactors", "ts-session", "ts-faults", "ts-meas", "ts-meas-note"):
        assert f'id="{i}"' in html
    assert html.index('id="topstatus"') < html.index('id="controls"')
    js = (STATIC / "app.js").read_text()
    assert "renderTopStatus" in js and "CLOSING" in js and "CLOSED (confirmed)" in js and "NO DATA" in js
    assert "/api/start" in js and js.count("/api/start") == 1


def test_controls_fault_list_ids_and_gating_function():
    html = (STATIC / "index.html").read_text()
    for i in ("ctl-faults", "ctl-faults-list", "ctl-faults-instr", "start-why", "clear-why"):
        assert f'id="{i}"' in html
    assert html.index('id="ctl-faults"') < html.index('id="btn-clear"')
    # Clear faults lives inside the fault block, next to the instruction line, not in the button row
    fstart = html.index('id="ctl-faults"')
    fend = html.index('<div class="ctl-grid">')
    for i in ("btn-clear", "clear-why", "reply-CLEAR"):
        assert fstart < html.index(f'id="{i}"') < fend
    assert html.index('id="ctl-faults-list"') < html.index('id="btn-clear"')
    grid = html[fend:]
    assert 'id="btn-clear"' not in grid
    for i in ("btn-start", "btn-sleep"):
        assert f'id="{i}"' in grid
    assert 'id="btn-stop"' not in grid and 'id="ctl-stop"' not in grid
    assert grid.index('id="btn-start"') < grid.index('id="btn-sleep"')
    js = (STATIC / "app.js").read_text()
    assert "function faultsGatingStart" in js and "Blocking faults present: " in js
    assert js.count("/api/start") == 1 and js.count("/api/clear") == 1
    # Start is never disabled because of faults
    start_lines = [l for l in js.splitlines() if 'btn-start").disabled' in l]
    assert start_lines and all("fault" not in l.lower() for l in start_lines)


def _css_rules(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", css)


def test_confirm_rows_hidden_by_default_and_css_keeps_hidden_hidden():
    html = (STATIC / "index.html").read_text()
    for which in ("start", "sleep"):
        tag = re.search(r'<div[^>]*id="confirm-%s"[^>]*>' % which, html).group(0)
        assert re.search(r"\shidden(\s|>)", tag), tag
    css = (STATIC / "style.css").read_text()
    rules = _css_rules(css)
    assert any(sel.strip() == "[hidden]" and re.search(r"display:\s*none\s*!important", body) for sel, body in rules)
    js = (STATIC / "app.js").read_text()
    assert "setTimeout(() => hideConfirm(which), 10000)" in js
    # a disabled Start/Sleep closes its confirmation row
    assert 'if (startWhy) hideConfirm("start")' in js and 'if (sleepWhy) hideConfirm("sleep")' in js


def test_show_all_toggle_persists_with_try_catch_and_hides_only_unseen():
    html = (STATIC / "index.html").read_text()
    assert html.count('id="show-all"') == 1 and "Show all signals (including not received)" in html
    js = (STATIC / "app.js").read_text()
    for call in ("localStorage.getItem", "localStorage.setItem"):
        line = next(l for l in js.splitlines() if call in l)
        assert "try" in line and "catch" in line, line
    assert "const visible = (s) => showAll || s.seen;" in js
    assert "can2_seen" in js


def test_comms_lost_bar_and_page_goes_dark():
    html = (STATIC / "index.html").read_text()
    assert html.index('id="commsbar"') < html.index("<header")
    js = (STATIC / "app.js").read_text()
    assert '"NO DATA: comms lost ("' in js and "function goDark" in js
    assert 'goDark("page lost its connection to the app")' in js
    assert 'if (s.blank) return "--";' in js


def test_page_sends_commands_only_from_click_handlers():
    js = (STATIC / "app.js").read_text()
    posts = [l for l in js.splitlines() if "post(\"/api/" in l]
    assert len(posts) == 4 and all('addEventListener("click"' in l for l in posts)


def _cell_html(html, cell_id):
    start = html.index(f'id="{cell_id}"')
    # the cell ends where the next ts-cell starts
    return html[start:html.index('class="ts-cell"', start + 1)]


def test_stop_button_lives_in_topstatus_contactors_cell():
    html = (STATIC / "index.html").read_text()
    assert html.count('id="btn-stop"') == 1
    cell = _cell_html(html, "ts-contactors-cell")
    assert 'id="btn-stop"' in cell and 'id="reply-STOP"' in cell
    top = html[html.index('id="topstatus"'):html.index('id="controls"')]
    assert 'id="btn-stop"' in top
    assert "Opens the contactors" not in html and "Always allowed" not in html
    js = (STATIC / "app.js").read_text()
    assert '$("btn-stop").addEventListener("click", () => { hideConfirm("start"); post("/api/stop", {}, "STOP"); });' in js
    assert js.count("/api/stop") == 1
    # the reply line is only shown when there is a reply
    assert "el.hidden = !shown;" in js


def test_no_sign_unverified_for_current_but_limits_keep_direction_lozenge():
    js = (STATIC / "app.js").read_text()
    html = (STATIC / "index.html").read_text()
    assert "sign unverified" not in js and "sign unverified" not in html
    assert 'card("Limits", hl.limits, false, "direction unverified")' in js
    from vanguard.web import view
    assert not hasattr(view, "SIGN_NOTE")


def test_fault_block_hidden_unless_faults_and_no_next_step_or_hidden_notes():
    html = (STATIC / "index.html").read_text()
    tag = re.search(r'<div[^>]*id="ctl-faults"[^>]*>', html).group(0)
    assert re.search(r"\shidden(\s|>)", tag), tag
    assert "<h3>Faults</h3>" not in html and 'id="ctl-next"' not in html
    js = (STATIC / "app.js").read_text()
    assert '$("ctl-faults").hidden = !show;' in js and "cf.items.length > 0" in js
    assert '"No faults"' not in js and '"nofault"' not in js and "Press Stop to turn it off" not in js and "function nextStep" not in js
    assert "not received yet (hidden)" not in js and "hiddenNote" not in js


def test_current_amps_two_decimals_and_power_formatting_in_js():
    js = (STATIC / "app.js").read_text()
    assert 'if (unit === "A") return v.toFixed(2) + " A";' in js
    assert 'p.kw.toFixed(2) + " kW"' in js and "function powerCard" in js
    assert '$("ts-power").textContent = fmtPower(t.power);' in js
    html = (STATIC / "index.html").read_text()
    meas = html[html.index('id="ts-meas-cell"'):html.index('id="controls"')]
    assert 'id="ts-power"' in meas
