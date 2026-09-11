"""Criptografia em repouso do auth_token (e outros segredos) salvos localmente.

No Windows, usa DPAPI com escopo de MAQUINA (CRYPTPROTECT_LOCAL_MACHINE) --
nao escopo de usuario, porque o agent roda como Windows Service (conta de
servico, sem perfil de usuario interativo estavel) e tambem pode ser aberto
por um administrador local. Escopo de maquina garante que o segredo so pode
ser decifrado NAQUELA maquina, por qualquer processo local com permissao no
arquivo -- protege contra copiar o config.json pra outra maquina, nao contra
outro processo com acesso admin na mesma maquina (equivalente ao que
qualquer credencial local do Windows sofre).

Fora do Windows nao existe DPAPI -- o fallback abaixo NAO e seguro pra
producao (so existe pra permitir desenvolver/testar o resto do agent em
Linux/Mac). O pacote so deve ser instalado em producao via o instalador MSI
(Windows), que sempre usa o backend DPAPI real.
"""

from __future__ import annotations

import base64
import os
import platform
import stat
from pathlib import Path


class SecretStore:
    def encrypt(self, data: bytes) -> bytes:
        raise NotImplementedError

    def decrypt(self, data: bytes) -> bytes:
        raise NotImplementedError


class WindowsDpapiSecretStore(SecretStore):
    def __init__(self):
        import win32crypt  # type: ignore

        self._win32crypt = win32crypt

    def encrypt(self, data: bytes) -> bytes:
        # CRYPTPROTECT_LOCAL_MACHINE mora em win32cryptcon, NAO em win32con
        # (bug real, issue #109: so foi exercido de verdade quando o
        # bootstrap de senha da UI local passou a chamar encrypt() em todo
        # start do servico -- antes disso so rodava apos um pareamento de
        # verdade, entao esse AttributeError nunca tinha aparecido, mesmo
        # jah estando quebrado desde sempre).
        import win32cryptcon  # type: ignore

        blob = self._win32crypt.CryptProtectData(
            data, "guardian-migration-agent", None, None, None, win32cryptcon.CRYPTPROTECT_LOCAL_MACHINE
        )
        return blob

    def decrypt(self, data: bytes) -> bytes:
        _description, blob = self._win32crypt.CryptUnprotectData(data, None, None, None, 0)
        return blob


class InsecureDevSecretStore(SecretStore):
    """Fallback só para desenvolvimento fora do Windows. NÃO usar em produção."""

    def __init__(self, key_path: Path):
        self._key_path = key_path
        self._key = self._load_or_create_key()

    def _load_or_create_key(self) -> bytes:
        if self._key_path.exists():
            return self._key_path.read_bytes()
        key = os.urandom(32)
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        self._key_path.write_bytes(key)
        try:
            os.chmod(self._key_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return key

    def _xor(self, data: bytes) -> bytes:
        key = self._key
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

    def encrypt(self, data: bytes) -> bytes:
        return base64.b64encode(self._xor(data))

    def decrypt(self, data: bytes) -> bytes:
        return self._xor(base64.b64decode(data))


def get_secret_store(state_dir: Path) -> SecretStore:
    if platform.system() == "Windows":
        return WindowsDpapiSecretStore()
    return InsecureDevSecretStore(state_dir / "dev-only.key")
