import http.cookiejar
import json
import threading
import urllib.error
import urllib.request
from urllib.parse import urlencode

import pytest

from migration_agent import status_server as status_server_module
from migration_agent.config import ConfigStore
from migration_agent.runtime_state import ActivityEntry, RuntimeState, classify_activity
from migration_agent.status_server import _render_activity_table, _render_connection_status, make_server


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


def _opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def _login(opener, port, username, password):
    data = urlencode({"username": username, "password": password}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/login", data=data, method="POST")
    return opener.open(req, timeout=5)


def _authenticated_opener(store, port):
    cfg = store.load()
    opener, _jar = _opener()
    _login(opener, port, cfg.ui_username, cfg.ui_default_password)
    return opener


def test_root_redirects_to_login_when_not_authenticated(running_server):
    _, port = running_server
    opener, _jar = _opener()
    body = opener.open(f"http://127.0.0.1:{port}/", timeout=5).read().decode("utf-8")
    assert "entrar" in body.lower()
    assert 'name="password"' in body


def test_status_json_requires_authentication(running_server):
    _, port = running_server
    opener, _jar = _opener()
    body = opener.open(f"http://127.0.0.1:{port}/status.json", timeout=5).read().decode("utf-8")
    # sem sessao valida, /status.json tambem redireciona pro /login (nao vaza estado)
    assert "entrar" in body.lower()


def test_login_with_default_password_succeeds_and_shows_pairing_form(running_server):
    store, port = running_server
    opener = _authenticated_opener(store, port)
    body = opener.open(f"http://127.0.0.1:{port}/", timeout=5).read().decode("utf-8")
    assert "pareamento" in body.lower()
    assert "pairing_token" in body


def test_login_with_wrong_password_returns_401(running_server):
    store, port = running_server
    cfg = store.load()
    opener, _jar = _opener()
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _login(opener, port, cfg.ui_username, "senha-errada")
    assert exc_info.value.code == 401


def test_default_password_banner_shown_until_password_changed(running_server):
    store, port = running_server
    opener = _authenticated_opener(store, port)
    body = opener.open(f"http://127.0.0.1:{port}/", timeout=5).read().decode("utf-8")
    assert "senha padrão ainda não foi trocada" in body.lower()


def test_pair_endpoint_requires_authentication(running_server):
    _, port = running_server
    opener, _jar = _opener()
    data = urlencode({"guardian_base_url": "https://guardian.exemplo.com", "pairing_token": "token-valido"}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/pair", data=data, method="POST")
    body = opener.open(req, timeout=5).read().decode("utf-8")
    assert "entrar" in body.lower()


def test_pair_endpoint_persists_config_and_redirects(running_server):
    store, port = running_server
    opener = _authenticated_opener(store, port)
    data = urlencode({"guardian_base_url": "https://guardian.exemplo.com", "pairing_token": "token-valido"}).encode()

    req = urllib.request.Request(f"http://127.0.0.1:{port}/pair", data=data, method="POST")
    resp = opener.open(req, timeout=5)
    assert resp.status == 200  # opener já seguiu o redirect 303

    reloaded = store.load()
    assert reloaded.is_paired is True
    assert reloaded.agent_id == "agent-9"
    assert reloaded.auth_token == "tok-9"
    assert reloaded.cliente_nome == "Cliente Fake"


def test_pair_endpoint_with_invalid_token_returns_400(running_server):
    store, port = running_server
    opener = _authenticated_opener(store, port)
    data = urlencode({"guardian_base_url": "https://guardian.exemplo.com", "pairing_token": "token-invalido"}).encode()

    req = urllib.request.Request(f"http://127.0.0.1:{port}/pair", data=data, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        opener.open(req, timeout=5)
    assert exc_info.value.code == 400


def test_status_json_reflects_paired_state(running_server):
    store, port = running_server
    cfg = store.load()
    cfg.agent_id = "agent-9"
    cfg.auth_token = "tok-9"
    cfg.cliente_nome = "Cliente Fake"
    store.save(cfg)

    opener = _authenticated_opener(store, port)
    body = opener.open(f"http://127.0.0.1:{port}/status.json", timeout=5).read()
    payload = json.loads(body)
    assert payload["paired"] is True
    assert payload["agent_id"] == "agent-9"


def test_change_password_with_wrong_current_password_returns_400(running_server):
    store, port = running_server
    opener = _authenticated_opener(store, port)
    data = urlencode(
        {"current_password": "senha-errada", "new_password": "nova-senha-123", "new_password_confirm": "nova-senha-123"}
    ).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/change-password", data=data, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        opener.open(req, timeout=5)
    assert exc_info.value.code == 400


def test_change_password_with_mismatched_confirmation_returns_400(running_server):
    store, port = running_server
    cfg = store.load()
    opener = _authenticated_opener(store, port)
    data = urlencode(
        {
            "current_password": cfg.ui_default_password,
            "new_password": "nova-senha-123",
            "new_password_confirm": "outra-coisa",
        }
    ).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/change-password", data=data, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        opener.open(req, timeout=5)
    assert exc_info.value.code == 400


def test_change_password_succeeds_and_old_password_stops_working(running_server):
    store, port = running_server
    cfg = store.load()
    opener = _authenticated_opener(store, port)
    data = urlencode(
        {
            "current_password": cfg.ui_default_password,
            "new_password": "nova-senha-123",
            "new_password_confirm": "nova-senha-123",
        }
    ).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/change-password", data=data, method="POST")
    resp = opener.open(req, timeout=5)
    assert resp.status == 200  # seguiu o redirect ate /login

    reloaded = store.load()
    assert reloaded.ui_password_is_default is False
    assert reloaded.ui_default_password is None

    # sessao anterior foi invalidada pela troca de senha
    body = opener.open(f"http://127.0.0.1:{port}/", timeout=5).read().decode("utf-8")
    assert "entrar" in body.lower()

    # senha antiga nao funciona mais, a nova sim
    fresh_opener, _jar = _opener()
    with pytest.raises(urllib.error.HTTPError):
        _login(fresh_opener, port, cfg.ui_username, cfg.ui_default_password)

    fresh_opener2, _jar2 = _opener()
    resp2 = _login(fresh_opener2, port, cfg.ui_username, "nova-senha-123")
    assert resp2.status == 200


def test_logout_invalidates_session(running_server):
    store, port = running_server
    opener = _authenticated_opener(store, port)
    opener.open(f"http://127.0.0.1:{port}/logout", timeout=5)

    body = opener.open(f"http://127.0.0.1:{port}/", timeout=5).read().decode("utf-8")
    assert "entrar" in body.lower()


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Pareado com sucesso ao cliente 'Cliente Fake'", "success"),
        ("Comando abc-123 concluído", "success"),
        ("Comando def-456 falhou: agent não respondeu a tempo", "error"),
        ("Guardian inacessível: timeout", "error"),
        ("Tentativa de login com credenciais invalidas na UI local", "error"),
        ("Comando recebido: run_transfer (abc-123)", "info"),
    ],
)
def test_classify_activity(message, expected):
    assert classify_activity(message) == expected


def test_render_activity_table_shows_human_readable_timestamp_not_raw_epoch():
    entries = [ActivityEntry(timestamp=1757600920.123456, message="Pareado com sucesso ao cliente 'X'")]
    rendered = _render_activity_table(entries)

    assert "<table" in rendered
    assert "1757600920" not in rendered  # timestamp cru (epoch) nao pode vazar pra UI
    assert "dot-success" in rendered


def test_render_activity_table_empty_state():
    rendered = _render_activity_table([])
    assert "Nenhuma atividade ainda" in rendered
    assert "<table" not in rendered


def _pair(store):
    cfg = store.load()
    cfg.guardian_base_url = "https://guardian.exemplo.com"
    cfg.agent_id = "agent-9"
    cfg.auth_token = "tok-9"
    cfg.cliente_nome = "Cliente Fake"
    store.save(cfg)
    return cfg


def test_update_check_shows_up_to_date_banner_when_no_newer_version(running_server, monkeypatch):
    store, port = running_server
    _pair(store)
    monkeypatch.setattr(status_server_module, "check_for_update", lambda client, cfg: None)
    opener = _authenticated_opener(store, port)

    body = opener.open(f"http://127.0.0.1:{port}/update/check", data=b"", timeout=5).read().decode("utf-8")

    assert "você já está na versão mais recente" in body.lower()


def test_update_check_shows_available_banner_with_apply_button(running_server, monkeypatch):
    store, port = running_server
    _pair(store)
    monkeypatch.setattr(status_server_module, "check_for_update", lambda client, cfg: "0.1.99.0")
    opener = _authenticated_opener(store, port)

    body = opener.open(f"http://127.0.0.1:{port}/update/check", data=b"", timeout=5).read().decode("utf-8")

    assert "nova versão disponível" in body.lower()
    assert "0.1.99.0" in body
    assert 'action="/update/apply"' in body


def test_update_check_shows_error_banner_on_network_failure(running_server, monkeypatch):
    store, port = running_server
    _pair(store)

    def _boom(client, cfg):
        raise Exception("Guardian inacessível: timeout")

    monkeypatch.setattr(status_server_module, "check_for_update", _boom)
    opener = _authenticated_opener(store, port)

    body = opener.open(f"http://127.0.0.1:{port}/update/check", data=b"", timeout=5).read().decode("utf-8")

    assert "não foi possível verificar atualizações" in body.lower()


def test_update_apply_shows_applying_banner_and_records_activity(running_server, monkeypatch):
    store, port = running_server
    _pair(store)
    calls = {}
    monkeypatch.setattr(status_server_module, "apply_update", lambda client, cfg: calls.setdefault("called", True))
    opener = _authenticated_opener(store, port)

    body = opener.open(f"http://127.0.0.1:{port}/update/apply", data=b"", timeout=5).read().decode("utf-8")

    assert calls.get("called") is True
    assert "atualização iniciada" in body.lower()


def test_update_apply_shows_error_banner_when_apply_fails(running_server, monkeypatch):
    store, port = running_server
    _pair(store)

    def _boom(client, cfg):
        raise Exception("msiexec não encontrado")

    monkeypatch.setattr(status_server_module, "apply_update", _boom)
    opener = _authenticated_opener(store, port)

    body = opener.open(f"http://127.0.0.1:{port}/update/apply", data=b"", timeout=5).read().decode("utf-8")

    assert "falha ao aplicar a atualização" in body.lower()
    assert "msiexec não encontrado" in body


def test_render_connection_status_shows_disconnected_by_default():
    state = RuntimeState()
    rendered = _render_connection_status(state)
    assert "desconectado" in rendered.lower()
    assert "dot-error" in rendered
    assert "outro agent" not in rendered.lower()


def test_render_connection_status_shows_connected_guardian():
    state = RuntimeState()
    state.set_guardian_status(True)
    rendered = _render_connection_status(state)
    assert "conectado" in rendered.lower()
    assert "dot-success" in rendered


def test_render_connection_status_shows_active_peer_transfer():
    state = RuntimeState()
    state.set_guardian_status(True)
    state.set_peer_status("Enviando arquivo para outro agent (10.0.0.5:5555)")

    rendered = _render_connection_status(state)

    assert "outro agent" in rendered.lower()
    assert "10.0.0.5:5555" in rendered
    assert "dot-info" in rendered


def test_render_connection_status_hides_peer_row_when_inactive():
    state = RuntimeState()
    rendered = _render_connection_status(state)
    assert "conn-row" in rendered  # so a linha do Guardian
    assert rendered.count("conn-row") == 1


def test_paired_page_shows_tabs_and_connection_status_in_config_tab(running_server):
    store, port = running_server
    _pair(store)
    opener = _authenticated_opener(store, port)

    body = opener.open(f"http://127.0.0.1:{port}/", timeout=5).read().decode("utf-8")

    assert 'data-tab="acoes"' in body
    assert 'data-tab="config"' in body
    assert 'data-panel="acoes"' in body
    assert 'data-panel="config"' in body
    assert "status de conexão" in body.lower()
    assert "versão instalada" in body.lower()


