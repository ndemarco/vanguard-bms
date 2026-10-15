# Development

Two independent Python 3.11+ packages (setuptools, `src/` layout): `app` (`vanguard`) and `bridge`
(`vanguard-bridge`). The bridge depends on `vanguard`, so install both into one venv, created in `app/`:

```bash
cd app
python3 -m venv .venv
.venv/bin/pip install -e '.[test]' -e ../bridge
.venv/bin/python -m pytest tests            # app tests
.venv/bin/python -m pytest ../bridge/tests  # bridge tests
.venv/bin/python -m pytest tests/test_wire.py::test_name   # single test
```

## Running without hardware

In two terminals:

```bash
.venv/bin/vanguard-bridge --simulate --socket /tmp/vgb.sock
.venv/bin/vanguard-app --socket /tmp/vgb.sock --port 8000     # http://127.0.0.1:8000/
```

The standalone simulator:

```bash
.venv/bin/python -m vanguard.simulator --duration 10
```

No linter or formatter is configured.
