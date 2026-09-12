# Codex-Multicontroller Environment 1.3.0

Pacote mestre reutilizável para Codex nativo, com SOLO e CO-OP entre dois PCs.
Inclui SPEC auditada, roles Luna/Sol/Astra, Adaptive Controller, skill, instalador Windows,
templates GitHub e testes. Não contém credenciais nem instala serviços externos.

## Iniciar com dois cliques

Extraia o ZIP em `C:\AI` e abra `Codex-Multicontroller\Iniciar.bat`.
O menu permite criar um projeto vazio, copiar uma pasta/arquivo para um projeto novo,
instalar em uma pasta de projeto existente ou validar sua configuração.
Escolha SOLO/CO-OP e balanced/critical, confira o destino e confirme com `S`.

Os novos projetos ficam em `AI\Projetos`, ao lado do mestre. A origem importada permanece intacta.
O assistente requer Python 3.11+; não precisa compilar um EXE nem instalar dependências Python adicionais.
Os scripts PowerShell continuam disponíveis para quem prefere a linha de comando.

Importação copia arquivos; não importa ZIP automaticamente, histórico Git, `.git`, ambientes virtuais,
`node_modules`, caches ou arquivos `.env` pessoais. Modelos `.env.example`, `.env.sample` e `.env.template`
são preservados. Outras credenciais não são detectadas automaticamente. Descompacte arquivos ZIP antes.
Diretórios vazios são preservados; erro de leitura interrompe a importação. Configurações conflitantes bloqueiam a operação e a nova pasta
temporária é removida; a origem permanece intacta. O menu não mescla arquivos em projetos existentes,
não inicializa Git nem faz uploads. Para continuar um clone com histórico, use a opção 3 na pasta original.

## Como organizar

```text
C:\AI\                            ← pasta organizadora em todos os PCs
  Codex-Multicontroller\           ← pacote mestre
    scripts\  lib\  presets\  template\  coop\  docs\  tests\
    local\operator.toml            ← identidade criada apenas neste PC
  Projetos\
    MeuProjeto\                   ← repositório independente
      .codex\  .agents\  .multicontroller\  AGENTS.md  AGENT_TEAM.yml
      src\  tests\  ...
    OutroProjeto\                 ← outro repositório independente
```

O instalador não altera `~/.codex/config.toml`, login, trust ou configurações globais.
`AI` é apenas a pasta organizadora: abra cada projeto individualmente no Codex e mantenha
um repositório Git por projeto. Não crie uma configuração `.codex`/`AGENTS.md` comum em `AI`.
Mestre e projetos são pastas irmãs; nenhum projeto fica dentro do mestre. Use esse mesmo
padrão nos dois PCs, com identidade local própria. A letra da unidade pode variar se necessário.
Cada projeto recebe uma cópia versionada da skill; atualizar o mestre não muda projetos automaticamente.
`AGENT_TEAM.yml` usa JSON, um subconjunto válido de YAML, para dispensar dependências de parsing.

## Instalar em projeto SOLO

Requisitos: Windows, PowerShell 7.4+, Python 3.11+ no PATH, Codex com os modelos desejados.
Git é necessário para branches/worktrees; use um repositório com commit inicial antes de delegar escritores.
No PowerShell, a partir da pasta mestre:

```powershell
# Crie antes a pasta do projeto, fora do mestre.
.\scripts\install.ps1 -ProjectPath 'C:\AI\Projetos\MeuProjeto' -Mode SOLO -Preset balanced -WhatIf
.\scripts\install.ps1 -ProjectPath 'C:\AI\Projetos\MeuProjeto' -Mode SOLO -Preset balanced
.\scripts\validate.ps1 -ProjectPath 'C:\AI\Projetos\MeuProjeto'
```

`-WhatIf` lista alterações sem gravar. O instalador recusa sobrescrever configurações existentes diferentes.
Em projeto com AGENTS/config anteriores, faça uma integração revisada dos templates; não renomeie nem apague
regras úteis só para contornar a recusa. `-Python 'C:\caminho\python.exe'` seleciona outro interpretador.

Abra o projeto no Codex. Confirme confiança e permissões pelo próprio aplicativo, sem copiar credenciais.
Comece com: “Use multicontroller. Leia o projeto, crie um contrato delimitado e confirme modelo/effort
efetivos antes de despachar um Worker.” A skill pode ser descoberta automaticamente pelo Codex.

Antes de considerar o projeto validado, configure `.multicontroller/checks.json` com comandos reais:

```json
{"commands": [["python", "-m", "unittest", "discover", "-s", "tests", "-v"]]}
```

Esse exemplo serve somente para projeto Python que tenha testes de produto. Cada comando é array de
argumentos, sem concatenação de shell. Para npm no Windows, use o executável adequado ao ambiente
ou um script PowerShell explicitamente revisado. O gate vazio falha intencionalmente.

```powershell
.\scripts\validate.ps1 -ProjectPath 'C:\AI\Projetos\MeuProjeto' -ProductGate
```

## CO-OP: responsáveis equivalentes

Pode começar sozinho e adicionar parceiros depois. Qualquer ID em `integration_operators` pode
assumir, retomar e integrar tarefas sem esperar outro computador. Nenhuma divisão de contribuição é imposta.
Os agentes rodam no Codex; operações curtas de coordenação/merge rodam em uma fila no GitHub Actions.
O job ativo ocupa temporariamente o papel de Integration Lead; não há uma pessoa fixa nesse papel.

Configure os logins em `operators`, autorize seus IDs em `integration_operators` e configure
`coordination.control_issue` com o número de uma Issue de estado. Exemplo de campos públicos:

```json
{
  "operators": [
    {"id": "one", "github_login": "SEU_LOGIN"},
    {"id": "two", "github_login": "LOGIN_DO_COLEGA"}
  ],
  "integration_operators": ["one", "two"]
}
```

Use esses campos no AGENT_TEAM completo instalado. Uma lista com apenas você também é válida.
O workflow `multicontroller-control` recebe pedidos JSON via Run workflow/gh. Ele serializa claims,
retomadas, releases e merges, valida ator/version/nonce e registra recibo no corpo da Issue.
Leia [COOP-BOOTSTRAP.md](docs/COOP-BOOTSTRAP.md) para habilitar a fila, permissões e recuperação.

O mesmo ZIP é distribuído a todos; cada PC mantém sua própria `local/operator.toml` fora do Git.
Cross-review entre pessoas é opcional. Agente revisor em contexto independente, testes e escopos
continuam obrigatórios. Branches/PRs antigos não podem ser integrados fora do coordenador.
Não há garantia contra administrador que burle o protocolo ou edite o estado manualmente.

## Presets

| | balanced | critical |
|---|---|---|
| Root inicial | Sol High | Astra High |
| Execução | Luna Medium/High | handoff explícito para Sol High + Luna |
| Subagentes locais máximos | 3 | 2 |
| Tentativas totais por tarefa | 4 | 5 |
| Tempo por tarefa | 45 min | 60 min |
| Revisão | Sol High independente | Sol High + Astra High final |

As configurações são intenções de roteamento, não comprovante de disponibilidade ou execução de modelos.
Verifique cada conta/host; o Codex pode aplicar políticas superiores. Não há Max/Ultra automático.

## Ferramentas de apoio

```powershell
python .\lib\multicontroller.py decide .\docs\examples\history.json --preset balanced
python .\lib\multicontroller.py usage .\docs\examples\usage.json
python .\lib\multicontroller.py grant .\docs\examples\grant.json
python .\lib\multicontroller.py gate .\docs\examples\gate.json
python .\lib\multicontroller.py reclaim .\docs\examples\reclaim.json
python -m unittest discover -s tests -v
```

Os exemplos são dados demonstrativos, não evidência real. `grant` valida um snapshot para o lead; não cria
lock remoto. `gate` confere evidência fornecida; não consulta/autentica GitHub. `decide` recomenda;
o root interrompe e despacha agentes. `reclaim` é helper legado 1.2 para planejar retomada; o CO-OP atual usa o workflow;
não altera permissões remotas. `reputation <events.json>` gera propostas e nunca reescreve política.
Projetos recebem as ferramentas em `.multicontroller/tools/`.

## Atualizar e reverter

Leia diff/changelog da nova release. Faça backup/commit do projeto. Rode `scripts/update.ps1 -ProjectPath ... -WhatIf`
e depois sem `-WhatIf`. Arquivos gerenciados com alterações locais bloqueiam upgrade; resolva por PR/review.
Identidades e comandos do produto são preservados. Schema 1/2 migra para schema 3, incluindo os
operadores cadastrados como integradores equivalentes. Confira a lista no diff; remova quem não deve
ter essa autoridade. `integration_lead` antigo é ignorado. Configure a Issue/fila antes de usar o novo CO-OP.
Migração exige encerrar ou importar claims antigos e desativar caminhos de merge fora do coordenador.
Regras remotas não são alteradas pelo instalador. Backups ficam em `local/backups/` do mestre.
Mudança SOLO↔CO-OP exige migração revisada, sem remoção automática de arquivos. Reversão preferida: revert do
PR de atualização; alternativamente compare/restaure o backup explicitamente, sem apagar trabalho novo.

## O que foi verificado

Consulte [VALIDATION.md](docs/VALIDATION.md) para testes, pressure scenarios e limites reais da release.
Nenhum repositório GitHub foi fornecido: regras remotas, grants em duas contas e disponibilidade de modelos
precisam do smoke test de ativação. O pacote oferece protocolo e ferramentas; não é um sistema distribuído
que imponha exclusividade contra colaboradores com acesso de escrita.

Leitura de auditoria: [SPEC V1](docs/SPEC-V1.md), [findings corrigidos](docs/ARCHITECTURE-REVIEW.md),
[fontes e compatibilidade](docs/SOURCES.md), [aprendizado](template/.agents/skills/multicontroller/references/learning.md).
