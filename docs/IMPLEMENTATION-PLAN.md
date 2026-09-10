# Codex-Multicontroller Implementation Plan

Goal: entregar pacote portátil V1 conforme SPEC-V1.md, sem tocar config pessoal.
Architecture: Codex nativo executa protocolo; Python stdlib fornece validações determinísticas; PowerShell expõe instalação Windows.
Tech stack: Python 3.11+, PowerShell 7.4+, TOML e JSON (JSON é subconjunto YAML para AGENT_TEAM.yml).

## Sequência e rastreabilidade

- [x] Consolidar R01–R12 e corrigir races antes de implementar.
- [x] T1: `tests/test_core.py` primeiro: `decide(history,preset)`, `overlap(a,b)`, `check_dag(tasks)`, `grant(active,request,lead)`, `gate(evidence)`, `usage(events)`, `reputation(events)`. Esperados literais para budgets, stale heads, scope conflict, dedup e infra. Rodar unittest e registrar RED. Implementar `lib/multicontroller.py`, repetir GREEN.
- [x] T2: `tests/test_install.py`: executar PowerShell em diretórios temporários com espaços/acentos, SOLO/CO-OP, preview, reinstall, drift, symlink e config preexistente. Testar efeitos e hash, não texto fonte. Implementar `lib/installer.py`, `scripts/install.ps1`, `update.ps1`, `validate.ps1` e templates. Validar GREEN.
- [x] T3: pressure baseline com agente fresco antes de skill; guardar respostas. Criar `.agents/skills/multicontroller/SKILL.md` e referências com regras específicas ausentes na baseline. Repetir cenários em contexto fresco. Registrar falhas e limites sem alegar garantia estatística.
- [x] T4: README, bootstrap de dois PCs, schemas de mensagens, presets, templates CI/Issues/PR/CODEOWNERS. Testar instalação final e gates positivos/negativos.
- [x] T5: auditoria independente, corrigir findings com regression tests; executar suite completa, registrar VALIDATION.md R01–R12, criar manifest da release e ZIP sanitizado.

## Comandos de verificação

`python -m unittest discover -s tests -v` na raiz do pacote.
`pwsh -NoProfile -File scripts/validate.ps1 -ProjectPath <sandbox>` após instalação.
`python lib/multicontroller.py validate <sandbox> --gate` deve falhar com comandos do produto vazios; configure comando real de produto para verde.

Não há remoto configurado para publicar; entregar artefatos locais. Não instalar no projeto pessoal real nem abrir PR remoto em nome do usuário sem destino.
