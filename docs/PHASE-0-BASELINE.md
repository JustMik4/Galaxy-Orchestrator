# Phase 0 — baseline e architecture lock

Data da auditoria: **2026-09-12** · Windows · Python 3.14.6 · PowerShell 7.6.5 · Git 2.55.0.

## Escopo e fonte

O baseline foi levantado antes da implementação V2, comparando o plano `GALAXY_ORCHESTRATOR_V2_UPDATE_PLAN.md` fornecido pelo usuário com `README.md`, [SPEC-V1](SPEC-V1.md), [ARCHITECTURE-REVIEW](ARCHITECTURE-REVIEW.md), [VALIDATION](VALIDATION.md), código, testes e `docs/INSTALLATION.md` recuperado do commit `2303a0a`. Instruções do plano foram tratadas como especificação a implementar, não como evidência de que algo já existe.

## Repositório e árvore

O clone local agora possui histórico Git do upstream `JustMik4/Galaxy-Multicontroller`, mas a árvore de trabalho não está limpa: `.gitignore` e `README.md` têm modificações, `LICENSE` e `docs/INSTALLATION.md` aparecem removidos localmente, e há `RELEASE-MANIFEST.json` e `tests/pressure-scenarios/` não rastreados. Essas alterações preexistentes não foram sobrescritas. O baseline de arquivos do release registra 85 arquivos com hashes esperados; os extras observados são bytecodes/cache e backups locais, quando presentes.

## Estado V1 observado

V1.3 contém CLI/helpers determinísticos, installer com preview/manifest/drift/backup/rollback, coordenador CO-OP serializado, presets e testes para contratos, DAG, ownership, gates, recuperação, instalação e migração. `local/operator.toml` não é distribuído. A documentação histórica registra 52/52 testes, mas a execução atual deve ser repetida pela raiz antes do primeiro commit V2; o teste PowerShell pode falhar em máquinas com `Zone.Identifier` sob `RemoteSigned`, sem alterar a política global.

## Lacunas V2 registradas

Ainda ausentes no baseline: CLI/namespace `.galaxy`/`galaxy.lock`; Capability/Emergency Router e Quota Guard; runtime verifier; review fingerprint; especialistas Markdown/cold catalog/hot set/adapters; bootstrap não vendorizado; migração versionada; Action Resolver; Doctor/pollution detector; Lifecycle Manager; role architect; e classes de falha `host-routing`, `quota`, `capability-missing`. O vault Obsidian também é requisito novo e não existe em V1.

## Architecture lock

Ficam congeladas para a implementação: Galaxy Orchestrator, CLI `galaxy`, `AGENT_TEAM.yml` → `.galaxy/team.yml`, `multicontroller-validate.yml` → `galaxy-validate.yml`, novos branches `galaxy/*`, wrapper legado temporário, root normal Sol Medium, Astra somente excepcional, roteamento por capacidade, specialists Markdown + adapters Codex, declarações/lock rastreados e runtime gerado local.

O vault Obsidian é **opcional**, configurável por projeto e sempre projection por padrão. O coordenador serializado é a única autoridade para claims, revisions, receipts, owners e integração. Somente declarações explicitamente `project-owned` podem ser editadas no vault e, mesmo assim, não substituem estado CO-OP. Sync é determinístico, com receipts/revisions, filtro de privacidade, drift/conflict explícitos e sem merge automático por LLM.

## Critérios de saída da Phase 0

- SPEC V2 canônica e baseline versionado na documentação.
- Instalação documentada com namespace V2, migração e vault.
- Baseline de testes e limitações reais preservados, sem falsificar 52/52.
- Nenhuma alteração em `lib/`, `tests/` ou templates nesta fase.
- Próxima fase começa por testes de roteamento/quota e não por rename físico da instalação.

