# Changelog

As mudanças relevantes do Galaxy Orchestrator são registradas aqui. A especificação histórica V1 permanece em [SPEC-V1.md](SPEC-V1.md).

## 2.0.0 — candidato V2

### Identidade e contrato do projeto

- produto e CLI canônicos renomeados para Galaxy Orchestrator e `galaxy`;
- namespace de projeto movido para `.galaxy/`, com `galaxy.lock` reproduzível;
- lock autenticando os quatro arquivos declarativos, com sincronização explícita e checkout CRLF protegido;
- `.codex/` passou a ser saída local gerada, não conteúdo vendorizado;
- `multicontroller.py` mantido temporariamente como wrapper de compatibilidade;
- branch prefix nova `galaxy/*`, com reconhecimento de `codex/*` no ciclo de vida histórico.

### Orquestração

- Sol Medium como root normal gerado pelo adaptador Codex;
- Capability Router com perfis `balanced` e `critical`, rotas intermediárias e escalada emergencial;
- Quota Guard local, com reserva padrão de 15% na janela de cinco horas e 2% na semanal;
- Runtime Verifier para registrar modelo/esforço efetivos e separar mismatch de roteamento de falha do modelo;
- cache de revisão por fingerprint de evidência para evitar revisões idênticas;
- limite operacional público de despacho: autorização antes de dispatch/retry/escalation, verificação do runtime efetivo e consulta de cache antes de revisão dispendiosa;
- especialistas com fonte Markdown/frontmatter, catálogo frio, hot set e materialização determinística.

### Operação e segurança

- instalação e bootstrap V2 com separação entre declaração versionada e artefato gerado;
- estado de bytes do bootstrap em `.galaxy/install/bootstrap-state.json`, preservando drift quando autoria gerada não é comprovada;
- migração V1.3→V2 sem substituição global, com preview, backup externo, receipt, conflitos bloqueantes e rollback;
- coordenação CO-OP V2 por workflow serializado, fixado à versão do lock e instalado somente nesse modo;
- Galaxy Doctor para configuração, lock, bootstrap, Git, legado, checks, runtime, quota, ações, Vault e ciclo de vida;
- Lifecycle Manager com preview padrão e aplicação explícita;
- limpeza endurecida contra links/reparse e troca após preview, com estágio e reverificação em POSIX e handle verificado no Windows;
- Action Resolver com navegador apenas após aprovação explícita;
- Resource Catalog local e determinístico, com importador offline de candidatos `public-apis` não verificados;
- projeção opcional em Obsidian Vault, desativada por padrão e com exclusões de privacidade.
- sync de Vault habilitado somente com envelope autoritativo versionado e conjunto de autoridade correspondente.

### Compatibilidade

- garantias V1 de SOLO, CO-OP, um escritor por escopo, DAG, revisão independente, merge gate, rollback, checks, Windows e Python 3.11+ preservadas no contrato V2;
- `.multicontroller/`, `AGENT_TEAM.yml`, `.multicontroller/tools` e metadados antigos reconhecidos somente para migração/compatibilidade;
- documentação V1 preservada como histórico.

### Estado da aceitação

A aceitação local e o smoke descartável da Lexy estão registrados em [VALIDATION.md](VALIDATION.md). A renomeação física para `C:\AI\Galaxy-Orchestrator`, a publicação/tag do repositório canônico no GitHub e a configuração final de rulesets permanecem externas; até essas evidências existirem, a versão continua candidato V2.

## 1.3.0 — histórico

- presets SOLO e CO-OP;
- manifestos, política e checks do projeto;
- coordenação serializada por issue no GitHub;
- automação `validate`/`gate` e ferramentas vendorizadas no projeto;
- validação independente, protocolo de claim e recuperação V1.

Os detalhes normativos dessa linha estão em [SPEC-V1.md](SPEC-V1.md).
