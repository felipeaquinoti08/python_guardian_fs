"""Listener TCP local para receber transferência direta de outro agent
(cenário agent<->agent, ver implementation_plan.md).

Protocolo (um arquivo por conexão, simples de propósito -- otimizações como
múltiplos arquivos por conexão/paralelismo ficam pra issue #107 quando o
volume real de tráfego for conhecido):

    1. Client conecta e manda o handshake: 4 bytes big-endian com o tamanho
       do JSON, seguido do JSON `{"job_id": str, "token": str,
       "relative_path": str, "size": int}`.
    2. Server valida `token` contra o que foi registrado via `expect()`
       (populado pelo handler de `run_transfer` quando este agent é o
       destino de um job -- o token de uso único vem do Guardian no comando
       recebido via long-poll). Inválido -> manda b"NO" e fecha a conexão.
    3. Válido -> manda b"OK", lê exatamente `size` bytes do socket, grava em
       `dest_root/relative_path` (cria diretórios intermediários, escreve
       em arquivo temporário e faz rename atômico ao final), calculando
       sha256 dos bytes recebidos.
    4. Server responde `{"ok": true, "sha256": "<hex>"}` (ou `{"ok": false,
       "error": "..."}` se algo falhar no meio da gravação).

Quem chama `run_transfer` no lado de origem usa esse mesmo protocolo como
client (a implementar na issue #107).
"""

from __future__ import annotations

import hashlib
import json
import logging
import socketserver
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

_HEADER_STRUCT = struct.Struct(">I")


@dataclass
class ExpectedTransfer:
    dest_root: Path


class PendingTokens:
    """Registro thread-safe de (job_id, token) -> destino esperado."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: Dict[str, ExpectedTransfer] = {}

    def expect(self, job_id: str, token: str, dest_root: Path) -> None:
        with self._lock:
            self._pending[f"{job_id}:{token}"] = ExpectedTransfer(dest_root=dest_root)

    def consume(self, job_id: str, token: str) -> Optional[ExpectedTransfer]:
        key = f"{job_id}:{token}"
        with self._lock:
            return self._pending.pop(key, None)


def _recv_exact(sock, n: int) -> bytes:
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(min(65536, remaining))
        if not chunk:
            raise ConnectionError("Conexão encerrada antes do esperado")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _make_handler(pending: PendingTokens, on_status: Callable[[Optional[str]], None]):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            sock = self.request
            try:
                header = _recv_exact(sock, _HEADER_STRUCT.size)
                (json_len,) = _HEADER_STRUCT.unpack(header)
                handshake = json.loads(_recv_exact(sock, json_len).decode("utf-8"))

                job_id = handshake["job_id"]
                token = handshake["token"]
                relative_path = handshake["relative_path"]
                size = int(handshake["size"])

                expected = pending.consume(job_id, token)
                if expected is None:
                    sock.sendall(b"NO")
                    logger.warning("Transferência recusada: job/token não esperado (%s)", job_id)
                    return

                sock.sendall(b"OK")
                # Issue #112: só marca "conectado" a partir daqui -- antes
                # disso a conexao pode ser qualquer coisa (porta escaneada,
                # handshake invalido), nao uma transferencia de verdade.
                peer_ip = self.client_address[0]
                on_status(f"Recebendo arquivo de outro agent ({peer_ip})")
                try:
                    self._receive_file(sock, expected.dest_root, relative_path, size)
                finally:
                    on_status(None)
            except Exception:
                logger.exception("Falha atendendo conexão de transferência agent<->agent")

        def _receive_file(self, sock, dest_root: Path, relative_path: str, size: int):
            dest_path = (dest_root / relative_path).resolve()
            if dest_root.resolve() not in dest_path.parents and dest_path != dest_root.resolve():
                raise ValueError(f"relative_path escapando do destino: {relative_path!r}")

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")

            digest = hashlib.sha256()
            remaining = size
            with open(tmp_path, "wb") as fh:
                while remaining > 0:
                    chunk = sock.recv(min(65536, remaining))
                    if not chunk:
                        raise ConnectionError("Conexão encerrada durante a transferência do arquivo")
                    fh.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)

            tmp_path.replace(dest_path)
            response = {"ok": True, "sha256": digest.hexdigest()}
            sock.sendall(json.dumps(response).encode("utf-8"))

    return Handler


class PeerListenerServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def make_peer_listener(
    bind_ip: str, port: int, pending: PendingTokens, on_status: Optional[Callable[[Optional[str]], None]] = None
) -> PeerListenerServer:
    return PeerListenerServer((bind_ip, port), _make_handler(pending, on_status or (lambda label: None)))
