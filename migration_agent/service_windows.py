"""Wrapper de Windows Service (pywin32) -- só importável/executável no
Windows. Registrado pelo instalador MSI (ver packaging/BUILD.md).

Uso manual (útil durante o desenvolvimento do instalador):
    python -m migration_agent.service_windows install
    python -m migration_agent.service_windows start
    python -m migration_agent.service_windows stop
    python -m migration_agent.service_windows remove
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
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.runtime = AgentRuntime()

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.runtime.stop()
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        self.runtime.start_background()
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(MigrationAgentService)
