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

# Issue #115: intervalo mínimo entre reports de progresso pro Guardian --
# `on_progress` (transfer.py) é chamado a cada chunk (64KB), o que viraria
# milhares de chamadas HTTP numa transferência de 600MB sem esse throttle.
# Sempre reporta a última chamada (100% de verdade), mesmo dentro do
# intervalo.
PROGRESS_REPORT_MIN_INTERVAL_SECONDS = 2.0


class AgentPoller:
    def __init__(
        self,
        config: AgentConfig,
        client: Optional[GuardianClient] = None,
        stop_event: Optional[threading.Event] = None,
        on_activity: Optional[Callable[[str], None]] = None,
        handlers: Optional[dict[str, CommandHandler]] = None,
        on_guardian_status: Optional[Callable[[bool, Optional[str]], None]] = None,
        on_peer_status: Optional[Callable[[Optional[str]], None]] = None,
    ):
        if not config.is_paired:
            raise ValueError("AgentPoller requer um AgentConfig pareado (agent_id + auth_token)")
        self.config = config
        self.client = client or GuardianClient(config.guardian_base_url)
        self.stop_event = stop_event or threading.Event()
        self.on_activity = on_activity or (lambda msg: None)
        self.handlers = handlers if handlers is not None else DEFAULT_HANDLERS
        # Issue #112: aba de configuração/conexão da UI local -- status real
        # da conexão de long-poll (não um "acho que está ok" derivado do
        # activity log) e, quando aplicável, da transferência agent<->agent
        # em andamento no papel de quem ENVIA (push_to_agent). O lado que
        # RECEBE (receive_from_agent) é reportado por peer_listener.py, não
        # aqui -- esse handler só registra a expectativa e retorna na hora
        # (ver commands.py::_run_receive_from_agent), o recebimento de fato
        # acontece depois, de forma assíncrona.
        self.on_guardian_status = on_guardian_status or (lambda connected, error=None: None)
        self.on_peer_status = on_peer_status or (lambda label: None)
        self._backoff_seconds = MIN_BACKOFF_SECONDS

    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            self._run_one_cycle()

    def _run_one_cycle(self) -> None:
        try:
            command = self.client.long_poll(self.config.agent_id, self.config.auth_token)
            self._backoff_seconds = MIN_BACKOFF_SECONDS
            self.on_guardian_status(True)
        except requests.RequestException as exc:
            logger.warning("Long-poll falhou (%s) — retry em %ss", exc, self._backoff_seconds)
            self.on_activity(f"Guardian inacessível: {exc}")
            self.on_guardian_status(False, str(exc))
            self.stop_event.wait(self._backoff_seconds)
            self._backoff_seconds = min(self._backoff_seconds * 2, MAX_BACKOFF_SECONDS)
            return

        if command is None:
            return  # nada pendente, o próprio long-poll já segurou o tempo de espera

        self.on_activity(f"Comando recebido: {self._describe_command(command)}")

        if command.type in BACKGROUND_COMMAND_TYPES:
            threading.Thread(
                target=self._execute_and_report, args=(command,), daemon=True, name=f"transfer-{command.command_id}"
            ).start()
            return

        self._execute_and_report(command)

    def _describe_command(self, command) -> str:
        """Descrição amigável do comando pra UI local/histórico -- issue
        #114: o command_id (UUID interno do Guardian) não diz nada pra
        quem está lendo a tela, o caminho do arquivo/pasta envolvido sim."""
        payload = command.payload or {}

        if command.type == "run_transfer":
            mode = payload.get("mode")
            if mode == "upload_to_cloud":
                return f"run_transfer, enviando \"{payload.get('source_path', '?')}\" para a nuvem"
            if mode == "download_from_cloud":
                return f"run_transfer, baixando da nuvem para \"{payload.get('dest_path', '?')}\""
            if mode == "push_to_agent":
                return f"run_transfer, enviando \"{payload.get('source_path', '?')}\" para outro agent"
            if mode == "receive_from_agent":
                return f"run_transfer, recebendo de outro agent em \"{payload.get('dest_root', '?')}\""
            return "run_transfer"

        if command.type in ("list_folder", "create_folder"):
            return f"{command.type} (\"{payload.get('path', '?')}\")"

        if command.type == "connectivity_check":
            return f"connectivity_check ({payload.get('ip', '?')}:{payload.get('port', '?')})"

        return command.type

    def _peer_label_for(self, command) -> Optional[str]:
        if command.type != "run_transfer":
            return None
        if command.payload.get("mode") != "push_to_agent":
            return None
        dest_ip = command.payload.get("dest_ip", "?")
        dest_port = command.payload.get("dest_port", "?")
        return f"Enviando arquivo para outro agent ({dest_ip}:{dest_port})"

    def _make_progress_reporter(self, command) -> Callable[[int, int], None]:
        """Issue #115: reporta progresso (bytes_done/bytes_total) pro
        Guardian via `POST .../jobs/{job_id}/report`, com throttle -- ver
        PROGRESS_REPORT_MIN_INTERVAL_SECONDS. `job_id` vem do payload do
        comando (todo `run_transfer` carrega, ver
        UniversalMigrationFileJob no lado Guardian); sem ele (payload
        malformado/versão antiga do Guardian) o reporter vira um no-op
        em vez de quebrar a transferência em si."""
        job_id = command.payload.get("job_id")
        last_report_at = [0.0]

        def _report(bytes_done: int, bytes_total: int) -> None:
            if not job_id:
                return
            now = time.monotonic()
            is_final = bytes_total > 0 and bytes_done >= bytes_total
            if not is_final and (now - last_report_at[0]) < PROGRESS_REPORT_MIN_INTERVAL_SECONDS:
                return
            last_report_at[0] = now
            try:
                self.client.report_job(
                    self.config.agent_id,
                    self.config.auth_token,
                    job_id,
                    "progress",
                    {"bytes_done": bytes_done, "bytes_total": bytes_total},
                )
            except Exception:
                logger.debug("Falha ao reportar progresso da transferência (não crítico)", exc_info=True)

        return _report

    def _execute_and_report(self, command) -> None:
        description = self._describe_command(command)
        peer_label = self._peer_label_for(command)
        if peer_label:
            self.on_peer_status(peer_label)

        handlers = self.handlers
        if command.type == "run_transfer" and "run_transfer" in self.handlers:
            progress_reporter = self._make_progress_reporter(command)
            base_handler = self.handlers["run_transfer"]
            handlers = {
                **self.handlers,
                "run_transfer": lambda payload: base_handler(payload, on_progress=progress_reporter),
            }

        try:
            result = dispatch(command.type, command.payload, handlers)
            self.client.send_command_result(self.config.agent_id, self.config.auth_token, command.command_id, ok=True, result=result)
            self.on_activity(f"Comando concluído: {description}")
        except UnknownCommandError as exc:
            logger.error("Comando desconhecido: %s", exc)
            self.client.send_command_result(
                self.config.agent_id, self.config.auth_token, command.command_id, ok=False, error=str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - qualquer falha do handler vira "erro" reportado, nunca derruba o loop/thread
            logger.exception("Falha executando comando %s", command.type)
            self.on_activity(f"Comando falhou: {description} -- {exc}")
            try:
                self.client.send_command_result(
                    self.config.agent_id, self.config.auth_token, command.command_id, ok=False, error=str(exc)
                )
            except requests.RequestException:
                logger.exception("Falha ao reportar erro do comando %s pro Guardian", command.command_id)
        finally:
            if peer_label:
                self.on_peer_status(None)
