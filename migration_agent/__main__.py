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
"""

from __future__ import annotations

import argparse
import logging
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


def main() -> None:
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
