# Validação do Galaxy Orchestrator V2

Esta página separa verificações executáveis de alegações de release. A documentação não substitui os resultados da suíte final, do smoke test Lexy ou da publicação no GitHub.

## Validação de um projeto

Execute da raiz do Galaxy:

```powershell
python .\galaxy.py bootstrap C:\AI\Projetos\MeuProjeto --check
python .\galaxy.py lock sync C:\AI\Projetos\MeuProjeto --check
python .\galaxy.py validate C:\AI\Projetos\MeuProjeto
python .\galaxy.py doctor C:\AI\Projetos\MeuProjeto
```

Resultados esperados:

- `bootstrap --check` sem `create`, `update`, `removed` ou `drift` pendentes;
- `lock sync --check` com status `current`; status `stale` exige revisar as quatro declarações antes de sincronizar;
- `validate` com configuração válida e versão do lock compatível;
- Doctor sem `FAIL`; `WARN` e `UNKNOWN` devem ser lidos, não escondidos.

Para executar checks reais do produto:

```powershell
python .\galaxy.py validate C:\AI\Projetos\MeuProjeto --gate
```

O gate exige pelo menos um comando em `.galaxy/checks.json`. Cada comando é executado como vetor de argumentos no diretório do projeto, sem concatenação de shell, com timeout. Lista vazia, timeout ou código diferente de zero bloqueiam o gate.

## Interpretação do Doctor

O Doctor cobre:

```text
configuração e galaxy.lock
consistência dos arquivos gerados
arquivos gerados rastreados pelo Git
resíduos V1
checks do produto
modelo/esforço de runtime
quota
capability para ações externas
Vault
ciclo de vida de branches, worktrees e runtime
```

Use JSON em automação:

```powershell
python .\galaxy.py doctor C:\AI\Projetos\MeuProjeto --json
if ($LASTEXITCODE -ne 0) { throw 'Galaxy Doctor encontrou falhas' }
```

Telemetria ausente deve aparecer como `UNKNOWN`. Um runtime diferente do solicitado deve ser classificado como mismatch de roteamento, não como falha genérica do modelo. Browser disponível sem aprovação não satisfaz silenciosamente uma capability externa.

## Bootstrap reproduzível

Em clone limpo do projeto:

```powershell
git clone <URL-DO-PROJETO> C:\Temp\MeuProjeto
python .\galaxy.py bootstrap C:\Temp\MeuProjeto
python .\galaxy.py bootstrap C:\Temp\MeuProjeto --check
python .\galaxy.py validate C:\Temp\MeuProjeto --gate
```

O segundo comando precisa ser idempotente. Confirme também:

```powershell
git -C C:\Temp\MeuProjeto status --short
git -C C:\Temp\MeuProjeto ls-files .codex .galaxy/local .galaxy/runtime .galaxy/cache .galaxy/install
```

Nenhum arquivo gerado/local deve aparecer rastreado. As declarações `.galaxy/`, `AGENTS.md`, `galaxy.lock` e o workflow devem permanecer versionadas.

Em Windows, repita o clone com `core.autocrlf=true`. `load_project`, Doctor e bootstrap precisam aceitar os mesmos bytes; o bloco Galaxy ao final de `.gitattributes` deve prevalecer sobre regras globais conflitantes.

## Migração V1.3

Use primeiro uma cópia controlada ou fixture:

```powershell
python .\galaxy.py migrate C:\Temp\ProjetoV1 --preview
python .\galaxy.py migrate C:\Temp\ProjetoV1
python .\galaxy.py doctor C:\Temp\ProjetoV1 --json
```

Verifique no receipt:

- inventário e hashes anteriores;
- classificação de conteúdo do projeto, Galaxy intacto e Galaxy modificado;
- itens transformados, preservados, gerados, retirados do índice e conflitantes;
- resultados separados de Git/untrack, bootstrap, validação e Doctor;
- backup externo ao projeto;
- dados suficientes para restaurar bytes e índice Git.

Teste rollback com o receipt produzido e compare árvore de trabalho e índice com o estado original. A migração não deve tocar um Vault V1 nem apagar conteúdo não Galaxy em `.agents/`.

O smoke test do projeto Lexy é um critério final separado. Sua evidência mais recente está registrada abaixo; repita-o depois da publicação/renomeação para validar também o limite externo.

## Lifecycle e ações externas

```powershell
python .\galaxy.py cleanup C:\AI\Projetos\MeuProjeto --preview
```

O plano deve preservar branches/worktrees ativos, itens sem prova de propriedade e runtime recente. Execute `--apply` somente após revisão humana do preview.

Para operações GitHub, valide que o Action Resolver seleciona capability nativa/conectada, connector/plugin, CLI ou API antes de navegador. Browser exige aprovação explícita e não comprova que a ação externa ocorreu.

## Suíte do repositório

Com o checkout parado e sem outros agentes alterando arquivos:

```powershell
python -m compileall -q lib tests galaxy.py multicontroller.py
python -m unittest discover -s tests -v
git diff --check
git status --short
```

Também execute as suítes focadas de routing/quota, runtime/review, specialists, bootstrap, migration, doctor/actions, lifecycle, resources e vault quando uma dessas áreas mudar.

## Evidência integrada de 2026-09-12

No checkout V2 integrado em Windows:

- `python -m unittest discover -s tests -v`: **278 testes executados**, **275 passaram**, zero falhas e três skips;
- os skips foram somente dois casos que criam symlink real, indisponível sem o privilégio do Windows (`WinError 1314`), e a integração de corrida POSIX que não se aplica ao host Windows; testes de junction/reparse sem esse privilégio, identidade de diretório e revisão estática permaneceram cobertos;
- `python -m compileall -q lib tests galaxy.py multicontroller.py`: passou;
- sincronização explícita do lock, incluindo snapshot autenticado, clone `autocrlf`, concorrência, staging e promoção por handle: passou;
- parsing dos três scripts PowerShell e execução dos entrypoints `galaxy.py` e `lib/galaxy.py`: passaram;
- `RELEASE-MANIFEST.json` correspondeu a todos os blobs rastreados da release;
- `git diff --check`: passou.

O smoke V1.3→V2 mais recente usou o clone local descartável
`C:\AI\Galaxy-Lexy-Smoke-20260912-final` do `HEAD` `6a2cd68` da Lexy. A árvore original,
que continha `M tests/test_event_bus.py`, permaneceu intocada no mesmo commit. Preview e aplicação
terminaram com status `success`; o receipt foi gravado em
`local/migrations/08744d78233a47359f865bcdac7dfb54/receipt.json` do master. O bootstrap posterior em
`--check` ficou sem create/update/remove/drift e sem poluição rastreada. O Doctor retornou zero `FAIL`,
17 `PASS`, dois `WARN` esperados (resíduos V1 locais e templates de
ambiente duplicados) e três `UNKNOWN` honestos (runtime, quota e action capability não fornecidos).
No clone migrado, a suíte do produto passou com **284 testes** e `pip check` informou zero dependências
quebradas.

O comando `validate --gate` do clone limpo não iniciou porque `.galaxy/checks.json` referencia
`.venv\\Scripts\\python.exe`, ambiente local corretamente ignorado e ausente no clone. Os mesmos dois
comandos foram executados no diretório do clone com o interpretador da Lexy original: ambos passaram.
Antes de usar o gate em CI/fresh clone, o projeto deve provisionar o ambiente declarado ou trocar o check
por um interpretador reproduzível do runner.

Pendências externas e de transição física continuam separadas da qualidade do checkout:

- a instalação local ainda precisa ser movida para `C:\AI\Galaxy-Orchestrator` depois de encerrar esta sessão e qualquer processo que use o caminho antigo;
- o repositório `JustMik4/Galaxy-Orchestrator`, a tag `v2.0.0`, o ruleset e o check remoto `galaxy / validate` ainda precisam ser publicados/verificados com capability GitHub autenticada;
- o workflow distribuído falha fechado enquanto o repositório/tag canônicos não existem.
