from unittest.mock import MagicMock

import pytest

from migration_agent.api_client import AgentCommand, GuardianClient, PairingError


def _fake_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


def test_pair_success_returns_credentials():
    session = MagicMock()
    session.post.return_value = _fake_response(
        200, {"agent_id": "agent-1", "auth_token": "tok", "cliente_nome": "Cliente X"}
    )
    client = GuardianClient("https://guardian.exemplo.com", session=session)

    result = client.pair("pairing-token-abc")

    assert result == {"agent_id": "agent-1", "auth_token": "tok", "cliente_nome": "Cliente X"}
    session.post.assert_called_once()
    args, kwargs = session.post.call_args
    assert args[0] == "https://guardian.exemplo.com/api/agents/pair"
    assert kwargs["json"] == {"pairing_token": "pairing-token-abc"}


def test_pair_failure_raises_pairing_error():
    session = MagicMock()
    session.post.return_value = _fake_response(400, text="token invalido")
    client = GuardianClient("https://guardian.exemplo.com", session=session)

    with pytest.raises(PairingError):
        client.pair("token-invalido")


def test_long_poll_returns_none_on_204():
    session = MagicMock()
    session.get.return_value = _fake_response(204)
    client = GuardianClient("https://guardian.exemplo.com", session=session)

    assert client.long_poll("agent-1", "tok") is None


def test_long_poll_parses_command():
    session = MagicMock()
    session.get.return_value = _fake_response(
        200, {"command_id": "cmd-1", "type": "list_folder", "payload": {"path": "C:\\dados"}}
    )
    client = GuardianClient("https://guardian.exemplo.com", session=session)

    command = client.long_poll("agent-1", "tok")

    assert command == AgentCommand(command_id="cmd-1", type="list_folder", payload={"path": "C:\\dados"})
    _, kwargs = session.get.call_args
    assert kwargs["headers"] == {"Authorization": "Bearer tok"}


def test_send_command_result_posts_expected_payload():
    session = MagicMock()
    session.post.return_value = _fake_response(200)
    client = GuardianClient("https://guardian.exemplo.com", session=session)

    client.send_command_result("agent-1", "tok", "cmd-1", ok=True, result={"entries": []})

    args, kwargs = session.post.call_args
    assert args[0] == "https://guardian.exemplo.com/api/agents/agent-1/commands/cmd-1/result"
    assert kwargs["json"] == {"ok": True, "error": None, "result": {"entries": []}}


def test_get_latest_installer_version_returns_version_when_available():
    session = MagicMock()
    session.get.return_value = _fake_response(200, {"available": True, "version": "0.1.34.0"})
    client = GuardianClient("https://guardian.exemplo.com", session=session)

    version = client.get_latest_installer_version("agent-1", "tok")

    assert version == "0.1.34.0"
    args, kwargs = session.get.call_args
    assert args[0] == "https://guardian.exemplo.com/api/agents/agent-1/installer/version"
    assert kwargs["headers"] == {"Authorization": "Bearer tok"}


def test_get_latest_installer_version_returns_none_when_unavailable():
    session = MagicMock()
    session.get.return_value = _fake_response(200, {"available": False, "version": None})
    client = GuardianClient("https://guardian.exemplo.com", session=session)

    assert client.get_latest_installer_version("agent-1", "tok") is None


def test_download_installer_writes_streamed_chunks_to_dest_path(tmp_path):
    session = MagicMock()
    resp = _fake_response(200)
    resp.iter_content.return_value = [b"MSI-parte-1-", b"MSI-parte-2"]
    session.get.return_value = resp
    client = GuardianClient("https://guardian.exemplo.com", session=session)

    dest = tmp_path / "GuardianMigrationAgent.msi"
    client.download_installer("agent-1", "tok", dest)

    assert dest.read_bytes() == b"MSI-parte-1-MSI-parte-2"
    assert not dest.with_suffix(dest.suffix + ".part").exists()
    args, kwargs = session.get.call_args
    assert args[0] == "https://guardian.exemplo.com/api/agents/agent-1/installer/download"
    assert kwargs["stream"] is True
