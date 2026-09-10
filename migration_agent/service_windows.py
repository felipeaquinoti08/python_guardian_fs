"""Wrapper de Windows Service (pywin32) -- só importável/executável no
Windows. Registrado pelo instalador MSI (ver packaging/BUILD.md).

Uso manual (útil durante o desenvolvimento do instalador):
    python -m migration_agent.service_windows install
    python -m migration_agent.service_windows start
    python -m migration_agent.service_windows stop
    python -m migration_agent.service_windows remove

Quando o Windows SCM inicia o serviço de verdade, ele invoca o `.exe`
registrado SEM argumento nenhum -- é `__main__.py` (`_dispatch_to_scm`)
quem detecta esse caso e delega pra cá, já com logging em arquivo
configurado (ver `%ProgramData%\\GuardianMigrationAgent\\agent.log`).
"""

from __future__ import annotations

import logging

from .agent_runtime import AgentRuntime

try:
    import servicemanager  # type: ignore
    import win32event  # type: ignore
    import win32service  # type: ignore
    import win32serviceutil  # type: ignore
except ImportError as exc:  # pragma: no cover - só acontece fora do Windows
    raise ImportError(
        "migration_agent.service_windows só funciona no Windows (requer pywin32)"
    ) from exc

logger = logging.getLogger(__name__)


class MigrationAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "GuardianMigrationAgent"
    _svc_display_name_ = "Guardian Migration Agent"
    _svc_description_ = (
        "Agent de migracao de fileserver on-premises do Guardian -- long-polling "
        "para o Guardian e transferencia direta agent<->agent."
    )

    def __init__(self, args):
        super().__init__(args)
        try:
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.runtime = AgentRuntime()
        except Exception:
            logger.exception("Falha inicializando MigrationAgentService.__init__")
            self._log_to_event_viewer("Falha ao inicializar o serviço (ver agent.log em %ProgramData%\\GuardianMigrationAgent\\)")
            raise

    def SvcStop(self):
        logger.info("SvcStop chamado -- parando o agent.")
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        try:
            self.runtime.stop()
        except Exception:
            logger.exception("Falha em runtime.stop()")
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        try:
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            logger.info("SvcDoRun iniciado -- subindo threads de fundo (UI local, peer-listener, poller).")
            self.runtime.start_background()
            # Sem isto, o SCM fica esperando confirmacao de que o servico
            # subiu ate estourar o timeout padrao (~30s) e desistir --
            # mesmo com as threads de fundo ja rodando normalmente.
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            logger.info("Threads de fundo no ar. Aguardando sinal de parada.")
            win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
        except Exception:
            logger.exception("Falha fatal em SvcDoRun")
            self._log_to_event_viewer("Falha fatal rodando o serviço (ver agent.log em %ProgramData%\\GuardianMigrationAgent\\)")
            raise

    def _log_to_event_viewer(self, message: str) -> None:
        try:
            servicemanager.LogErrorMsg(f"{self._svc_display_name_}: {message}")
        except Exception:  # noqa: BLE001 - o Event Viewer é só um bônus, nunca pode mascarar o erro original
            pass


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(MigrationAgentService)
