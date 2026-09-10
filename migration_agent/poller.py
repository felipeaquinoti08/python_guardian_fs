"""Loop principal: long-poll no Guardian, despacha comando, reporta resultado.

Roda em thread própria (ver service_windows.py / __main__.py) até o evento
`stop_event` ser sinalizado. Erros de rede (Guardian fora do ar, etc.) não
derrubam o processo -- espera com backoff e tenta de novo.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import requests

from .api_client import GuardianClient
from .commands import DEFAULT_HANDLERS, CommandHandler, UnknownCommandError, dispatch
from .config import AgentConfig

logger = logging.getLogger(__name__)

MIN_BACKOFF_SECONDS = 2
MAX_BACKOFF_SECONDS = 60

# Comandos que podem demorar (transferência de arquivo real, issue #107
# Incremento 2) rodam em thread própria -- senão um arquivo grande travaria
# o long-poll inteiro, impedindo o agent de responder a outros comandos
# (list_folder do wizard, connectivity_check, outras transferências) até
# terminar. A concorrência de verdade (quantos `run_transfer` ficam "em
# voo" ao mesmo tempo) é controlada pelo Guardian (max_concurrent_transfers
# por agent), não aqui.
BACKGROUND_COMMAND_TYPES = {"run_transfer"}


class AgentPoller:
    def __init__(
        self,
        config: AgentConfig,
        client: Optional[GuardianClient] = None,
        stop_event: Optional[threading.Event] = None,
        on_activity: Optional[Callable[[str], None]] = None,
        handlers: Optional[dict[str, CommandHandler]] = None,
    ):
        if not config.is_paired:
            raise ValueError("AgentPoller requer um AgentConfig pareado (agent_id + auth_token)")
        self.config = config
        self.client = client or GuardianClient(config.guardian_base_url)
        self.stop_event = stop_event or threading.Event()
        self.on_activity = on_activity or (lambda msg: None)
        self.handlers = handlers if handlers is not None else DEFAULT_HANDLERS
        self._backoff_seconds = MIN_BACKOFF_SECONDS

    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            self._run_one_cycle()

    def _run_one_cycle(self) -> None:
        try:
            command = self.client.long_poll(self.config.agent_id, self.config.auth_token)
            self._backoff_seconds = MIN_BACKOFF_SECONDS
        except requests.RequestException as exc:
            logger.warning("Long-poll falhou (%s) — retry em %ss", exc, self._backoff_seconds)
            self.on_activity(f"Guardian inacessível: {exc}")
            self.stop_event.wait(self._backoff_seconds)
            self._backoff_seconds = min(self._backoff_seconds * 2, MAX_BACKOFF_SECONDS)
            return

        if command is None:
            return  # nada pendente, o próprio long-poll já segurou o tempo de espera

        self.on_activity(f"Comando recebido: {command.type} ({command.command_id})")

        if command.type in BACKGROUND_COMMAND_TYPES:
            threading.Thread(
                target=self._execute_and_report, args=(command,), daemon=True, name=f"transfer-{command.command_id}"
            ).start()
            return

        self._execute_and_report(command)

    def _execute_and_report(self, command) -> None:
        try:
            result = dispatch(command.type, command.payload, self.handlers)
            self.client.send_command_result(self.config.agent_id, self.config.auth_token, command.command_id, ok=True, result=result)
            self.on_activity(f"Comando {command.command_id} concluído")
        except UnknownCommandError as exc:
            logger.error("Comando desconhecido: %s", exc)
            self.client.send_command_result(
                self.config.agent_id, self.config.auth_token, command.command_id, ok=False, error=str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - qualquer falha do handler vira "erro" reportado, nunca derruba o loop/thread
            logger.exception("Falha executando comando %s", command.type)
            self.on_activity(f"Comando {command.command_id} falhou: {exc}")
            try:
                self.client.send_command_result(
                    self.config.agent_id, self.config.auth_token, command.command_id, ok=False, error=str(exc)
                )
            except requests.RequestException:
                logger.exception("Falha ao reportar erro do comando %s pro Guardian", command.command_id)
