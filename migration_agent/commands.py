"""Handlers dos comandos recebidos do Guardian via long-poll.

`run_transfer` (issue #107, Incremento 2) despacha por `payload["mode"]`
pros 4 cenários reais de transferência -- ver `transfer.py` pra a
implementação de cada um. O contrato de retorno é sempre um dict
serializável, reportado de volta ao Guardian via
`GuardianClient.send_command_result`.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from pathlib import Path, PureWindowsPath
from typing import Callable, Optional

from .peer_listener import PendingTokens
from .transfer import (
    ProgressCallback,
    TransferError,
    _noop_progress,
    download_from_cloud,
    push_file_to_peer,
    upload_to_cloud,
)

logger = logging.getLogger(__name__)

CommandHandler = Callable[[dict], dict]


class UnknownCommandError(Exception):
    pass


def handle_list_folder(payload: dict) -> dict:
    """Lista o conteúdo direto (não-recursivo) de um path local ou UNC.

    payload: {"path": str, "username": str|None, "password": str|None}
    """
    path = payload["path"]
    username = payload.get("username")
    password = payload.get("password")

    unmount = None
    if username and _looks_like_unc(path):
        unmount = _connect_unc_with_credentials(path, username, password)

    try:
        entries = []
        with os.scandir(path) as it:
            for entry in it:
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                entries.append(
                    {
                        "name": entry.name,
                        "is_dir": entry.is_dir(follow_symlinks=False),
                        "size": stat.st_size,
                        "modified_at": stat.st_mtime,
                    }
                )
        return {"path": path, "entries": entries}
    finally:
        if unmount:
            unmount()


def _looks_like_unc(path: str) -> bool:
    return path.startswith("\\\\") or path.startswith("//")


def _connect_unc_with_credentials(path: str, username: str, password: Optional[str]) -> Optional[Callable[[], None]]:
    """Mapeia temporariamente o share UNC usando credenciais específicas (Windows).

    Fora do Windows isso é inaplicável (sem SMB nativo) -- devolve None e o
    caller segue com o path como está (uso em dev/teste apenas).
    """
    try:
        import win32wnet  # type: ignore
        import win32netcon  # type: ignore
    except ImportError:
        logger.warning("win32wnet indisponível (não-Windows?) — ignorando credencial de share")
        return None

    unc_root = str(PureWindowsPath(path).drive or "") or _unc_root(path)
    win32wnet.WNetAddConnection2(win32netcon.RESOURCETYPE_DISK, None, unc_root, None, username, password)

    def _disconnect():
        try:
            win32wnet.WNetCancelConnection2(unc_root, 0, True)
        except Exception:  # pragma: no cover - best-effort cleanup
            logger.exception("Falha ao desconectar %s", unc_root)

    return _disconnect


def _unc_root(path: str) -> str:
    parts = path.replace("/", "\\").split("\\")
    # ['', '', 'server', 'share', ...] -> '\\server\share'
    non_empty = [p for p in parts if p]
    return "\\\\" + "\\".join(non_empty[:2])


def handle_create_folder(payload: dict) -> dict:
    """Cria uma pasta (com intermediárias, se preciso) num path local ou UNC.

    payload: {"path": str, "username": str|None, "password": str|None}
    """
    path = payload["path"]
    username = payload.get("username")
    password = payload.get("password")

    unmount = None
    if username and _looks_like_unc(path):
        unmount = _connect_unc_with_credentials(path, username, password)

    try:
        os.makedirs(path, exist_ok=True)
        return {"path": path}
    finally:
        if unmount:
            unmount()


def handle_connectivity_check(payload: dict) -> dict:
    """Testa alcance TCP direto até outro agent (cenário agent<->agent).

    payload: {"ip": str, "port": int, "timeout_seconds": float|None}
    """
    ip = payload["ip"]
    port = int(payload["port"])
    timeout = float(payload.get("timeout_seconds") or 5.0)

    start = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            latency_ms = (time.monotonic() - start) * 1000
            return {"reachable": True, "latency_ms": round(latency_ms, 1)}
    except OSError as exc:
        return {"reachable": False, "error": str(exc)}


def _with_unc_credentials(path: str, username: Optional[str], password: Optional[str]):
    """Context manager simples: mapeia o share (se UNC + credencial) e desmapeia ao sair."""

    class _Ctx:
        def __enter__(self):
            self.unmount = None
            if username and _looks_like_unc(path):
                self.unmount = _connect_unc_with_credentials(path, username, password)
            return self

        def __exit__(self, *exc):
            if self.unmount:
                self.unmount()
            return False

    return _Ctx()


def _run_upload_to_cloud(payload: dict, on_progress: Optional[ProgressCallback] = None) -> dict:
    """Agent é ORIGEM, nuvem é destino. Sobe direto pro provedor cloud
    usando a credencial de curta duração que o Guardian gerou -- os bytes
    nunca passam pelo Guardian.

    payload: {"source_path", "username", "password", "upload_credential",
              "max_bandwidth_mbps", "delete_source", "job_id"}
    """
    source_path = payload["source_path"]
    with _with_unc_credentials(source_path, payload.get("username"), payload.get("password")):
        result = upload_to_cloud(
            Path(source_path),
            payload["upload_credential"],
            max_bandwidth_mbps=payload.get("max_bandwidth_mbps"),
            on_progress=on_progress or _noop_progress,
        )
        if payload.get("delete_source"):
            os.remove(source_path)
        return result


def _run_download_from_cloud(payload: dict, on_progress: Optional[ProgressCallback] = None) -> dict:
    """Nuvem é origem, agent é DESTINO. Baixa direto do provedor cloud
    usando o link temporário que o Guardian gerou.

    payload: {"download_url", "dest_path", "username", "password",
              "expected_size", "max_bandwidth_mbps", "job_id"}
    """
    dest_path = payload["dest_path"]
    with _with_unc_credentials(dest_path, payload.get("username"), payload.get("password")):
        parent = os.path.dirname(dest_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return download_from_cloud(
            payload["download_url"],
            Path(dest_path),
            max_bandwidth_mbps=payload.get("max_bandwidth_mbps"),
            expected_size=payload.get("expected_size"),
            on_progress=on_progress or _noop_progress,
        )


def _run_push_to_agent(payload: dict, on_progress: Optional[ProgressCallback] = None) -> dict:
    """Agent é ORIGEM, outro agent é DESTINO -- conexão direta (protocolo
    de peer_listener.py, issue #105), Guardian nunca vê os bytes.

    payload: {"source_path", "username", "password", "dest_ip", "dest_port",
              "job_id", "token", "relative_path", "max_bandwidth_mbps",
              "delete_source"}
    """
    source_path = payload["source_path"]
    with _with_unc_credentials(source_path, payload.get("username"), payload.get("password")):
        result = push_file_to_peer(
            source_path,
            payload["dest_ip"],
            int(payload["dest_port"]),
            payload["job_id"],
            payload["token"],
            payload["relative_path"],
            max_bandwidth_mbps=payload.get("max_bandwidth_mbps"),
            on_progress=on_progress or _noop_progress,
        )
        if payload.get("delete_source"):
            os.remove(source_path)
        return result


def _run_receive_from_agent(
    payload: dict, pending_tokens: Optional[PendingTokens], on_progress: Optional[ProgressCallback] = None
) -> dict:
    """Agent é DESTINO de uma transferência agent<->agent -- só registra a
    expectativa no listener (`peer_listener.py`) já rodando em background;
    o recebimento de fato acontece de forma assíncrona quando a origem
    conectar. Retorna na hora, não espera o arquivo chegar -- por isso
    `on_progress` (issue #115) é guardado junto da expectativa
    (`PendingTokens.expect`), não chamado aqui: quem sabe o progresso de
    verdade é `peer_listener.py::_receive_file`, quando a conexão chegar.

    payload: {"job_id", "token", "dest_root"}
    """
    if pending_tokens is None:
        raise RuntimeError("Este agent não tem o listener de peer inicializado (pending_tokens ausente).")

    pending_tokens.expect(payload["job_id"], payload["token"], Path(payload["dest_root"]), on_progress=on_progress)
    return {"expecting": True}


def handle_run_transfer(
    payload: dict, pending_tokens: Optional[PendingTokens] = None, on_progress: Optional[ProgressCallback] = None
) -> dict:
    mode = payload.get("mode")
    try:
        if mode == "upload_to_cloud":
            return _run_upload_to_cloud(payload, on_progress)
        if mode == "download_from_cloud":
            return _run_download_from_cloud(payload, on_progress)
        if mode == "push_to_agent":
            return _run_push_to_agent(payload, on_progress)
        if mode == "receive_from_agent":
            return _run_receive_from_agent(payload, pending_tokens, on_progress)
    except TransferError as exc:
        raise RuntimeError(str(exc)) from exc

    raise ValueError(f"Modo de run_transfer desconhecido: {mode!r}")


def make_run_transfer_handler(pending_tokens: Optional[PendingTokens]):
    """Factory -- liga o handler ao `PendingTokens` de verdade do processo
    (só existe em tempo de execução, via `AgentRuntime`). Ver `agent_runtime.py`.

    Assinatura de 2 parâmetros (`payload`, `on_progress` opcional) de
    propósito -- não é um `CommandHandler` comum (issue #115): o poller
    reconhece esse caso especial só pra `run_transfer` e monta um
    `on_progress` com throttle próprio antes de despachar (ver
    poller.py::_execute_and_report).
    """

    def _handler(payload: dict, on_progress: Optional[ProgressCallback] = None) -> dict:
        return handle_run_transfer(payload, pending_tokens, on_progress)

    return _handler


DEFAULT_HANDLERS: dict[str, CommandHandler] = {
    "list_folder": handle_list_folder,
    "create_folder": handle_create_folder,
    "connectivity_check": handle_connectivity_check,
    "run_transfer": handle_run_transfer,
}


def dispatch(command_type: str, payload: dict, handlers: Optional[dict[str, CommandHandler]] = None) -> dict:
    registry = handlers if handlers is not None else DEFAULT_HANDLERS
    handler = registry.get(command_type)
    if handler is None:
        raise UnknownCommandError(f"Comando desconhecido: {command_type!r}")
    return handler(payload)
