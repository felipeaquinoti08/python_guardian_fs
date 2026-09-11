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
  binários Python/pywin32 podem precisar. O próprio MSI agora **embute e
  instala silenciosamente o `vc_redist.x64.exe`** (via Custom Action em
  `installer.wxs`, só roda se o runtime ainda não estiver presente na
  máquina de destino — não depende de internet no servidor).
- **PyInstaller em modo "onedir" (não "onefile")** de propósito: um `.exe`
  "onefile" precisa se auto-extrair numa pasta temporária *toda vez* que é
  executado, inclusive toda vez que o Windows tenta iniciar o serviço --
  isso pode ficar lento o bastante (antivírus escaneando cada arquivo
  extraído) pra estourar o timeout de ~30s que o SCM dá pro serviço
  responder (erro 1920/1053, "did not respond in a timely fashion",
  descoberto analisando o log verbose do `msiexec` numa instalação real:
  39s entre o Windows tentar iniciar o serviço e o erro). Com "onedir" os
  arquivos ficam soltos em disco, extraídos só uma vez na instalação.

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
64-bit, DLLs do pywin32 não embutidos, modo "onedir" do PyInstaller
gerando uma pasta que o instalador não empacotava (resolvido trocando pra
"onefile" e depois, ao investigar o log verbose do `msiexec` numa
instalação real, revertido de volta pra "onedir" por causa da lentidão de
auto-extração -- ver seção "Compatibilidade" acima -- agora com `heat.exe`
harvestando a pasta `_internal\` automaticamente em vez de listar arquivos
a mão).

## Caminho manual (fallback, ou pra debugar um passo específico)

### 1. Gerar o executável (PyInstaller, modo onedir)

```powershell
cd migration-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt pyinstaller

pyinstaller packaging\build_exe.spec
```

Saída esperada: `dist\guardian-migration-agent\guardian-migration-agent.exe`
(o lançador) + `dist\guardian-migration-agent\_internal\` (DLLs/runtime do
Python, incluindo os do pywin32 que `build_exe.spec` bundla
explicitamente) -- modo "onedir", não gera mais um `.exe` único.

Ponto de atenção conhecido (comum em projetos PyInstaller + pywin32): rodar
o script de pós-instalação do pywin32 (`python Scripts\pywin32_postinstall.py -install`)
dentro do venv **antes** de gerar o build -- reforço além do bundling
explícito já feito no spec.

### 2. Validar o serviço manualmente (antes de empacotar o MSI)

```powershell
cd dist\guardian-migration-agent
.\guardian-migration-agent.exe service install
.\guardian-migration-agent.exe service start
# conferir em services.msc que "Guardian Migration Agent" está rodando
.\guardian-migration-agent.exe service stop
.\guardian-migration-agent.exe service remove
```

Se `service start` falhar, rodando direto assim (fora do instalador) o
erro real do Python aparece no console -- bem mais útil que o diálogo
genérico do instalador ("failed to start... verify sufficient privileges",
que aparece pra qualquer causa de falha, não só permissão).

### 3. Gerar o MSI (WiX Toolset v3)

`packaging\installer.wxs` já tem GUIDs reais fixos (`UpgradeCode` e o
`Guid` do `Component`) — não trocar depois de publicado, senão upgrades
futuros param de reconhecer a instalação anterior.

O `installer.wxs` embute `packaging\vc_redist.x64.exe` (não versionado no
repo — baixar manualmente antes de compilar) e referencia um
`ComponentGroup` (`InternalFiles`) que precisa ser gerado antes por
`heat.exe` (harvester do próprio WiX Toolset), escaneando a pasta
`_internal\` do PyInstaller:

```powershell
Invoke-WebRequest -Uri "https://aka.ms/vs/17/release/vc_redist.x64.exe" -OutFile "packaging\vc_redist.x64.exe"

$internalDir = Resolve-Path "dist\guardian-migration-agent\_internal"
heat.exe dir "$internalDir" -cg InternalFiles -gg -sfrag -srd -dr INTERNALDIR -var var.InternalSourceDir -t packaging\heat_x64_transform.xsl -out packaging\internal_files.wxs

candle.exe "-dInternalSourceDir=$internalDir" -ext WixUIExtension packaging\installer.wxs packaging\internal_files.wxs -out packaging\
light.exe -ext WixUIExtension packaging\installer.wixobj packaging\internal_files.wixobj -out dist\GuardianMigrationAgent.msi
```

`-ext WixUIExtension` é necessário desde que o instalador ganhou um wizard
(`WixUI_Minimal` + tela extra de atalhos, ver seção de atalhos abaixo) --
vem junto com o pacote `wixtoolset` do Chocolatey, não precisa instalar
nada a mais.

### 4. Publicar manualmente

```powershell
curl.exe --fail -X POST "https://SEU_GUARDIAN/api/agent-installer/publish" `
  -H "Authorization: Bearer SEU_AGENT_INSTALLER_PUBLISH_TOKEN" `
  -F "installer=@dist/GuardianMigrationAgent.msi;filename=GuardianMigrationAgent.msi"
```

(O botão "Publicar instalador" que existia na tela de Agents foi removido
a pedido do usuário -- o publish automático via CI cobre esse fluxo. O
endpoint continua existindo como fallback manual, restrito a Super Admin.)

## Atalhos opcionais (Menu Iniciar / Área de Trabalho)

O instalador (rodando com UI completa, ex: dando duplo clique no `.msi`)
mostra uma tela com dois checkboxes -- ambos marcados por padrão -- pra
criar atalho no Menu Iniciar e/ou na Área de Trabalho. Cada atalho aponta
pro próprio `.exe` com o argumento `open-ui`, que abre no navegador a UI
local de pareamento na porta persistida em `config.json` (a porta é
escolhida dinamicamente na primeira execução, por isso o atalho não pode
apontar direto pra uma URL fixa).

Com `/qb`/`/qn` (instalação silenciosa, como o Guardian dispara via
`AgentInstallerPublisher.php`) a tela do wizard não aparece -- os dois
atalhos são criados por padrão mesmo assim. Pra desativar um deles numa
instalação silenciosa, passar a propriedade correspondente:

```powershell
msiexec /i GuardianMigrationAgent.msi /qb INSTALLDESKTOPSHORTCUT=0
msiexec /i GuardianMigrationAgent.msi /qb INSTALLSTARTMENUSHORTCUT=0
```

## Checklist de validação manual (numa VM Windows limpa)

- [ ] Instalar o MSI — serviço "Guardian Migration Agent" aparece em
      `services.msc`, iniciado automaticamente.
- [ ] Instalar dando duplo clique no `.msi` (não via linha de comando) —
      a tela de atalhos aparece, com os dois checkboxes marcados por
      padrão. Desmarcar um deles e confirmar que só o outro atalho é
      criado.
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
