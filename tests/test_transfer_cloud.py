"""Testa upload_to_cloud()/download_from_cloud() contra um servidor HTTP
local que imita o formato de requisição esperado por cada provedor (Graph
upload session, Azure SAS PUT, S3 presigned PUT) -- não é um teste contra
provedores reais (sem tenant/storage de teste disponível neste ambiente),
mas valida que a mecânica HTTP (headers, Content-Range, reconstrução dos
chunks, códigos de status) está correta.
"""
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from migration_agent.transfer import TransferError, download_from_cloud, upload_to_cloud


class _CapturingHandler(BaseHTTPRequestHandler):
    """Guarda os PUTs recebidos (headers + corpo) num dict compartilhado,
    reconstrói o conteúdo por Content-Range quando presente, e serve
    conteúdo fixo em GET pra testar download."""

    received = {"chunks": [], "headers": []}
    status_code = 201
    get_body = b""

    def log_message(self, fmt, *args):
        pass

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.__class__.received["chunks"].append(body)
        self.__class__.received["headers"].append(dict(self.headers.items()))
        self.send_response(self.__class__.status_code)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(self.__class__.get_body)


def _start_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


@pytest.fixture
def capturing_server():
    _CapturingHandler.received = {"chunks": [], "headers": []}
    _CapturingHandler.status_code = 201
    server, port = _start_server(_CapturingHandler)
    yield server, port, _CapturingHandler
    server.shutdown()


def test_upload_graph_session_sends_content_range_headers(tmp_path, capturing_server):
    server, port, handler = capturing_server
    handler.status_code = 200

    source = tmp_path / "arquivo.bin"
    # menor que o chunk do Graph -- sobe num PUT só
    source.write_bytes(b"a" * 1000)

    result = upload_to_cloud(source, {"type": "graph_upload_session", "uploadUrl": f"http://127.0.0.1:{port}/upload"})

    assert result == {"bytes_sent": 1000}
    assert len(handler.received["chunks"]) == 1
    assert handler.received["chunks"][0] == b"a" * 1000
    assert handler.received["headers"][0]["Content-Range"] == "bytes 0-999/1000"


def test_upload_graph_session_multiple_chunks_reconstruct_correctly(tmp_path, capturing_server):
    server, port, handler = capturing_server
    handler.status_code = 200

    source = tmp_path / "arquivo.bin"
    # forca 2 chunks: usa um tamanho de chunk pequeno via monkeypatch do modulo
    import migration_agent.transfer as transfer_mod
    original_chunk = transfer_mod._GRAPH_CHUNK_SIZE
    transfer_mod._GRAPH_CHUNK_SIZE = 100
    try:
        content = bytes(range(256)) * 1  # 256 bytes -> 3 chunks de 100/100/56
        source.write_bytes(content)
        result = upload_to_cloud(source, {"type": "graph_upload_session", "uploadUrl": f"http://127.0.0.1:{port}/upload"})
    finally:
        transfer_mod._GRAPH_CHUNK_SIZE = original_chunk

    assert result["bytes_sent"] == 256
    reconstructed = b"".join(handler.received["chunks"])
    assert reconstructed == content
    assert len(handler.received["chunks"]) == 3


def test_upload_graph_session_empty_file(tmp_path, capturing_server):
    server, port, handler = capturing_server
    handler.status_code = 200
    source = tmp_path / "vazio.bin"
    source.write_bytes(b"")

    result = upload_to_cloud(source, {"type": "graph_upload_session", "uploadUrl": f"http://127.0.0.1:{port}/upload"})

    assert result == {"bytes_sent": 0}
    assert len(handler.received["chunks"]) == 1


def test_upload_azure_sas_sends_blob_type_header(tmp_path, capturing_server):
    server, port, handler = capturing_server
    handler.status_code = 201
    source = tmp_path / "arquivo.bin"
    source.write_bytes(b"conteudo azure" * 100)

    result = upload_to_cloud(source, {"type": "azure_sas_put", "url": f"http://127.0.0.1:{port}/blob?sas=fake"})

    assert result == {"bytes_sent": len(b"conteudo azure" * 100)}
    # headers HTTP sao case-insensitive -- normaliza antes de comparar
    received_headers_lower = {k.lower(): v for k, v in handler.received["headers"][0].items()}
    assert received_headers_lower["x-ms-blob-type"] == "BlockBlob"
    assert b"".join(handler.received["chunks"]) == b"conteudo azure" * 100


def test_upload_s3_presigned_put(tmp_path, capturing_server):
    server, port, handler = capturing_server
    handler.status_code = 200
    source = tmp_path / "arquivo.bin"
    source.write_bytes(b"conteudo s3" * 100)

    result = upload_to_cloud(source, {"type": "s3_presigned_put", "url": f"http://127.0.0.1:{port}/bucket/key?X-Amz-Signature=fake"})

    assert result == {"bytes_sent": len(b"conteudo s3" * 100)}
    assert b"".join(handler.received["chunks"]) == b"conteudo s3" * 100


def test_upload_failure_status_raises_transfer_error(tmp_path, capturing_server):
    server, port, handler = capturing_server
    handler.status_code = 403
    source = tmp_path / "arquivo.bin"
    source.write_bytes(b"dados")

    with pytest.raises(TransferError):
        upload_to_cloud(source, {"type": "s3_presigned_put", "url": f"http://127.0.0.1:{port}/x"})


def test_upload_unknown_credential_type_raises(tmp_path):
    source = tmp_path / "arquivo.bin"
    source.write_bytes(b"dados")

    with pytest.raises(TransferError):
        upload_to_cloud(source, {"type": "tipo_bizarro"})


def test_upload_missing_source_file_raises(tmp_path):
    with pytest.raises(TransferError):
        upload_to_cloud(tmp_path / "nao_existe.bin", {"type": "s3_presigned_put", "url": "http://127.0.0.1:1/x"})


def test_download_from_cloud_writes_file_and_hash(tmp_path, capturing_server):
    server, port, handler = capturing_server
    content = b"conteudo baixado da nuvem" * 500
    handler.get_body = content

    dest = tmp_path / "baixado.bin"
    result = download_from_cloud(f"http://127.0.0.1:{port}/download", dest, expected_size=len(content))

    assert dest.read_bytes() == content
    assert result["bytes_received"] == len(content)
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_from_cloud_size_mismatch_raises_and_cleans_temp(tmp_path, capturing_server):
    server, port, handler = capturing_server
    handler.get_body = b"conteudo"

    dest = tmp_path / "baixado.bin"
    with pytest.raises(TransferError):
        download_from_cloud(f"http://127.0.0.1:{port}/download", dest, expected_size=999999)

    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_upload_graph_session_reports_progress(tmp_path, capturing_server):
    server, port, handler = capturing_server
    handler.status_code = 200
    import migration_agent.transfer as transfer_mod

    original_chunk = transfer_mod._GRAPH_CHUNK_SIZE
    transfer_mod._GRAPH_CHUNK_SIZE = 100
    progress_calls = []
    try:
        source = tmp_path / "arquivo.bin"
        source.write_bytes(b"a" * 256)
        upload_to_cloud(
            source,
            {"type": "graph_upload_session", "uploadUrl": f"http://127.0.0.1:{port}/upload"},
            on_progress=lambda done, total: progress_calls.append((done, total)),
        )
    finally:
        transfer_mod._GRAPH_CHUNK_SIZE = original_chunk

    assert progress_calls[0] == (0, 256)
    assert progress_calls[-1] == (256, 256)
    assert all(total == 256 for _done, total in progress_calls)


def test_upload_via_put_reports_progress(tmp_path, capturing_server):
    server, port, handler = capturing_server
    handler.status_code = 201
    progress_calls = []

    source = tmp_path / "arquivo.bin"
    source.write_bytes(b"x" * 5000)
    upload_to_cloud(
        source,
        {"type": "s3_presigned_put", "url": f"http://127.0.0.1:{port}/bucket/key"},
        on_progress=lambda done, total: progress_calls.append((done, total)),
    )

    assert progress_calls[-1] == (5000, 5000)
    assert all(total == 5000 for _done, total in progress_calls)


def test_download_from_cloud_reports_progress(tmp_path, capturing_server):
    server, port, handler = capturing_server
    content = b"conteudo" * 1000
    handler.get_body = content
    progress_calls = []

    dest = tmp_path / "baixado.bin"
    download_from_cloud(
        f"http://127.0.0.1:{port}/download",
        dest,
        expected_size=len(content),
        on_progress=lambda done, total: progress_calls.append((done, total)),
    )

    assert progress_calls[0] == (0, len(content))
    assert progress_calls[-1] == (len(content), len(content))


def test_download_from_cloud_progress_defaults_to_done_when_size_unknown(tmp_path, capturing_server):
    server, port, handler = capturing_server
    content = b"conteudo sem tamanho esperado"
    handler.get_body = content
    progress_calls = []

    dest = tmp_path / "baixado.bin"
    download_from_cloud(
        f"http://127.0.0.1:{port}/download",
        dest,
        on_progress=lambda done, total: progress_calls.append((done, total)),
    )

    # sem expected_size e sem Content-Length no response de teste -- o total
    # fica igual ao "done" de cada chamada (melhor do que None/0 pra quem
    # calcula porcentagem, embora nesse caso a % sempre feche em 100%)
    assert progress_calls[-1][0] == progress_calls[-1][1] == len(content)
