# Galaxy Orchestrator V2

O Galaxy Orchestrator coordena agentes de engenharia com roteamento por capacidade, proteção de quota, bootstrap reproduzível e limites explícitos de autoridade. A versão canônica deste checkout é **2.0.0**.

O código de desenvolvimento ainda pode estar aberto na pasta histórica `C:\AI\Codex-Multicontroller`. O destino canônico é `C:\AI\Galaxy-Orchestrator` e o repositório-alvo é `JustMik4/Galaxy-Orchestrator`; a mudança física e a renomeação no GitHub só são consideradas concluídas depois da validação final descrita em [Publicação](docs/PUBLISH.md).

## Requisitos

- Windows com PowerShell 7;
- Python 3.11 ou superior;
- Git;
- um projeto Git existente, separado da instalação master do Galaxy.

O núcleo usa a biblioteca padrão do Python. Integrações externas continuam sujeitas à autenticação, disponibilidade e limites dos respectivos serviços.

## Quickstart

Execute os comandos a partir da raiz deste repositório. O exemplo usa `C:\AI\Projetos\MeuProjeto` como projeto de produto.

```powershell
python .\galaxy.py --help

# Mostra o que seria instalado, sem alterar o projeto.
python .\galaxy.py install C:\AI\Projetos\MeuProjeto --mode SOLO --preset balanced --check

# Instala as declarações V2 e gera o adaptador Codex local.
python .\galaxy.py install C:\AI\Projetos\MeuProjeto --mode SOLO --preset balanced

# Confere configuração e artefatos gerados.
python .\galaxy.py bootstrap C:\AI\Projetos\MeuProjeto --check
python .\galaxy.py validate C:\AI\Projetos\MeuProjeto
python .\galaxy.py doctor C:\AI\Projetos\MeuProjeto --json
```

Antes de usar o gate, configure comandos reais do produto em `.galaxy/checks.json`:

```json
{
  "commands": [
    ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
  ],
  "schema_version": 1
}
```

Então execute:

```powershell
python .\galaxy.py validate C:\AI\Projetos\MeuProjeto --gate
```

O gate falha de propósito quando `commands` está vazio. Veja [Instalação](docs/INSTALLATION.md) e [Validação](docs/VALIDATION.md).

## Layout de um projeto V2

```text
MeuProjeto/
├── .gitattributes                    # preserva bytes das declarações em qualquer checkout
├── AGENTS.md                         # instruções estáveis do projeto
├── galaxy.lock                       # versão e revisões reproduzíveis
├── .galaxy/
│   ├── project.yml                   # adaptador, routing, specialists e vault
│   ├── team.yml                      # modo e política de coordenação
│   └── checks.json                   # comandos reais do produto
├── .github/workflows/
│   └── galaxy-validate.yml           # gate reproduzível no GitHub
└── .codex/                           # saída local gerada; não versionada
```

`AGENTS.md`, `.galaxy/`, `galaxy.lock`, `.gitattributes` e os workflows aplicáveis são versionados. O lock autentica os bytes de `AGENTS.md`, `project.yml`, `team.yml` e `checks.json`; `.gitattributes` mantém esses bytes estáveis inclusive com `core.autocrlf=true`. Depois de revisar uma alteração legítima nessas quatro declarações, atualize o lock explicitamente:

```powershell
python .\galaxy.py lock sync C:\AI\Projetos\MeuProjeto --check
python .\galaxy.py lock sync C:\AI\Projetos\MeuProjeto
```

O primeiro comando é somente leitura. `.codex/`, `.galaxy/local/`, `.galaxy/runtime/`, `.galaxy/cache/` e `.galaxy/install/` são locais ou gerados. O bootstrap recusa drift em arquivos gerados em vez de sobrescrever customizações silenciosamente.

## SOLO e CO-OP

`SOLO` usa backend local e mantém as garantias de branch protegida, checks do produto e revisão independente configurada:

```powershell
python .\galaxy.py init C:\AI\Projetos\Solo --mode SOLO --preset balanced
```

`CO-OP` grava em `.galaxy/team.yml` a coordenação serializada, instala o workflow `galaxy-control.yml` e declara o check requerido `galaxy / validate`:

```powershell
python .\galaxy.py init C:\AI\Projetos\Equipe --mode CO-OP --preset critical
```

O modo preserva as garantias V1 de um escritor por escopo, revisão independente, dependências em DAG e merge condicionado. O workflow falha fechado até que operadores, integradores, issue de controle, repositório/tag canônicos e regras da branch estejam configurados. A instalação não cria credenciais, issue, ruleset ou permissões no GitHub; veja [CO-OP Bootstrap](docs/COOP-BOOTSTRAP.md).

## Modelos, routing e quota

O adaptador Codex gerado usa **Sol Medium** (`gpt-5.6-sol`, esforço `medium`) para o root normal. Papéis de leitura e execução limitada podem usar Luna; revisão e arquitetura podem usar Sol High. Astra é rota excepcional para problemas que exigem capacidade adicional, não root permanente.

O Capability Router escolhe a rota mais barata que satisfaz capacidade, autoridade, evidência, perfil (`balanced` ou `critical`), disponibilidade observada e quota. O Runtime Verifier compara modelo/esforço solicitado com o observado e separa mismatch de roteamento de falha do modelo. A identidade efetiva, e não apenas a pedida, alimenta evidência e telemetria.

O Quota Guard reserva por padrão 15% da janela de cinco horas e 2% da janela semanal. Abaixo do piso não inicia novos despachos; interrupção de filhos já em execução é apenas best effort. Limiares são política local do operador e não devem ser gravados no repositório do produto. Se a telemetria não estiver disponível, o Doctor informa `UNKNOWN`; disponibilidade desconhecida não equivale a quota infinita.

## Papéis e especialistas

Papel, especialista, modelo e ferramenta são dimensões independentes:

- o **papel** define autoridade;
- o **especialista** fornece instruções e recursos do domínio;
- o **modelo/esforço** é escolhido pelo router;
- a **capability** determina quais ações estão realmente disponíveis.

Especialistas têm fonte Markdown com frontmatter, catálogo frio e hot set materializado sob demanda. A saída do adaptador é determinística e não amplia autoridade. Para inspecionar ou sincronizar:

```powershell
python .\galaxy.py specialists list
python .\galaxy.py specialists sync C:\AI\Projetos\MeuProjeto --check
python .\galaxy.py specialists sync C:\AI\Projetos\MeuProjeto
```

## Bootstrap, Doctor e ciclo de vida

O bootstrap deriva `.codex/` das declarações do projeto. `--check` detecta criação, atualização, remoção ou drift pendente sem aplicar mudanças.

O Galaxy Doctor verifica configuração, lock, consistência do bootstrap, poluição Git, resíduos V1, checks, runtime, quota, capabilities de ação, Vault e ciclo de vida. `WARN`/`UNKNOWN` não são convertidos artificialmente em sucesso; `FAIL` retorna código diferente de zero.

O Lifecycle Manager apresenta um plano antes de remover candidatos inativos. Ele reconhece branches novas `galaxy/*` e branches históricas `codex/*`, preservando itens ativos ou inseguros:

```powershell
python .\galaxy.py cleanup C:\AI\Projetos\MeuProjeto --preview
python .\galaxy.py cleanup C:\AI\Projetos\MeuProjeto --apply
```

Revise o preview antes de `--apply`.

## Migração V1 para V2

A migração é semântica: classifica arquivos do projeto, arquivos Galaxy intactos e arquivos Galaxy modificados; não faz substituição global de texto. O preview não altera o projeto:

```powershell
python .\galaxy.py migrate C:\AI\Projetos\Legado --preview
python .\galaxy.py migrate C:\AI\Projetos\Legado
```

Antes da primeira mutação, o motor cria backup fora do projeto, em `local/migrations/` da instalação master, e registra um receipt com hashes, estado do índice Git, transformações, preservações, conflitos, validação e Doctor. Conflitos bloqueiam a finalização. Para desfazer, use o caminho de receipt retornado:

```powershell
python .\galaxy.py migrate C:\AI\Projetos\Legado --rollback C:\caminho\para\receipt.json
```

Conteúdo não pertencente ao Galaxy sob `.agents/` é preservado. Um Vault encontrado no projeto V1 entra apenas no inventário e não é modificado pela migração. Consulte [SPEC V1](docs/SPEC-V1.md) para o formato histórico e [SPEC V2](docs/SPEC-V2.md) para o contrato atual.

## Action Resolver e Resource Catalog

O Action Resolver escolhe, em ordem padrão, capability nativa/aplicativo conectado, connector ou plugin, CLI, API e navegador. Fallback pelo navegador exige aprovação explícita. A seleção é uma API interna; não existe comando `galaxy action` na CLI atual.

O Resource Catalog é local, compacto e determinístico. `registry/resources.json` começa com poucas fontes verificadas. Conteúdo do projeto `public-apis` pode ser importado somente de arquivo ou string local como candidatos `unverified`; o importador não acessa a rede, não transforma essa lista em fonte de verdade e não injeta READMEs completos. O catálogo também é API interna nesta versão.

## Obsidian Vault opcional

O Vault fica **desativado por padrão** em `.galaxy/project.yml`. Quando ativado pelo proprietário do projeto, ele projeta tarefas, marcos e resumos em Markdown. Prompts, respostas, telemetria e segredos ficam excluídos por padrão; o Obsidian não é requisito para operar o Galaxy.

```powershell
python .\galaxy.py vault status C:\AI\Projetos\MeuProjeto
python .\galaxy.py vault sync C:\AI\Projetos\MeuProjeto --check
python .\galaxy.py vault sync C:\AI\Projetos\MeuProjeto
```

Snapshots adicionais podem ser fornecidos com `--snapshot ARQUIVO`; a origem precisa ser um JSON local. `--force` só deve ser usado depois de revisar conflitos de propriedade. Detalhes de modos e privacidade estão no [SPEC V2](docs/SPEC-V2.md).

Um Vault externo é configurado somente no arquivo local ignorado `.galaxy/local/operator.toml`. O caminho deve ser absoluto, sem link/reparse point e totalmente separado da árvore do projeto: não pode ser o próprio projeto, um ancestral ou um descendente.

## Compatibilidade e limites

- `multicontroller.py` é um wrapper temporário com aviso de depreciação; novos fluxos usam `galaxy.py`.
- `.multicontroller/`, `AGENT_TEAM.yml`, `.multicontroller/tools` e metadados V1 são aceitos apenas em migração/compatibilidade.
- Projetos novos usam `.galaxy/` e não vendorizam ferramentas do master.
- Provedores LLM gratuitos externos não fazem parte da versão 2.0.0.
- O Galaxy não concede credenciais, quota, permissões do GitHub ou acesso a modelos.
- A aceitação local e o smoke Lexy estão registrados em [Validação](docs/VALIDATION.md). Renomeação local, publicação do repositório/tag, rulesets e execução remota continuam limites externos separados.

## Documentação

- [Especificação V2](docs/SPEC-V2.md)
- [Instalação](docs/INSTALLATION.md)
- [Validação](docs/VALIDATION.md)
- [CO-OP Bootstrap](docs/COOP-BOOTSTRAP.md)
- [Publicação](docs/PUBLISH.md)
- [Fontes](docs/SOURCES.md)
- [Changelog](docs/CHANGELOG.md)
- [Especificação V1 histórica](docs/SPEC-V1.md)

Licença: [MIT](LICENSE).
