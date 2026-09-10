import threading
import time
from unittest.mock import MagicMock

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

    def slow_handler(payload):
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
