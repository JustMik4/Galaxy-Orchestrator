# Fontes do Galaxy Orchestrator V2

Fontes normativas internas têm precedência sobre descrições históricas. Fontes externas explicam capabilities e plataformas; elas não concedem credenciais, quota ou garantias de disponibilidade.

## Fontes normativas do repositório

- [SPEC-V2.md](SPEC-V2.md): arquitetura e contrato canônicos V2.
- [SPEC-V1.md](SPEC-V1.md): contrato histórico para compatibilidade e migração.
- [`VERSION`](../VERSION): versão do produto.
- [`galaxy.py`](../galaxy.py): entrada canônica da CLI.
- [`lib/galaxy.py`](../lib/galaxy.py): comandos e códigos de saída implementados.
- [`lib/project.py`](../lib/project.py): carregamento das declarações e do lock.
- [`lib/bootstrap.py`](../lib/bootstrap.py): geração determinística e detecção de drift.
- [`lib/migrations/`](../lib/migrations/): migração V1.3→V2 e receipts.
- [`lib/doctor.py`](../lib/doctor.py): checks e semântica do Doctor.
- [`lib/resources/`](../lib/resources/): catálogo local e importador offline.
- [`lib/vault.py`](../lib/vault.py): projeção opcional do Vault.
- [`template/`](../template/): declarações e workflow instalados.

Se README, plano ou exemplo divergir do código e da SPEC V2, trate a divergência como bug documental; não altere o comportamento do projeto por inferência.

## OpenAI e modelos

- [OpenAI — Model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [OpenAI — All models](https://developers.openai.com/api/docs/models/all)

Os identificadores `gpt-5.6-sol`, `gpt-5.6-luna` e `gpt-6-astra` no Galaxy são alvos de roteamento do adaptador. A disponibilidade efetiva depende do host/conta. O Runtime Verifier deve registrar o que foi observado; documentação de modelo não substitui telemetria do runtime.

## Git e GitHub

- [Git — documentação oficial](https://git-scm.com/docs)
- [GitHub Actions](https://docs.github.com/en/actions)
- [Sintaxe de workflows](https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions)
- [Permissões do `GITHUB_TOKEN`](https://docs.github.com/en/actions/security-for-github-actions/security-guides/automatic-token-authentication)
- [REST API do GitHub](https://docs.github.com/en/rest)
- [Rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
- [Renomear um repositório](https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository)

O Galaxy prefere capabilities programáticas e exige aprovação para fallback por navegador. A presença de uma URL nesta lista não autoriza uma ação externa.

## Formatos, segurança e operação

- [Python 3.11](https://docs.python.org/3.11/)
- [PowerShell](https://learn.microsoft.com/powershell/)
- [JSON](https://www.rfc-editor.org/rfc/rfc8259)
- [YAML 1.2.2](https://yaml.org/spec/1.2.2/)
- [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)
- [Obsidian — arquivos e pastas](https://help.obsidian.md/Files+and+folders/How+Obsidian+stores+data)
- [Obsidian — Vault](https://help.obsidian.md/Getting+started/Create+a+vault)

O Vault é projeção Markdown opcional e desativada por padrão. As fontes do Obsidian explicam o consumidor; a política de propriedade, privacidade e sync é definida pela SPEC V2 e pelo código local.

## Resource Catalog

- [`registry/resources.json`](../registry/resources.json): registro inicial pequeno e revisado.
- [public-apis/public-apis](https://github.com/public-apis/public-apis): possível entrada local para descoberta de candidatos.

`public-apis` nunca é fonte de verdade do Galaxy. O importador recebe conteúdo já disponível em arquivo/string, não faz fetch, marca candidatos como `unverified` e mantém contexto compacto. Uma entrada só se torna verificada após validação explícita contra documentação oficial.

## Política de manutenção

Para adicionar ou atualizar uma fonte:

1. prefira documentação oficial e URL estável;
2. registre qual afirmação a fonte sustenta;
3. não copie documentação extensa para prompts, catálogo ou Vault;
4. confirme versão/data quando a informação puder mudar;
5. trate README comunitário como pista até validação independente;
6. preserve a distinção entre documentação, observação de runtime e garantia operacional.
