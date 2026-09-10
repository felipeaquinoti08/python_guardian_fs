# Guardian Migration Agent

Agent Python que expõe um fileserver on-premises como origem/destino do
módulo de Migração Universal do Guardian. Roda no Windows como Serviço, ou
em foreground para desenvolvimento/teste.

Desenho completo da funcionalidade (as 3 outras issues que dependem deste
agent): ver `implementation_plan.md` na raiz do repositório principal.

## Desenvolvimento (qualquer SO)

```bash
cd migration-agent
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

pytest                          # roda a suíte de testes
python -m migration_agent run   # roda em foreground, UI local em http://127.0.0.1:<porta aleatória>
```

No primeiro `run`, o agent gera um `config.json` (ver `migration_agent.config.default_state_dir()`)
e escolhe uma porta local aleatória para a UI de pareamento — o log de
inicialização mostra a URL exata.

Fora do Windows, a criptografia do `auth_token` em disco usa um fallback
**inseguro só para desenvolvimento** (`InsecureDevSecretStore`) — produção
sempre roda no Windows via o instalador MSI, que usa DPAPI real. Ver
`migration_agent/crypto_store.py`.

## Empacotamento para produção (Windows)

Ver `packaging/BUILD.md`. Resumo: PyInstaller gera o `.exe`, WiX empacota o
instalador `.msi` que registra o Windows Service. Essa etapa **não foi
testada** neste ambiente (container Linux, sem toolchain Windows/WiX
disponível) — precisa de validação numa máquina Windows real antes de
distribuir para clientes.

## Estrutura

- `migration_agent/config.py` — persistência local (`config.json` + segredo cifrado).
- `migration_agent/crypto_store.py` — DPAPI (Windows) / fallback dev (outros SOs).
- `migration_agent/api_client.py` — cliente HTTP do Guardian; documenta o contrato de API esperado (issue #106).
- `migration_agent/poller.py` — loop de long-polling.
- `migration_agent/commands.py` — handlers dos comandos (`list_folder`, `connectivity_check`; `run_transfer` é placeholder, ver issues #106/#107).
- `migration_agent/peer_listener.py` — servidor TCP que recebe transferência direta de outro agent.
- `migration_agent/status_server.py` — UI web local (porta aleatória): pareamento + status.
- `migration_agent/agent_runtime.py` — orquestra as threads de fundo (usado tanto pelo `run` em foreground quanto pelo Windows Service).
- `migration_agent/service_windows.py` — wrapper de Windows Service (pywin32).
