import threading
import time
from unittest.mock import patch

import pytest

import migration_agent.transfer as transfer_mod
from migration_agent.commands import handle_run_transfer
from migration_agent.peer_listener import PendingTokens, make_peer_listener


def _start_listener(tmp_path):
    pending = PendingTokens()
    server = make_peer_listener("127.0.0.1", 0, pending)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, pending, port


def test_receive_then_push_writes_file_on_destination(tmp_path):
    server, pending, port = _start_listener(tmp_path)
    try:
        dest_root = tmp_path / "dest_share"
        dest_root.mkdir()
        source_file = tmp_path / "arquivo.txt"
        source_file.write_bytes(b"conteudo de teste" * 1000)

        # Destino registra a expectativa (mode receive_from_agent)
        recv_result = handle_run_transfer({
            "mode": "receive_from_agent",
            "job_id": "job-1",
            "token": "token-abc",
            "dest_root": str(dest_root),
        }, pending_tokens=pending)
        assert recv_result == {"expecting": True}

        # Origem empurra o arquivo de verdade (mode push_to_agent)
        push_result = handle_run_transfer({
            "mode": "push_to_agent",
            "source_path": str(source_file),
            "dest_ip": "127.0.0.1",
            "dest_port": port,
            "job_id": "job-1",
            "token": "token-abc",
            "relative_path": "subpasta/arquivo.txt",
        })

        assert "sha256" in push_result
        written = dest_root / "subpasta" / "arquivo.txt"
        assert written.read_bytes() == source_file.read_bytes()
        assert source_file.exists()  # delete_source não foi pedido
    finally:
        server.shutdown()


def test_push_to_agent_deletes_source_when_requested(tmp_path):
    server, pending, port = _start_listener(tmp_path)
    try:
        dest_root = tmp_path / "dest_share"
        dest_root.mkdir()
        source_file = tmp_path / "arquivo.txt"
        source_file.write_bytes(b"dados")

        handle_run_transfer({
            "mode": "receive_from_agent", "job_id": "job-2", "token": "tok-2", "dest_root": str(dest_root),
        }, pending_tokens=pending)

        handle_run_transfer({
            "mode": "push_to_agent",
            "source_path": str(source_file),
            "dest_ip": "127.0.0.1",
            "dest_port": port,
            "job_id": "job-2",
            "token": "tok-2",
            "relative_path": "arquivo.txt",
            "delete_source": True,
        })

        assert not source_file.exists()
        assert (dest_root / "arquivo.txt").read_bytes() == b"dados"
    finally:
        server.shutdown()


def test_push_to_agent_without_matching_expectation_raises(tmp_path):
    server, pending, port = _start_listener(tmp_path)
    try:
        source_file = tmp_path / "arquivo.txt"
        source_file.write_bytes(b"dados")

        with pytest.raises(RuntimeError):
            handle_run_transfer({
                "mode": "push_to_agent",
                "source_path": str(source_file),
                "dest_ip": "127.0.0.1",
                "dest_port": port,
                "job_id": "job-x",
                "token": "token-nao-esperado",
                "relative_path": "arquivo.txt",
            })

        # arquivo de origem preservado -- falha não deve nunca apagar nada
        assert source_file.exists()
    finally:
        server.shutdown()


def test_push_to_agent_unreachable_destination_raises(tmp_path):
    source_file = tmp_path / "arquivo.txt"
    source_file.write_bytes(b"dados")

    with pytest.raises(RuntimeError):
        handle_run_transfer({
            "mode": "push_to_agent",
            "source_path": str(source_file),
            "dest_ip": "127.0.0.1",
            "dest_port": 1,  # ninguém escutando
            "job_id": "job-y",
            "token": "tok",
            "relative_path": "arquivo.txt",
        })


def test_receive_from_agent_without_pending_tokens_raises():
    with pytest.raises(RuntimeError):
        handle_run_transfer({"mode": "receive_from_agent", "job_id": "j", "token": "t", "dest_root": "/tmp"})


def test_unknown_mode_raises_value_error():
    with pytest.raises(ValueError):
        handle_run_transfer({"mode": "modo_bizarro"})


def test_bandwidth_limit_does_not_break_correctness(tmp_path):
    """Só confirma que o throttle não corrompe/trava a transferência --
    não mede tempo real (seria um teste lento e frágil em CI)."""
    server, pending, port = _start_listener(tmp_path)
    try:
        dest_root = tmp_path / "dest_share"
        dest_root.mkdir()
        source_file = tmp_path / "arquivo.bin"
        source_file.write_bytes(b"x" * (200 * 1024))  # 200KB

        handle_run_transfer({
            "mode": "receive_from_agent", "job_id": "job-bw", "token": "tok-bw", "dest_root": str(dest_root),
        }, pending_tokens=pending)

        result = handle_run_transfer({
            "mode": "push_to_agent",
            "source_path": str(source_file),
            "dest_ip": "127.0.0.1",
            "dest_port": port,
            "job_id": "job-bw",
            "token": "tok-bw",
            "relative_path": "arquivo.bin",
            "max_bandwidth_mbps": 50,  # bem acima do necessário, só exercita o código do throttle
        })

        assert result["bytes_sent"] == 200 * 1024
        assert (dest_root / "arquivo.bin").stat().st_size == 200 * 1024
    finally:
        server.shutdown()


def test_push_to_agent_reports_progress_on_sender_side(tmp_path):
    server, pending, port = _start_listener(tmp_path)
    try:
        dest_root = tmp_path / "dest_share"
        dest_root.mkdir()
        source_file = tmp_path / "arquivo.bin"
        source_file.write_bytes(b"x" * (500 * 1024))

        handle_run_transfer({
            "mode": "receive_from_agent", "job_id": "job-p1", "token": "tok-p1", "dest_root": str(dest_root),
        }, pending_tokens=pending)

        progress_calls = []
        handle_run_transfer(
            {
                "mode": "push_to_agent",
                "source_path": str(source_file),
                "dest_ip": "127.0.0.1",
                "dest_port": port,
                "job_id": "job-p1",
                "token": "tok-p1",
                "relative_path": "arquivo.bin",
            },
            on_progress=lambda done, total: progress_calls.append((done, total)),
        )

        assert progress_calls[0] == (0, 500 * 1024)
        assert progress_calls[-1] == (500 * 1024, 500 * 1024)
    finally:
        server.shutdown()


def test_receive_from_agent_reports_progress_on_receiver_side(tmp_path):
    """O lado que recebe só sabe o progresso de verdade quando a conexão
    chega -- on_progress passado em receive_from_agent fica guardado em
    PendingTokens e só é chamado dentro de peer_listener.py, bem depois
    de handle_run_transfer(mode=receive_from_agent) já ter retornado."""
    server, pending, port = _start_listener(tmp_path)
    try:
        dest_root = tmp_path / "dest_share"
        dest_root.mkdir()
        source_file = tmp_path / "arquivo.bin"
        source_file.write_bytes(b"y" * (500 * 1024))

        progress_calls = []
        handle_run_transfer(
            {"mode": "receive_from_agent", "job_id": "job-p2", "token": "tok-p2", "dest_root": str(dest_root)},
            pending_tokens=pending,
            on_progress=lambda done, total: progress_calls.append((done, total)),
        )
        assert progress_calls == []  # nada ainda -- so registrou a expectativa

        handle_run_transfer({
            "mode": "push_to_agent",
            "source_path": str(source_file),
            "dest_ip": "127.0.0.1",
            "dest_port": port,
            "job_id": "job-p2",
            "token": "tok-p2",
            "relative_path": "arquivo.bin",
        })

        assert progress_calls[0] == (0, 500 * 1024)
        assert progress_calls[-1] == (500 * 1024, 500 * 1024)
    finally:
        server.shutdown()


def test_push_to_agent_fails_fast_instead_of_hanging_on_locked_source_file(tmp_path):
    """Issue #116: reproduz o bug real -- um arquivo bloqueado por outro
    processo (ex: .pst aberto no Outlook) não pode travar a transferência
    indefinidamente. Simula o lock via um arquivo "preso" que nunca
    termina de ler; `push_to_agent` deve falhar rápido (timeout do
    TimeoutFileReader), não travar por horas."""
    server, pending, port = _start_listener(tmp_path)
    try:
        dest_root = tmp_path / "dest_share"
        dest_root.mkdir()
        source_file = tmp_path / "arquivo_travado.pst"
        source_file.write_bytes(b"dados")

        handle_run_transfer({
            "mode": "receive_from_agent", "job_id": "job-lock", "token": "tok-lock", "dest_root": str(dest_root),
        }, pending_tokens=pending)

        class _StuckFile:
            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def read(self, _n):
                time.sleep(5)
                return b""

        original_read_timeout = transfer_mod._READ_TIMEOUT_SECONDS
        original_lock_timeout = transfer_mod._LOCK_CHECK_TIMEOUT_SECONDS
        transfer_mod._READ_TIMEOUT_SECONDS = 0.2
        transfer_mod._LOCK_CHECK_TIMEOUT_SECONDS = 0.2
        try:
            with patch("migration_agent.transfer.open", return_value=_StuckFile()):
                start = time.monotonic()
                # O pre-check de disponibilidade (issue #116, parte 2) pega
                # isso antes mesmo da transferencia de verdade comecar --
                # mensagem distinta ("em uso"), nao a generica de timeout
                # ("travou") do meio da transferencia.
                with pytest.raises(RuntimeError, match="em uso por outro programa"):
                    handle_run_transfer({
                        "mode": "push_to_agent",
                        "source_path": str(source_file),
                        "dest_ip": "127.0.0.1",
                        "dest_port": port,
                        "job_id": "job-lock",
                        "token": "tok-lock",
                        "relative_path": "arquivo_travado.pst",
                    })
                elapsed = time.monotonic() - start
        finally:
            transfer_mod._READ_TIMEOUT_SECONDS = original_read_timeout
            transfer_mod._LOCK_CHECK_TIMEOUT_SECONDS = original_lock_timeout

        assert elapsed < 2, f"deveria falhar rapido (timeout curto), levou {elapsed}s"
    finally:
        server.shutdown()
