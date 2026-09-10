# Publicar o pacote mestre

Publique o conteúdo da pasta Codex-Multicontroller como raiz do repositório mestre. Preserve template,
coop e pastas ocultas; não publique local/, __pycache__, contas ou arquivos de projetos pessoais.
O repositório mestre distribui o environment; cada projeto da equipe terá seu próprio repositório.

Crie release v1.3.0 e anexe o ZIP e o arquivo SHA256. Colaboradores baixam a release, extraem em C:/AI
e abrem Iniciar.bat. No projeto comum eles clonam a configuração aprovada, com identidade local própria.
O diretório raiz .github roda testes do pacote; os workflows em coop/.github são templates para projetos.
Publicação do mestre não ativa automaticamente o coordenador em Lexy ou em outros repositórios.

Nenhuma credencial é incluída. Esta entrega não afirma publicação até existir URL de release confirmada.
