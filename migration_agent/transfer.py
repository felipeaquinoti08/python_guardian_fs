"""Issue #107 (Incremento 2): execução real de transferência agent<->agent.

Lado cliente (quem empurra o arquivo) do protocolo já implementado em
`peer_listener.py` (issue #105, testado via socket loopback). O outro
agent (destino) já está rodando esse listener o tempo todo -- aqui só
implementamos quem CONECTA nele.

Throttle de banda (issue #107, pedido do usuário): como o Guardian nunca
vê os bytes, só o próprio agent consegue limitar a taxa de transferência
de verdade -- `RateLimiter` dorme o tempo necessário entre chunks pra não
ultrapassar `max_bandwidth_mbps` (MB/s, não KB/s).
"""

from __future__ import annotations

import hashlib
import json
import socket
import struct
import time
from pathlib import Path
from typing import Optional

import requests

_HEADER_STRUCT = struct.Struct(">I")
_CHUNK_SIZE = 65536
# Múltiplo de 320 KiB exigido pela API de upload session do Microsoft Graph
# -- mesmo tamanho de chunk usado pelo Guardian em SharepointAdapter::uploadStream()
# quando é ele quem sobe (cenário cloud-a-cloud).
_GRAPH_CHUNK_SIZE = 3276800


class TransferError(Exception):
    pass


class RateLimiter:
    """Limita a taxa de envio/recebimento a `mbps` MB/s (None = sem limite)."""

    def __init__(self, mbps: Optional[float]):
        self._bytes_per_second = (mbps * 1024 * 1024) if mbps else None
        self._start = time.monotonic()
        self._sent = 0

    def throttle(self, chunk_size: int) -> None:
        self._sent += chunk_size
        if not self._bytes_per_second:
            return

        expected_seconds = self._sent / self._bytes_per_second
        elapsed_seconds = time.monotonic() - self._start
        if expected_seconds > elapsed_seconds:
            time.sleep(expected_seconds - elapsed_seconds)


def push_file_to_peer(
    source_path: str,
    dest_ip: str,
    dest_port: int,
    job_id: str,
    token: str,
    relative_path: str,
    max_bandwidth_mbps: Optional[float] = None,
    connect_timeout: float = 10.0,
) -> dict:
    """Empurra um arquivo direto pro peer_listener de outro agent.

    Levanta TransferError em qualquer falha (arquivo recusado, conexão
    perdida no meio, etc.) -- o chamador (handle_run_transfer) decide o
    que fazer (nunca apaga a origem se isso levantar).
    """
    path = Path(source_path)
    if not path.is_file():
        raise TransferError(f"Arquivo de origem não encontrado: {source_path!r}")

    size = path.stat().st_size
    limiter = RateLimiter(max_bandwidth_mbps)
    digest = hashlib.sha256()

    try:
        with socket.create_connection((dest_ip, dest_port), timeout=connect_timeout) as sock:
            handshake = json.dumps(
                {"job_id": job_id, "token": token, "relative_path": relative_path, "size": size}
            ).encode("utf-8")
            sock.sendall(_HEADER_STRUCT.pack(len(handshake)) + handshake)

            ack = _recv_exact(sock, 2)
            if ack != b"OK":
                raise TransferError(f"Destino recusou a transferência (handshake inválido/token não esperado): {ack!r}")

            with open(path, "rb") as fh:
                sent = 0
                while sent < size:
                    chunk = fh.read(min(_CHUNK_SIZE, size - sent))
                    if not chunk:
                        break
                    sock.sendall(chunk)
                    digest.update(chunk)
                    sent += len(chunk)
                    limiter.throttle(len(chunk))

            response_raw = sock.recv(65536)
            if not response_raw:
                raise TransferError("Destino encerrou a conexão sem confirmar o recebimento.")

            response = json.loads(response_raw.decode("utf-8"))
            if not response.get("ok"):
                raise TransferError(f"Destino reportou falha: {response.get('error', 'desconhecido')}")

            if response.get("sha256") != digest.hexdigest():
                raise TransferError("Hash do arquivo recebido no destino não bate com o enviado (transferência corrompida).")

            return {"bytes_sent": sent, "sha256": digest.hexdigest()}
    except OSError as exc:
        # Conexão recusada, timeout, host inalcançável, etc. -- normaliza
        # pra TransferError igual qualquer outra falha deste módulo, assim
        # quem chama (handle_run_transfer) só precisa tratar um tipo de erro.
        raise TransferError(f"Não foi possível conectar/transferir pro destino {dest_ip}:{dest_port}: {exc}") from exc


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise TransferError("Conexão encerrada antes do esperado (handshake incompleto).")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


# ── Cloud <-> Agent (issue #107, Incremento 2) ──────────────────────────
#
# O Guardian gera a credencial (link de download pré-autenticado, ou sessão/
# SAS/URL pré-assinada de upload) reaproveitando o que cada adapter cloud já
# tem hoje (SharepointAdapter::getDownloadUrl(), etc.) -- o agent só entra
# com o outro lado da mesma URL, como qualquer cliente HTTP externo faria.
# Bytes nunca passam pelo Guardian nessas chamadas.
#
# NÃO TESTADO contra provedores cloud reais neste ambiente (sem tenant/
# storage de teste disponível) -- só a mecânica HTTP foi validada contra um
# servidor local que imita o comportamento esperado. Validar contra
# SharePoint/Blob/S3 reais antes de liberar pra produção.


class ThrottledFileReader:
    """Arquivo lido em chunks com throttle, exposto como file-like object
    (tem `.read()` e `__len__`) -- o `requests` usa `__len__` pra mandar
    `Content-Length` corretamente em vez de cair em chunked transfer-encoding
    (que a maioria dos serviços de blob storage não aceita num PUT simples).
    """

    def __init__(self, path: Path, limiter: RateLimiter):
        self._file = open(path, "rb")
        self._limiter = limiter
        self._size = path.stat().st_size

    def __len__(self) -> int:
        return self._size

    def read(self, size: int = -1) -> bytes:
        chunk = self._file.read(_CHUNK_SIZE if size in (-1, None) else size)
        if chunk:
            self._limiter.throttle(len(chunk))
        return chunk

    def close(self) -> None:
        self._file.close()


def upload_to_cloud(path: Path, credential: dict, max_bandwidth_mbps: Optional[float] = None) -> dict:
    """Sobe `path` direto pro provedor cloud usando a credencial de curta
    duração gerada pelo Guardian. `credential["type"]` decide o protocolo.
    """
    if not path.is_file():
        raise TransferError(f"Arquivo de origem não encontrado: {path}")

    cred_type = credential.get("type")
    if cred_type == "graph_upload_session":
        return _upload_graph_session(path, credential, max_bandwidth_mbps)
    if cred_type == "azure_sas_put":
        return _upload_via_put(path, credential, max_bandwidth_mbps, default_headers={"x-ms-blob-type": "BlockBlob"})
    if cred_type == "s3_presigned_put":
        return _upload_via_put(path, credential, max_bandwidth_mbps, default_headers={})

    raise TransferError(f"Tipo de credencial de upload desconhecido: {cred_type!r}")


def _upload_graph_session(path: Path, credential: dict, max_bandwidth_mbps: Optional[float]) -> dict:
    size = path.stat().st_size
    limiter = RateLimiter(max_bandwidth_mbps)
    upload_url = credential["uploadUrl"]

    if size == 0:
        resp = requests.put(
            upload_url, data=b"", headers={"Content-Length": "0", "Content-Range": "bytes 0-0/0"}, timeout=120
        )
        if resp.status_code not in (200, 201, 202):
            raise TransferError(f"Upload Graph (arquivo vazio) falhou (HTTP {resp.status_code}): {resp.text[:300]}")
        return {"bytes_sent": 0}

    with open(path, "rb") as fh:
        offset = 0
        while offset < size:
            chunk = fh.read(min(_GRAPH_CHUNK_SIZE, size - offset))
            if not chunk:
                break
            end = offset + len(chunk) - 1
            resp = requests.put(
                upload_url,
                data=chunk,
                headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {offset}-{end}/{size}"},
                timeout=120,
            )
            if resp.status_code not in (200, 201, 202):
                raise TransferError(f"Upload Graph falhou (HTTP {resp.status_code}): {resp.text[:300]}")
            limiter.throttle(len(chunk))
            offset += len(chunk)

    return {"bytes_sent": size}


def _upload_via_put(path: Path, credential: dict, max_bandwidth_mbps: Optional[float], default_headers: dict) -> dict:
    limiter = RateLimiter(max_bandwidth_mbps)
    reader = ThrottledFileReader(path, limiter)
    headers = {**default_headers, **(credential.get("headers") or {})}
    try:
        resp = requests.put(credential["url"], data=reader, headers=headers, timeout=600)
    finally:
        reader.close()

    if resp.status_code not in (200, 201):
        raise TransferError(f"Upload falhou (HTTP {resp.status_code}): {resp.text[:300]}")

    return {"bytes_sent": path.stat().st_size}


def download_from_cloud(
    url: str, dest_path: Path, max_bandwidth_mbps: Optional[float] = None, expected_size: Optional[int] = None
) -> dict:
    """Baixa direto do link temporário gerado pelo Guardian pra `dest_path`."""
    limiter = RateLimiter(max_bandwidth_mbps)
    digest = hashlib.sha256()
    tmp_path = dest_path.with_name(dest_path.name + ".part")
    total = 0

    with requests.get(url, stream=True, timeout=120) as resp:
        if resp.status_code != 200:
            raise TransferError(f"Download falhou (HTTP {resp.status_code}): {resp.text[:300]}")

        with open(tmp_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                if not chunk:
                    continue
                fh.write(chunk)
                digest.update(chunk)
                total += len(chunk)
                limiter.throttle(len(chunk))

    if expected_size is not None and total != expected_size:
        tmp_path.unlink(missing_ok=True)
        raise TransferError(f"Tamanho baixado ({total}) diferente do esperado ({expected_size}).")

    tmp_path.replace(dest_path)
    return {"bytes_received": total, "sha256": digest.hexdigest()}
