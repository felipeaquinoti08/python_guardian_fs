"""Entrypoint de linha de comando.

    python -m migration_agent run             # roda em foreground (dev/teste, qualquer SO)
    python -m migration_agent service install  # registra Windows Service (só Windows)
    python -m migration_agent service start
    python -m migration_agent service stop
    python -m migration_agent service remove

Em produção (instalado via MSI), o serviço é registrado apontando pro
`service_windows.py` congelado pelo PyInstaller -- este `run` em foreground
existe pra permitir desenvolver e testar toda a lógica (pareamento, poller,
listener) em qualquer sistema operacional, sem precisar de uma máquina
Windows.

Issue #107, bug real encontrado em produção: o Windows Service Control
Manager (SCM) invoca o `.exe` registrado SEM NENHUM ARGUMENTO quando o
serviço é iniciado de verdade (não passa "service start" nem nada --
só executa o binário puro, é `win32serviceutil.HandleCommandLine()` quem
reconhece esse caso e assume o papel de dispatcher do SCM). O `argparse`
abaixo, com subcomando obrigatório, saía imediatamente com erro de uso
nesse cenário -- o processo morria antes até de chegar perto do código do
serviço, causando exatamente "Service ... failed to start" sem log nenhum
(nem chegava a inicializar o logging). Corrigido tratando "sem argumentos"
como um caso especial, delegando direto pro dispatcher do SCM.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import platform
import sys

from .agent_runtime import AgentRuntime


def _cmd_run(_args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    runtime = AgentRuntime()
    runtime.start_foreground()


def _cmd_service(args: argparse.Namespace) -> None:
    if platform.system() != "Windows":
        print("O modo 'service' só está disponível no Windows.", file=sys.stderr)
        sys.exit(1)

    from . import service_windows

    sys.argv = ["migration_agent.service_windows", args.action]
    service_windows.win32serviceutil.HandleCommandLine(service_windows.MigrationAgentService)


def _setup_file_logging() -> None:
    """Log em arquivo o mais cedo possível -- é a service ISTO (não o
    `run` em foreground) que roda em produção, e sem isso qualquer falha
    de inicialização (inclusive um import quebrado de pywin32) desaparece
    sem deixar rastro nenhum, nem no Visualizador de Eventos."""
    try:
        from .config import default_state_dir

        state_dir = default_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            state_dir / "agent.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)
    except Exception:  # noqa: BLE001 - mesmo sem conseguir logar em arquivo, não pode impedir o serviço de tentar subir
        pass


def _dispatch_to_scm() -> None:
    """Sem argumentos = invocado pelo Windows SCM pra rodar o serviço de
    verdade -- delega pro dispatcher do pywin32, que reconhece esse caso
    sozinho (ver docstring do módulo)."""
    _setup_file_logging()
    logger = logging.getLogger(__name__)
    try:
        from . import service_windows

        logger.info("Despachando pro Service Control Manager (sem argumentos recebidos).")
        service_windows.win32serviceutil.HandleCommandLine(service_windows.MigrationAgentService)
    except Exception:
        logger.exception("Falha fatal despachando o serviço pro SCM")
        raise


def main() -> None:
    if len(sys.argv) == 1:
        _dispatch_to_scm()
        return

    parser = argparse.ArgumentParser(prog="migration_agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="Roda em foreground (dev/teste)").set_defaults(func=_cmd_run)

    service_parser = subparsers.add_parser("service", help="Gerencia o Windows Service")
    service_parser.add_argument("action", choices=["install", "start", "stop", "remove"])
    service_parser.set_defaults(func=_cmd_service)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
