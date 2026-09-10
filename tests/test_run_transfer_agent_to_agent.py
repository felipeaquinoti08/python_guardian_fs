import threading

import pytest

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
