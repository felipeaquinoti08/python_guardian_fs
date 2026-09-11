import threading
import time
import urllib.request

import migration_agent.agent_runtime as agent_runtime_module
from migration_agent.agent_runtime import AgentRuntime


def test_start_background_serves_status_ui_and_stops_cleanly(tmp_path):
    runtime = AgentRuntime(state_dir=tmp_path)
    runtime.start_background()
    try:
        cfg = runtime.config_store.load()
        deadline = time.monotonic() + 5
        last_error = None
        while time.monotonic() < deadline:
            try:
                # /status.json agora exige login (issue #107) -- a pagina de
                # login sem sessao ja e prova suficiente de que o servidor HTTP
                # esta de pe.
                body = urllib.request.urlopen(f"http://127.0.0.1:{cfg.local_ui_port}/status.json", timeout=1).read()
                assert b"Entrar" in body
                break
            except Exception as exc:  # servidor pode ainda não ter subido na primeira tentativa
                last_error = exc
                time.sleep(0.1)
        else:
            raise AssertionError(f"UI local não respondeu a tempo: {last_error}")
    finally:
        runtime.stop()


class _FakeGuardianClient:
    def __init__(self, base_url):
        self.base_url = base_url

    def report_event(self, agent_id, auth_token, event_type, status, message, detail=None):
        _FakeGuardianClient.last_call = {
            "agent_id": agent_id,
            "auth_token": auth_token,
            "event_type": event_type,
            "status": status,
            "message": message,
        }
        _FakeGuardianClient.done.set()


def _paired_runtime(tmp_path):
    runtime = AgentRuntime(state_dir=tmp_path)
    cfg = runtime.config_store.load()
    cfg.guardian_base_url = "https://guardian.exemplo.com"
    cfg.agent_id = "agent-9"
    cfg.auth_token = "tok-9"
    runtime.config_store.save(cfg)
    return runtime


def test_report_event_async_sends_operational_event_when_paired(tmp_path, monkeypatch):
    _FakeGuardianClient.done = threading.Event()
    _FakeGuardianClient.last_call = None
    monkeypatch.setattr(agent_runtime_module, "GuardianClient", _FakeGuardianClient)

    runtime = _paired_runtime(tmp_path)
    runtime._report_event_async("Pareado com sucesso ao cliente 'X'")

    assert _FakeGuardianClient.done.wait(timeout=2)
    assert _FakeGuardianClient.last_call["agent_id"] == "agent-9"
    assert _FakeGuardianClient.last_call["event_type"] == "operational"
    assert _FakeGuardianClient.last_call["status"] == "success"
    assert _FakeGuardianClient.last_call["message"] == "Pareado com sucesso ao cliente 'X'"


def test_report_event_async_skips_when_not_paired(tmp_path, monkeypatch):
    _FakeGuardianClient.done = threading.Event()
    _FakeGuardianClient.last_call = None
    monkeypatch.setattr(agent_runtime_module, "GuardianClient", _FakeGuardianClient)

    runtime = AgentRuntime(state_dir=tmp_path)  # nao pareado
    runtime._report_event_async("Aguardando pareamento (acesse a UI local)")

    assert not _FakeGuardianClient.done.wait(timeout=0.3)
    assert _FakeGuardianClient.last_call is None


def test_report_event_async_skips_comando_messages(tmp_path, monkeypatch):
    _FakeGuardianClient.done = threading.Event()
    _FakeGuardianClient.last_call = None
    monkeypatch.setattr(agent_runtime_module, "GuardianClient", _FakeGuardianClient)

    runtime = _paired_runtime(tmp_path)
    runtime._report_event_async("Comando recebido: run_transfer (abc-123)")

    assert not _FakeGuardianClient.done.wait(timeout=0.3)
    assert _FakeGuardianClient.last_call is None
