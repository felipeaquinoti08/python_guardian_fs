"""Cliente HTTP para o Guardian.

Contrato de API esperado do backend (a ser implementado na issue #106 --
"Cadastro de Agents no Guardian"). Documentado aqui porque este agent é o
consumidor de referência do contrato; qualquer mudança de um lado precisa
refletir no outro.

    POST {base}/api/agents/pair
        body: {"pairing_token": str}
        200: {"agent_id": str, "auth_token": str, "cliente_nome": str}
        4xx: token inválido/expirado

    GET {base}/api/agents/{agent_id}/commands?wait=<segundos>
        header: Authorization: Bearer <auth_token>
        Long-polling: o Guardian só responde quando tiver um comando
        pendente OU quando o tempo de `wait` esgotar.
        200: {"command_id": str, "type": "list_folder"|"create_folder"|"run_transfer"|"connectivity_check", "payload": {...}}
        204: nada pendente (agent deve reabrir o poll imediatamente)

    POST {base}/api/agents/{agent_id}/commands/{command_id}/result
        header: Authorization: Bearer <auth_token>
        body: {"ok": bool, "error": str|None, "result": {...}}

    POST {base}/api/agents/{agent_id}/jobs/{job_id}/report
        header: Authorization: Bearer <auth_token>
        body: {"status": "progress"|"file_ok"|"file_error"|"done", "detail": {...}}

    GET {base}/api/agents/{agent_id}/installer/version
        header: Authorization: Bearer <auth_token>
        200: {"available": bool, "version": str|None}

    GET {base}/api/agents/{agent_id}/installer/download
        header: Authorization: Bearer <auth_token>
        200: bytes do .msi (streamed)
        404: nenhum instalador publicado ainda

    POST {base}/api/agents/{agent_id}/events
        header: Authorization: Bearer <auth_token>
        body: {"type": str, "status": "success"|"error"|"info", "message": str, "detail": {...}|None}
        histórico de 30 dias (issue #113) -- só eventos OPERACIONAIS
        (pareamento, login, conexão). Eventos de transferência de
        arquivo não passam por aqui, o Guardian já sabe o resultado de
        cada arquivo sem precisar que o agent reporte de novo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests

DEFAULT_LONG_POLL_WAIT_SECONDS = 25
HTTP_TIMEOUT_MARGIN_SECONDS = 10  # margem além do wait, pra não cortar a resposta do servidor


@dataclass
class AgentCommand:
    command_id: str
    type: str
    payload: dict


class PairingError(Exception):
    pass


class GuardianClient:
    def __init__(self, base_url: str, session: Optional[requests.Session] = None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def pair(self, pairing_token: str) -> dict:
        resp = self.session.post(
            f"{self.base_url}/api/agents/pair",
            json={"pairing_token": pairing_token},
            timeout=15,
        )
        if resp.status_code >= 400:
            raise PairingError(f"Falha ao parear (HTTP {resp.status_code}): {resp.text}")
        return resp.json()

    def _auth_headers(self, auth_token: str) -> dict:
        return {"Authorization": f"Bearer {auth_token}"}

    def long_poll(
        self, agent_id: str, auth_token: str, wait_seconds: int = DEFAULT_LONG_POLL_WAIT_SECONDS
    ) -> Optional[AgentCommand]:
        resp = self.session.get(
            f"{self.base_url}/api/agents/{agent_id}/commands",
            params={"wait": wait_seconds},
            headers=self._auth_headers(auth_token),
            timeout=wait_seconds + HTTP_TIMEOUT_MARGIN_SECONDS,
        )
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        data = resp.json()
        return AgentCommand(command_id=data["command_id"], type=data["type"], payload=data.get("payload") or {})

    def send_command_result(
        self, agent_id: str, auth_token: str, command_id: str, ok: bool, result: Any = None, error: Optional[str] = None
    ) -> None:
        resp = self.session.post(
            f"{self.base_url}/api/agents/{agent_id}/commands/{command_id}/result",
            json={"ok": ok, "error": error, "result": result},
            headers=self._auth_headers(auth_token),
            timeout=30,
        )
        resp.raise_for_status()

    def report_job(self, agent_id: str, auth_token: str, job_id: str, status: str, detail: Optional[dict] = None) -> None:
        resp = self.session.post(
            f"{self.base_url}/api/agents/{agent_id}/jobs/{job_id}/report",
            json={"status": status, "detail": detail or {}},
            headers=self._auth_headers(auth_token),
            timeout=30,
        )
        resp.raise_for_status()

    def get_latest_installer_version(self, agent_id: str, auth_token: str) -> Optional[str]:
        resp = self.session.get(
            f"{self.base_url}/api/agents/{agent_id}/installer/version",
            headers=self._auth_headers(auth_token),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("version") if data.get("available") else None

    def download_installer(self, agent_id: str, auth_token: str, dest_path: Path) -> None:
        resp = self.session.get(
            f"{self.base_url}/api/agents/{agent_id}/installer/download",
            headers=self._auth_headers(auth_token),
            timeout=120,
            stream=True,
        )
        resp.raise_for_status()
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if chunk:
                    f.write(chunk)
        tmp_path.replace(dest_path)

    def report_event(
        self, agent_id: str, auth_token: str, event_type: str, status: str, message: str, detail: Optional[dict] = None
    ) -> None:
        resp = self.session.post(
            f"{self.base_url}/api/agents/{agent_id}/events",
            json={"type": event_type, "status": status, "message": message, "detail": detail},
            headers=self._auth_headers(auth_token),
            timeout=15,
        )
        resp.raise_for_status()
