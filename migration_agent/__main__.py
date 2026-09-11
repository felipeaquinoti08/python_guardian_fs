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
import traceback
from datetime import datetime


def _emergency_log(context: str) -> None:
    """Ultima linha de defesa pra diagnostico. Issue #107: numa instalacao
    real confirmamos que o .exe roda e crasha (custom action do MSI
    retornou codigo 1), mas C:\\ProgramData\\GuardianMigrationAgent nunca
    chegou a ser criada -- ou seja, ate o _setup_file_logging() abaixo
    (que tambem tenta criar essa pasta) estava falhando silenciosamente,
    sem deixar rastro nenhum de qual era o erro real. Grava direto na raiz
    do C:\\, que nao precisa criar pasta nenhuma e e o lugar mais dificil
    de falhar por permissao (mesmo como LocalSystem)."""
    if platform.system() != "Windows":
        return
    try:
        with open(r"C:\guardian_agent_crash.log", "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {context} ({datetime.now().isoformat()}) ===\n")
            fh.write(traceback.format_exc())
            fh.write("\n")
    except Exception:  # noqa: BLE001 - literalmente a ultima linha de defesa, nao pode lancar
        pass


try:
    from .agent_runtime import AgentRuntime
except Exception:
    _emergency_log("import migration_agent.agent_runtime")
    raise


def _cmd_run(_args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    runtime = AgentRuntime()
    runtime.start_foreground()


def _cmd_selftest(_args: argparse.Namespace) -> None:
    """Usado como Custom Action do MSI (installer.wxs), logo apos copiar os
    arquivos e antes de registrar o servico -- roda o .exe fora do Windows
    Service pra descobrir se ele consegue nem executar naquele ambiente
    (bloqueio de politica, DLL faltando, etc). Se falhar aqui, o msiexec
    mostra o erro real do Windows em vez do generico "Error 1920"."""
    _setup_file_logging()
    logger = logging.getLogger(__name__)
    try:
        runtime = AgentRuntime()
        logger.info("selftest: AgentRuntime() construido com sucesso")
        print("OK")
    except Exception:
        logger.exception("selftest falhou")
        raise


def _cmd_open_ui(_args: argparse.Namespace) -> None:
    """Abre a UI local de pareamento/status no navegador padrao. Alvo dos
    atalhos opcionais de Menu Iniciar/Area de Trabalho do instalador
    (issue #107) -- a porta e escolhida dinamicamente na primeira
    execucao (ver config.py::_pick_free_port) e persistida em
    config.json, entao o atalho nao pode apontar direto pra uma URL
    fixa: precisa perguntar pro proprio agent qual porta esta em uso."""
    import webbrowser

    from .config import ConfigStore

    cfg = ConfigStore().load()
    webbrowser.open(f"http://127.0.0.1:{cfg.local_ui_port}")


def _cmd_reset_ui_password(_args: argparse.Namespace) -> None:
    """Gera uma nova senha padrao pra UI local e imprime no console
    (issue #107) -- unico jeito de recuperar acesso se a senha for
    esquecida, ja que nao ha fluxo de recuperacao por e-mail: quem roda
    esse comando ja tem acesso local a maquina (mesmo nivel de confianca
    de quem conseguiria ler o config.json direto)."""
    from .config import ConfigStore

    store = ConfigStore()
    cfg = store.load()
    password = store.reset_ui_password()
    print(f"Nova senha da UI local gerada. Usuario: {cfg.ui_username}  Senha: {password}")
    print("Troque assim que possivel em /change-password (fica marcada como padrao ate la).")


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
    verdade.

    Issue #107: o `agent.log` de uma instalação real (build 21) mostrou
    "Despachando pro Service Control Manager" logado **7 vezes**, em
    intervalos de ~5.3-5.6s, cobrindo exatamente os ~33s até o Error 1920
    -- ou seja, o SCM mata e reinicia o processo repetidamente, e em
    NENHUMA dessas tentativas o código chega a entrar em `SvcDoRun()`
    (nenhum log das linhas novas adicionadas lá em service_windows.py
    apareceu). O problema está aqui, dentro do handshake com o SCM --
    antes mesmo do nosso código de serviço rodar. Antes chamávamos
    `win32serviceutil.HandleCommandLine(...)`, que internamente (pro caso
    "sem argumentos") só faz `PrepareToHostSingle` +
    `servicemanager.Initialize()` + `servicemanager.StartServiceCtrlDispatcher()`
    -- reimplementado aqui explicitamente, com log entre cada uma, pra
    finalmente descobrir qual dessas três chamadas está falhando ou
    retornando cedo demais (ao inves de bloquear ate o servico parar,
    como deveria)."""
    _setup_file_logging()
    logger = logging.getLogger(__name__)
    logger.info("Despachando pro Service Control Manager (sem argumentos recebidos).")
    try:
        from . import service_windows

        logger.info("Chamando servicemanager.PrepareToHostSingle...")
        service_windows.servicemanager.PrepareToHostSingle(service_windows.MigrationAgentService)
        logger.info("PrepareToHostSingle concluido -- chamando servicemanager.Initialize...")
        service_windows.servicemanager.Initialize()
        logger.info("Initialize concluido -- chamando StartServiceCtrlDispatcher (deveria bloquear ate o servico parar)...")
        service_windows.servicemanager.StartServiceCtrlDispatcher()
        logger.warning(
            "StartServiceCtrlDispatcher RETORNOU -- isso so deveria acontecer apos o servico ser "
            "parado de verdade (SvcStop). Se SvcDoRun nunca rodou antes disso, o SCM nao conseguiu "
            "conectar este processo ao pedido de start pendente."
        )
    except Exception:
        logger.exception("Falha fatal despachando o serviço pro SCM")
        raise


def main() -> None:
    try:
        if len(sys.argv) == 1:
            _dispatch_to_scm()
            return

        parser = argparse.ArgumentParser(prog="migration_agent")
        subparsers = parser.add_subparsers(dest="command", required=True)

        subparsers.add_parser("run", help="Roda em foreground (dev/teste)").set_defaults(func=_cmd_run)
        subparsers.add_parser(
            "selftest", help="Verificacao rapida (usada pelo instalador MSI)"
        ).set_defaults(func=_cmd_selftest)
        subparsers.add_parser(
            "open-ui", help="Abre a UI local de pareamento no navegador (usada pelos atalhos do instalador)"
        ).set_defaults(func=_cmd_open_ui)
        subparsers.add_parser(
            "reset-ui-password", help="Gera uma nova senha padrao pra UI local (recuperacao de acesso)"
        ).set_defaults(func=_cmd_reset_ui_password)

        service_parser = subparsers.add_parser("service", help="Gerencia o Windows Service")
        service_parser.add_argument("action", choices=["install", "start", "stop", "remove"])
        service_parser.set_defaults(func=_cmd_service)

        args = parser.parse_args()
        args.func(args)
    except Exception:
        # Rede de seguranca final: qualquer excecao nao tratada em qualquer
        # comando (inclusive um ImportError silencioso escondido atras de
        # um _setup_file_logging() que tambem falhou) grava o traceback
        # real em C:\guardian_agent_crash.log antes de deixar propagar.
        _emergency_log("main")
        raise


if __name__ == "__main__":
    main()
