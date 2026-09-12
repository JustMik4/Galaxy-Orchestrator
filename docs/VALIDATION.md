# Validação do Galaxy Orchestrator V2

Esta página separa verificações executáveis de alegações de release. A documentação não substitui os resultados da suíte final, do smoke test Lexy ou da publicação no GitHub.

## Validação de um projeto

Execute da raiz do Galaxy:

```powershell
python .\galaxy.py bootstrap C:\AI\Projetos\MeuProjeto --check
python .\galaxy.py validate C:\AI\Projetos\MeuProjeto
python .\galaxy.py doctor C:\AI\Projetos\MeuProjeto
```

Resultados esperados:

- `bootstrap --check` sem `create`, `update`, `removed` ou `drift` pendentes;
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

O smoke test do projeto Lexy é um critério final separado. Só marque como concluído depois de executá-lo e registrar o receipt e o resultado; esta revisão documental não o executa.

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

## Estado de aceitação desta documentação

No momento desta atualização:

- a documentação descreve a arquitetura e a CLI V2 presentes no checkout;
- os testes focados pertencem aos respectivos relatórios de implementação;
- a suíte final completa ainda precisa ser executada após a integração de todas as mudanças concorrentes;
- o smoke test Lexy ainda precisa de evidência final;
- a renomeação local e a renomeação do repositório GitHub ainda precisam ser verificadas;
- a proteção `galaxy / validate` ainda precisa ser confirmada no repositório publicado.

Não substitua esta lista por números antigos da linha V1.3. Registre comando, commit, ambiente, duração e saída de cada execução final.
