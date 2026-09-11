"""Config local do agent: onde fica salvo, e o que é persistido em disco.

O único segredo de longa duração salvo em disco é o `auth_token` (obtido na
troca do token de pareamento -- ver `pairing.py`), sempre cifrado via
`crypto_store`. O resto do arquivo é texto puro (não é sensível: URL do
Guardian, id do agent, portas locais escolhidas).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import secrets
import string
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .crypto_store import get_secret_store

logger = logging.getLogger(__name__)

_PBKDF2_ITERATIONS = 260_000
# Senha inicial da UI local (issue #109): usuario pediu simplicidade nessa
# primeira fase (credenciais fixas e conhecidas, "admin"/"admin"), em vez de
# uma senha aleatoria dificil de descobrir logo na primeira instalacao --
# ainda fica marcada como "padrao" e o dashboard/login mostram um aviso ate
# ser trocada em /change-password. Recuperacao de senha esquecida (CLI
# `reset-ui-password`) continua gerando uma senha aleatoria de verdade (ver
# generate_default_password), so o bootstrap inicial que ficou fixo.
_BOOTSTRAP_PASSWORD = "admin"
_DEFAULT_PASSWORD_ALPHABET = "".join(
    c for c in (string.ascii_uppercase + string.ascii_lowercase + string.digits) if c not in "0O1lI"
)


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


def generate_default_password(length: int = 12) -> str:
    """Senha inicial da UI local (issue #107): alfabeto sem caracteres
    ambíguos (0/O, 1/l/I) pra facilitar digitar num teclado real."""
    return "".join(secrets.choice(_DEFAULT_PASSWORD_ALPHABET) for _ in range(length))


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    try:
        algorithm, iterations_str, salt_hex, digest_hex = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations_str))
        return secrets.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


@dataclass
class AgentConfig:
    guardian_base_url: str = ""
    agent_id: Optional[str] = None
    cliente_nome: Optional[str] = None
    auth_token: Optional[str] = None  # nunca fica em texto puro no disco
    local_ui_port: int = field(default_factory=_pick_free_port)
    peer_listener_port: int = field(default_factory=_pick_free_port)
    peer_listener_ip: Optional[str] = None
    ui_username: str = "admin"
    ui_password_hash: str = ""
    ui_password_is_default: bool = True
    # Só preenchido enquanto a senha ainda é a gerada automaticamente (pra
    # poder mostrar na tela de login sem precisar de um fluxo de "esqueci
    # minha senha" -- ver status_server.py). Nunca fica em texto puro no
    # disco (mesmo tratamento do auth_token).
    ui_default_password: Optional[str] = None

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
            self._bootstrap_default_password(cfg)
            self.save(cfg)
            return cfg

        raw = json.loads(self.config_path.read_text("utf-8"))
        encrypted_token = raw.pop("auth_token_encrypted", None)
        encrypted_default_password = raw.pop("ui_default_password_encrypted", None)
        cfg = AgentConfig(**raw)
        if encrypted_token:
            cfg.auth_token = self._secret_store.decrypt(bytes.fromhex(encrypted_token)).decode("utf-8")
        if encrypted_default_password:
            cfg.ui_default_password = self._secret_store.decrypt(bytes.fromhex(encrypted_default_password)).decode(
                "utf-8"
            )

        if not cfg.ui_password_hash:
            # config.json de uma instalacao anterior a issue #109 (sem
            # autenticacao na UI local ainda) -- sem isso, o login ficava
            # permanentemente quebrado apos a atualizacao (hash vazio nunca
            # bate com nenhuma senha) e o aviso de senha padrao nao aparecia
            # (ui_default_password ficava None). Backfill igual a um
            # primeiro start.
            self._bootstrap_default_password(cfg)
            self.save(cfg)

        return cfg

    def _bootstrap_default_password(self, cfg: AgentConfig) -> None:
        cfg.ui_password_hash = hash_password(_BOOTSTRAP_PASSWORD)
        cfg.ui_default_password = _BOOTSTRAP_PASSWORD
        cfg.ui_password_is_default = True
        logger.info(
            "Senha padrao da UI local definida (usuario %r). Troque em /change-password assim que possivel.",
            cfg.ui_username,
        )

    def save(self, cfg: AgentConfig) -> None:
        data = asdict(cfg)
        auth_token = data.pop("auth_token", None)
        if auth_token:
            data["auth_token_encrypted"] = self._secret_store.encrypt(auth_token.encode("utf-8")).hex()
        default_password = data.pop("ui_default_password", None)
        if default_password:
            data["ui_default_password_encrypted"] = self._secret_store.encrypt(default_password.encode("utf-8")).hex()
        tmp_path = self.config_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, indent=2), "utf-8")
        tmp_path.replace(self.config_path)

    def reset_ui_password(self) -> str:
        """Gera e persiste uma nova senha padrao pra UI local (CLI
        `reset-ui-password`, issue #107) -- util se o usuario esquecer a
        senha atual: quem roda esse comando ja tem acesso local a maquina
        (mesmo nivel de confianca de quem conseguiria ler o config.json
        direto), entao nao precisa de um fluxo de recuperacao por e-mail."""
        password = generate_default_password()
        cfg = self.load()
        cfg.ui_password_hash = hash_password(password)
        cfg.ui_default_password = password
        cfg.ui_password_is_default = True
        self.save(cfg)
        return password

    def change_ui_password(self, new_password: str) -> None:
        cfg = self.load()
        cfg.ui_password_hash = hash_password(new_password)
        cfg.ui_default_password = None
        cfg.ui_password_is_default = False
        self.save(cfg)
