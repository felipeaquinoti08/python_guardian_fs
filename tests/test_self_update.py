from unittest.mock import MagicMock

import pytest

from migration_agent import self_update
from migration_agent._version import AGENT_VERSION
from migration_agent.config import AgentConfig
from migration_agent.self_update import apply_update, check_for_update, is_newer_version


@pytest.mark.parametrize(
    "candidate,current,expected",
    [
        ("0.1.34.0", "0.1.33.0", True),
        ("0.1.33.0", "0.1.33.0", False),
        ("0.1.32.0", "0.1.33.0", False),
        ("0.2.0.0", "0.1.99.0", True),
        ("0.1.34.0", "0.0.0-dev", True),
        (AGENT_VERSION, AGENT_VERSION, False),
    ],
)
def test_is_newer_version(candidate, current, expected):
    assert is_newer_version(candidate, current) is expected


def test_check_for_update_returns_none_when_not_paired():
    client = MagicMock()
    cfg = AgentConfig()  # sem agent_id/auth_token -- is_paired False

    assert check_for_update(client, cfg) is None
    client.get_latest_installer_version.assert_not_called()


def test_check_for_update_returns_none_when_already_up_to_date(monkeypatch):
    monkeypatch.setattr(self_update, "AGENT_VERSION", "0.1.34.0")
    client = MagicMock()
    client.get_latest_installer_version.return_value = "0.1.34.0"
    cfg = AgentConfig(agent_id="agent-1", auth_token="tok")

    assert check_for_update(client, cfg) is None


def test_check_for_update_returns_version_when_newer_available(monkeypatch):
    monkeypatch.setattr(self_update, "AGENT_VERSION", "0.1.33.0")
    client = MagicMock()
    client.get_latest_installer_version.return_value = "0.1.34.0"
    cfg = AgentConfig(agent_id="agent-1", auth_token="tok")

    assert check_for_update(client, cfg) == "0.1.34.0"


def test_check_for_update_returns_none_when_nothing_published():
    client = MagicMock()
    client.get_latest_installer_version.return_value = None
    cfg = AgentConfig(agent_id="agent-1", auth_token="tok")

    assert check_for_update(client, cfg) is None


def test_apply_update_raises_outside_windows(monkeypatch):
    monkeypatch.setattr(self_update.platform, "system", lambda: "Linux")
    client = MagicMock()
    cfg = AgentConfig(agent_id="agent-1", auth_token="tok")

    with pytest.raises(RuntimeError):
        apply_update(client, cfg)


def test_apply_update_downloads_and_launches_detached_msiexec(monkeypatch, tmp_path):
    monkeypatch.setattr(self_update.platform, "system", lambda: "Windows")
    monkeypatch.setattr(self_update.tempfile, "gettempdir", lambda: str(tmp_path))

    fake_popen = MagicMock()
    monkeypatch.setattr(self_update.subprocess, "Popen", fake_popen)

    client = MagicMock()
    cfg = AgentConfig(agent_id="agent-1", auth_token="tok")

    apply_update(client, cfg)

    client.download_installer.assert_called_once()
    args, _ = client.download_installer.call_args
    assert args[0] == "agent-1"
    assert args[1] == "tok"
    dest_path = args[2]
    assert dest_path.name == "GuardianMigrationAgentUpdate.msi"

    fake_popen.assert_called_once()
    popen_args, popen_kwargs = fake_popen.call_args
    cmd = popen_args[0]
    assert cmd[0] == "msiexec"
    assert "/i" in cmd and str(dest_path) in cmd
    assert "/qn" in cmd
    assert popen_kwargs["creationflags"] == self_update._DETACHED_PROCESS | self_update._CREATE_NEW_PROCESS_GROUP
