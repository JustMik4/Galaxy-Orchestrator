# Bootstrap CO-OP do Galaxy Orchestrator V2

CO-OP combina declarações locais reproduzíveis com coordenação remota explícita. A instalação V2 configura o projeto para o modo, mas não cria credenciais, issue de controle, ruleset ou permissões no GitHub.

## 1. Pré-requisitos

- projeto Git com remote conhecido;
- checkout do Galaxy Orchestrator;
- permissão administrativa no repositório para configurar branch protection/ruleset;
- autenticação GitHub fora dos arquivos do projeto;
- checks reais definidos em `.galaxy/checks.json`.

Não grave tokens em `.galaxy/`, `AGENTS.md`, `.codex/`, issue de controle ou Vault.

## 2. Instale a declaração CO-OP

```powershell
python .\galaxy.py install C:\AI\Projetos\Equipe --mode CO-OP --preset critical --check
python .\galaxy.py install C:\AI\Projetos\Equipe --mode CO-OP --preset critical
```

Revise `.galaxy/team.yml`. A forma inicial é equivalente a:

```json
{
  "coordination": {
    "backend": "github-actions-issue",
    "claim_protocol": "serialized-workflow",
    "control_issue": null
  },
  "integration_branch": "main",
  "mode": "CO-OP",
  "operators": [],
  "required_checks": ["galaxy / validate"],
  "review": {
    "independent_agent_required": true
  },
  "rules": {
    "direct_main_push": false,
    "one_writer_per_scope": true
  },
  "schema_version": 1
}
```

Os arquivos têm extensão YAML por contrato de produto, mas o conteúdo V2 atual é JSON estrito, que é YAML válido.

## 3. Configure operadores e issue de controle

Crie uma única issue de controle no repositório correto. Registre o número em `coordination.control_issue` e liste somente os operadores autorizados em `operators`. Faça essa mudança em branch e revisão normais.

O protocolo serializado deve preservar:

- um escritor por escopo;
- revisão otimista por `revision` do contrato;
- claims com lease/heartbeat;
- dependências em DAG sem ciclos;
- merge somente após checks e revisão independente;
- eventos idempotentes e recuperação explícita.

Não edite o corpo de controle em paralelo por caminhos diferentes. A issue é estado de coordenação, não cofre de segredos nem log de prompts.

## 4. Ative validação no GitHub

O projeto V2 inclui `.github/workflows/galaxy-validate.yml`. Ele:

- usa permissões `contents: read`;
- lê `galaxy.lock`;
- aceita um SHA exato ou a tag exata `v<versão>`;
- obtém a revisão fixada do Galaxy;
- executa `galaxy.py validate <workspace> --gate` em Python 3.11.

Configure no ruleset da branch de integração:

```text
pull request obrigatório
push direto bloqueado
aprovação independente exigida pela política da equipe
status check obrigatório: galaxy / validate
```

A renomeação do repositório para `JustMik4/Galaxy-Orchestrator` precisa ocorrer antes de depender da URL canônica gravada no workflow publicado. Não conte com redirects do nome antigo como configuração permanente.

## 5. Ative o coordenador remoto conscientemente

A distribuição V2 versiona o workflow de **validação**. A mera presença desse workflow não prova que um coordenador remoto serializado está ativo. Antes de operar CO-OP remoto, publique e revise a automação de coordenação compatível com o contrato V2, com permissões mínimas e concorrência por repositório.

Uma automação de coordenação aceitável deve:

- validar actor contra `operators`;
- rejeitar revisão obsoleta;
- serializar mutações do estado de controle;
- nunca executar comandos fornecidos livremente por comentários;
- não expor secrets em eventos de fork/PR;
- registrar resultado inequívoco de claim, heartbeat, finish e reclaim;
- preservar as regras de gate do produto.

Artefatos históricos sob `coop/` e `.multicontroller/` descrevem ou implementam a linha V1. Não os copie para projetos novos sem uma migração e revisão V2 explícitas.

## 6. Valide antes do primeiro claim

```powershell
python .\galaxy.py bootstrap C:\AI\Projetos\Equipe --check
python .\galaxy.py validate C:\AI\Projetos\Equipe --gate
python .\galaxy.py doctor C:\AI\Projetos\Equipe --json
git -C C:\AI\Projetos\Equipe status --short
```

No GitHub, confirme manualmente:

1. workflow executado a partir da revisão fixada no lock;
2. check publicado exatamente como `galaxy / validate`;
3. ruleset bloqueando merge quando o check falha;
4. operador não autorizado sem permissão de mutar a coordenação;
5. duas solicitações concorrentes resolvidas por serialização;
6. claim expirado recuperado sem sobrescrever trabalho ativo;
7. revisão independente antes do merge.

## 7. Fluxo operacional

```text
registrar tarefa e dependências
→ solicitar claim com revisão esperada
→ trabalhar em galaxy/<operador>/<tarefa>-<slug>-<máquina>-<tentativa>
→ heartbeat enquanto a tarefa estiver ativa
→ publicar evidência e checks
→ revisão independente
→ concluir claim
→ integrar apenas com galaxy / validate verde
```

Branches históricas `codex/*` continuam reconhecidas pelo Lifecycle Manager, mas novas tarefas usam `galaxy/*`.

## 8. Diagnóstico e recuperação

- `revision mismatch`: recarregue o estado, reavalie dependências e tente com a revisão atual.
- `actor not authorized`: corrija `operators` por mudança revisada; não amplie permissões do workflow.
- check ausente: confirme nome do job, gatilho, lock e commands do produto.
- coordenador não ativo: interrompa novos claims; o arquivo CO-OP local não substitui automação remota.
- lease expirado: verifique heartbeat e atividade real antes de reclaim.
- `.codex/` rastreado: retire apenas caminhos comprovadamente gerados do índice e preserve a cópia local.
- estado V1: execute `galaxy migrate --preview`; não renomeie diretórios manualmente.

Use `python .\galaxy.py cleanup <PROJETO> --preview` para inspecionar candidatos antigos. Aplique limpeza somente depois de excluir branches/worktrees ativos.
