import time
import urllib.request

from migration_agent.agent_runtime import AgentRuntime


def test_start_background_serves_status_ui_and_stops_cleanly(tmp_path):
    runtime = AgentRuntime(state_dir=tmp_path)
    runtime.start_background()
    try:
        cfg = runtime.config_store.load()
        deadline = time.monotonic() + 5
        last_error = None
        while time.monotonic() < deadline:
            try:
                # /status.json agora exige login (issue #107) -- a pagina de
                # login sem sessao ja e prova suficiente de que o servidor HTTP
                # esta de pe.
                body = urllib.request.urlopen(f"http://127.0.0.1:{cfg.local_ui_port}/status.json", timeout=1).read()
                assert b"Entrar" in body
                break
            except Exception as exc:  # servidor pode ainda não ter subido na primeira tentativa
                last_error = exc
                time.sleep(0.1)
        else:
            raise AssertionError(f"UI local não respondeu a tempo: {last_error}")
    finally:
        runtime.stop()
