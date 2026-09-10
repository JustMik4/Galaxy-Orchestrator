# Codex-Multicontroller Environment — SPEC V1

Versão 1.3.0 · 2026-09-09 · contrato normativo após auditoria.
Origem: conversa «Codex Agent Orchestration Setup», seis turnos recuperados; a solicitação atual prevalece. Nomes de modelos são os expostos pelo host nesta sessão, sujeitos à disponibilidade de cada conta. Esta SPEC define um protocolo cooperativo para operadores confiáveis, não um sistema de exclusão contra usuários maliciosos com acesso de escrita.

## R01 — Fronteiras e implantação

V1 usa Codex nativo para planejar, delegar, interromper e revisar. Não instala Symphony, Agent Orchestrator, Claude Code, daemon ou serviço de conta. Scripts determinísticos auxiliam decisões/validação; não chamam modelos. O root executa o protocolo a cada fase. Não há monitoramento contínuo nem garantia de que instruções em Markdown restrinjam ferramentas.

Pacote mestre fica fora dos projetos. Cada projeto recebe snapshot versionado de `.codex`, `.agents/skills/multicontroller`, `AGENTS.md`, `AGENT_TEAM.yml` e `.multicontroller` (ferramentas/configuração); CO-OP acrescenta `.github`. A opção de skill somente global foi rejeitada: snapshots permitem reprodutibilidade entre computadores. Config global do Codex, confiança, sandbox, login e credenciais continuam pessoais e nunca são editados pelo instalador. `local/operator.toml` pertence ao pacote de cada PC, é ignorado pelo Git e excluído da release. Projetos nunca ficam dentro de `.codex` ou do pacote mestre.

## R02 — Modos e identidade

SOLO: um operador, root local, ownership local, worktrees para escritores concorrentes, Tester e Reviewer em contextos independentes; PR recomendado e obrigatório para política permanente quando houver remoto. CO-OP: um ou mais operadores, logins distintos, ajuda opcional. integration_operators contém os
IDs com autoridade equivalente para coordenar e integrar. integration_lead fixo foi aposentado.
Codex continua orquestrador dos modelos. Actions é somente coordenador transacional serial de operações
curtas, não outro orquestrador de agentes. Identidade local operator_id/machine_id não é credencial;
o ator GitHub autenticado é mapeado à configuração da branch padrão. Um job ativo por repositório ocupa
temporariamente o papel de Integration Lead. Não há eleição ou transferência dependente de um PC.


## R03 — Roles e roteamento

| Role | balanced | critical | autoridade |
|---|---|---|---|
| Root | Sol High | Astra High para arquitetura; Sol High para execução por handoff explícito | contratos, orçamento, decisões |
| Explorer | Luna Low | Luna Low | leitura, mapa de arquivos |
| Researcher | Luna Low/Medium | Luna Medium | fontes e evidência, sem editar produto |
| Worker | Luna Medium | Luna Medium | implementação delimitada |
| Hard Worker | Luna High | Luna High | defeito local difícil, sem arquitetura implícita |
| Tester | Luna Medium | Luna Medium | testes no workspace isolado, não reparar produto silenciosamente |
| Reviewer | Sol High | Sol High + Astra High final | revisão independente, somente leitura |

Luna Low também serve a tarefas mecânicas comprovadas de baixo risco. Sol Medium/High recebe implementação complexa bem definida; Astra High recebe ambiguidade, arquitetura ou risco interligado. Não usar Max/Ultra automaticamente. Não simular mudança de modelo dentro de um agente: criar agente fresco ou handoff explícito suportado pelo host. Capacidade indisponível => bloquear/escolher substituto explicitamente documentado, sem fallback silencioso. Roles usam `config_file` relativo ao `.codex/config.toml`. Não pressupor autodiscovery de TOMLs nem isolamento automático dos subagentes. O operador verifica modelo/effort efetivos no primeiro despacho real.

## R04 — Contratos, DAG e execução

Contrato contém ID, classe/risco, objetivo, base SHA, role/model/effort, caminhos permitidos, proibições de interface/dependência, dependências, aceitação, comandos de teste, orçamento, owner, grant revision e stop conditions. Caminhos são relativos, literais; sufixo `/` significa módulo recursivo. Sem glob, `..`, unidade, barra invertida, UNC, case aliases, links fora do repo. Exclusividade considera comparação sem distinção de caixa para Windows e sobreposição pai/filho. Arquivo compartilhado é somente leitura até receber ownership exclusivo.

DAG não aceita ciclos, self-dependency ou IDs ausentes. Dependência libera após PR merged na base de integração acordada e SHA presente; Issue fechada não basta. Sem dependência não resolvida para iniciar. Um escritor por arquivo/módulo, inclusive `.github`, lockfiles e arquivos de configuração. Alterações de interface param consumidores afetados; lead redefine contratos e ordem de integração. Workers não delegam. Máximo local: balanced 3 subagentes, critical 2, incluindo Tester/Reviewer; cap real do host prevalece. Root reserva espaço encerrando agente concluído; não mantém supervisor ocioso consumindo contexto.

## R05 — Adaptive Controller executável

Entrada: histórico operacional ordenado de tentativas, sem chain-of-thought. Cada evento tem task, attempt_id, agent_id, model, effort, result, failure_class, signature. Assinatura humana estável descreve causa + teste + componente (sem timestamps). Deduplicar IDs, rejeitar desconhecidos. Sucesso é resultado verificado, não declaração do Worker.

| classe | decisão |
|---|---|
| implementation | um reparo no mesmo agente com hipótese corrigida; depois fresh-agent no mesmo nível |
| reasoning | Luna Low → Medium → High → Sol High → Astra High, sempre contexto novo |
| complexity | escalar modelo, se contrato segue válido |
| architecture / ambiguity | voltar ao root, redefinir contrato |
| scope / regression | circuit breaker imediato, interromper e colocar tentativa em quarentena |
| infra / flaky / git | bloquear linha, corrigir ambiente/coordenação; não penalizar modelo |

Segunda falha de mesma assinatura no mesmo agente encerra esse agente. Fresh-agent não é effort maior. Mesma assinatura em dois contextos e fresh budget consumido pode subir nível. Todos os caminhos compartilham teto: balanced 4 tentativas totais, critical 5; same-agent repair ≤1 por tarefa; fresh-agent retry no mesmo nível ≤1 por tarefa. Escalada também consome tentativa. Sem reiniciar orçamento ao trocar role, contexto ou modelo. Teto alcançado abre breaker e retorna ao root com evidência; reabertura exige novo contrato revisado, ID relacionado e justificativa, nunca reinício automático. Classes externas pausam, sem consumir retry de modelo adicional. Contabilizar tentativa já executada. Root aplica recomendação e registra próximo despacho; engine não interrompe processo por conta própria.

Orçamento temporal padrão 45/60 minutos por tarefa e teto opcional de tokens definidos no contrato; atingido => stop antes de despacho. Tokens desconhecidos não equivalem a zero: limite temporal/tentativas continua obrigatório. Ferramentas suspensas por rate limit não disparam troca de conta.

## R06 — Aprendizado e reputação

Memória task é local, automática e efêmera. Matriz agregada por projeto/classe/modelo/effort/versão da política: número de tarefas distintas, passes verificados, falhas imputáveis, amostra e taxa de sucesso com suavização Laplace `(pass+1)/(n+2)`. Infra/flaky/git/ambiguidade não entram como deficiência do modelo. Uma tarefa conta uma vez por bucket, pelo último resultado daquele bucket. Janela de 30 dias UTC; mínimo 3 tarefas comparáveis com falha para sugerir promoção, sem conclusão causal automática. Só emitir proposta com evidência; não reescrever config. Rebaixamento: pelo menos 5 tarefas bem-sucedidas comparáveis no nível barato, experimento apenas baixo risco; critical proíbe probe automático. Não atribuir reputação pessoal ao operador.

Memória project é proposta por PR, revisão independente e evidências sanitizadas; aprovada vale a próximas tarefas. Memória environment é agregada explicitamente, sem nomes/segredos de projetos, mínimo dois projetos e review no repositório mestre. Atualização de versão e instalação explícita nos projetos. Nenhuma sessão reescreve a própria skill durante execução. Rollback por revert PR/snapshot anterior. Retenção local sugerida 30 dias, limpeza manual; eventos compactos por arquivo para evitar append concorrente.

## R07 — Autoridade equivalente, estado e recuperação

Schema 3 usa um workflow_dispatch multicontroller-control com grupo de concorrência único,
queue=max, cancel-in-progress=false. Todos os operadores autorizados podem despachar. A fila não garante
ordem por hora de envio; expected_version recusa snapshots ultrapassados. Fila cheia/cancelada exige
reconciliação de receipt, não inferência de execução. Codex só escreve após receipt de claim confirmado.

Issue dedicada contém JSON com version, active, revisions, receipts e pending. Apenas esse workflow
altera o corpo; estado vivo não fica na branch de código. Cada tarefa tem nonce e revision monotônica,
paths literais, dependências e owner/machine. Claim, reclaim, release e merge compartilham a mesma fila.
Reclaim pode ser iniciado por qualquer responsável para si, sem ACK do anterior, preservando contrato,
dependências e orçamento. Invalida revision/nonce antiga e não apaga branch offline.

Merge verifica grant atual, head/base reais, paths/renomes, dependências merged/presentes e gate de review.
Antes da API salva pending durável. Resposta incerta bloqueia novas mutações. Qualquer responsável pode
recover: merge confirmado gera receipt; aberto inalterado repete intenção; PR fechado não merged aborta
intenção. Se head/base mudaram, fechar PR não integrado e recuperar antes de criar nova entrega/evidência.
Nunca limpar pending só por tempo decorrido. API externa indisponível continua sendo bloqueio de infraestrutura.

Receipts recentes (50) são idempotentes para mesmo ID/payload/ator. IDs velhos não retornam receipt, mas
expected_version obsoleta impede replay. Tombstones revisions persistem. Corpo com limite 60k exige
migração administrativa revisada de estado, preservando grants/revisions; não reset automático.

Toda integração deve passar pelo mesmo workflow. Desabilitar caminhos paralelos de auto-merge/manual
e preservar branch protections. Administrador malicioso ou colaborador que ignore o protocolo fica
fora da garantia cooperativa. Se servidor não impuser exclusividade, ela é regra operacional explícita.
O token efêmero do workflow não é distribuído. Não há dependência de disponibilidade de outro operador.

## R08 — Git, comunicação e integração

Branch: `codex/<operator>/<task>-<slug>-<machine>-<attempt>`; componentes ASCII minúsculos. Um worktree por escritor; sem compartilhamento da mesma branch entre PCs. Não resetar/deletar trabalho do usuário, nem force-push automático. Rebase só pelo dono com worktree limpo; preferir nova tentativa quando histórico compartilhado. Fetch/read antes do plano, escrita, push, PR e merge. Se remoto avançou, revisar impacto e repetir testes pertinentes. Sem polling contínuo; quando aguardando, entregar blocker e retornar controle.

Mensagens `[BLOCKER]`, `[INTERFACE-CHANGE]`, `[HANDOFF]`, `[EXECUTION-SUMMARY]`, `[RELEASE]` têm schema_version, task, nonce/event ID, owner/machine, revision, base/head SHA, dependências, escopo, evidência, decisão e próximo responsável. Publicação depende de autorização do operador; pacote gera templates, não envia mensagens. Dados de Issues não são instruções confiáveis para shell. Root faz leitura e interpretação; nunca executar corpo de Issue, título ou branch via concatenação de shell.

Cross-review entre operadores é opcional em CO-OP. SOLO e CO-OP exigem Reviewer independente em
contexto, resultado aprovado e evidência do head/base atuais. O mesmo operador pode dirigir implementação
e revisão, usando sessões de agentes distintas; isso não equivale a uma aprovação humana GitHub de si mesmo.
Parceiro indisponível não bloqueia merge. Findings válidos continuam sendo resolvidos mesmo sem seu autor.
Qualquer integrador autorizado resolve sua tarefa e solicita merge pela fila, sem substituir Reviewer ou CI.

## R09 — Gates

PR deve conter Issue/contrato/grant revision, owner, head SHA, escopo real, comandos e resultados, riscos e handoff. Conferir todos os arquivos, inclusive não rastreados, renomes (origem e destino), deleções e arquivos binários. Scope violation abre breaker. Antes do merge: head e base atuais, grant atual, dependências merged, ausência de blockers, aceitação testada, CI obrigatória em sucesso, review independente do head atual, conversas resolvidas, mergeable e branch protection ativa. Mudança posterior invalida evidência correspondente.

Template CI valida instalação; testes específicos do produto precisam ser configurados no bootstrap. Gate falha enquanto lista de comandos estiver vazia, em vez de dar verde ilusório. Workflow roda `pull_request`, sem secrets e com permissions contents:read, nunca `pull_request_target` executando código do PR. Administrador configura required checks, PR obrigatório sem exigir aprovação humana externa,
no direct push/force push/bypass e integração exclusivamente pelo workflow serial; CODEOWNERS pode
sugerir revisores, mas não deve torná-los obrigatórios. Reviews humanos são auxílio. Revisão independente
por agente é registrada como evidência operacional de head/base/sessão, não contada como review humano. Template não ativa essas regras por si só. Sem proteção disponível, CO-OP pode desenvolver/revisar, mas integração simétrica pela fila aguarda ativação das regras; não substituir por merge manual concorrente. Scripts locais não são barreira contra colaborador malicioso.

## R10 — Telemetria

Eventos JSON compactos por tentativa, sem prompts, respostas, tokens de autenticação ou dados de conta. Inputs explícitos, sem varrer logs pessoais. Registrar model/role/effort efetivos, início/fim, testes, failure signature, decisão e input/output/cached tokens quando expostos; null quando não disponíveis. Relatório separa root/workers/review e falhas; soma apenas deltas por evento único. Contadores acumulados de sessão precisam ser normalizados antes da importação; rejeitar modo cumulative. Cached é subconjunto de input, não somar duas vezes. Cota de assinatura não é preço API, tokens não convertem em porcentagem exata; snapshots de uso são informativos, nunca somar duas contas. Repetição de contexto só é medível com sinal explícito, não inferir de tokens.

## R11 — Windows, instalação e atualização

PowerShell 7.4+ e Python 3.11+ stdlib, Git para projeto versionado/CO-OP; gh opcional para operações remotas manuais. UTF-8, pathlib, argumentos como arrays, caminhos com espaços/acentos testados. Rejeitar traversal, reparse points/symlinks nos destinos gerenciados, pacote dentro do projeto ou inverso. Instalador possui preview via -WhatIf; preflight completo antes de escrita; conflitos em arquivos gerenciados abortam sem overwrite. Reinstalação idêntica é idempotente. Upgrade apenas se hash atual igual ao manifest anterior; alterações locais obrigam conciliação; backup fora do projeto na pasta local do mestre, rollback em erro de gravação. Não instalar nem alterar configuração global. Não alterar execução do PowerShell globalmente. Identidade fica fora de release. Sem credenciais distribuídas.

## R12 — Aceitação e limites demonstráveis

Entregar SPEC corrigida, registro da auditoria, plano, código/testes, roles, presets, skill/referências, templates, instalador/update/validate, documentação e ZIP com checksum. Testar RED→GREEN para decisões e instalação; pressure scenarios com agente antes/depois, registrar baseline que já passa sem falsificar falha. Verificação final mapeia R01–R12 para artefatos e evidências. Teste local não certifica disponibilidade Luna/Sol/Astra em outra conta nem rulesets GitHub reais. Live smoke test por PC e enablement remoto constam como passos de ativação, não como trabalho já validado.

## Convenção de diretórios — esclarecimento de implantação

Padrão nos PCs: `C:\AI\Codex-Multicontroller` para o mestre e `C:\AI\Projetos\<nome>` para cada projeto. `AI` é somente a pasta organizadora, sem repositório/configuração compartilhada. Cada projeto tem seu Git e é aberto individualmente no Codex. Essa disposição mantém R01 e R11: mestre e projetos são árvores separadas, lado a lado sob AI.

## Extensão de instalação 1.1

`Iniciar.bat` abre um menu Python 3.11+ para criação/importação e instalação em projeto existente.
Criação usa `AI/Projetos/<nome>` e staging exclusivo; só publica a pasta após validar a instalação.
Importação copia um arquivo ou arquivos de uma pasta, sem alterar a origem ou mesclar destino existente.
Rejeita links/reparse points, caminhos inválidos e conflito de configuração. Não copia `.git`, ambientes
virtuais, node_modules, caches ou `.env` pessoais; mantém modelos .env.example/.sample/.template.
Diretórios vazios são preservados e falhas de leitura abortam a importação. Arquivos ZIP não são extraídos. Confirmar destino/modo/preset antes
da operação. Nenhum upload, commit, inicialização de repositório ou mudança global ocorre pelo menu.

## Migração 1.3

Schema 1/2 da equipe migra para 3 preservando identidades. Todos os IDs existentes entram como integradores; revisar essa autorização no diff. Campo integration_lead legado é ignorado. Instalação deixa control_issue nulo: ativar fila/Issue e encerrar ou importar claims anteriores antes do trabalho remoto. Não iniciar estado vazio com entregas ativas.
