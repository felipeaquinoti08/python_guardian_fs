"""Config local do agent: onde fica salvo, e o que é persistido em disco.

O único segredo de longa duração salvo em disco é o `auth_token` (obtido na
troca do token de pareamento -- ver `pairing.py`), sempre cifrado via
`crypto_store`. O resto do arquivo é texto puro (não é sensível: URL do
Guardian, id do agent, portas locais escolhidas).
"""

from __future__ import annotations

import json
import os
import platform
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .crypto_store import get_secret_store


def default_state_dir() -> Path:
    if platform.system() == "Windows":
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return Path(base) / "GuardianMigrationAgent"
    return Path.home() / ".guardian-migration-agent"


def _pick_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class AgentConfig:
    guardian_base_url: str = ""
    agent_id: Optional[str] = None
    cliente_nome: Optional[str] = None
    auth_token: Optional[str] = None  # nunca fica em texto puro no disco
    local_ui_port: int = field(default_factory=_pick_free_port)
    peer_listener_port: int = field(default_factory=_pick_free_port)
    peer_listener_ip: Optional[str] = None

    @property
    def is_paired(self) -> bool:
        return bool(self.agent_id and self.auth_token)


class ConfigStore:
    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = state_dir or default_state_dir()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.state_dir / "config.json"
        self._secret_store = get_secret_store(self.state_dir)

    def load(self) -> AgentConfig:
        if not self.config_path.exists():
            cfg = AgentConfig()
            self.save(cfg)
            return cfg

        raw = json.loads(self.config_path.read_text("utf-8"))
        encrypted_token = raw.pop("auth_token_encrypted", None)
        cfg = AgentConfig(**raw)
        if encrypted_token:
            cfg.auth_token = self._secret_store.decrypt(bytes.fromhex(encrypted_token)).decode("utf-8")
        return cfg

    def save(self, cfg: AgentConfig) -> None:
        data = asdict(cfg)
        auth_token = data.pop("auth_token", None)
        if auth_token:
            data["auth_token_encrypted"] = self._secret_store.encrypt(auth_token.encode("utf-8")).hex()
        tmp_path = self.config_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, indent=2), "utf-8")
        tmp_path.replace(self.config_path)

    @staticmethod
    def new_local_ui_secret() -> str:
        """Token curto pra proteger a UI local (ver status_server.py)."""
        return secrets.token_urlsafe(24)
