"""Issue #107, bug real em produção: o Windows SCM invoca o `.exe` sem
nenhum argumento quando inicia o serviço de verdade -- o argparse com
subcomando obrigatório saía com erro de uso nesse caso, matando o
processo antes de chegar perto do código do serviço (e sem log nenhum,
já que o logging em arquivo só era configurado dentro do modo `run`).
Estes testes cobrem a detecção do caso "sem argumentos" e o setup de
logging -- não cobrem o dispatch real pro SCM em si (isso só existe no
Windows, ver o guard de import em service_windows.py).
"""
import logging
import sys

import pytest

import migration_agent.__main__ as main_module


@pytest.fixture(autouse=True)
def _clean_root_logger_handlers():
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for h in list(root.handlers):
        if h not in before:
            root.removeHandler(h)
            h.close()


def test_setup_file_logging_creates_log_file(tmp_path, monkeypatch):
    monkeypatch.setattr("migration_agent.config.default_state_dir", lambda: tmp_path)

    main_module._setup_file_logging()
    logging.getLogger("qualquer.modulo").info("mensagem de teste")

    log_file = tmp_path / "agent.log"
    assert log_file.exists()
    assert "mensagem de teste" in log_file.read_text(encoding="utf-8")


def test_setup_file_logging_does_not_raise_when_state_dir_unwritable(monkeypatch):
    def _boom():
        raise OSError("disco cheio (simulado)")

    monkeypatch.setattr("migration_agent.config.default_state_dir", _boom)

    # nao pode derrubar o processo so porque nao conseguiu logar em arquivo
    main_module._setup_file_logging()


def test_main_with_no_args_dispatches_to_scm(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(main_module, "_dispatch_to_scm", lambda: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr(sys, "argv", ["guardian-migration-agent.exe"])

    main_module.main()

    assert called["n"] == 1


def test_main_with_run_argument_uses_normal_cli(monkeypatch):
    called = {"n": 0}

    def _fake_cmd_run(_args):
        called["n"] += 1

    monkeypatch.setattr(main_module, "_cmd_run", _fake_cmd_run)
    monkeypatch.setattr(sys, "argv", ["guardian-migration-agent.exe", "run"])

    main_module.main()

    assert called["n"] == 1


def test_main_selftest_constructs_agent_runtime(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("migration_agent.config.default_state_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["guardian-migration-agent.exe", "selftest"])

    main_module.main()

    assert "OK" in capsys.readouterr().out


def test_main_open_ui_opens_browser_at_persisted_port(monkeypatch, tmp_path):
    monkeypatch.setattr("migration_agent.config.default_state_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["guardian-migration-agent.exe", "open-ui"])

    from migration_agent.config import ConfigStore

    port = ConfigStore(tmp_path).load().local_ui_port

    opened = {}
    monkeypatch.setattr("webbrowser.open", lambda url: opened.setdefault("url", url))

    main_module.main()

    assert opened["url"] == f"http://127.0.0.1:{port}"


def test_main_emergency_logs_and_reraises_on_unhandled_exception(monkeypatch):
    """Issue #107: numa instalacao real o .exe crashou (custom action do
    MSI retornou codigo 1) mas C:\\ProgramData nunca chegou a ser criada --
    ou seja, ate o _setup_file_logging() estava falhando silenciosamente.
    main() precisa de uma rede de seguranca que nao dependa dela."""
    called = {"context": None}
    monkeypatch.setattr(main_module, "_emergency_log", lambda context: called.__setitem__("context", context))

    def _boom(_args):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(main_module, "_cmd_run", _boom)
    monkeypatch.setattr(sys, "argv", ["guardian-migration-agent.exe", "run"])

    with pytest.raises(RuntimeError):
        main_module.main()

    assert called["context"] == "main"


def test_emergency_log_is_noop_outside_windows():
    # Neste ambiente (Linux, testes) _emergency_log nao deve criar nada nem lancar.
    main_module._emergency_log("teste")


def test_dispatch_to_scm_logs_and_reraises_when_service_windows_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr("migration_agent.config.default_state_dir", lambda: tmp_path)

    # Fora do Windows, importar service_windows sempre falha (ImportError) --
    # _dispatch_to_scm precisa logar isso em arquivo antes de deixar propagar.
    with pytest.raises(ImportError):
        main_module._dispatch_to_scm()

    log_file = tmp_path / "agent.log"
    assert log_file.exists()
    assert "Falha fatal despachando o serviço pro SCM" in log_file.read_text(encoding="utf-8")
