# Publicação do Galaxy Orchestrator V2

A publicação V2 inclui versão, código, documentação, identidade local e identidade remota. Nenhuma etapa de renomeação é considerada concluída por esta documentação.

## 1. Congelar a integração

1. pare novos agentes e processos que usam o checkout;
2. confirme que não há migração, bootstrap ou cleanup em andamento;
3. revise `git status --short` e atribua cada alteração ao seu responsável;
4. integre e revise mudanças por módulo;
5. confirme `VERSION` igual a `2.0.0` e documentação V2 canônica.

Não renomeie a pasta enquanto um shell, agente ou subprocesso estiver usando-a como diretório atual.

## 2. Executar a aceitação final

```powershell
python -m compileall -q lib tests galaxy.py multicontroller.py
python -m unittest discover -s tests -v
git diff --check
git status --short
```

Além da suíte, valide:

- fresh install SOLO e CO-OP;
- clone limpo + bootstrap + `bootstrap --check` idempotente;
- `validate --gate` com checks reais;
- Doctor sem `FAIL` e com `UNKNOWN` preservado onde não há telemetria;
- migração V1.3, receipt e rollback exato;
- smoke migration do projeto Lexy;
- Lifecycle Manager em preview;
- runtime model/effort quando o host fornecer observação;
- Resource Catalog e Vault sem rede ou mutação implícita;
- ausência de `.codex/` e outros gerados no índice.

Registre commit, ambiente, comandos, códigos de saída e logs. Não reutilize contagens V1 como evidência V2.

## 3. Preparar o artefato

O mantenedor da release deve revisar e, se necessário, regenerar `RELEASE-MANIFEST.json` a partir do conteúdo final. Verifique que ele não inclui cache, receipts, backups, runtime, Vault privado ou arquivos ignorados.

Faça o release a partir de estado limpo e conhecido. Fixe dependências por SHA/tag exatos; não use `main` flutuante no workflow distribuído.

## 4. Renomear a instalação local

Somente depois de encerrar processos e registrar estado conhecido:

```powershell
Set-Location C:\AI
Move-Item -LiteralPath C:\AI\Codex-Multicontroller -Destination C:\AI\Galaxy-Orchestrator
Set-Location C:\AI\Galaxy-Orchestrator
python .\galaxy.py --help
python -m unittest discover -s tests -v
git status --short
```

Atualize atalhos, terminais, automações e referências locais. Se qualquer processo ainda apontar para a pasta antiga, pare e corrija antes de prosseguir.

## 5. Renomear o repositório GitHub

Destino canônico:

```text
JustMik4/Galaxy-Multicontroller
→ JustMik4/Galaxy-Orchestrator
```

Use o Action Resolver: aplicativo/conector/plugin GitHub, depois `gh`, API oficial e navegador apenas com aprovação explícita. Antes da ação, confirme proprietário, repositório, branch padrão, workflows, environments e rulesets. Depois, verifique a URL retornada pela própria capability.

Atualize o remote somente após confirmar a renomeação:

```powershell
git remote -v
git remote set-url origin https://github.com/JustMik4/Galaxy-Orchestrator.git
git remote -v
git ls-remote --exit-code origin HEAD
```

Redirects do nome anterior são compatibilidade temporária, não configuração canônica.

## 6. Revalidar GitHub e projetos

1. confirme `.github/workflows/galaxy-validate.yml` usando a URL canônica;
2. confirme o check `galaxy / validate` no ruleset;
3. execute workflow por push/PR controlado;
4. confirme que o lock resolve a tag/SHA publicada;
5. faça fresh clone do master no caminho canônico;
6. faça fresh clone de um projeto V2 e rode bootstrap/gate;
7. repita Doctor e smoke Lexy após a renomeação.

## 7. Tag e release

Somente após todos os gates anteriores:

```powershell
git tag -s v2.0.0 -m "Galaxy Orchestrator 2.0.0"
git push origin v2.0.0
```

Crie a release com changelog, instruções de migração, limites externos conhecidos e hashes dos artefatos. A assinatura da tag e a publicação remota dependem das credenciais e da política do mantenedor.

## Critério de conclusão

A release está publicada apenas quando o commit/tag remoto, fresh clone, workflow fixado, ruleset, instalação canônica, migração Lexy e suíte final têm evidência registrada. Até lá, use “candidato V2”.
