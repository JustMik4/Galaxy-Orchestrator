# Alterações

Versão 1.3: integração equivalente por fila Actions e Issue canônica substitui lead pessoal fixo. Seções anteriores registram histórico e não prevalecem sobre a SPEC atual.

## 1.3.0

- Integradores autorizados com autoridade equivalente, sem lead pessoal obrigatório.
- Claims e merges serializados por GitHub Actions e Issue canônica versionada.
- Recuperação por outro integrante após resposta perdida; retomada preserva contrato e dependências.
- Migração para schema 3 e roteiro de ativação/publicação.
- Onze testes adicionais do coordenador com transporte simulado.

## 1.2.0

- CO-OP com um operador; revisão do parceiro opcional.
- Agente revisor independente obrigatório com evidência de sessão/head/base.
- Retomada pelo principal sem ACK após impedir integração antiga.
- Migração da política AGENT_TEAM schema 1→2 preserva identidades.
- Instruções GitHub retiram dependência de aprovação humana externa.

## 1.1.0

- `Iniciar.bat`: menu para criar/importar projeto, instalar em projeto existente e validar configuração.
- Destino padrão `AI/Projetos`, irmão de `AI/Codex-Multicontroller`.
- Importação para pasta nova sem mover origem; criação temporária com rollback; recusa de conflitos.
- Seleção de modo/preset e confirmação do destino.
- Oito testes adicionais de criação/importação, incluindo pastas vazias e erros de leitura.

## 1.0.0

SPEC, auditoria, protocolo SOLO/CO-OP, Adaptive Controller, roles, skill, instalador PowerShell e 27 testes.
