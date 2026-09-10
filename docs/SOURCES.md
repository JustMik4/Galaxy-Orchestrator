# Fontes e compatibilidade

Consultadas em 2026-09-09. Conteúdo de conversa serve como requisitos históricos; não como documentação técnica atual.

- OpenAI, [Configuration Reference](https://learn.chatgpt.com/docs/config-file/config-reference): roles por
  `agents.<name>.config_file`, caminhos relativos à config declarante, modelo/effort padrão e
  `max_concurrent_threads_per_session`. Foi escolhido o nome atual; `max_threads` é alias legado.
  O pacote não define approval policy, trust global ou sandbox do Windows. Roles de leitura usam `read-only`;
  escritores usam `workspace-write`, sujeitos à política do host.
- OpenAI, [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents): contexto de agentes nativos.
  A garantia de isolamento de worktree deve ser verificada no host; nosso protocolo não presume isso.
- GitHub, [About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches): reviews, checks, stale approvals,
  proteção e limites de plano. Templates não ativam regras do servidor automaticamente.
- GitHub, [checkout action v4.2.2](https://github.com/actions/checkout/releases/tag/v4.2.2): workflow fixa commit da action,
  não branch flutuante. Atualizações de dependências do workflow devem ser revisadas.

Catálogo desta sessão expõe `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-6-astra` e esforços low/medium/high.
Não foi executada inferência separada de cada modelo nesta release; isso não prova acesso na conta do colega.
CLI local identificada: codex-cli 0.153.4. Parsing TOML/Python e testes locais não equivalem a teste end-to-end
de role no aplicativo. Na primeira execução em cada PC, compare o modelo/effort efetivos com o contrato.

Não foram copiados os repositórios de terceiros citados no chat original. V1 implementa o protocolo descrito
na SPEC com arquivos próprios, sem depender de outro orquestrador.

## Coordenação 1.3

GitHub [concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
documenta um job ativo por grupo e queue=max (até 100 pendentes), sem garantir ordem pelo envio.
GitHub [merge PR API](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request)
suporta SHA esperado do head. Isso não é CAS da base: exclusividade de integrações pela mesma fila
e regras de branch são precondições da arquitetura. Fontes consultadas em 2026-09-09/10.
