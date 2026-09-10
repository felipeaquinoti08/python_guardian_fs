import socket
import threading

import pytest

from migration_agent.commands import (
    UnknownCommandError,
    dispatch,
    handle_connectivity_check,
    handle_create_folder,
    handle_list_folder,
)


def test_handle_list_folder_lists_entries(tmp_path):
    (tmp_path / "arquivo.txt").write_text("conteudo")
    (tmp_path / "subpasta").mkdir()

    result = handle_list_folder({"path": str(tmp_path)})

    names = {e["name"] for e in result["entries"]}
    assert names == {"arquivo.txt", "subpasta"}
    by_name = {e["name"]: e for e in result["entries"]}
    assert by_name["arquivo.txt"]["is_dir"] is False
    assert by_name["subpasta"]["is_dir"] is True


def test_handle_create_folder_creates_intermediate_dirs(tmp_path):
    target = tmp_path / "a" / "b" / "c"

    result = handle_create_folder({"path": str(target)})

    assert result == {"path": str(target)}
    assert target.is_dir()


def test_handle_create_folder_is_idempotent(tmp_path):
    target = tmp_path / "existente"
    target.mkdir()

    result = handle_create_folder({"path": str(target)})

    assert result == {"path": str(target)}


def test_handle_connectivity_check_reachable():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def accept_once():
        try:
            conn, _ = server.accept()
            conn.close()
        except OSError:
            pass

    threading.Thread(target=accept_once, daemon=True).start()
    try:
        result = handle_connectivity_check({"ip": "127.0.0.1", "port": port, "timeout_seconds": 2})
        assert result["reachable"] is True
        assert "latency_ms" in result
    finally:
        server.close()


def test_handle_connectivity_check_unreachable():
    # porta fechada localmente (não escutando) -> conexão recusada rapidamente
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.close()  # libera a porta sem deixar nada escutando

    result = handle_connectivity_check({"ip": "127.0.0.1", "port": port, "timeout_seconds": 2})

    assert result["reachable"] is False
    assert "error" in result


def test_dispatch_unknown_command_raises():
    with pytest.raises(UnknownCommandError):
        dispatch("algo_inexistente", {})


def test_dispatch_known_command_routes_to_handler(tmp_path):
    result = dispatch("list_folder", {"path": str(tmp_path)})
    assert result["path"] == str(tmp_path)
