from migration_agent.config import (
    AgentConfig,
    ConfigStore,
    generate_default_password,
    hash_password,
    verify_password,
)


def test_round_trip_persists_fields(tmp_path):
    store = ConfigStore(state_dir=tmp_path)
    cfg = store.load()
    assert cfg.is_paired is False

    cfg.guardian_base_url = "https://guardian.exemplo.com"
    cfg.agent_id = "agent-123"
    cfg.auth_token = "super-secret-token"
    cfg.cliente_nome = "Cliente Teste"
    store.save(cfg)

    reloaded = ConfigStore(state_dir=tmp_path).load()
    assert reloaded.guardian_base_url == "https://guardian.exemplo.com"
    assert reloaded.agent_id == "agent-123"
    assert reloaded.auth_token == "super-secret-token"
    assert reloaded.cliente_nome == "Cliente Teste"
    assert reloaded.is_paired is True


def test_auth_token_never_written_in_plaintext(tmp_path):
    store = ConfigStore(state_dir=tmp_path)
    cfg = store.load()
    cfg.auth_token = "super-secret-token"
    store.save(cfg)

    raw = store.config_path.read_text("utf-8")
    assert "super-secret-token" not in raw
    assert "auth_token_encrypted" in raw


def test_local_ui_port_is_stable_across_reloads(tmp_path):
    store = ConfigStore(state_dir=tmp_path)
    cfg = store.load()
    port_first_load = cfg.local_ui_port

    reloaded = ConfigStore(state_dir=tmp_path).load()
    assert reloaded.local_ui_port == port_first_load


def test_hash_password_round_trip_with_verify_password():
    hashed = hash_password("minha-senha-123")
    assert verify_password("minha-senha-123", hashed) is True
    assert verify_password("senha-errada", hashed) is False


def test_hash_password_uses_random_salt_by_default():
    assert hash_password("mesma-senha") != hash_password("mesma-senha")


def test_generate_default_password_avoids_ambiguous_characters():
    password = generate_default_password()
    assert len(password) == 12
    assert not any(c in password for c in "0O1lI")


def test_first_load_bootstraps_default_ui_password_as_admin_admin(tmp_path):
    cfg = ConfigStore(state_dir=tmp_path).load()
    assert cfg.ui_username == "admin"
    assert cfg.ui_password_is_default is True
    assert cfg.ui_default_password == "admin"
    assert verify_password("admin", cfg.ui_password_hash) is True


def test_loading_pre_issue_109_config_without_password_fields_backfills_default(tmp_path):
    store = ConfigStore(state_dir=tmp_path)
    # simula um config.json de uma instalacao anterior a issue #109 (sem
    # nenhum campo de autenticacao ainda, so os campos que ja existiam)
    store.config_path.write_text(
        '{"guardian_base_url": "", "agent_id": null, "cliente_nome": null, '
        '"local_ui_port": 12345, "peer_listener_port": 12346, "peer_listener_ip": null}',
        "utf-8",
    )

    cfg = store.load()

    assert cfg.ui_password_hash
    assert cfg.ui_password_is_default is True
    assert cfg.ui_default_password == "admin"
    assert verify_password("admin", cfg.ui_password_hash) is True

    # persistido: um load seguinte nao perde o backfill
    reloaded = ConfigStore(state_dir=tmp_path).load()
    assert reloaded.ui_default_password == "admin"


def test_ui_default_password_never_written_in_plaintext(tmp_path):
    import json

    store = ConfigStore(state_dir=tmp_path)
    store.load()

    raw = json.loads(store.config_path.read_text("utf-8"))
    assert "ui_default_password_encrypted" in raw
    # a chave em texto puro nao pode existir (mesmo tratamento do auth_token)
    # -- "admin" aparecer como valor de ui_username e esperado, nao e leak
    assert "ui_default_password" not in raw


def test_reset_ui_password_generates_new_password_and_invalidates_old_one(tmp_path):
    store = ConfigStore(state_dir=tmp_path)
    original = store.load()

    new_password = store.reset_ui_password()

    reloaded = store.load()
    assert new_password != original.ui_default_password
    assert reloaded.ui_default_password == new_password
    assert reloaded.ui_password_is_default is True
    assert verify_password(original.ui_default_password, reloaded.ui_password_hash) is False
    assert verify_password(new_password, reloaded.ui_password_hash) is True


def test_change_ui_password_clears_default_flag(tmp_path):
    store = ConfigStore(state_dir=tmp_path)
    store.load()

    store.change_ui_password("nova-senha-forte")

    reloaded = store.load()
    assert reloaded.ui_password_is_default is False
    assert reloaded.ui_default_password is None
    assert verify_password("nova-senha-forte", reloaded.ui_password_hash) is True
