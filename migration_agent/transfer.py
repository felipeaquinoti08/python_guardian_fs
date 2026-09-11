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
import queue
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests

_HEADER_STRUCT = struct.Struct(">I")
_CHUNK_SIZE = 65536
# Múltiplo de 320 KiB exigido pela API de upload session do Microsoft Graph
# -- mesmo tamanho de chunk usado pelo Guardian em SharepointAdapter::uploadStream()
# quando é ele quem sobe (cenário cloud-a-cloud).
_GRAPH_CHUNK_SIZE = 3276800

# Issue #116: bug real em produção -- um arquivo .pst aberto no Outlook na
# máquina de origem travou uma transferência por mais de 55min sem NENHUM
# erro reportado (o agent continuava online/respondendo ao long-poll
# normalmente -- só a thread de fundo daquele arquivo específico ficava
# presa pra sempre). Causa: leitura de um arquivo bloqueado por outro
# processo (lock do Windows, comum em .pst aberto/em uso) pode travar
# indefinidamente no nível do SO -- `fh.read()` não tem NENHUM mecanismo de
# timeout nativo em Python, diferente de chamadas de rede (socket/requests,
# que já tinham timeout configurado desde sempre). 60s é bem acima do que
# uma leitura local/de rede legítima deveria levar pra um chunk só.
_READ_TIMEOUT_SECONDS = 60.0

# Issue #116 (parte 2, pedido do usuário): um arquivo em uso não é um erro
# definitivo, é uma condição transitória -- em vez de só falhar rápido,
# detecta isso ANTES de começar a transferência de verdade (probe curto,
# só precisa confirmar que dá pra ler o começo do arquivo) e levanta um
# erro DISTINGUÍVEL (mensagem com "em uso por outro programa"), que o
# lado Guardian reconhece pra mostrar um status amigável -- o retry em si
# já existe (Horizon, backoff crescente: 1min/3min/5min/15min/30min/1h),
# reaproveitado em vez de reinventar um loop de espera dentro do agent.
_LOCK_CHECK_TIMEOUT_SECONDS = 10.0

# Issue #115: (bytes_ja_transferidos, bytes_totais) -- chamado a cada chunk
# em todas as funções deste módulo. Quem passa o callback (poller.py) decide
# a cadência de report de verdade pro Guardian (throttle próprio, pra não
# virar uma chamada HTTP por chunk de 64KB numa transferência de 600MB).
ProgressCallback = Callable[[int, int], None]


def _noop_progress(_done: int, _total: int) -> None:
    pass


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


_READ_DONE = object()


class TimeoutFileReader:
    """Lê `path` em chunks numa thread de fundo dedicada, alimentando uma
    fila -- `read_chunk()` espera cada chunk com timeout, em vez de um
    `fh.read()` direto que pode travar pra sempre se o arquivo estiver
    bloqueado por outro processo (issue #116).

    A thread de leitura fica presa (não tem como matar uma thread Python à
    força) se o `open()`/`read()` subjacente nunca retornar -- mas isso é
    aceitável: o objetivo aqui não é liberar esse recurso do SO, é NÃO
    deixar a transferência (e o comando `run_transfer` inteiro) travada
    indefinidamente sem reportar nada pro Guardian. Uma thread órfã e
    presa é um custo bem menor que um comando que nunca retorna.
    """

    def __init__(self, path: Path, chunk_size: int, timeout_seconds: Optional[float] = None):
        self._path = path
        # Lido em tempo de chamada (não como valor padrão do parâmetro,
        # fixado em tempo de definição da função) -- testes precisam
        # conseguir ajustar _READ_TIMEOUT_SECONDS e ver efeito imediato
        # em quem cria um TimeoutFileReader sem passar o parâmetro.
        self._timeout_seconds = timeout_seconds if timeout_seconds is not None else _READ_TIMEOUT_SECONDS
        self._queue: "queue.Queue" = queue.Queue(maxsize=4)
        self._error: Optional[Exception] = None
        self._thread = threading.Thread(target=self._read_loop, args=(chunk_size,), daemon=True, name="file-reader")
        self._thread.start()

    def _read_loop(self, chunk_size: int) -> None:
        try:
            with open(self._path, "rb") as fh:
                while True:
                    chunk = fh.read(chunk_size)
                    if not chunk:
                        self._queue.put(_READ_DONE)
                        return
                    self._queue.put(chunk)
        except Exception as exc:  # noqa: BLE001 - qualquer erro de leitura vira TransferError claro em read_chunk()
            self._error = exc
            self._queue.put(_READ_DONE)

    def read_chunk(self) -> bytes:
        try:
            item = self._queue.get(timeout=self._timeout_seconds)
        except queue.Empty:
            raise TransferError(
                f"Leitura do arquivo travou por mais de {int(self._timeout_seconds)}s -- "
                f"ele pode estar aberto/bloqueado por outro programa na origem: {self._path}"
            )

        if item is _READ_DONE:
            if self._error is not None:
                raise TransferError(f"Falha lendo o arquivo {self._path}: {self._error}")
            return b""

        return item


def _ensure_file_available(path: Path, timeout_seconds: Optional[float] = None) -> None:
    """Probe curto (só o suficiente pra confirmar que dá pra ler o começo
    do arquivo) ANTES de iniciar a transferência de verdade -- se o
    arquivo estiver aberto/bloqueado por outro programa (ex: .pst no
    Outlook), detecta rápido (10s) em vez dos 60s do timeout geral de
    transferência, e levanta um erro com uma mensagem reconhecível pelo
    lado Guardian (ver UniversalMigrationFileJob, issue #116)."""
    resolved_timeout = timeout_seconds if timeout_seconds is not None else _LOCK_CHECK_TIMEOUT_SECONDS
    probe = TimeoutFileReader(path, chunk_size=1, timeout_seconds=resolved_timeout)
    try:
        probe.read_chunk()
    except TransferError as exc:
        raise TransferError(
            f"Arquivo em uso por outro programa (ex: aberto no Outlook/Excel/etc.) -- {path}"
        ) from exc


def push_file_to_peer(
    source_path: str,
    dest_ip: str,
    dest_port: int,
    job_id: str,
    token: str,
    relative_path: str,
    max_bandwidth_mbps: Optional[float] = None,
    connect_timeout: float = 10.0,
    on_progress: ProgressCallback = _noop_progress,
) -> dict:
    """Empurra um arquivo direto pro peer_listener de outro agent.

    Levanta TransferError em qualquer falha (arquivo recusado, conexão
    perdida no meio, etc.) -- o chamador (handle_run_transfer) decide o
    que fazer (nunca apaga a origem se isso levantar).
    """
    path = Path(source_path)
    if not path.is_file():
        raise TransferError(f"Arquivo de origem não encontrado: {source_path!r}")
    _ensure_file_available(path)

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

            reader = TimeoutFileReader(path, _CHUNK_SIZE)
            sent = 0
            on_progress(sent, size)
            while sent < size:
                chunk = reader.read_chunk()
                if not chunk:
                    break
                sock.sendall(chunk)
                digest.update(chunk)
                sent += len(chunk)
                limiter.throttle(len(chunk))
                on_progress(sent, size)

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

    Lê via `TimeoutFileReader` (issue #116) num tamanho de chunk fixo,
    passando por um buffer interno pra atender `read(size)` com qualquer
    tamanho que o `requests`/`urllib3` pedir (que não necessariamente bate
    com o chunk que o reader de fundo produz).
    """

    def __init__(self, path: Path, limiter: RateLimiter, on_progress: ProgressCallback = _noop_progress):
        self._reader = TimeoutFileReader(path, _CHUNK_SIZE)
        self._limiter = limiter
        self._size = path.stat().st_size
        self._on_progress = on_progress
        self._read_so_far = 0
        self._buffer = b""
        self._exhausted = False

    def __len__(self) -> int:
        return self._size

    def read(self, size: int = -1) -> bytes:
        want = _CHUNK_SIZE if size in (-1, None) else size
        while len(self._buffer) < want and not self._exhausted:
            chunk = self._reader.read_chunk()
            if not chunk:
                self._exhausted = True
                break
            self._buffer += chunk

        result, self._buffer = self._buffer[:want], self._buffer[want:]
        if result:
            self._limiter.throttle(len(result))
            self._read_so_far += len(result)
            self._on_progress(self._read_so_far, self._size)
        return result

    def close(self) -> None:
        pass


def upload_to_cloud(
    path: Path, credential: dict, max_bandwidth_mbps: Optional[float] = None, on_progress: ProgressCallback = _noop_progress
) -> dict:
    """Sobe `path` direto pro provedor cloud usando a credencial de curta
    duração gerada pelo Guardian. `credential["type"]` decide o protocolo.
    """
    if not path.is_file():
        raise TransferError(f"Arquivo de origem não encontrado: {path}")
    _ensure_file_available(path)

    cred_type = credential.get("type")
    if cred_type == "graph_upload_session":
        return _upload_graph_session(path, credential, max_bandwidth_mbps, on_progress)
    if cred_type == "azure_sas_put":
        return _upload_via_put(
            path, credential, max_bandwidth_mbps, default_headers={"x-ms-blob-type": "BlockBlob"}, on_progress=on_progress
        )
    if cred_type == "s3_presigned_put":
        return _upload_via_put(path, credential, max_bandwidth_mbps, default_headers={}, on_progress=on_progress)

    raise TransferError(f"Tipo de credencial de upload desconhecido: {cred_type!r}")


def _upload_graph_session(
    path: Path, credential: dict, max_bandwidth_mbps: Optional[float], on_progress: ProgressCallback = _noop_progress
) -> dict:
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

    reader = TimeoutFileReader(path, _GRAPH_CHUNK_SIZE)
    offset = 0
    on_progress(offset, size)
    while offset < size:
        chunk = reader.read_chunk()
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
        on_progress(offset, size)

    return {"bytes_sent": size}


def _upload_via_put(
    path: Path,
    credential: dict,
    max_bandwidth_mbps: Optional[float],
    default_headers: dict,
    on_progress: ProgressCallback = _noop_progress,
) -> dict:
    limiter = RateLimiter(max_bandwidth_mbps)
    reader = ThrottledFileReader(path, limiter, on_progress)
    headers = {**default_headers, **(credential.get("headers") or {})}
    try:
        resp = requests.put(credential["url"], data=reader, headers=headers, timeout=600)
    finally:
        reader.close()

    if resp.status_code not in (200, 201):
        raise TransferError(f"Upload falhou (HTTP {resp.status_code}): {resp.text[:300]}")

    return {"bytes_sent": path.stat().st_size}


def download_from_cloud(
    url: str,
    dest_path: Path,
    max_bandwidth_mbps: Optional[float] = None,
    expected_size: Optional[int] = None,
    on_progress: ProgressCallback = _noop_progress,
) -> dict:
    """Baixa direto do link temporário gerado pelo Guardian pra `dest_path`."""
    limiter = RateLimiter(max_bandwidth_mbps)
    digest = hashlib.sha256()
    tmp_path = dest_path.with_name(dest_path.name + ".part")
    total = 0

    with requests.get(url, stream=True, timeout=120) as resp:
        if resp.status_code != 200:
            raise TransferError(f"Download falhou (HTTP {resp.status_code}): {resp.text[:300]}")

        # expected_size normalmente vem do Guardian (tamanho já conhecido na
        # origem); Content-Length é só um fallback pro raro caso de vir vazio.
        total_size = expected_size or int(resp.headers.get("Content-Length") or 0) or None

        with open(tmp_path, "wb") as fh:
            on_progress(0, total_size or 0)
            for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                if not chunk:
                    continue
                fh.write(chunk)
                digest.update(chunk)
                total += len(chunk)
                limiter.throttle(len(chunk))
                on_progress(total, total_size or total)

    if expected_size is not None and total != expected_size:
        tmp_path.unlink(missing_ok=True)
        raise TransferError(f"Tamanho baixado ({total}) diferente do esperado ({expected_size}).")

    tmp_path.replace(dest_path)
    return {"bytes_received": total, "sha256": digest.hexdigest()}
