"""UI web local do agent (porta aleatória, bind só em 127.0.0.1).

Propósito: (1) colar o token de pareamento gerado no Guardian no primeiro
start, (2) mostrar status/health local (última atividade, se está pareado).
Nenhuma configuração de negócio (compartilhamentos, credenciais de cópia)
fica aqui -- isso é cadastrado no Guardian (issue #106).

Protegida por login (usuário/senha, issue #107): mesmo só escutando em
127.0.0.1, qualquer outro processo/usuário local na mesma máquina Windows
conseguiria abrir essa porta antes -- a senha padrão gerada no primeiro
start (ver config.py::generate_default_password) dá um mínimo de proteção,
com troca obrigatória incentivada por um aviso na tela até o usuário trocar.
"""

from __future__ import annotations

import base64
import datetime
import html
import json
import logging
import secrets
import socketserver
import time
from http import cookies as http_cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlsplit

from ._version import AGENT_VERSION
from .api_client import GuardianClient, PairingError
from .config import AgentConfig, ConfigStore, verify_password
from .runtime_state import RuntimeState
from .self_update import apply_update, check_for_update
from .webassets import GUARDIAN_LOGO_PNG

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "guardian_agent_session"
SESSION_TTL_SECONDS = 12 * 60 * 60  # 12h -- reiniciar o serviço tambem derruba a sessao (store em memoria)
MIN_PASSWORD_LENGTH = 8


# ---------------------------------------------------------------------------
# Tema visual (mesma identidade do Guardian: fundo escuro em gradiente
# indigo/navy, card branco flutuante, logo -- ver resources/views/auth/
# login.blade.php no repo principal). CSS inline de proposito: a UI local
# roda embutida num .exe congelado, sem servidor de assets estaticos, e
# pode rodar num fileserver sem acesso a internet (sem CDN de fontes).
# ---------------------------------------------------------------------------

_PAGE_STYLE = """
    :root { color-scheme: light; }
    * { box-sizing: border-box; }
    body {
        margin: 0;
        min-height: 100vh;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, Arial, sans-serif;
        background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 40%, #0f172a 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 24px;
    }
    .card {
        width: 100%;
        max-width: 480px;
        background: rgba(255, 255, 255, 0.97);
        border-radius: 20px;
        box-shadow: 0 32px 80px rgba(0,0,0,0.35), 0 0 0 1px rgba(255,255,255,0.1);
        padding: 40px;
    }
    .card.wide { max-width: 640px; }
    .brand { display: flex; flex-direction: column; align-items: center; text-align: center; margin-bottom: 28px; }
    .brand img { height: 48px; object-fit: contain; margin-bottom: 4px; }
    .brand .subtitle { color: #64748b; font-size: 13px; margin-top: 2px; }
    h1 { font-size: 18px; font-weight: 700; color: #0f172a; margin: 0 0 4px; }
    h2 { font-size: 14px; font-weight: 700; color: #0f172a; margin: 24px 0 12px; }
    p { color: #334155; font-size: 14px; line-height: 1.6; }
    label { display: block; font-size: 13px; font-weight: 600; color: #334155; margin-bottom: 6px; }
    input[type=text], input[type=password], input[type=url] {
        width: 100%;
        padding: 11px 14px;
        border: 1px solid #cbd5e1;
        border-radius: 10px;
        font-size: 14px;
        margin-bottom: 16px;
        outline: none;
        transition: border-color .15s ease, box-shadow .15s ease;
    }
    input[type=text]:focus, input[type=password]:focus, input[type=url]:focus {
        border-color: #6366f1;
        box-shadow: 0 0 0 3px rgba(99,102,241,0.15);
    }
    button, .btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        padding: 12px 20px;
        background: linear-gradient(135deg, #6366f1, #4f46e5);
        color: #fff;
        border: none;
        border-radius: 10px;
        font-size: 14px;
        font-weight: 600;
        cursor: pointer;
        text-decoration: none;
        transition: transform .15s cubic-bezier(.165,.84,.44,1), box-shadow .15s ease;
        box-shadow: 0 4px 15px rgba(79,70,229,0.3);
    }
    button:hover, .btn:hover { transform: translateY(-1px); box-shadow: 0 8px 25px rgba(79,70,229,0.4); }
    .btn-secondary {
        background: #f1f5f9;
        color: #334155;
        box-shadow: none;
    }
    .btn-secondary:hover { box-shadow: 0 4px 15px rgba(0,0,0,0.08); }
    .row { display: flex; gap: 12px; margin-top: 8px; }
    .row > * { flex: 1; }
    .alert {
        border-radius: 12px;
        padding: 12px 16px;
        font-size: 13px;
        margin-bottom: 20px;
        line-height: 1.5;
    }
    .alert-error { background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; }
    .alert-success { background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d; }
    .alert-warning { background: #fffbeb; border: 1px solid #fde68a; color: #92400e; }
    .alert code {
        background: rgba(0,0,0,0.06);
        padding: 2px 6px;
        border-radius: 5px;
        font-size: 12px;
    }
    .meta-list { list-style: none; padding: 0; margin: 0 0 8px; }
    .meta-list li { font-size: 13px; color: #334155; padding: 6px 0; border-bottom: 1px solid #f1f5f9; }
    .meta-list li:last-child { border-bottom: none; }
    .meta-list b { color: #0f172a; }
    .activity-table-wrap { max-height: 320px; overflow-y: auto; overflow-x: auto; border: 1px solid #f1f5f9; border-radius: 12px; }
    .activity-table { width: 100%; border-collapse: collapse; font-size: 13px; }
    .activity-table thead th {
        position: sticky;
        top: 0;
        background: #f8fafc;
        text-align: left;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: #64748b;
        padding: 8px 12px;
        border-bottom: 1px solid #e2e8f0;
    }
    .activity-table td { padding: 9px 12px; border-bottom: 1px solid #f1f5f9; color: #334155; vertical-align: top; }
    .activity-table tbody tr:last-child td { border-bottom: none; }
    .activity-table tbody tr:hover { background: #f8fafc; }
    .activity-table td.activity-dot { width: 10px; padding-right: 0; }
    .activity-table td.activity-time { white-space: nowrap; color: #64748b; font-variant-numeric: tabular-nums; }
    .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; }
    .dot-success { background: #22c55e; }
    .dot-error { background: #ef4444; }
    .dot-info { background: #94a3b8; }
    .activity-empty { color: #94a3b8; font-size: 13px; }
    .top-links { display: flex; justify-content: flex-end; gap: 16px; margin-bottom: 12px; }
    .top-links a { color: #64748b; font-size: 12px; text-decoration: none; }
    .top-links a:hover { color: #4f46e5; text-decoration: underline; }
    .footer-note { text-align: center; color: #94a3b8; font-size: 11px; margin-top: 24px; }
"""


def _page_shell(title: str, body: str, *, wide: bool = False) -> str:
    logo_b64 = base64.b64encode(GUARDIAN_LOGO_PNG).decode("ascii")
    card_class = "card wide" if wide else "card"
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — Guardian Migration Agent</title>
<style>{_PAGE_STYLE}</style>
</head>
<body>
<div class="{card_class}">
    <div class="brand">
        <img src="data:image/png;base64,{logo_b64}" alt="Guardian">
        <div class="subtitle">Migration Agent</div>
    </div>
    {body}
</div>
</body>
</html>"""


def _default_password_alert(cfg: AgentConfig) -> str:
    if not cfg.ui_password_is_default or not cfg.ui_default_password:
        return ""
    return f"""
    <div class="alert alert-warning">
        <b>Senha padrão ainda não foi trocada.</b><br>
        Usuário: <code>{html.escape(cfg.ui_username)}</code> &nbsp;
        Senha: <code>{html.escape(cfg.ui_default_password)}</code><br>
        Troque assim que possível em <a href="/change-password">Trocar senha</a>.
    </div>
    """


def _render_login_page(cfg: AgentConfig, error: str = "") -> str:
    error_html = f'<div class="alert alert-error">{html.escape(error)}</div>' if error else ""
    body = f"""
    <h1>Entrar</h1>
    <p>Acesso local ao Guardian Migration Agent.</p>
    {error_html}
    {_default_password_alert(cfg)}
    <form method="post" action="/login">
        <label>Usuário</label>
        <input type="text" name="username" autocomplete="username" required autofocus>
        <label>Senha</label>
        <input type="password" name="password" autocomplete="current-password" required>
        <button type="submit">Entrar</button>
    </form>
    <p class="footer-note">Acesso restrito a esta máquina (127.0.0.1).</p>
    """
    return _page_shell("Entrar", body)


def _render_change_password_page(cfg: AgentConfig, error: str = "", success: str = "") -> str:
    error_html = f'<div class="alert alert-error">{html.escape(error)}</div>' if error else ""
    success_html = f'<div class="alert alert-success">{html.escape(success)}</div>' if success else ""
    body = f"""
    <div class="top-links"><a href="/">&larr; Voltar</a></div>
    <h1>Trocar senha</h1>
    <p>Usuário: <b>{html.escape(cfg.ui_username)}</b></p>
    {error_html}
    {success_html}
    {_default_password_alert(cfg)}
    <form method="post" action="/change-password">
        <label>Senha atual</label>
        <input type="password" name="current_password" autocomplete="current-password" required>
        <label>Nova senha (mínimo {MIN_PASSWORD_LENGTH} caracteres)</label>
        <input type="password" name="new_password" autocomplete="new-password" required minlength="{MIN_PASSWORD_LENGTH}">
        <label>Confirmar nova senha</label>
        <input type="password" name="new_password_confirm" autocomplete="new-password" required minlength="{MIN_PASSWORD_LENGTH}">
        <button type="submit">Salvar nova senha</button>
    </form>
    """
    return _page_shell("Trocar senha", body)


def _classify_activity(message: str) -> str:
    """Heurística simples (so pra colorir a linha na tabela, nao muda o
    dado em si) baseada nas mensagens que o proprio agent gera -- ver
    agent_runtime.py/poller.py/status_server.py (todo lugar que chama
    state.record)."""
    lowered = message.lower()
    if any(term in lowered for term in ("falh", "erro", "inválid", "invalid", "inacessível", "inacessivel")):
        return "error"
    if any(term in lowered for term in ("sucesso", "concluíd", "concluid", "pareado", "disponível", "disponivel")):
        return "success"
    return "info"


def _render_activity_table(entries) -> str:
    if not entries:
        return '<p class="activity-empty">Nenhuma atividade ainda.</p>'

    rows = []
    for entry in entries:
        when = datetime.datetime.fromtimestamp(entry.timestamp).strftime("%d/%m/%Y %H:%M:%S")
        kind = _classify_activity(entry.message)
        rows.append(
            "<tr>"
            f'<td class="activity-dot"><span class="dot dot-{kind}"></span></td>'
            f'<td class="activity-time">{html.escape(when)}</td>'
            f"<td>{html.escape(entry.message)}</td>"
            "</tr>"
        )

    return (
        '<div class="activity-table-wrap"><table class="activity-table">'
        "<thead><tr><th></th><th>Horário</th><th>Evento</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _render_update_section(query: dict) -> str:
    status = (query.get("update_status") or [""])[0]
    version = (query.get("update_version") or [""])[0]
    error = (query.get("update_error") or [""])[0]

    banner = ""
    if status == "checking_error":
        banner = f'<div class="alert alert-error">Não foi possível verificar atualizações: {html.escape(error)}</div>'
    elif status == "up_to_date":
        banner = '<div class="alert alert-success">Você já está na versão mais recente.</div>'
    elif status == "available":
        banner = (
            f'<div class="alert alert-warning">Nova versão disponível: <b>{html.escape(version)}</b>.'
            '<form method="post" action="/update/apply" style="margin-top:10px;">'
            "<button type=\"submit\">Atualizar agora</button></form></div>"
        )
    elif status == "applying":
        banner = (
            '<div class="alert alert-success">Atualização iniciada! O serviço vai reiniciar sozinho em '
            "instantes -- esta página pode ficar temporariamente indisponível durante a troca.</div>"
        )
    elif status == "apply_error":
        banner = f'<div class="alert alert-error">Falha ao aplicar a atualização: {html.escape(error)}</div>'

    return f"""
    {banner}
    <form method="post" action="/update/check">
        <button type="submit" class="btn-secondary">Verificar atualização</button>
    </form>
    """


def _render_main_page(config: AgentConfig, state: RuntimeState, query: Optional[dict] = None) -> str:
    query = query or {}
    activity_html = _render_activity_table(list(reversed(state.recent())))

    top_links = '<div class="top-links"><a href="/change-password">Trocar senha</a><a href="/logout">Sair</a></div>'

    if config.is_paired:
        body = f"""
        {top_links}
        <h1>Guardian Migration Agent — pareado</h1>
        {_default_password_alert(config)}
        <ul class="meta-list">
            <li><b>Cliente:</b> {html.escape(config.cliente_nome or '?')}</li>
            <li><b>Agent ID:</b> {html.escape(config.agent_id or '?')}</li>
            <li><b>Guardian:</b> {html.escape(config.guardian_base_url)}</li>
            <li><b>Versão instalada:</b> {html.escape(AGENT_VERSION)}</li>
        </ul>
        <h2>Atualização</h2>
        {_render_update_section(query)}
        <h2>Atividade recente</h2>
        {activity_html}
        """
    else:
        body = f"""
        {top_links}
        <h1>Pareamento</h1>
        {_default_password_alert(config)}
        <p>Cole abaixo a URL do Guardian e o token de pareamento gerado na
        tela de Agents do cliente (módulo Migração).</p>
        <form method="post" action="/pair">
            <label>URL do Guardian</label>
            <input type="url" name="guardian_base_url" placeholder="https://guardian.exemplo.com" required>
            <label>Token de pareamento</label>
            <input type="text" name="pairing_token" required>
            <button type="submit">Parear</button>
        </form>
        """

    return _page_shell("Status", body, wide=True)


def make_handler(config_store: ConfigStore, state: RuntimeState):
    sessions: dict[str, float] = {}

    def _new_session() -> str:
        token = secrets.token_urlsafe(32)
        sessions[token] = time.time() + SESSION_TTL_SECONDS
        return token

    def _session_token_from_cookie(cookie_header: str) -> str:
        if not cookie_header:
            return ""
        jar = http_cookies.SimpleCookie()
        try:
            jar.load(cookie_header)
        except http_cookies.CookieError:
            return ""
        morsel = jar.get(SESSION_COOKIE_NAME)
        return morsel.value if morsel else ""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silencia log padrão barulhento
            logger.debug("status_server: " + fmt, *args)

        # -- autenticação -------------------------------------------------

        def _is_authenticated(self) -> bool:
            token = _session_token_from_cookie(self.headers.get("Cookie", ""))
            if not token:
                return False
            expiry = sessions.get(token)
            if expiry is None or expiry < time.time():
                sessions.pop(token, None)
                return False
            return True

        def _require_auth(self) -> bool:
            if self._is_authenticated():
                return True
            self.send_response(303)
            self.send_header("Location", "/login")
            self.end_headers()
            return False

        def _read_form(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            fields = parse_qs(body)
            return {k: v[0] for k, v in fields.items()}

        # -- roteamento -----------------------------------------------------

        def do_GET(self):
            if self.path == "/login":
                if self._is_authenticated():
                    self._redirect("/")
                    return
                cfg = config_store.load()
                self._send_html(200, _render_login_page(cfg))
                return

            if self.path == "/logout":
                token = _session_token_from_cookie(self.headers.get("Cookie", ""))
                sessions.pop(token, None)
                self._redirect("/login", clear_cookie=True)
                return

            if self.path == "/change-password":
                if not self._require_auth():
                    return
                cfg = config_store.load()
                self._send_html(200, _render_change_password_page(cfg))
                return

            if self.path == "/status.json":
                if not self._require_auth():
                    return
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

            if not self._require_auth():
                return
            cfg = config_store.load()
            query = parse_qs(urlsplit(self.path).query)
            self._send_html(200, _render_main_page(cfg, state, query=query))

        def do_POST(self):
            if self.path == "/login":
                cfg = config_store.load()
                fields = self._read_form()
                username = (fields.get("username") or "").strip()
                password = fields.get("password") or ""
                if username == cfg.ui_username and verify_password(password, cfg.ui_password_hash):
                    token = _new_session()
                    state.record(f"Login na UI local (usuario {username!r})")
                    self._redirect("/", set_cookie=token)
                    return
                state.record("Tentativa de login com credenciais invalidas na UI local")
                self._send_html(401, _render_login_page(cfg, error="Usuário ou senha inválidos."))
                return

            if self.path == "/change-password":
                if not self._require_auth():
                    return
                cfg = config_store.load()
                fields = self._read_form()
                current_password = fields.get("current_password") or ""
                new_password = fields.get("new_password") or ""
                new_password_confirm = fields.get("new_password_confirm") or ""

                if not verify_password(current_password, cfg.ui_password_hash):
                    self._send_html(400, _render_change_password_page(cfg, error="Senha atual incorreta."))
                    return
                if len(new_password) < MIN_PASSWORD_LENGTH:
                    self._send_html(
                        400,
                        _render_change_password_page(
                            cfg, error=f"A nova senha precisa ter pelo menos {MIN_PASSWORD_LENGTH} caracteres."
                        ),
                    )
                    return
                if new_password != new_password_confirm:
                    self._send_html(400, _render_change_password_page(cfg, error="As senhas não coincidem."))
                    return

                config_store.change_ui_password(new_password)
                sessions.clear()  # forca re-login em todas as sessoes ativas
                state.record("Senha da UI local alterada")
                self._redirect("/login", clear_cookie=True)
                return

            if self.path == "/pair":
                if not self._require_auth():
                    return
                fields = self._read_form()
                guardian_base_url = (fields.get("guardian_base_url") or "").strip()
                pairing_token = (fields.get("pairing_token") or "").strip()

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

                self._redirect("/")
                return

            if self.path == "/update/check":
                if not self._require_auth():
                    return
                cfg = config_store.load()
                if not cfg.is_paired:
                    self._redirect("/?" + urlencode({"update_status": "checking_error", "update_error": "Agent não pareado."}))
                    return
                try:
                    client = GuardianClient(cfg.guardian_base_url)
                    latest = check_for_update(client, cfg)
                except Exception as exc:  # noqa: BLE001 -- qualquer falha de rede/HTTP vira aviso, nao 500
                    self._redirect("/?" + urlencode({"update_status": "checking_error", "update_error": str(exc)}))
                    return

                if latest:
                    state.record(f"Nova versão disponível: {latest}")
                    self._redirect("/?" + urlencode({"update_status": "available", "update_version": latest}))
                else:
                    self._redirect("/?" + urlencode({"update_status": "up_to_date"}))
                return

            if self.path == "/update/apply":
                if not self._require_auth():
                    return
                cfg = config_store.load()
                if not cfg.is_paired:
                    self._redirect("/?" + urlencode({"update_status": "apply_error", "update_error": "Agent não pareado."}))
                    return
                try:
                    client = GuardianClient(cfg.guardian_base_url)
                    apply_update(client, cfg)
                except Exception as exc:  # noqa: BLE001 -- qualquer falha (rede, download, msiexec) vira aviso, nao 500
                    state.record(f"Falha ao aplicar atualização: {exc}")
                    self._redirect("/?" + urlencode({"update_status": "apply_error", "update_error": str(exc)}))
                    return

                state.record("Atualização disparada -- serviço vai reiniciar em instantes")
                self._redirect("/?" + urlencode({"update_status": "applying"}))
                return

            self._send_html(404, "not found")

        # -- helpers de resposta --------------------------------------------

        def _redirect(self, location: str, *, set_cookie: str = "", clear_cookie: bool = False):
            self.send_response(303)
            self.send_header("Location", location)
            if set_cookie:
                cookie = http_cookies.SimpleCookie()
                cookie[SESSION_COOKIE_NAME] = set_cookie
                cookie[SESSION_COOKIE_NAME]["path"] = "/"
                cookie[SESSION_COOKIE_NAME]["httponly"] = True
                cookie[SESSION_COOKIE_NAME]["samesite"] = "Lax"
                cookie[SESSION_COOKIE_NAME]["max-age"] = SESSION_TTL_SECONDS
                self.send_header("Set-Cookie", cookie[SESSION_COOKIE_NAME].OutputString())
            if clear_cookie:
                self.send_header(
                    "Set-Cookie", f"{SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
                )
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


class _LocalOnlyHTTPServer(ThreadingHTTPServer):
    """Evita o `socket.getfqdn()` que `HTTPServer.server_bind()` roda por
    padrão -- esse lookup de DNS reverso pode travar por dezenas de
    segundos em ambientes corporativos com DNS lento/mal configurado
    (issue #107: essa era a causa real do timeout de ~30s do SCM ao
    iniciar o Windows Service -- não tinha nada a ver com onefile/onedir
    nem com o resto do empacotamento, só apareceu porque o `selftest`
    nunca chama `start_background()`/liga este servidor). Como só
    escutamos em 127.0.0.1, `server_name` não precisa ser um FQDN de
    verdade."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def make_server(config_store: ConfigStore, state: RuntimeState, port: int) -> ThreadingHTTPServer:
    handler_cls = make_handler(config_store, state)
    return _LocalOnlyHTTPServer(("127.0.0.1", port), handler_cls)
