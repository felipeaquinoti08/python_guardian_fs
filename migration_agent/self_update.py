"""Auto-atualização do agent (issue #111).

Fluxo: checa a versão mais recente publicada no Guardian (mesmo canal
autenticado que o agent já usa pra pareamento/long-poll -- decisão do
usuário, em vez de depender do GitHub estar acessível a partir do
fileserver on-premises do cliente) e, se houver uma mais nova, baixa o
`.msi` e dispara o upgrade via `msiexec`.

`apply_update()` lança o `msiexec` como processo DESTACADO (flags
DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP) e retorna imediatamente, sem
esperar o upgrade terminar -- essencial, porque o próprio processo/serviço
do agent vai ser parado pelo `msiexec` como parte do upgrade (o `.msi`
troca os arquivos em disco, o que exige o processo atual não estar mais
com eles abertos). No Windows, um processo filho sobrevive normalmente ao
processo pai (mesmo um Windows Service) sendo encerrado -- não há Job
Object atrelado ao serviço que mataria o `msiexec` junto.
"""

from __future__ import annotations

import logging
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from ._version import AGENT_VERSION
from .api_client import GuardianClient
from .config import AgentConfig

logger = logging.getLogger(__name__)

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def _parse_version(version: str) -> Tuple[int, ...]:
    """Extrai os componentes numéricos de uma string de versão (ex:
    "0.1.34.0" -> (0, 1, 34, 0)). Segmentos não-numéricos (ex: "-dev")
    viram 0, então a versão de desenvolvimento nunca parece "mais nova"
    que uma versão de build real."""
    parts = []
    for segment in version.split("."):
        digits = "".join(c for c in segment if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer_version(candidate: str, current: str) -> bool:
    return _parse_version(candidate) > _parse_version(current)


def check_for_update(client: GuardianClient, cfg: AgentConfig) -> Optional[str]:
    """Retorna a versão mais nova disponível no Guardian, ou None se o
    agent já está atualizado (ou nenhum instalador foi publicado ainda)."""
    if not cfg.is_paired:
        return None

    latest = client.get_latest_installer_version(cfg.agent_id, cfg.auth_token)
    if latest and is_newer_version(latest, AGENT_VERSION):
        return latest
    return None


def apply_update(client: GuardianClient, cfg: AgentConfig) -> None:
    """Baixa o instalador mais recente e dispara o upgrade via msiexec como
    processo destacado. Não espera o upgrade terminar -- ver docstring do
    módulo."""
    if platform.system() != "Windows":
        raise RuntimeError("Self-update só é suportado no Windows (ambiente real do agent).")

    dest = Path(tempfile.gettempdir()) / "GuardianMigrationAgentUpdate.msi"
    client.download_installer(cfg.agent_id, cfg.auth_token, dest)

    log_path = Path(tempfile.gettempdir()) / "GuardianMigrationAgentUpdate.log"
    cmd = ["msiexec", "/i", str(dest), "/qn", "/norestart", "/l*v", str(log_path)]

    subprocess.Popen(
        cmd,
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    logger.info("Self-update: msiexec disparado (processo destacado) para %s", dest)
