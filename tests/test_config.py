from migration_agent.config import AgentConfig, ConfigStore


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
