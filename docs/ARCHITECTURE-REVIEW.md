# Auditoria arquitetural — antes da implementação

Versão 1.3: integração equivalente por fila Actions e Issue canônica substitui lead pessoal fixo. Seções anteriores registram histórico e não prevalecem sobre a SPEC atual.

Histórico V1. A revisão 1.2 remove dependência de parceiro: cross-review opcional, Reviewer independente
local obrigatório e retomada pelo principal sem ACK após impedir integração antiga. A SPEC atual prevalece.

Revisão crítica equivalente ao papel solicitado de Astra; não afirma chamada a um modelo externo específico.

| Finding | Gravidade | Correção congelada |
|---|---|---|
| Check-then-assign em Issue permite dois owners | P0 | R07: grants serializados por lead único; assignee não é lock |
| TTL permite laptop antigo continuar escrevendo | P0 | Sem expiração automática; release/stop ou revogação administrativa |
| Shared files permitem conflitos indiretos | P1 | R04: compartilhado só leitura, ownership exclusivo para alterar |
| Config global + cópias divergentes da skill | P1 | Snapshot por projeto com versão e hashes; global intocada |
| Todos os erros sobem modelo | P1 | R05 classes externas bloqueiam, não penalizam reputação |
| Retry budgets separados permitem loop de escalada | P1 | Teto agregado por task, escalada consome tentativa |
| Root Astra permanente e supervisores duplicados | P2 | Critical faz handoff explícito para Sol; sem root duplo |
| Issue closed confundida com integração | P1 | DAG libera por merge/SHA presente |
| Review antigo + commit novo | P0 | R09 revisão e CI da versão atual; proteção externa requerida |
| Markdown tratado como sandbox | P1 | Protocolo cooperativo explícito, verificação de diff + CI |
| Subagentes compartilham filesystem | P1 | Worktrees explícitos ou execução serial; não presumir isolamento |
| Uso estimado apresentado como cota real | P1 | Deltas, null, deduplicação e sem conversão de assinatura |
| Aprendizado muda política durante execução | P1 | Task adapta; project/environment somente PR + review |
| Instalador sobrescreve trabalho existente | P0 | Preflight, manifest, recusa de drift, backup e rollback |
| Workflow genérico verde sem testar produto | P1 | Comandos de produto obrigatórios no gate, vazio falha |
| Windows links/drive/caixa e shell injection | P1 | Paths literais, contenção, reject links e argumentos sem shell |

Alternativas: bot transacional de claims traz operação/credenciais extras; branch de lock viola preferência por estado em Issues e exige protocolo de concorrência adicional. Lead serializado é implementável na V1 nativa, com indisponibilidade explícita quando lead/offline. Não promete progressão autônoma 24/7.

Decisão: SPEC V1 corrigida apta à implementação do pacote. Ativação real exige projeto, identidades GitHub, comandos de produto e smoke test, que não foram fornecidos nesta tarefa de empacotamento.

## Revisão independente da implementação

Revisor com contexto novo examinou código e SPEC, reproduziu cinco findings: gate sem modo válido;
scope string em vez de lista; ausência de binding à base de integração; login GitHub comparado com caixa;
lock do instalador adquirido após leitura. Todos receberam testes de regressão RED e correções GREEN.
Na revisão das correções encontrou `src//` escapando do overlap; agora segmentos vazios são recusados.
Revisão local adicional encontrou reputação usando sucessos High como evidência barata; restringido a Luna Low/Medium.

Os testes de upgrade também verificam rollback após falha de disco simulada e preservação de configuração de equipe.

## Revisão independente 1.2

Sem finding importante no código de revisão/retomada/migração. Corrigida a redação absoluta sobre ACK de handoff na SPEC para distinguir recuperação sem parceiro.
