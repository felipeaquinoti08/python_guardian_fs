# PyInstaller spec — gera o executável congelado usado pelo instalador MSI.
#
# Rodar (no Windows, dentro do venv com pyinstaller instalado):
#   pyinstaller packaging/build_exe.spec
#
# Gera dist/guardian-migration-agent.exe (arquivo único -- ver nota abaixo)
#
# Issue #107: DOIS bugs reais encontrados em instalações de verdade, o
# segundo só apareceu depois de corrigir o primeiro (mesmo sintoma na tela:
# "Service 'Guardian Migration Agent' failed to start"):
#
# 1. pythoncomXX.dll/pywintypesXX.dll (usados por `import win32api`/
#    `win32com`/`win32crypt` em crypto_store.py/commands.py/service_windows.py)
#    ficam num diretório irmão (pywin32_system32), fora do pacote pywin32
#    "normal" -- a análise estática do PyInstaller não pega esses DLLs
#    sozinha. Corrigido bundlando-os explicitamente (`binaries` abaixo).
#
# 2. MAIS GRAVE, e a causa real de o erro persistir mesmo depois do (1):
#    o modo "onedir" (EXE + COLLECT separados) gera o .exe MAIS uma pasta
#    cheia de DLLs/runtime do Python ao lado -- o instalador (installer.wxs)
#    só empacotava o .exe sozinho, nunca essa pasta. Ou seja: mesmo com os
#    DLLs do pywin32 no lugar certo dentro da pasta de build, o MSI nunca
#    levava a pasta pro cliente, só o executável (que sozinho não roda,
#    faltando até o runtime do Python). Trocado pro modo "onefile": um
#    único .exe autocontido, exatamente o que installer.wxs já esperava
#    (um `<File>` só) -- mais simples que ensinar o WiX a empacotar uma
#    pasta inteira (precisaria de heat.exe, outra ferramenta do WiX
#    Toolset, pra "escanear" o diretório automaticamente).

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
    ["../migration_agent/__main__.py"],
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

# Onefile: passa a.binaries/a.zipfiles/a.datas direto pro EXE() (sem
# exclude_binaries e sem COLLECT depois) -- produz dist/guardian-migration-agent.exe
# como arquivo único, autocontido.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="guardian-migration-agent",
    console=True,
    icon=None,
)
