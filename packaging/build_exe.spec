# PyInstaller spec — gera o executável congelado usado pelo instalador MSI.
#
# Rodar (no Windows, dentro do venv com pyinstaller instalado):
#   pyinstaller packaging/build_exe.spec
#
# Gera dist/guardian-migration-agent/guardian-migration-agent.exe
#
# Issue #107: bug real encontrado numa instalação de verdade -- "Service
# 'Guardian Migration Agent' failed to start". Causa raiz conhecida em
# projetos PyInstaller + pywin32: pythoncomXX.dll/pywintypesXX.dll ficam
# num diretório irmão (pywin32_system32), fora do pacote pywin32 "normal"
# -- a análise estática do PyInstaller não pega esses DLLs sozinha, e sem
# eles qualquer `import win32api`/`win32com`/`win32crypt` (usados em
# crypto_store.py, commands.py, service_windows.py) falha no processo
# congelado mesmo funcionando perfeitamente no venv de desenvolvimento.
# Bundla os DLLs explicitamente abaixo em vez de depender só do passo de
# pywin32_postinstall.py no workflow (mantido como reforço, não como única
# defesa).

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
