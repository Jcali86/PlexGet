"""Shared set-up for the server's tests.

A config file is written before anything imports `config`, because config.py
reads it the moment it is imported and a fixture runs too late for that. Every
test therefore runs against a throwaway config.yaml, database and library index
in a temporary folder, and never against the real ones beside the code.
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TEMP = Path(tempfile.mkdtemp(prefix="plexget-tests-"))
(_TEMP / "config.yaml").write_text(
    "plex:\n"
    '  url: "http://plex.test:32400"\n'
    '  token: "FIXTURE-PLEX-TOKEN"\n'
    "flask:\n"
    '  host: "127.0.0.1"\n'
    "database:\n"
    f'  path: "{_TEMP / "plex_ops.db"}"\n'
    "index:\n"
    f'  path: "{_TEMP / "library_index.db"}"\n'
    "  background: false\n",
    encoding="utf-8",
)
os.environ["PLEXGET_CONFIG"] = str(_TEMP / "config.yaml")

import config as config_module  # noqa: E402  (must follow the environment above)
import api  # noqa: E402,F401

FIXTURES = ROOT / "tests" / "fixtures" / "api"


@pytest.fixture
def app():
    from api import create_app

    application = create_app()
    application.testing = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


_SQLITE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _normalise(value, key=None):
    """03-server-api.md section 6.2: the four rules that make a fixture deterministic."""
    if key == "token" and isinstance(value, str):
        return "FIXTURE-TOKEN-" + "0" * 29
    if key in ("server_key", "relay_handle") and isinstance(value, str):
        return "FIXTURE-KEY"
    if isinstance(value, dict):
        return {k: _normalise(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    if isinstance(value, str):
        if _SQLITE_TIME.match(value):
            return "2026-01-02 03:04:05"
        if _ISO_TIME.match(value):
            return "2026-01-02T03:04:05Z"
    return value


@pytest.fixture
def record():
    """record(name, response, body=None, role="none"): write tests/fixtures/api/<name>.json."""
    def write(name, response, body=None, role="none"):
        request = response.request
        path = request.path + (f"?{request.query_string.decode()}" if request.query_string else "")
        is_json = (response.mimetype or "").endswith("json")
        envelope = {
            "request": {"method": request.method, "path": path, "body": body, "role": role},
            "status": response.status_code,
            "body": response.get_json() if is_json and response.status_code != 204 else None,
        }
        FIXTURES.mkdir(parents=True, exist_ok=True)
        text = json.dumps(_normalise(envelope), indent=2, sort_keys=True, ensure_ascii=False)
        (FIXTURES / f"{name}.json").write_text(text + "\n", encoding="utf-8")
    return write


# ---- the library index and a fake Plex ------------------------------------------

@pytest.fixture
def index_path(tmp_path, monkeypatch):
    """A fresh, empty library index for this test alone."""
    from scanner import library_index

    path = tmp_path / "library_index.db"
    monkeypatch.setitem(config_module.config, "index",
                        {"path": str(path), "background": False, "enabled": True})
    monkeypatch.setattr(library_index, "_availability", {"at": 0.0, "verdict": (False, "")})
    monkeypatch.setattr(library_index, "_schema_ready", set())
    monkeypatch.setattr(library_index, "_names", {"key": None, "choices": {}})
    return path


@pytest.fixture
def plex(monkeypatch, index_path):
    """A fake Plex, installed as the owner's server and as every member's view of it."""
    from api import request_assistant
    from scanner import discover, wanted_search
    from tests.fake_plex import FakeServer

    server = FakeServer()
    monkeypatch.setattr(wanted_search, "_server", server)
    monkeypatch.setattr(wanted_search, "_as_user", {})
    monkeypatch.setattr(wanted_search, "server_for", lambda token=None: server)
    monkeypatch.setattr(wanted_search, "_wedged_until", 0.0)
    monkeypatch.setattr(request_assistant, "_people", {})
    monkeypatch.setattr(discover, "_owned", None)
    return server
