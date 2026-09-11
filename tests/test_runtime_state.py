from migration_agent.runtime_state import RuntimeState


def test_guardian_status_starts_disconnected():
    state = RuntimeState()
    status = state.guardian_status()
    assert status.connected is False
    assert status.last_success is None
    assert status.last_error is None


def test_set_guardian_status_connected_records_last_success():
    state = RuntimeState()

    state.set_guardian_status(True)

    status = state.guardian_status()
    assert status.connected is True
    assert status.last_success is not None
    assert status.last_error is None


def test_set_guardian_status_disconnected_keeps_error_and_clears_connected():
    state = RuntimeState()
    state.set_guardian_status(True)

    state.set_guardian_status(False, "timeout")

    status = state.guardian_status()
    assert status.connected is False
    assert status.last_error == "timeout"


def test_peer_status_starts_inactive():
    state = RuntimeState()
    status = state.peer_status()
    assert status.active is False
    assert status.label == ""


def test_set_peer_status_with_label_activates():
    state = RuntimeState()

    state.set_peer_status("Enviando arquivo para outro agent (10.0.0.5:5555)")

    status = state.peer_status()
    assert status.active is True
    assert status.label == "Enviando arquivo para outro agent (10.0.0.5:5555)"
    assert status.started_at is not None


def test_set_peer_status_none_deactivates():
    state = RuntimeState()
    state.set_peer_status("em andamento")

    state.set_peer_status(None)

    status = state.peer_status()
    assert status.active is False
    assert status.label == ""


def test_guardian_status_returns_independent_copies():
    state = RuntimeState()
    state.set_guardian_status(True)

    first = state.guardian_status()
    state.set_guardian_status(False, "erro")
    second = state.guardian_status()

    assert first.connected is True
    assert second.connected is False


def test_record_calls_on_record_hook_after_storing_locally():
    seen = []
    state = RuntimeState(on_record=seen.append)

    state.record("Pareado com sucesso")

    assert seen == ["Pareado com sucesso"]
    assert state.recent()[0].message == "Pareado com sucesso"


def test_record_survives_on_record_hook_raising():
    def _boom(message):
        raise RuntimeError("Guardian inacessível")

    state = RuntimeState(on_record=_boom)

    state.record("Login na UI local")  # nao pode propagar a exceção

    assert state.recent()[0].message == "Login na UI local"


def test_record_without_on_record_hook_works_normally():
    state = RuntimeState()
    state.record("qualquer coisa")
    assert state.recent()[0].message == "qualquer coisa"
