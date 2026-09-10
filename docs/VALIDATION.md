# Verificação final da release 1.3.0

2026-09-10 · Windows · Python 3.14 local · PowerShell 7 · Codex CLI 0.153.4.

Release 1.2: seis novos testes RED→GREEN de CO-OP com um operador, Reviewer independente,
retomada sem ACK, recusa de retomada sem fencing/autorização e migração da política.
Evidência GitHub é fornecida pelo root; o helper não modifica o servidor.

Version 1.3: 11 testes de coordenação com transporte simulado: autoridade simétrica, conflitos, replay,
revocation, merge serial, erro após merge, recuperação por outro operador, head alterado/fechado e
preservação de dependências. Não são testes live do GitHub Actions. Ativação ainda precisa de smoke test.

## Evidência executável

Comando: `python -m unittest discover -s tests -v`, na raiz do pacote.
Resultado: **52 testes passaram, zero falhas, zero skips**.

Release 1.1: oito testes novos RED→GREEN para criação/importação, preservação da origem,
recusa de destino existente, nomes inválidos e rollback após conflito. `Iniciar.bat` também foi
executado no Windows para verificar abertura e saída do menu. A execução automatizada não simula
um duplo clique físico; confirma o mesmo launcher via cmd.
Revisão independente acrescentou regressões para pastas vazias e origem ilegível; ambas corrigidas.

- RED inicial: 13 testes falharam porque o engine não existia. GREEN após implementação.
- RED instalador: 8 testes falharam porque instalador não existia. GREEN após implementação.
- Regressões da revisão: 4 testes falharam para modo/scope/base/identidade/lock; corrigidos.
- Regressões finais: trailing `//` e evidência de custo falharam; corrigidos.
- Windows: preview e instalação PowerShell reais; paths com espaço/ação; junction real recusada.
- Upgrade: idempotência, conflito de arquivo, preservação de dados, rollback após erro de escrita simulado.
- Gates: falta de comando de produto falha; comando com exit 0 passa; exit 7 falha.
- Testes não chamam modelos, não usam credentials e não publicam no GitHub.

## Rastreabilidade da SPEC

| Requisito | Artefatos | Verificação / limite |
|---|---|---|
| R01 separação mestre/global/projeto | installer, README, manifest | paths separados; global nunca escrito |
| R02 modos e identidade | AGENT_TEAM, operator.example, bootstrap | modos instalados; login casefold; identidade local manual |
| R03 roles e routing | 6 TOMLs, 2 presets, skill | parsing de role refs; modelo efetivo exige smoke test |
| R04 contrato/DAG/ownership | contract reference, check_dag, overlap | ciclo/ID ausente/conflito recusados; worktree pelo root |
| R05 adaptive | decide + skill | budgets, fresh, escalation, infra, quarantine; root aplica |
| R06 learning | reputation, learning reference | dedup, janela, proposta, cheap evidence; PR/review via protocolo |
| R07 claims | coordinator, workflow, remote reference | fila serial, versões, replay, retomada e recuperação simulados; ativação GitHub pendente |
| R08 Git/comunicação | namespace, 8 mensagens, bootstrap | pressure scenarios; push/merge não executados |
| R09 CI/PR | gate, workflow, templates | head/base/revision/current checks; rulesets reais pendentes de ativação |
| R10 uso | usage, examples, learning | delta/dedup/cache/null; sem coletor automático de logs pessoais |
| R11 Windows | PowerShell + installer | instalação/preview/junction/drift/rollback |
| R12 artefatos e auditoria | docs, tests, release hashes | auditoria independente + pressure results |

## Limites preservados

Não foi fornecido um repositório alvo nem identidades GitHub reais. Não executamos grants/PRs em duas
contas, verificamos rulesets reais ou despachamos Luna/Sol/Astra para comprovar disponibilidade.
O roteiro de ativação por PC está em COOP-BOOTSTRAP.md. CI deve receber comandos reais do produto.
Seu gate vazio falha deliberadamente; validação de configuração sozinha não declara produto aprovado.

O validador auxiliar de skills do host dependia de PyYAML, ausente nos dois Python disponíveis. Não foi
instalada dependência global para contornar isso. Frontmatter/referências foram inspecionados diretamente
e a skill passou pelos testes comportamentais documentados, que não dependem desse auxiliar.

Protocolo cooperativo depende de um lead único e de operadores confiáveis; Markdown não impõe exclusão.
Não há daemon, live auto-learning global ou contabilização automática de cotas de duas contas.
