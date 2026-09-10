# PyInstaller spec — gera o executável congelado usado pelo instalador MSI.
#
# Rodar (no Windows, dentro do venv com pyinstaller instalado):
#   pyinstaller packaging/build_exe.spec
#
# Gera dist/guardian-migration-agent/guardian-migration-agent.exe
#
# NÃO TESTADO neste ambiente (container Linux, sem toolchain Windows) --
# validar numa máquina Windows real antes de gerar o instalador final.

# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ["../migration_agent/__main__.py"],
    pathex=[".."],
    binaries=[],
    datas=[],
    # win32timezone é um import escondido clássico de projetos com pywin32 --
    # sem isso o serviço falha silenciosamente ao iniciar no Windows.
    hiddenimports=["win32timezone"],
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
