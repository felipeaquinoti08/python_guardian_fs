"""Ponto de entrada real do executavel congelado (ver build_exe.spec).

Issue #107, bug real confirmado numa instalacao de verdade (via
C:\\guardian_agent_crash.log, criado pelo _emergency_log() em
migration_agent/__main__.py): quando o PyInstaller analisa e congela
`migration_agent/__main__.py` diretamente como script de entrada, o
bootloader roda esse arquivo como um modulo solto, sem pacote pai
(`__package__` vazio) -- isso quebra o import relativo
`from .agent_runtime import AgentRuntime` la dentro, com
`ImportError: attempted relative import with no known parent package`,
derrubando o processo (selftest/servico) com exit code 1 antes de
qualquer logica rodar. Este bootstrap importa `migration_agent.__main__`
como submodulo de verdade do pacote (preservando `__package__`) em vez de
rodar o arquivo isolado.
"""
from migration_agent.__main__ import main

if __name__ == "__main__":
    main()
