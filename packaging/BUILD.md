# Build do instalador (Windows)

## Compatibilidade (pedido do usuário: "qualquer versão do Windows")

- **64-bit obrigatório** (`Platform="x64"` no MSI) — cobre praticamente
  todo Windows Server (2012 R2 em diante) e Windows 10/11 modernos.
  Windows 32-bit não é suportado (extremamente raro em servidor hoje).
- **Python 3.11** (versão usada no build) exige no mínimo Windows 8.1 /
  Windows Server 2012 R2 -- não instala/roda em Windows 7 ou Server 2008 R2
  ou mais antigos.
- O alvo real de produção é um **fileserver Windows Server** (2012 R2,
  2016, 2019, 2022...), não um Windows client -- testar num Windows 365 /
  Cloud PC (Windows 10/11) primeiro é válido como sandbox rápido, mas o
  ambiente que precisa funcionar de verdade é o Server.
- Instalações "Server Core" (sem interface gráfica) ou muito enxutas às
  vezes não têm o **Visual C++ Redistributable** pré-instalado, que
  binários Python/pywin32 podem precisar. O build em modo "onefile" (ver
  abaixo) já embute os DLLs que o PyInstaller detectar automaticamente na
  máquina de build -- mas se a instalação falhar de novo com algo tipo
  "VCRUNTIME140.dll não encontrado", o `vc_redist.x64.exe` (Microsoft,
  gratuito) precisa ser instalado à parte no servidor de destino.

## Caminho automático (GitHub Actions) — recomendado

`.github/workflows/build-agent-msi.yml` builda tudo sozinho num runner
`windows-latest` a cada push na branch `main` (ou disparo manual pela aba
Actions): PyInstaller → WiX v3 (via Chocolatey) → publica o `.msi`
automaticamente no Guardian via `POST /api/agent-installer/publish`
(token de máquina).

Secrets necessários no repositório GitHub (Settings → Secrets and
variables → Actions):
- `AGENT_INSTALLER_PUBLISH_TOKEN` — precisa ser **exatamente** o mesmo
  valor de `AGENT_INSTALLER_PUBLISH_TOKEN` no `.env` do Guardian.
- `GUARDIAN_BASE_URL` — a URL pública real do Guardian (confirmar antes de
  configurar -- já rolou de o `APP_URL` do `.env` estar desatualizado e
  apontar pra outro domínio).

Cada build também vira uma **GitHub Release** (tag `build-<numero da
run>`, `.msi` anexado) além de ir pro Guardian -- histórico navegável sem
precisar de acesso ao Guardian. E fica guardado como artefato da própria
run (aba Actions → run → Artifacts) e em `agent-releases/history/` no
Guardian, com timestamp -- dá pra recuperar qualquer build anterior se uma
versão nova tiver problema.

**Bugs reais já encontrados e corrigidos rodando o pipeline de verdade**
(não dava pra prever sem Windows/WiX disponível durante o desenvolvimento):
erro de sintaxe XML (`--` dentro de comentário), string acentuada
incompatível com a codepage do MSI, componente 32-bit num diretório
64-bit, DLLs do pywin32 não embutidos, e o modo "onedir" do PyInstaller
gerando uma pasta que o instalador nunca empacotava (só o `.exe` sozinho)
-- corrigido trocando pro modo "onefile" (ver seção 1 abaixo).

## Caminho manual (fallback, ou pra debugar um passo específico)

### 1. Gerar o executável (PyInstaller, modo onefile)

```powershell
cd migration-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt pyinstaller

pyinstaller packaging\build_exe.spec
```

Saída esperada: **um único arquivo** `dist\guardian-migration-agent.exe`
(sem pasta ao lado -- o spec usa modo "onefile", tudo embutido no próprio
`.exe`, incluindo os DLLs do pywin32 que `build_exe.spec` bundla
explicitamente).

Ponto de atenção conhecido (comum em projetos PyInstaller + pywin32): rodar
o script de pós-instalação do pywin32 (`python Scripts\pywin32_postinstall.py -install`)
dentro do venv **antes** de gerar o build -- reforço além do bundling
explícito já feito no spec.

### 2. Validar o serviço manualmente (antes de empacotar o MSI)

```powershell
dist\guardian-migration-agent.exe service install
dist\guardian-migration-agent.exe service start
# conferir em services.msc que "Guardian Migration Agent" está rodando
dist\guardian-migration-agent.exe service stop
dist\guardian-migration-agent.exe service remove
```

Se `service start` falhar, rodando direto assim (fora do instalador) o
erro real do Python aparece no console -- bem mais útil que o diálogo
genérico do instalador ("failed to start... verify sufficient privileges",
que aparece pra qualquer causa de falha, não só permissão).

### 3. Gerar o MSI (WiX Toolset v3)

`packaging\installer.wxs` já tem GUIDs reais fixos (`UpgradeCode` e o
`Guid` do `Component`) — não trocar depois de publicado, senão upgrades
futuros param de reconhecer a instalação anterior.

```powershell
candle.exe packaging\installer.wxs -out packaging\installer.wixobj
light.exe packaging\installer.wixobj -out dist\GuardianMigrationAgent.msi
```

### 4. Publicar manualmente

```powershell
curl.exe --fail -X POST "https://SEU_GUARDIAN/api/agent-installer/publish" `
  -H "Authorization: Bearer SEU_AGENT_INSTALLER_PUBLISH_TOKEN" `
  -F "installer=@dist/GuardianMigrationAgent.msi;filename=GuardianMigrationAgent.msi"
```

(O botão "Publicar instalador" que existia na tela de Agents foi removido
a pedido do usuário -- o publish automático via CI cobre esse fluxo. O
endpoint continua existindo como fallback manual, restrito a Super Admin.)

## Checklist de validação manual (numa VM Windows limpa)

- [ ] Instalar o MSI — serviço "Guardian Migration Agent" aparece em
      `services.msc`, iniciado automaticamente.
- [ ] Se falhar: rodar `guardian-migration-agent.exe service start`
      direto no console primeiro (erro real aparece ali), e/ou checar o
      Visualizador de Eventos → Windows Logs → Application.
- [ ] Descobrir a porta da UI local (log do serviço / Visualizador de
      Eventos) e abrir `http://127.0.0.1:<porta>` — formulário de
      pareamento aparece.
- [ ] Colar um token de pareamento válido (gerado na tela de Agents do
      Guardian, issue #106) — pareamento completa, tela vira status.
- [ ] Parar/reiniciar o serviço via `services.msc` — volta a funcionar sem
      perder o pareamento (config.json + DPAPI persistem).
- [ ] Desinstalar o MSI — serviço é removido de `services.msc`.
- [ ] Testar em pelo menos um Windows Server real (2012 R2/2016/2019/2022),
      não só num Windows client/Cloud PC -- é o ambiente real de produção.
