import hashlib
import json
import socket
import struct
import threading

from migration_agent.peer_listener import PendingTokens, make_peer_listener

_HEADER = struct.Struct(">I")


def _send_file(port, job_id, token, relative_path, data):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        handshake = json.dumps(
            {"job_id": job_id, "token": token, "relative_path": relative_path, "size": len(data)}
        ).encode("utf-8")
        sock.sendall(_HEADER.pack(len(handshake)) + handshake)

        ack = sock.recv(2)
        if ack != b"OK":
            return ack, None

        sock.sendall(data)
        response = sock.recv(65536)
        return ack, json.loads(response.decode("utf-8"))


def _run_server(tmp_path, on_status=None):
    pending = PendingTokens()
    server = make_peer_listener("127.0.0.1", 0, pending, on_status=on_status)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, pending, port


def test_valid_token_writes_file_and_returns_matching_hash(tmp_path):
    server, pending, port = _run_server(tmp_path)
    try:
        dest_root = tmp_path / "dest"
        dest_root.mkdir()
        pending.expect("job-1", "token-abc", dest_root)

        data = b"conteudo de teste" * 1000
        ack, response = _send_file(port, "job-1", "token-abc", "subpasta/arquivo.bin", data)

        assert ack == b"OK"
        assert response["ok"] is True
        assert response["sha256"] == hashlib.sha256(data).hexdigest()

        written = dest_root / "subpasta" / "arquivo.bin"
        assert written.read_bytes() == data
    finally:
        server.shutdown()


def test_invalid_token_is_rejected(tmp_path):
    server, pending, port = _run_server(tmp_path)
    try:
        ack, response = _send_file(port, "job-x", "token-errado", "arquivo.bin", b"dados")
        assert ack == b"NO"
        assert response is None
    finally:
        server.shutdown()


def test_token_can_only_be_used_once(tmp_path):
    server, pending, port = _run_server(tmp_path)
    try:
        dest_root = tmp_path / "dest"
        dest_root.mkdir()
        pending.expect("job-2", "token-unico", dest_root)

        _send_file(port, "job-2", "token-unico", "a.bin", b"dados")
        ack, _ = _send_file(port, "job-2", "token-unico", "a.bin", b"dados")

        assert ack == b"NO"
    finally:
        server.shutdown()


def test_valid_transfer_reports_peer_status_during_and_clears_after(tmp_path):
    statuses = []
    server, pending, port = _run_server(tmp_path, on_status=statuses.append)
    try:
        dest_root = tmp_path / "dest"
        dest_root.mkdir()
        pending.expect("job-3", "token-status", dest_root)

        data = b"conteudo" * 500
        ack, response = _send_file(port, "job-3", "token-status", "a.bin", data)

        assert ack == b"OK"
        assert response["ok"] is True
        assert statuses[0] is not None and "127.0.0.1" in statuses[0]
        assert statuses[-1] is None
    finally:
        server.shutdown()


def test_invalid_token_never_reports_peer_status(tmp_path):
    statuses = []
    server, pending, port = _run_server(tmp_path, on_status=statuses.append)
    try:
        _send_file(port, "job-x", "token-errado", "arquivo.bin", b"dados")
        assert statuses == []
    finally:
        server.shutdown()
