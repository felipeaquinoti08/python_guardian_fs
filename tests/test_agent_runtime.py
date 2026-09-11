import threading
import time
import urllib.request
from unittest.mock import MagicMock

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


class _AlwaysFailingClient:
    def __init__(self, base_url):
        self.base_url = base_url

    def report_event(self, *args, **kwargs):
        raise Exception("Guardian inacessível: timeout")


class _RecordingClient:
    """Sucesso sempre -- guarda cada chamada em CALLS (classe, nao
    instancia, pra sobreviver a varias instancias criadas por request)."""

    CALLS = []

    def __init__(self, base_url):
        self.base_url = base_url

    def report_event(self, agent_id, auth_token, event_type, status, message, detail=None):
        _RecordingClient.CALLS.append((status, message))


def test_report_event_async_queues_event_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runtime_module, "GuardianClient", _AlwaysFailingClient)
    runtime = _paired_runtime(tmp_path)

    runtime._report_event_async("Pareado com sucesso ao cliente 'X'")

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not runtime._pending_events:
        time.sleep(0.05)

    assert list(runtime._pending_events) == [("success", "Pareado com sucesso ao cliente 'X'")]


def test_flush_pending_events_resends_and_clears_queue_on_success(tmp_path, monkeypatch):
    _RecordingClient.CALLS = []
    monkeypatch.setattr(agent_runtime_module, "GuardianClient", _RecordingClient)
    runtime = _paired_runtime(tmp_path)
    runtime._pending_events.append(("error", "Guardian inacessível: timeout"))
    runtime._pending_events.append(("success", "Login na UI local (usuario 'admin')"))

    runtime._flush_pending_events()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and len(_RecordingClient.CALLS) < 2:
        time.sleep(0.05)

    assert _RecordingClient.CALLS == [
        ("error", "Guardian inacessível: timeout"),
        ("success", "Login na UI local (usuario 'admin')"),
    ]
    assert list(runtime._pending_events) == []


def test_flush_pending_events_requeues_events_that_fail_again(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runtime_module, "GuardianClient", _AlwaysFailingClient)
    runtime = _paired_runtime(tmp_path)
    runtime._pending_events.append(("error", "Guardian inacessível: timeout"))

    runtime._flush_pending_events()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not runtime._pending_events:
        time.sleep(0.05)

    assert list(runtime._pending_events) == [("error", "Guardian inacessível: timeout")]


def test_flush_pending_events_noop_when_queue_empty(tmp_path, monkeypatch):
    fake_thread = MagicMock()
    monkeypatch.setattr(agent_runtime_module.threading, "Thread", fake_thread)
    runtime = _paired_runtime(tmp_path)

    runtime._flush_pending_events()

    fake_thread.assert_not_called()


def test_on_guardian_status_connected_triggers_flush(tmp_path, monkeypatch):
    _RecordingClient.CALLS = []
    monkeypatch.setattr(agent_runtime_module, "GuardianClient", _RecordingClient)
    runtime = _paired_runtime(tmp_path)
    runtime._pending_events.append(("success", "Pareado com sucesso"))

    runtime._on_guardian_status(True)

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not _RecordingClient.CALLS:
        time.sleep(0.05)

    assert _RecordingClient.CALLS == [("success", "Pareado com sucesso")]
    assert runtime.state.guardian_status().connected is True


def test_pending_events_queue_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runtime_module, "GuardianClient", _AlwaysFailingClient)
    runtime = _paired_runtime(tmp_path)

    for i in range(agent_runtime_module._MAX_PENDING_EVENTS + 10):
        runtime._report_event_async(f"Evento {i}")

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and len(runtime._pending_events) < agent_runtime_module._MAX_PENDING_EVENTS:
        time.sleep(0.05)

    assert len(runtime._pending_events) == agent_runtime_module._MAX_PENDING_EVENTS
