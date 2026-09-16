---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls]
---

# Segurança e privacidade

Não comite `.env`, originais, manifestos privados ou exports de revisão de clientes. Revise a árvore antes de publicar. O servidor de exemplo escuta apenas localhost. Não distribua `.venv/`, `.tools/`, perfis de navegador, cookies ou pares CDN assinados. Os instaladores obtêm as dependências nos registros oficiais.

Projetos/JSON são dados locais confiáveis. Não execute a skill como serviço público aceitando URLs, caminhos ou manifests arbitrários. Os filtros de URL não constituem isolamento de rede contra todo ataque de DNS rebinding. A revisão não autentica quem clicou: importação exige atribuição humana com `--by`.

Para reportar vulnerabilidades, use o canal privado de segurança do repositório quando habilitado pelo mantenedor. Até existir um canal privado, não publique segredos ou dados de clientes em issues. Nenhum endereço de contato é presumido neste pacote.
