# Build do instalador (Windows)

## Caminho automático (GitHub Actions) — recomendado

`.github/workflows/build-agent-msi.yml` builda tudo sozinho num runner
`windows-latest` a cada push na branch `main` (ou disparo manual pela aba
Actions): PyInstaller → WiX v3 (via Chocolatey) → publica o `.msi`
automaticamente no Guardian via `POST /api/agent-installer/publish`
(token de máquina).

⚠️ **Esse workflow nunca rodou de verdade ainda** — foi escrito sem acesso
a uma máquina Windows/WiX pra testar. A primeira execução real é a
primeira validação de fato; se falhar, o log da run no GitHub Actions
mostra exatamente em qual passo, e o arquivo `.yml` deve ser ajustado a
partir daí (comum: nome exato do pacote Chocolatey, caminho do
`pywin32_postinstall.py`, versão do WiX instalada).

Secrets necessários no repositório GitHub (Settings → Secrets and
variables → Actions):
- `AGENT_INSTALLER_PUBLISH_TOKEN` — precisa ser **exatamente** o mesmo
  valor de `AGENT_INSTALLER_PUBLISH_TOKEN` no `.env` do Guardian.
- `GUARDIAN_BASE_URL` — ex: `https://eap.cloudessential.tech`.

Mesmo com o CI publicando sozinho, todo `.msi` novo fica também guardado
como artefato da run (aba Actions → run → Artifacts) e em
`agent-releases/history/` no Guardian, com timestamp — dá pra recuperar
qualquer build anterior se uma versão nova tiver problema.

## Caminho manual (fallback, ou pra debugar um passo específico)

### 1. Gerar o executável (PyInstaller)

```powershell
cd migration-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt pyinstaller

pyinstaller packaging\build_exe.spec
```

Saída esperada: `dist\guardian-migration-agent\guardian-migration-agent.exe`
(+ DLLs/dependências no mesmo diretório).

Ponto de atenção conhecido (comum em projetos PyInstaller + pywin32): rodar
o script de pós-instalação do pywin32 (`python Scripts\pywin32_postinstall.py -install`)
dentro do venv **antes** de gerar o build, senão o serviço pode falhar ao
iniciar por falta de `pywintypes*.dll`/`pythoncom*.dll` no PATH do processo
congelado.

### 2. Validar o serviço manualmente (antes de empacotar o MSI)

```powershell
dist\guardian-migration-agent\guardian-migration-agent.exe service install
dist\guardian-migration-agent\guardian-migration-agent.exe service start
# conferir em services.msc que "Guardian Migration Agent" está rodando
dist\guardian-migration-agent\guardian-migration-agent.exe service stop
dist\guardian-migration-agent\guardian-migration-agent.exe service remove
```

### 3. Gerar o MSI (WiX Toolset v3)

`packaging\installer.wxs` já tem GUIDs reais fixos (`UpgradeCode` e o
`Guid` do `Component`) — não trocar depois de publicado, senão upgrades
futuros param de reconhecer a instalação anterior.

```powershell
candle.exe packaging\installer.wxs -out packaging\installer.wixobj
light.exe packaging\installer.wixobj -out dist\GuardianMigrationAgent.msi
```

### 4. Publicar manualmente

Fazer upload do `.msi` gerado pela tela de Agents do Guardian (botão
"Publicar instalador", restrito a Super Admin) -- ou, se preferir linha de
comando, o mesmo endpoint do CI:

```powershell
curl.exe --fail -X POST "https://SEU_GUARDIAN/api/agent-installer/publish" `
  -H "Authorization: Bearer SEU_AGENT_INSTALLER_PUBLISH_TOKEN" `
  -F "installer=@dist/GuardianMigrationAgent.msi;filename=GuardianMigrationAgent.msi"
```

## Checklist de validação manual (numa VM Windows limpa)

- [ ] Instalar o MSI — serviço "Guardian Migration Agent" aparece em
      `services.msc`, iniciado automaticamente.
- [ ] Descobrir a porta da UI local (log do serviço / Visualizador de
      Eventos) e abrir `http://127.0.0.1:<porta>` — formulário de
      pareamento aparece.
- [ ] Colar um token de pareamento válido (gerado na tela de Agents do
      Guardian, issue #106) — pareamento completa, tela vira status.
- [ ] Parar/reiniciar o serviço via `services.msc` — volta a funcionar sem
      perder o pareamento (config.json + DPAPI persistem).
- [ ] Desinstalar o MSI — serviço é removido de `services.msc`.
