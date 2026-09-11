"""Issue #116: bug real em produção -- um arquivo .pst aberto no Outlook
travou uma transferência por mais de 55min sem nenhum erro reportado.
`TimeoutFileReader` (transfer.py) lê em thread de fundo com timeout, pra
uma leitura travada falhar rápido com um erro claro em vez de travar pra
sempre."""

import time
from unittest.mock import patch

import pytest

from migration_agent.transfer import TimeoutFileReader, TransferError, _ensure_file_available


def test_reads_full_file_correctly_in_chunks(tmp_path):
    content = bytes(range(256)) * 500  # 128000 bytes
    path = tmp_path / "arquivo.bin"
    path.write_bytes(content)

    reader = TimeoutFileReader(path, chunk_size=1000)
    chunks = []
    while True:
        chunk = reader.read_chunk()
        if not chunk:
            break
        chunks.append(chunk)

    assert b"".join(chunks) == content


def test_read_chunk_returns_empty_bytes_at_eof(tmp_path):
    path = tmp_path / "vazio.bin"
    path.write_bytes(b"")

    reader = TimeoutFileReader(path, chunk_size=1024)
    assert reader.read_chunk() == b""


def test_raises_transfer_error_when_read_stalls_past_timeout(tmp_path):
    """Simula o bug real: um `fh.read()` que nunca retorna (arquivo
    travado por outro processo) -- `read_chunk()` deve desistir de
    esperar e falhar com uma mensagem clara, em vez de travar pra
    sempre."""

    class _StuckFile:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self, _n):
            time.sleep(5)  # bem mais que o timeout do teste abaixo
            return b""

    path = tmp_path / "travado.bin"
    path.write_bytes(b"nao importa, o open() vai ser trocado")

    with patch("migration_agent.transfer.open", return_value=_StuckFile()):
        reader = TimeoutFileReader(path, chunk_size=1024, timeout_seconds=0.2)
        with pytest.raises(TransferError, match="travou"):
            reader.read_chunk()


def test_propagates_read_errors_as_transfer_error(tmp_path):
    reader = TimeoutFileReader(tmp_path / "nao_existe.bin", chunk_size=1024, timeout_seconds=5)
    with pytest.raises(TransferError, match="Falha lendo"):
        reader.read_chunk()


def test_ensure_file_available_passes_for_normal_file(tmp_path):
    path = tmp_path / "normal.bin"
    path.write_bytes(b"conteudo qualquer")

    _ensure_file_available(path, timeout_seconds=5)  # nao deve levantar nada


def test_ensure_file_available_passes_for_empty_file(tmp_path):
    path = tmp_path / "vazio.bin"
    path.write_bytes(b"")

    _ensure_file_available(path, timeout_seconds=5)  # arquivo vazio nao e "travado"


def test_ensure_file_available_raises_distinguishable_error_when_locked(tmp_path):
    """Issue #116 (parte 2): mensagem precisa conter "em uso por outro
    programa" -- é o que o lado Guardian reconhece pra mostrar um status
    amigável em vez do genérico "Tentativa falhou"."""

    class _StuckFile:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self, _n):
            time.sleep(5)
            return b""

    path = tmp_path / "travado.pst"
    path.write_bytes(b"nao importa")

    with patch("migration_agent.transfer.open", return_value=_StuckFile()):
        start = time.monotonic()
        with pytest.raises(TransferError, match="em uso por outro programa"):
            _ensure_file_available(path, timeout_seconds=0.2)
        elapsed = time.monotonic() - start

    assert elapsed < 1, f"deveria detectar rapido (timeout curto), levou {elapsed}s"
