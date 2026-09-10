import json
import threading
import urllib.error
import urllib.request
from urllib.parse import urlencode

import pytest

from migration_agent import status_server as status_server_module
from migration_agent.config import ConfigStore
from migration_agent.runtime_state import RuntimeState
from migration_agent.status_server import make_server


class _FakeGuardianClient:
    def __init__(self, base_url):
        self.base_url = base_url

    def pair(self, pairing_token):
        if pairing_token == "token-valido":
            return {"agent_id": "agent-9", "auth_token": "tok-9", "cliente_nome": "Cliente Fake"}
        from migration_agent.api_client import PairingError

        raise PairingError("token invalido ou expirado")


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    monkeypatch.setattr(status_server_module, "GuardianClient", _FakeGuardianClient)

    store = ConfigStore(state_dir=tmp_path)
    state = RuntimeState()
    server = make_server(store, state, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield store, port
    finally:
        server.shutdown()


def test_root_shows_pairing_form_when_not_paired(running_server):
    _, port = running_server
    body = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode("utf-8")
    assert "pareamento" in body.lower()
    assert "pairing_token" in body


def test_pair_endpoint_persists_config_and_redirects(running_server):
    store, port = running_server
    data = urlencode({"guardian_base_url": "https://guardian.exemplo.com", "pairing_token": "token-valido"}).encode()

    req = urllib.request.Request(f"http://127.0.0.1:{port}/pair", data=data, method="POST")
    resp = urllib.request.urlopen(req, timeout=5)
    assert resp.status == 200  # urllib já seguiu o redirect 303

    reloaded = store.load()
    assert reloaded.is_paired is True
    assert reloaded.agent_id == "agent-9"
    assert reloaded.auth_token == "tok-9"
    assert reloaded.cliente_nome == "Cliente Fake"


def test_pair_endpoint_with_invalid_token_returns_400(running_server):
    _, port = running_server
    data = urlencode({"guardian_base_url": "https://guardian.exemplo.com", "pairing_token": "token-invalido"}).encode()

    req = urllib.request.Request(f"http://127.0.0.1:{port}/pair", data=data, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 400


def test_status_json_reflects_paired_state(running_server):
    store, port = running_server
    cfg = store.load()
    cfg.agent_id = "agent-9"
    cfg.auth_token = "tok-9"
    cfg.cliente_nome = "Cliente Fake"
    store.save(cfg)

    body = urllib.request.urlopen(f"http://127.0.0.1:{port}/status.json", timeout=5).read()
    payload = json.loads(body)
    assert payload["paired"] is True
    assert payload["agent_id"] == "agent-9"
