# Galaxy Orchestrator V2 — instalação e migração

Este guia instala o runtime local do Galaxy em projetos independentes. O mestre e cada projeto são árvores separadas; o instalador não altera `~/.codex`, login, trust, permissões globais ou credenciais.

## Requisitos

Windows, PowerShell 7.4+, Python 3.11+ e Git para projetos versionados/CO-OP. `gh` ou connector GitHub é opcional. Não é necessário instalar dependências Python adicionais.

## Projeto novo ou existente

Use a pasta mestre para executar preview, instalação e validação:

```powershell
.\scripts\install.ps1 -ProjectPath 'C:\AI\Projetos\MeuProjeto' -Mode SOLO -Preset balanced -WhatIf
.\scripts\install.ps1 -ProjectPath 'C:\AI\Projetos\MeuProjeto' -Mode SOLO -Preset balanced
python .\galaxy.py doctor --project 'C:\AI\Projetos\MeuProjeto'
python .\galaxy.py validate --project 'C:\AI\Projetos\MeuProjeto'
```

V2 cria `.galaxy/project.yml`, `.galaxy/team.yml`, `.galaxy/checks.json` e `galaxy.lock`. `.codex/`, runtime, cache, install e ferramentas geradas são locais/não rastreados. O projeto deve manter seus próprios comandos de produto em `.galaxy/checks.json`; lista vazia falha deliberadamente.

Para continuar um clone existente, faça backup/commit e rode `-WhatIf`. Drift em arquivo gerenciado bloqueia overwrite. Resolva por integração revisada; não apague regras úteis para contornar o preflight.

## Vault opcional do Obsidian

O vault é uma projeção para acompanhar tarefas, milestones e resumos. Ative em `.galaxy/project.yml`:

```yaml
vault:
  enabled: true
  path: .galaxy/vault
  mode: projection
  sync: manual
  include: [tasks, milestones, summaries]
  exclude: [prompts, responses, telemetry, secrets]
```

Para um vault externo, mantenha o caminho apenas em `.galaxy/local/operator.toml`; não rastreie caminhos pessoais. Execute `galaxy vault status` e `galaxy vault sync --check` antes de gravar. A projeção é determinística e contém task ID, revision, status, owner e receipt, nunca prompts, respostas, tokens, credenciais ou `.env`. O estado CO-OP continua no coordenador serializado; uma nota não pode criar claim, liberar dependência ou integrar PR.

Edições humanas devem ser feitas em notas `project-owned` explicitamente declaradas. Drift em projeções bloqueia sync por padrão; `--force` exige backup e confirmação. Conflitos project-owned preservam os dois lados e exigem resolução manual. Não há merge automático por LLM.

## CO-OP

Configure operadores e a Issue de controle em `.galaxy/team.yml`; cada PC mantém sua identidade em `local/operator.toml`, fora do Git. O workflow V2 é `galaxy-control`/`galaxy-validate.yml` conforme o pacote instalado. Claims, releases e merges usam a fila serializada, revisions e receipts. Consulte `docs/COOP-BOOTSTRAP.md` para permissões e smoke test; testes locais não comprovam regras remotas ou disponibilidade de modelos em outra conta.

## Migração V1 → V2

```powershell
python .\galaxy.py migrate --project 'C:\AI\Projetos\MeuProjeto' --what-if
python .\galaxy.py migrate --project 'C:\AI\Projetos\MeuProjeto'
python .\galaxy.py doctor --project 'C:\AI\Projetos\MeuProjeto'
```

A migração é semântica e restartável: preview, preflight, backup fora do projeto, aplicação, validação e receipt. Converte `AGENT_TEAM.yml` para `.galaxy/team.yml`, `.multicontroller/` para `.galaxy/` e workflows/checks para nomes Galaxy. Arquivos modificados pelo usuário, links/reparse points, secrets e conflitos bloqueiam a operação sem destruir conteúdo. Vaults encontrados são apenas inventariados; o usuário escolhe projeção ou declarations `project-owned`.

O rename físico de `C:\AI\Codex-Multicontroller` para `C:\AI\Galaxy-Orchestrator` só ocorre após parar shells/processos, registrar estado limpo e repetir testes + Doctor. Branches publicados `codex/*` não são renomeados automaticamente.

## Verificação

```powershell
python -m unittest discover -s tests -v
```

Consulte [PHASE-0-BASELINE.md](PHASE-0-BASELINE.md), [SPEC-V2.md](SPEC-V2.md) e [VALIDATION.md](VALIDATION.md) para evidências, limites e critérios. Nenhum comando de instalação publica dados nem inicializa Git automaticamente.
