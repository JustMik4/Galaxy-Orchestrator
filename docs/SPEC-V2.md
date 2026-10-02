# Galaxy Orchestrator V2 — especificação canônica

Este documento é a especificação executável de V2. Ele traduz o plano de atualização em contratos verificáveis e incorpora o vault opcional para Obsidian. A [SPEC-V1](SPEC-V1.md) permanece histórica; quando houver conflito, esta especificação prevalece para projetos V2.

## Identidade e compatibilidade

- Produto e CLI: **Galaxy Orchestrator** / `galaxy`.
- Repositório-alvo: `JustMik4/Galaxy-Orchestrator`; instalação futura: `C:\AI\Galaxy-Orchestrator`.
- Configuração de projeto rastreada: `.galaxy/project.yml`, `.galaxy/team.yml`, `.galaxy/checks.json` e `galaxy.lock`.
- `AGENT_TEAM.yml` migra semanticamente para `.galaxy/team.yml`; `multicontroller.py` permanece apenas como wrapper de depreciação.
- `.multicontroller/` e branches `codex/*` são reconhecidos somente para compatibilidade/migração; novos projetos usam `.galaxy/` e branches `galaxy/*`.
- `.codex/`, runtime, cache, instalação local e artefatos gerados são locais/não rastreados por padrão.
- Provedores LLM externos não fazem parte desta versão.

## Princípios

Role, specialist, model/effort e action capability são dimensões independentes. Há poucos roles operacionais (`root`, `explorer`, `researcher`, `worker`, `hard-worker`, `tester`, `reviewer`, `architect`) e especialistas Markdown carregados sob demanda. O root normal é GPT-6.1 Sol Medium; GPT-6.1 Sol High e GPT-6 Astra são escalonamentos justificados, e Astra nunca é root permanente. GPT-6 Luna permanece a rota econômica para trabalho focado quando o catálogo do host a confirma.

O Capability Router escolhe o menor custo esperado que satisfaça risco, histórico, capacidade observada do host e quota. Em empate exato de custo e capacidade, `gpt-6.1-sol` tem preferência sobre os demais modelos GPT-6, que têm preferência sobre gerações anteriores. O Emergency Router pode mudar modelo, effort ou contexto, mas exige evidência e nunca ignora a reserva de quota. O Runtime Verifier registra o modelo/effort realmente executados; configuração declarada não é evidência de execução.

Cada dispatch, retry ou escalation chama `dispatch authorize` antes de iniciar o subagente e usa somente a rota efetiva devolvida; a autorização requer suporte configurado e observado e aplica a quota local. O perfil padrão do projeto pode ser sobrescrito pelo contrato da tarefa apenas por um valor válido. Emergency exige `task_id` estável, evidência e motivo; seu limite é consumido atomicamente em estado local, nunca aceito como contador do request. Após o spawn, `dispatch verify` confirma o modelo/effort efetivos. Antes de revisão dispendiosa, `dispatch review-check` consulta o cache; somente fingerprint idêntico em head/base, escopo, contrato, testes, política e classe de revisor permite reutilização, que `dispatch review-record` registra. A normalização de escopo preserva caixa em sistemas case-sensitive.

Contratos continuam exigindo DAG acíclico, escopos literais, um escritor por escopo, worktree isolado, revisão independente quando aplicável, CI da versão atual e integração pela autoridade serializada. Estado CO-OP autoritativo é o coordenador serializado (Issue/workflow e receipts), nunca uma nota Markdown ou inferência de branch.

## Economia de contexto

Economia de contexto é política de transmissão interna, independente de quota.
Sua declaração canônica em `.galaxy/project.yml` é:

```yaml
context_economy:
  mode: off                    # off | balanced | aggressive
  handoff: {max_summary_tokens: 350}
  tool_output: {compress_success: true, preserve_failures: true}
  logs: {inline_max_lines: 80}
  evidence: {prefer_references: true}
  specialists: {progressive_disclosure: true}
```

Campos omitidos usam o preset do modo. `off` é o padrão compatível e não aceita
overrides de compactação. `balanced` usa por padrão 350 tokens aproximados no
handoff e 80 linhas; `aggressive`, 200 e 40. Esses limites não autorizam perda
de STATUS, BLOCKERS, RISKS, HEAD, decisões, identidade/mensagem/localização de
falhas ou evidência de validação. Overflow completo e sanitizado é persistido
em `.galaxy/evidence/<task>/` e transmitido por path e SHA-256; conteúdo idêntico
é deduplicado. Referências e corpos de especialistas são expandidos sob demanda.

O relatório de agente usa exatamente `STATUS`, `CHANGED`, `TESTS`, `DECISIONS`,
`BLOCKERS`, `RISKS`, `EVIDENCE` e `HEAD`. Saída bem-sucedida extensa pode virar
resumo mais referência; em falha, nomes, mensagens, locais e excertos relevantes
ficam inline. Redação de credenciais precede persistência. Telemetria mede tokens
estimados de entrada/saída, itens compactados, referências reutilizadas, bytes de
evidência e retries por contexto; jamais converte isso em quota ou publica uma
alegação de economia sem benchmark A/B.

`context-insufficient` não alimenta reputação negativa: expande evidência e
repete a rota atual antes de promoção. A migração V1 grava `off`. O Doctor relata
o modo efetivo e suas garantias. A implementação é nativa em `lib/context/`, sem
hook, proxy ou dependência Caveman.

## Layout e autoridade do vault Obsidian

O vault é uma projeção opcional por projeto, destinada à visibilidade e ao controle humano das tarefas. Não é requisito de bootstrap nem dependência de execução.

Configuração rastreada, sem caminhos absolutos ou segredos, em `.galaxy/project.yml`:

```yaml
vault:
  enabled: false
  # relativo ao projeto; para vault externo, use caminho configurado localmente
  path: .galaxy/vault
  mode: projection       # projection | project-owned
  sync: manual            # manual | on-event | on-command
  include: [tasks, milestones, summaries]
  exclude: [prompts, responses, telemetry, secrets]
```

`enabled: true` permite um vault interno (`.galaxy/vault/`) ou externo. O caminho interno deve ser um descendente estrito do projeto e nunca pode ser `.`/a raiz do repositório. Caminho externo e preferências do operador ficam em `.galaxy/local/operator.toml` (não rastreado); o lock registra apenas a forma normalizada da configuração, nunca o caminho privado. O caminho deve ser validado como absoluto conhecido, sem traversal, symlink/reparse point ou ser pai/filho do repositório de forma ambígua.

### Arquivos e rastreamento

- `project-owned`: notas/declarations explicitamente mantidas pelo projeto podem ser rastreadas e editadas pelo usuário; seu schema é validado. Ainda assim, o estado vivo de claim/revision/receipt continua no coordenador.
- `projection`: notas geradas determinísticas (`tasks/`, `milestones/`, `summaries/`) são derivadas de estado e devem ser ignoradas pelo Git, salvo opt-in explícito do projeto. Nunca conterão prompts completos, respostas, tokens, credenciais, caminhos pessoais ou telemetria bruta.
- `.galaxy/vault/.gitignore` deve ignorar saídas geradas; o bootstrap não deve capturar um vault externo nem copiar dados pessoais.
- Frontmatter mínimo: `galaxy_schema`, `task_id`, `revision`, `status`, `owner`, `updated_at` UTC e `source_receipt`; conteúdo humano fica após um marcador reservado.

### Sincronização e conflitos

O sincronizador lê snapshots do coordenador e produz a mesma árvore para o mesmo estado, configuração e versão. Ordenação, timestamps derivados e serialização são determinísticos; `updated_at` de projeção vem do evento-fonte, não do relógio local. Cada nota traz `source_receipt`/revision para impedir downgrade.

Com `enabled: true`, `galaxy vault sync` exige `--snapshot` contendo envelope autoritativo local e versionado exatamente nesta forma:

```json
{
  "schema_version": 1,
  "tasks": [{"task_id":"T-9","revision":1,"source_receipt":"receipt-1","status":"active","owner":"alice","updated_at":"2026-09-12T12:00:00Z"}],
  "authority": [{"task_id":"T-9","revision":1,"source_receipt":"receipt-1"}]
}
```

Os conjuntos e as tuplas (`task_id`, `revision`, `source_receipt`) de `tasks` e `authority` devem coincidir exatamente. Snapshots legados como objeto ou lista permanecem compatibilidade de status/leitura, mas não são aceitos para mutação. Com `enabled: false`, `sync` é no-op e não requer snapshot. Dependências são IDs escalares de tarefa; `include`/`exclude` é aplicado antes da renderização e não pode excluir frontmatter obrigatório de autoridade.

Em `projection`, edição humana na área gerada é sobrescrita somente com `--force` após backup; por padrão o sync recusa e relata drift. `--force` nunca contorna envelope/autoridade, monotonicidade de revision/receipt, frontmatter malformado ou duplicado, nem validações de caminho/link. Em `project-owned`, alterações concorrentes ou revision incompatível geram `migration-conflict`/`vault-conflict`, preservam ambos os conteúdos (`*.conflict-*`) e exigem resolução explícita; nunca fazem merge semântico por LLM. Enquanto houver conflito, o coordenador permanece autoritativo e nenhuma claim/merge é inferida da nota. Offline, o vault pode ser lido/editado, mas publicação exige `galaxy vault sync` e validação do receipt atual.

### Privacidade

Vault externo pode estar em área pessoal e não deve ser assumido como repositório Git. Exclusões são deny-by-default: prompts, respostas, segredos, `.env`, tokens, identificadores de conta, logs pessoais e conteúdo fora dos campos permitidos não são projetados. `galaxy doctor` sinaliza permissões excessivas, path fora da intenção, notas não rastreadas suspeitas e padrões de segredo; não envia conteúdo. O usuário controla habilitação, include/exclude e remoção do vault.

## Comandos V2

Comandos aceitam caminhos como argumentos posicionais, sem concatenação de shell. Comandos que produzem
relatórios estruturados escrevem JSON por padrão; o Doctor oferece `--json` explicitamente:

```text
galaxy install PROJECT [--mode SOLO|CO-OP] [--preset balanced|critical] [--context-economy off|balanced|aggressive] [--check]
galaxy init PROJECT [--mode SOLO|CO-OP] [--preset balanced|critical] [--context-economy off|balanced|aggressive] [--check]
galaxy bootstrap PROJECT [--context-economy off|balanced|aggressive] [--check]
galaxy migrate PROJECT [--preview | --rollback RECEIPT]
galaxy validate PROJECT [--gate]
galaxy doctor PROJECT [--json]
galaxy lock sync PROJECT [--check]
galaxy dispatch authorize PROJECT --request FILE --capabilities FILE --quota FILE [--operator-config FILE]
galaxy dispatch verify PROJECT --dispatch-id ID --spawn-id ID [--effective-model MODEL] [--effective-effort EFFORT] [--parent-thread ID]
galaxy dispatch review-check PROJECT --fingerprint FILE
galaxy dispatch review-record PROJECT --fingerprint FILE --evidence FILE
galaxy specialists list
galaxy specialists sync PROJECT [--check]
galaxy cleanup PROJECT [--preview | --apply] [--retention-days DAYS]
galaxy vault status PROJECT [--snapshot FILE]
galaxy vault sync PROJECT [--snapshot FILE] [--check] [--force]
```

`vault sync --check` não grava; `--force` é explícito e só permitido após backup/drift report, sem relaxar os invariantes de autoridade e integridade do Vault. A sincronização não despacha agentes nem altera claims. `cleanup` aceita apenas retenção finita e não negativa e nunca remove os estados canônicos de verificação ou de orçamento emergency. Doctor verifica namespace legado, lock, manifests, drift, permissões do vault, notas fora do schema, segredos, runtime/quota/action capabilities, arquivos gerados rastreados e conflitos pendentes.

Action Resolver e Resource Catalog são APIs internas nesta versão. Não há comandos públicos `route`,
`specialist explain`, `vault export` ou `doctor --fix-safe`; documentação e automação não devem presumir
superfícies que a CLI não registra. Roteamento operacional e revisão são públicos exclusivamente pela
família `galaxy dispatch` listada acima.

O Action Resolver privilegia capabilities programáticas sem confirmação extra.
Uma solicitação explícita do usuário autoriza passos locais reversíveis e o lote
externo nomeado. Browser, credencial/permissão ausente ou novo efeito público e
irreversível exigem uma única aprovação escopada; esse grant cobre todas as
subações declaradas do lote e não pode autorizar outro namespace destrutivo.

## Lock, bootstrap e migração

`galaxy.lock` fixa versão do schema, release, hashes de declarações/especialistas/adapters e política efetiva. O mapa `declarations` contém exatamente SHA-256 de `AGENTS.md`, `.galaxy/project.yml`, `.galaxy/team.yml` e `.galaxy/checks.json`. O loader autentica e faz parse do mesmo snapshot de bytes; divergência falha fechada. Uma mudança revisada é aceita somente por `galaxy lock sync PROJECT`; `--check` é somente leitura. Fresh clone + bootstrap recria runtime Codex local sem vendorizar ferramentas no produto. `.galaxy/install/bootstrap-state.json` é ignorado e registra os bytes gerados de que o bootstrap é dono; marcador textual não autoriza alteração e estado ausente, corrompido ou divergente preserva artefatos como drift. O bootstrap não altera config global, login ou trust.

Migração V1→V2 é semântica e restartável: preflight/preview, classificação (gerenciado, projeto-owned, gerado modificado), backup fora do projeto, aplicação, validação, receipt durável e rollback explícito. `AGENT_TEAM.yml` → `.galaxy/team.yml`, `.multicontroller/` → `.galaxy/` e workflows/checks têm regras próprias; nunca há substituição global de strings. Vaults V1, se encontrados, são apenas inventariados até o usuário escolher projeção ou declarations project-owned. Conflitos bloqueiam o commit da migração, preservando origem e destino.

## Testes e aceitação

Além dos invariantes V1, V2 exige testes RED→GREEN para: catálogo/roteamento por capacidade; emergency e quota hard stop; verificação de modelo/effort; fingerprint/cache de review; parsing/adapters especialistas; bootstrap sem runtime vendorizado; migração preview/backup/rollback/receipt; Doctor e pollution detector; Action Resolver; lifecycle; e SOLO/CO-OP.

Vault exige testes para: disabled/no-op; path interno e externo; projeção determinística byte-a-byte; filtro de privacidade; frontmatter/schema; drift e `--force`; revision/receipt stale; conflito project-owned; offline read-only; Doctor e migração. Testes não podem acessar vault pessoal real, modelos, credenciais ou publicar no GitHub.

## Decisões de implementação

Resource Catalog/public-apis é secundário e opcional. Browser é fallback com aprovação quando connector/API/CLI não existir. Quota padrão para novos dispatches: parar com `<=15%` restante em cinco horas ou `<=2%` semanal; hard stop permite apenas cleanup/handoff. Review é independente e reutilizável por fingerprint somente quando head/base, escopo, contrato, testes, política e classe de revisor coincidirem. O Lifecycle Manager recusa componentes link/reparse, vincula a identidade do preview ao apply, faz estágio e reverificação antes de apagar em POSIX e apaga no Windows somente pelo handle exato verificado.
