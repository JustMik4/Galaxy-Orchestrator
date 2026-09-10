# CO-OP 1.3 — responsáveis equivalentes

## Uma pessoa hoje, parceiros amanhã

Cadastre operadores reais no AGENT_TEAM e seus IDs em integration_operators. Todos nessa lista têm
autoridade equivalente para solicitar claims, retomadas e merges. A lista pode conter apenas você.
integration_lead antigo é ignorado. Codex continua executando os agentes; um job curto no GitHub Actions
coordena cada operação de estado/integração. O job ativo é o lead temporário, não o computador de alguém.

Cada projeto tem seu próprio repositório, Issue de controle e fila. Contribuições podem ser distribuídas
livremente, respeitando ownership por escopo e dependências. Revisão por parceiro é opcional; Reviewer
local em contexto separado continua obrigatório. Não há conta, login ou token compartilhado no pacote.

## Ativação no repositório de cada projeto

1. Instale CO-OP, configure operators/integration_operators e comandos reais em checks.json.
2. Coloque workflow multicontroller-control.yml, ferramentas e AGENT_TEAM na branch padrão por uma
   mudança de bootstrap revisada. O workflow só roda a partir da branch padrão e lê sua versão atual.
3. Crie uma Issue dedicada, aberta, cujo corpo seja exatamente o JSON de examples/control-state.json.
   Coloque o número dessa Issue em coordination.control_issue e publique a configuração.
   Não faça isso com estado vazio se existirem claims antigos: encerre-os ou importe uma fotografia
   revisada de active/revisions antes de qualquer operação. Não edite o corpo durante operação.
4. Habilite Actions e permissões de contents/issues/pull-requests write para o workflow, usando o token
   efêmero GITHUB_TOKEN. Não coloque PATs em arquivos. A organização pode restringir essas permissões.
5. Configure PR/checks obrigatórios, base atualizada e ausência de bypass/direct push. Não exija revisão
   humana de parceiro/CODEOWNER. Toda integração deve usar SOMENTE este workflow: desative auto-merge,
   merge queues paralelas e merges manuais pelos operadores. Proteja workflow/tools/team contra alterações
   sem revisão. Quando as regras do plano não puderem impor exclusividade, ela será uma obrigação dos
   mantenedores confiáveis; não alegamos proteção contra administrador ou edição manual do estado.
6. Confira que o token do workflow consegue integrar respeitando regras, sem bypass. Se o servidor não
   permite isso, CO-OP remoto ainda não está ativado: ajustar permissões/rulesets é pré-requisito técnico.

## Enviar operação

No GitHub: Actions → multicontroller-control → Run workflow, selecione a branch padrão e cole o JSON
do pedido. Comece pelo exemplo control-claim.json com seus dados reais. Um root com ferramenta GitHub
autorizada também pode disparar workflow_dispatch. Usuários com permissão para abrir Issues mas fora de
integration_operators não podem usar o coordenador.

Antes de enviar, leia version/active/receipts na Issue. Cada pedido usa id novo, expected_version e
task com task/owner/machine/nonce/revision/scope/depends_on (números dos PRs pré-requisitos).
- claim: reserva tarefa e escopo; owner deve ser o ator autenticado; revision é a próxima da tarefa.
- reclaim: responsável retoma para si sem ACK do parceiro, com reason, revision+1 e nonce novo.
  Preserve todos os campos de contrato e dependências. Estado/integradores não dependem de relógio.
- release: somente owner atual com revision/nonce/machine atuais pode liberar.
- merge: inclua pr e evidence no formato gate.json; task deve corresponder ao grant atual. Qualquer
  integrador autorizado pode solicitar integração, mesmo que outro tenha implementado.
- recover: use id novo, operation=recover e pending_id do merge incerto. Qualquer integrador pode recuperar.

Não comece a editar porque o job foi enfileirado. Espere receipt confirmado no corpo da Issue; compare
owner/machine/nonce/revision com seu contrato. O workflow processa apenas um job por repositório.
queue=max guarda até 100 pendentes no GitHub atual; ordem de envio não é garantia de ordem de processamento.
Estado mudou => pedido falha por expected_version. Releia/reconcilie e prepare novo pedido; sem retry em loop.
Mesmo id/payload/ator recente devolve receipt. ID reutilizado com outro payload falha. Guarde receipts no
resumo da Issue da tarefa; o estado central mantém 50 recentes e revisions duráveis contra replay antigo.

## Parceiro desconectado ou desistente

Não exige resposta nem transferência manual de permissões entre PCs. Outro integrador envia reclaim
na mesma fila. Revision antiga deixa de autorizar merge. Todas as integrações devem passar pela fila
para essa garantia valer. Preserve branches antigas; código útil é trazido para uma tentativa atual,
com orçamento/histórico mantidos e testes/review novos. Dependências reais não desaparecem com a saída.

## Merge interrompido

A intenção pending é gravada antes da chamada API. Se houver falha de rede/runner, novos claims/reclaims
ficam bloqueados para não disputar uma integração incerta. Qualquer responsável usa recover:
- PR já merged: reconcilia resultado e libera o grant.
- PR aberto e mesma evidência/head/base: repete a intenção com SHA esperado.
- Head/base mudaram ou evidência não serve: feche o PR não integrado e use recover para registrar abort.
  O grant é preservado; prepare outro PR/pedido com evidências atuais. Não apague pending manualmente.

Falhas de infraestrutura ainda podem impedir operação; isso não é dependência de uma pessoa específica.
Corpo da Issue próximo de 60k requer migração administrativa revisada preservando grants/revisions,
sem reset automático. O workflow é um protocolo entre mantenedores confiáveis, não um banco contra fraude.

## Atualizar instalações antigas

Use update.ps1 com preview e revise o diff. Schema 1/2 migra para 3, mantendo IDs/logins e autorizando
os IDs existentes como integration_operators; revise essa lista. control_issue começa nulo para evitar
inventar estado. Desative o protocolo anterior, reconcilie claims e só depois habilite a fila.
Regras que exigiam outro humano precisam ser ajustadas no GitHub pelo administrador.

## Smoke test antes de uso real

1. Um operador faz claim, Worker, Reviewer separado, testes e merge pela fila.
2. Dois operadores enviam pedidos com mesma version: um vence, o outro reconcilia sem dupla reserva.
3. Segundo operador retoma a tarefa do primeiro offline; entrega antiga é recusada.
4. Simule resposta perdida/runner encerrado durante merge e recupere com outro operador.
5. Confirme recusa de ator não autorizado, escopo extra, nonce velho, review/head/base antigos.

Testes locais não substituem esse smoke test. Nenhum repositório real foi ativado na geração do pacote.
