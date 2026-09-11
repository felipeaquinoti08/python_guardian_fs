"""Testes do WindowsDpapiSecretStore rodando fora do Windows (CI/dev em
Linux, sem pywin32 de verdade instalado): os módulos win32crypt/
win32cryptcon são injetados em sys.modules como fakes -- isso é o
suficiente pra pegar erros de "módulo errado" como o de issue #109
(CRYPTPROTECT_LOCAL_MACHINE foi referenciado em win32con por engano, que
não tem esse atributo -- só quebrou numa maquina Windows de verdade,
porque nenhum teste automatizado exercia esse caminho antes)."""

from __future__ import annotations

import sys
import types

from migration_agent.crypto_store import WindowsDpapiSecretStore


def _install_fake_pywin32(monkeypatch):
    calls = {}

    fake_win32crypt = types.ModuleType("win32crypt")

    def fake_protect(data, description, entropy, reserved, prompt, flags):
        calls["encrypt_flags"] = flags
        return b"protected:" + data

    def fake_unprotect(data, entropy, reserved, prompt, flags):
        assert data.startswith(b"protected:")
        return ("description", data[len(b"protected:") :])

    fake_win32crypt.CryptProtectData = fake_protect
    fake_win32crypt.CryptUnprotectData = fake_unprotect

    fake_win32cryptcon = types.ModuleType("win32cryptcon")
    fake_win32cryptcon.CRYPTPROTECT_LOCAL_MACHINE = 0x4

    monkeypatch.setitem(sys.modules, "win32crypt", fake_win32crypt)
    monkeypatch.setitem(sys.modules, "win32cryptcon", fake_win32cryptcon)
    return calls


def test_encrypt_uses_cryptprotect_local_machine_flag_from_win32cryptcon(monkeypatch):
    calls = _install_fake_pywin32(monkeypatch)

    store = WindowsDpapiSecretStore()
    result = store.encrypt(b"segredo")

    assert result == b"protected:segredo"
    assert calls["encrypt_flags"] == 0x4


def test_encrypt_decrypt_round_trip(monkeypatch):
    _install_fake_pywin32(monkeypatch)

    store = WindowsDpapiSecretStore()
    encrypted = store.encrypt(b"segredo")
    assert store.decrypt(encrypted) == b"segredo"
