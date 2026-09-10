"""UI web local do agent (porta aleatória, bind só em 127.0.0.1).

Único propósito: (1) colar o token de pareamento gerado no Guardian no
primeiro start, (2) mostrar status/health local (última atividade, se está
pareado). Nenhuma configuração de negócio (compartilhamentos, credenciais de
cópia) fica aqui — isso é cadastrado no Guardian (issue #106).
"""

from __future__ import annotations

import html
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from .api_client import GuardianClient, PairingError
from .config import AgentConfig, ConfigStore
from .runtime_state import RuntimeState

logger = logging.getLogger(__name__)


def _render_page(config: AgentConfig, state: RuntimeState) -> str:
    activity_html = "".join(
        f"<li><code>{html.escape(str(e.timestamp))}</code> — {html.escape(e.message)}</li>" for e in reversed(state.recent())
    ) or "<li>(sem atividade ainda)</li>"

    if config.is_paired:
        body = f"""
        <h1>Guardian Migration Agent — pareado</h1>
        <p><b>Cliente:</b> {html.escape(config.cliente_nome or '?')}</p>
        <p><b>Agent ID:</b> {html.escape(config.agent_id or '?')}</p>
        <p><b>Guardian:</b> {html.escape(config.guardian_base_url)}</p>
        <h2>Atividade recente</h2>
        <ul>{activity_html}</ul>
        """
    else:
        body = """
        <h1>Guardian Migration Agent — pareamento</h1>
        <p>Cole abaixo a URL do Guardian e o token de pareamento gerado na
        tela de Agents do cliente (módulo Migração).</p>
        <form method="post" action="/pair">
            <label>URL do Guardian<br><input name="guardian_base_url" placeholder="https://guardian.exemplo.com" size="50" required></label><br><br>
            <label>Token de pareamento<br><input name="pairing_token" size="50" required></label><br><br>
            <button type="submit">Parear</button>
        </form>
        """

    return f"<!doctype html><html><head><meta charset='utf-8'><title>Guardian Migration Agent</title></head><body>{body}</body></html>"


def make_handler(config_store: ConfigStore, state: RuntimeState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silencia log padrão barulhento
            logger.debug("status_server: " + fmt, *args)

        def do_GET(self):
            if self.path == "/status.json":
                cfg = config_store.load()
                payload = {
                    "paired": cfg.is_paired,
                    "agent_id": cfg.agent_id,
                    "cliente_nome": cfg.cliente_nome,
                    "guardian_base_url": cfg.guardian_base_url,
                    "recent_activity": [{"timestamp": e.timestamp, "message": e.message} for e in state.recent()],
                }
                self._send_json(200, payload)
                return

            cfg = config_store.load()
            self._send_html(200, _render_page(cfg, state))

        def do_POST(self):
            if self.path != "/pair":
                self._send_html(404, "not found")
                return

            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            fields = parse_qs(body)
            guardian_base_url = (fields.get("guardian_base_url") or [""])[0].strip()
            pairing_token = (fields.get("pairing_token") or [""])[0].strip()

            if not guardian_base_url or not pairing_token:
                self._send_html(400, "URL do Guardian e token são obrigatórios")
                return

            try:
                client = GuardianClient(guardian_base_url)
                result = client.pair(pairing_token)
            except PairingError as exc:
                state.record(f"Falha no pareamento: {exc}")
                self._send_html(400, html.escape(str(exc)))
                return

            cfg = config_store.load()
            cfg.guardian_base_url = guardian_base_url
            cfg.agent_id = result["agent_id"]
            cfg.auth_token = result["auth_token"]
            cfg.cliente_nome = result.get("cliente_nome")
            config_store.save(cfg)
            state.record(f"Pareado com sucesso ao cliente {cfg.cliente_nome!r}")

            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

        def _send_html(self, status: int, body: str):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, status: int, payload: dict):
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def make_server(config_store: ConfigStore, state: RuntimeState, port: int) -> ThreadingHTTPServer:
    handler_cls = make_handler(config_store, state)
    return ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
