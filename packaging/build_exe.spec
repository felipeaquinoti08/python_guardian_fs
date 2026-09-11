# PyInstaller spec — gera o executável congelado usado pelo instalador MSI.
#
# Rodar (no Windows, dentro do venv com pyinstaller instalado):
#   pyinstaller packaging/build_exe.spec
#
# Gera dist/guardian-migration-agent/guardian-migration-agent.exe +
# dist/guardian-migration-agent/_internal/ (modo "onedir" -- ver nota abaixo)
#
# Issue #107: histórico de bugs reais encontrados em instalações de
# verdade, cada um só apareceu depois de corrigir o anterior (mesmo
# sintoma na tela: "Service 'Guardian Migration Agent' failed to start"):
#
# 1. pythoncomXX.dll/pywintypesXX.dll (usados por `import win32api`/
#    `win32com`/`win32crypt` em crypto_store.py/commands.py/service_windows.py)
#    ficam num diretório irmão (pywin32_system32), fora do pacote pywin32
#    "normal" -- a análise estática do PyInstaller não pega esses DLLs
#    sozinha. Corrigido bundlando-os explicitamente (`binaries` abaixo).
#
# 2. O modo "onedir" (EXE + COLLECT separados) gera o .exe MAIS uma pasta
#    cheia de DLLs/runtime do Python ao lado, mas o instalador (installer.wxs)
#    só empacotava o .exe sozinho. Corrigido temporariamente trocando pro
#    modo "onefile" (arquivo único autocontido) -- só que isso introduziu o
#    bug (3) abaixo, então foi revertido de volta pra onedir.
#
# 3. DESCOBERTO ANALISANDO O LOG VERBOSE DO MSIEXEC (msiexec /l*v) NUMA
#    INSTALAÇÃO REAL: com "onefile", o .exe precisa se auto-extrair (DLLs
#    do Python/pywin32, ~dezenas de arquivos) numa pasta temporária TODA
#    VEZ que é executado -- inclusive toda vez que o Windows tenta iniciar
#    o serviço. Numa máquina com antivírus fazendo verificação em tempo
#    real de cada arquivo novo/desconhecido sendo escrito (comum em
#    Windows 365/Cloud PC corporativo), essa auto-extração fica lenta o
#    bastante pra estourar o timeout de ~30s que o Windows dá pro serviço
#    responder -- log mostrou 39s entre o Windows tentar iniciar o serviço
#    e o erro 1920/1053 ("did not respond in a timely fashion"), não um
#    crash instantâneo. Voltado definitivamente pro modo "onedir": os
#    arquivos ficam soltos em disco, extraídos só UMA VEZ na instalação,
#    nunca mais recriados a cada start do serviço. O empacotamento desses
#    arquivos no MSI agora usa heat.exe (harvester do próprio WiX Toolset)
#    pra escanear a pasta `_internal` automaticamente em vez de listar
#    manualmente -- ver build-agent-msi.yml e packaging/installer.wxs.
#
# 4. DESCOBERTO VIA C:\guardian_agent_crash.log (rede de seguranca
#    adicionada em migration_agent/__main__.py) NUMA INSTALACAO REAL: com
#    `Analysis(["../migration_agent/__main__.py"])` abaixo, o PyInstaller
#    congelava esse arquivo como um script solto -- o bootloader roda ele
#    sem contexto de pacote (`__package__` vazio), quebrando o import
#    relativo `from .agent_runtime import AgentRuntime` la dentro com
#    `ImportError: attempted relative import with no known parent
#    package`. Isso derrubava o processo (selftest OU o servico de
#    verdade) com exit code 1 antes de qualquer logica rodar -- nada a
#    ver com SCM/timeout/onefile-onedir, um bug de empacotamento
#    separado. Corrigido usando `entrypoint.py` (bootstrap que importa
#    `migration_agent.__main__` como submodulo de verdade) como script de
#    entrada em vez do arquivo do pacote diretamente.

# -*- mode: python ; coding: utf-8 -*-

import os

block_cipher = None

binaries = []
try:
    import win32api

    # win32api.__file__ = .../site-packages/win32/win32api.pyd
    # pywin32_system32 fica em .../site-packages/pywin32_system32/
    site_packages = os.path.dirname(os.path.dirname(win32api.__file__))
    pywin32_system32 = os.path.join(site_packages, "pywin32_system32")
    if os.path.isdir(pywin32_system32):
        for fname in os.listdir(pywin32_system32):
            if fname.lower().endswith(".dll"):
                binaries.append((os.path.join(pywin32_system32, fname), "."))
except ImportError:
    pass  # build rodando fora do Windows (dev/teste) -- sem pywin32 mesmo

a = Analysis(
    ["entrypoint.py"],
    pathex=[".."],
    binaries=binaries,
    datas=[],
    # Imports usados condicionalmente (dentro de função, não no topo do
    # módulo) que a análise estática do PyInstaller não enxerga sozinha --
    # sem isso o serviço falha ao carregar essas partes em runtime.
    hiddenimports=[
        "win32timezone",
        "win32api",
        "win32con",
        "win32event",
        "win32service",
        "win32serviceutil",
        "win32wnet",
        "win32netcon",
        "win32crypt",
        "pythoncom",
        "pywintypes",
        "servicemanager",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Onedir: EXE com exclude_binaries=True + COLLECT depois -- produz
# dist/guardian-migration-agent/guardian-migration-agent.exe (só o
# lançador) + dist/guardian-migration-agent/_internal/ (DLLs/runtime do
# Python, extraídos uma única vez na instalação, não a cada start do
# serviço -- ver nota (3) acima).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="guardian-migration-agent",
    console=True,
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="guardian-migration-agent",
)
