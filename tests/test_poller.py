import threading
import time
from unittest.mock import MagicMock

import pytest

from migration_agent.api_client import AgentCommand
from migration_agent.config import AgentConfig
from migration_agent.poller import AgentPoller


def _paired_config():
    return AgentConfig(guardian_base_url="https://guardian.exemplo.com", agent_id="agent-1", auth_token="tok")


def test_cycle_with_no_pending_command_does_nothing():
    client = MagicMock()
    client.long_poll.return_value = None
    poller = AgentPoller(_paired_config(), client=client)

    poller._run_one_cycle()

    client.send_command_result.assert_not_called()


def test_successful_cycle_reports_guardian_connected():
    client = MagicMock()
    client.long_poll.return_value = None
    statuses = []
    poller = AgentPoller(_paired_config(), client=client, on_guardian_status=lambda connected, error=None: statuses.append((connected, error)))

    poller._run_one_cycle()

    assert statuses == [(True, None)]


def test_failed_cycle_reports_guardian_disconnected_with_error(monkeypatch):
    import requests

    client = MagicMock()
    client.long_poll.side_effect = requests.ConnectionError("timeout")
    statuses = []
    poller = AgentPoller(_paired_config(), client=client, on_guardian_status=lambda connected, error=None: statuses.append((connected, error)))
    monkeypatch.setattr(poller.stop_event, "wait", lambda seconds: None)  # nao espera o backoff de verdade no teste

    poller._run_one_cycle()

    assert statuses == [(False, "timeout")]


def test_push_to_agent_reports_peer_status_during_transfer_and_clears_after():
    transfer_started = threading.Event()
    transfer_may_finish = threading.Event()

    def slow_push_handler(payload, on_progress=None):
        transfer_started.set()
        transfer_may_finish.wait(timeout=5)
        return {"done": True}

    client = MagicMock()
    client.long_poll.return_value = AgentCommand(
        command_id="cmd-push",
        type="run_transfer",
        payload={"mode": "push_to_agent", "dest_ip": "192.168.1.50", "dest_port": 5555},
    )
    peer_statuses = []
    poller = AgentPoller(
        _paired_config(),
        client=client,
        handlers={"run_transfer": slow_push_handler},
        on_peer_status=peer_statuses.append,
    )

    thread = threading.Thread(target=poller._execute_and_report, args=(client.long_poll.return_value,))
    thread.start()
    assert transfer_started.wait(timeout=2)

    assert peer_statuses[-1] == "Enviando arquivo para outro agent (192.168.1.50:5555)"

    transfer_may_finish.set()
    thread.join(timeout=5)

    assert peer_statuses[-1] is None


def test_non_transfer_command_never_touches_peer_status():
    client = MagicMock()
    client.long_poll.return_value = AgentCommand(command_id="cmd-1", type="connectivity_check", payload={"ip": "127.0.0.1", "port": 1, "timeout_seconds": 0.1})
    peer_statuses = []
    poller = AgentPoller(_paired_config(), client=client, on_peer_status=peer_statuses.append)

    poller._run_one_cycle()

    assert peer_statuses == []


def test_cycle_dispatches_known_command_and_reports_success():
    client = MagicMock()
    client.long_poll.return_value = AgentCommand(command_id="cmd-1", type="connectivity_check", payload={"ip": "127.0.0.1", "port": 1, "timeout_seconds": 0.1})
    poller = AgentPoller(_paired_config(), client=client)

    poller._run_one_cycle()

    client.send_command_result.assert_called_once()
    args, kwargs = client.send_command_result.call_args
    assert args[:3] == ("agent-1", "tok", "cmd-1")
    assert kwargs["ok"] is True
    assert "reachable" in kwargs["result"]


def test_cycle_reports_error_for_unknown_command():
    client = MagicMock()
    client.long_poll.return_value = AgentCommand(command_id="cmd-2", type="algo_inexistente", payload={})
    poller = AgentPoller(_paired_config(), client=client)

    poller._run_one_cycle()

    args, kwargs = client.send_command_result.call_args
    assert args[:3] == ("agent-1", "tok", "cmd-2")
    assert kwargs["ok"] is False
    assert "algo_inexistente" in kwargs["error"]


def test_cycle_reports_error_when_handler_raises():
    client = MagicMock()
    client.long_poll.return_value = AgentCommand(command_id="cmd-3", type="list_folder", payload={"path": "/caminho/que/nao/existe/de/verdade"})
    poller = AgentPoller(_paired_config(), client=client)

    poller._run_one_cycle()

    args, kwargs = client.send_command_result.call_args
    assert kwargs["ok"] is False
    assert kwargs["error"]


def test_run_forever_stops_when_event_is_set():
    client = MagicMock()
    client.long_poll.return_value = None
    stop_event = threading.Event()
    poller = AgentPoller(_paired_config(), client=client, stop_event=stop_event)

    def stop_soon():
        stop_event.set()

    # simula o stop chegando entre um ciclo e outro
    call_count = {"n": 0}

    def long_poll_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] >= 3:
            stop_event.set()
        return None

    client.long_poll.side_effect = long_poll_side_effect

    poller.run_forever()

    assert call_count["n"] >= 3


def test_run_transfer_runs_in_background_without_blocking_the_loop():
    """Um run_transfer lento não pode travar o poller -- issue #107
    (transferência real pode demorar minutos, o agent precisa continuar
    respondendo a outros comandos, ex: connectivity_check, list_folder)."""
    handler_started = threading.Event()
    handler_may_finish = threading.Event()

    def slow_handler(payload, on_progress=None):
        handler_started.set()
        handler_may_finish.wait(timeout=5)
        return {"done": True}

    client = MagicMock()
    commands = iter([
        AgentCommand(command_id="cmd-slow", type="run_transfer", payload={}),
        None,  # o ciclo seguinte já deveria rodar sem esperar o handler acima
    ])
    client.long_poll.side_effect = lambda *a, **k: next(commands, None)

    poller = AgentPoller(_paired_config(), client=client, handlers={"run_transfer": slow_handler})

    poller._run_one_cycle()  # despacha o run_transfer em background
    assert handler_started.wait(timeout=2), "handler deveria ter começado"

    start = time.monotonic()
    poller._run_one_cycle()  # não deve esperar o handler lento acima
    elapsed = time.monotonic() - start
    assert elapsed < 1, f"_run_one_cycle não deveria bloquear esperando o run_transfer em background (levou {elapsed}s)"

    handler_may_finish.set()
    time.sleep(0.2)
    ok_calls = [c for c in client.send_command_result.call_args_list if c.args[2] == "cmd-slow"]
    assert len(ok_calls) == 1 and ok_calls[0].kwargs["ok"] is True


@pytest.mark.parametrize(
    "command,expected_fragment",
    [
        (
            AgentCommand(command_id="c1", type="run_transfer", payload={"mode": "upload_to_cloud", "source_path": "C:\\dados\\a.txt"}),
            'enviando "C:\\dados\\a.txt" para a nuvem',
        ),
        (
            AgentCommand(command_id="c2", type="run_transfer", payload={"mode": "download_from_cloud", "dest_path": "C:\\dados\\b.txt"}),
            'baixando da nuvem para "C:\\dados\\b.txt"',
        ),
        (
            AgentCommand(command_id="c3", type="run_transfer", payload={"mode": "push_to_agent", "source_path": "C:\\dados\\c.txt"}),
            'enviando "C:\\dados\\c.txt" para outro agent',
        ),
        (
            AgentCommand(command_id="c4", type="run_transfer", payload={"mode": "receive_from_agent", "dest_root": "C:\\dados"}),
            'recebendo de outro agent em "C:\\dados"',
        ),
        (
            AgentCommand(command_id="c5", type="run_transfer", payload={}),
            "run_transfer",
        ),
        (
            AgentCommand(command_id="c6", type="list_folder", payload={"path": "C:\\dados"}),
            'list_folder ("C:\\dados")',
        ),
        (
            AgentCommand(command_id="c7", type="create_folder", payload={"path": "C:\\dados\\nova"}),
            'create_folder ("C:\\dados\\nova")',
        ),
        (
            AgentCommand(command_id="c8", type="connectivity_check", payload={"ip": "10.0.0.5", "port": 5555}),
            "connectivity_check (10.0.0.5:5555)",
        ),
    ],
)
def test_describe_command_shows_path_not_id(command, expected_fragment):
    poller = AgentPoller(_paired_config(), client=MagicMock())
    description = poller._describe_command(command)

    assert expected_fragment in description
    assert command.command_id not in description


def test_activity_messages_never_expose_the_raw_command_id():
    client = MagicMock()
    command = AgentCommand(command_id="56bebf98-13d7-427a-9bc0-7644b1597cd5", type="run_transfer", payload={"mode": "push_to_agent", "source_path": "C:\\dados\\x.txt"})
    client.long_poll.return_value = command

    messages = []
    poller = AgentPoller(_paired_config(), client=client, on_activity=messages.append, handlers={"run_transfer": lambda payload, on_progress=None: {"ok": True}})

    poller._run_one_cycle()

    assert all(command.command_id not in m for m in messages)
    assert any("C:\\dados\\x.txt" in m for m in messages)


def test_run_transfer_handler_receives_progress_callback(monkeypatch):
    """O poller deve envolver o handler run_transfer com um on_progress --
    issue #115."""
    monkeypatch.setattr("migration_agent.poller.PROGRESS_REPORT_MIN_INTERVAL_SECONDS", 0)
    client = MagicMock()
    command = AgentCommand(command_id="c1", type="run_transfer", payload={"mode": "push_to_agent", "job_id": "job-42"})
    client.long_poll.return_value = command

    received_callback = {}

    def handler(payload, on_progress=None):
        received_callback["callback"] = on_progress
        on_progress(50, 100)
        return {"ok": True}

    poller = AgentPoller(_paired_config(), client=client, handlers={"run_transfer": handler})
    poller._run_one_cycle()

    assert received_callback["callback"] is not None
    client.report_job.assert_called_once_with("agent-1", "tok", "job-42", "progress", {"bytes_done": 50, "bytes_total": 100})


def test_progress_reporter_throttles_intermediate_reports_but_always_sends_final():
    client = MagicMock()
    command = AgentCommand(command_id="c1", type="run_transfer", payload={"mode": "push_to_agent", "job_id": "job-42"})
    poller = AgentPoller(_paired_config(), client=client)

    reporter = poller._make_progress_reporter(command)
    reporter(10, 100)  # primeira chamada -- ainda dentro do intervalo de throttle
    reporter(20, 100)  # deveria ser suprimida (throttle)
    reporter(100, 100)  # final -- sempre reporta, mesmo dentro do intervalo

    calls = [c.args[4] for c in client.report_job.call_args_list]
    assert {"bytes_done": 10, "bytes_total": 100} in calls
    assert {"bytes_done": 20, "bytes_total": 100} not in calls
    assert {"bytes_done": 100, "bytes_total": 100} in calls


def test_progress_reporter_is_noop_without_job_id():
    client = MagicMock()
    command = AgentCommand(command_id="c1", type="run_transfer", payload={"mode": "push_to_agent"})  # sem job_id
    poller = AgentPoller(_paired_config(), client=client)

    reporter = poller._make_progress_reporter(command)
    reporter(50, 100)

    client.report_job.assert_not_called()


def test_progress_reporter_swallows_report_failures():
    client = MagicMock()
    client.report_job.side_effect = Exception("Guardian inacessível")
    command = AgentCommand(command_id="c1", type="run_transfer", payload={"mode": "push_to_agent", "job_id": "job-42"})
    poller = AgentPoller(_paired_config(), client=client)

    reporter = poller._make_progress_reporter(command)
    reporter(100, 100)  # nao deve levantar, so logar em debug
