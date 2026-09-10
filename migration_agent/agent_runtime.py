"""Orquestra as 3 threads de fundo do agent: UI local, listener de peer
(agent<->agent) e o loop de long-poll com o Guardian. Usado tanto pelo modo
de execução em foreground (dev/teste, ver __main__.py) quanto pelo Windows
Service (service_windows.py) -- a lógica de wiring é a mesma nos dois.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from .api_client import GuardianClient
from .commands import DEFAULT_HANDLERS, make_run_transfer_handler
from .config import ConfigStore
from .peer_listener import PendingTokens, make_peer_listener
from .poller import AgentPoller
from .runtime_state import RuntimeState
from .status_server import make_server

logger = logging.getLogger(__name__)

POLL_RETRY_WHILE_UNPAIRED_SECONDS = 5


class AgentRuntime:
    def __init__(self, state_dir: Optional[Path] = None):
        self.config_store = ConfigStore(state_dir)
        self.state = RuntimeState()
        self.pending_tokens = PendingTokens()
        self.handlers = DEFAULT_HANDLERS
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._http_server = None
        self._peer_server = None

    def start_background(self) -> None:
        cfg = self.config_store.load()

        self._http_server = make_server(self.config_store, self.state, cfg.local_ui_port)
        self._spawn(self._http_server.serve_forever, "status-server")
        self.state.record(f"UI local disponível em http://127.0.0.1:{cfg.local_ui_port}")

        self._peer_server = make_peer_listener("0.0.0.0", cfg.peer_listener_port, self.pending_tokens)
        self._spawn(self._peer_server.serve_forever, "peer-listener")

        # run_transfer no modo "receive_from_agent" precisa do PendingTokens
        # de verdade deste processo (o mesmo que o peer-listener acima usa)
        # -- por isso não dá pra usar commands.DEFAULT_HANDLERS direto.
        self.handlers = {**DEFAULT_HANDLERS, "run_transfer": make_run_transfer_handler(self.pending_tokens)}

        self._spawn(self._poll_loop, "poller")

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

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            cfg = self.config_store.load()
            if not cfg.is_paired:
                self.state.record("Aguardando pareamento (acesse a UI local)")
                self._stop_event.wait(POLL_RETRY_WHILE_UNPAIRED_SECONDS)
                continue

            client = GuardianClient(cfg.guardian_base_url)
            poller = AgentPoller(
                cfg, client=client, stop_event=self._stop_event, on_activity=self.state.record, handlers=self.handlers
            )
            poller.run_forever()
