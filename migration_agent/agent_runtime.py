"""Orquestra as 3 threads de fundo do agent: UI local, listener de peer
(agent<->agent) e o loop de long-poll com o Guardian. Usado tanto pelo modo
de execução em foreground (dev/teste, ver __main__.py) quanto pelo Windows
Service (service_windows.py) -- a lógica de wiring é a mesma nos dois.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from .api_client import GuardianClient
from .commands import DEFAULT_HANDLERS, make_run_transfer_handler
from .config import ConfigStore
from .peer_listener import PendingTokens, make_peer_listener
from .poller import AgentPoller
from .runtime_state import RuntimeState, classify_activity
from .status_server import make_server

logger = logging.getLogger(__name__)

POLL_RETRY_WHILE_UNPAIRED_SECONDS = 5

# Issue #113: mensagens que NAO viram evento operacional no histórico de
# 30 dias do Guardian -- comandos individuais (list_folder,
# connectivity_check, e principalmente run_transfer) já geram seu próprio
# registro do lado do Guardian sem precisar de uma chamada de rede extra
# do agent por comando (ver UniversalMigrationFileJob::recordAgentTransferEvents
# pra transferências; os outros tipos de comando não têm valor de
# auditoria de 30 dias, só ruído de debug local).
_EVENT_REPORT_SKIP_PREFIXES = ("Comando ",)

# Issue #113: quantos eventos operacionais ficam esperando reenvio se o
# Guardian estiver inacessível no momento do report -- limite pra não
# crescer sem parar numa queda longa (favorece os eventos mais recentes,
# descarta os mais antigos quando a fila enche). Fica só em memória (igual
# ao resto do RuntimeState) -- não sobrevive a um restart do serviço, mas
# sobrevive a quedas de conexão com o Guardian enquanto o processo roda.
_MAX_PENDING_EVENTS = 200


class AgentRuntime:
    def __init__(self, state_dir: Optional[Path] = None):
        self.config_store = ConfigStore(state_dir)
        self.state = RuntimeState(on_record=self._report_event_async)
        self.pending_tokens = PendingTokens()
        self.handlers = DEFAULT_HANDLERS
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._http_server = None
        self._peer_server = None
        self._pending_events_lock = threading.Lock()
        self._pending_events: collections.deque = collections.deque(maxlen=_MAX_PENDING_EVENTS)

    def start_background(self) -> None:
        # Issue #107: instrumentado com timestamps -- builds 11/14/19/20
        # mostraram o SCM travando ~30-33s no start do servico (Error 1920)
        # mesmo depois de eliminar o socket.getfqdn() do status_server
        # (build 20), com o MESMO atraso de antes. Ou seja, tem outro ponto
        # bloqueando por tempo parecido, ainda nao identificado. Em vez de
        # seguir advinhando, cada etapa abaixo loga quanto tempo levou --
        # o proximo teste real aponta exatamente qual linha e a culpada.
        t0 = time.monotonic()

        def _mark(label: str) -> None:
            logger.info("start_background: %s (%.2fs desde o inicio)", label, time.monotonic() - t0)

        cfg = self.config_store.load()
        _mark("config carregado")

        self._http_server = make_server(self.config_store, self.state, cfg.local_ui_port)
        _mark("http_server criado (bind feito)")
        self._spawn(self._http_server.serve_forever, "status-server")
        _mark("thread status-server iniciada")
        self.state.record(f"UI local disponível em http://127.0.0.1:{cfg.local_ui_port}")

        self._peer_server = make_peer_listener(
            "0.0.0.0", cfg.peer_listener_port, self.pending_tokens, on_status=self.state.set_peer_status
        )
        _mark("peer_server criado (bind feito)")
        self._spawn(self._peer_server.serve_forever, "peer-listener")
        _mark("thread peer-listener iniciada")

        # run_transfer no modo "receive_from_agent" precisa do PendingTokens
        # de verdade deste processo (o mesmo que o peer-listener acima usa)
        # -- por isso não dá pra usar commands.DEFAULT_HANDLERS direto.
        self.handlers = {**DEFAULT_HANDLERS, "run_transfer": make_run_transfer_handler(self.pending_tokens)}

        self._spawn(self._poll_loop, "poller")
        _mark("thread poller iniciada -- start_background concluido")

    def start_foreground(self) -> None:
        self.start_background()
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        self._stop_event.set()
        if self._http_server is not None:
            self._http_server.shutdown()
        if self._peer_server is not None:
            self._peer_server.shutdown()

    def _spawn(self, target, name: str) -> None:
        thread = threading.Thread(target=target, daemon=True, name=name)
        thread.start()
        self._threads.append(thread)

    def _report_event_async(self, message: str) -> None:
        """Issue #113: reporta um evento operacional pro histórico de 30
        dias do Guardian, best-effort. Chamado de dentro de
        RuntimeState.record() (pode vir de qualquer thread -- handler HTTP
        da UI local, poller, peer-listener), por isso dispara uma thread
        própria em vez de bloquear quem chamou record() com uma requisição
        de rede. Se a chamada falhar (Guardian fora do ar, etc.), o evento
        entra na fila de retry em vez de ser descartado -- ver
        _flush_pending_events(), chamado a cada ciclo de long-poll
        bem-sucedido."""
        if any(message.startswith(prefix) for prefix in _EVENT_REPORT_SKIP_PREFIXES):
            return

        cfg = self.config_store.load()
        if not cfg.is_paired:
            return

        status = classify_activity(message)

        def _send() -> None:
            try:
                client = GuardianClient(cfg.guardian_base_url)
                client.report_event(cfg.agent_id, cfg.auth_token, "operational", status, message)
            except Exception:
                logger.debug("Falha ao reportar evento operacional pro Guardian -- entrando na fila de retry", exc_info=True)
                with self._pending_events_lock:
                    self._pending_events.append((status, message))

        threading.Thread(target=_send, daemon=True, name="report-event").start()

    def _flush_pending_events(self) -> None:
        """Reenvia eventos operacionais que falharam antes (fila em
        memória, ver _report_event_async). Chamado a cada ciclo de
        long-poll bem-sucedido (on_guardian_status(True) no poller) --
        na prática, tenta de novo a cada ~25s enquanto o Guardian estiver
        acessível, até esvaziar a fila."""
        with self._pending_events_lock:
            if not self._pending_events:
                return
            pending = list(self._pending_events)
            self._pending_events.clear()

        def _flush() -> None:
            cfg = self.config_store.load()
            if not cfg.is_paired:
                with self._pending_events_lock:
                    self._pending_events.extend(pending)
                return

            client = GuardianClient(cfg.guardian_base_url)
            remaining = []
            for status, message in pending:
                try:
                    client.report_event(cfg.agent_id, cfg.auth_token, "operational", status, message)
                except Exception:
                    remaining.append((status, message))

            if remaining:
                logger.debug("Ainda %d evento(s) pendente(s) apos tentativa de flush", len(remaining))
                with self._pending_events_lock:
                    self._pending_events.extend(remaining)

        threading.Thread(target=_flush, daemon=True, name="flush-pending-events").start()

    def _on_guardian_status(self, connected: bool, error: Optional[str] = None) -> None:
        self.state.set_guardian_status(connected, error)
        if connected:
            self._flush_pending_events()

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            cfg = self.config_store.load()
            if not cfg.is_paired:
                self.state.record("Aguardando pareamento (acesse a UI local)")
                self._stop_event.wait(POLL_RETRY_WHILE_UNPAIRED_SECONDS)
                continue

            client = GuardianClient(cfg.guardian_base_url)
            poller = AgentPoller(
                cfg,
                client=client,
                stop_event=self._stop_event,
                on_activity=self.state.record,
                handlers=self.handlers,
                on_guardian_status=self._on_guardian_status,
                on_peer_status=self.state.set_peer_status,
            )
            poller.run_forever()
