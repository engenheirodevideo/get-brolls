---
type: documentation
status: current
created: 2026-11-06
updated: 2026-11-06
tags: [get-brolls, documentation, index]
---

# Documentação do getbrolls

Dois públicos, duas listas. Quem usa o getbrolls começa pela primeira; quem escreve plugin, automatiza a CLI ou muda o código vai para a segunda.

## Para quem usa

| Documento | Para quê |
|---|---|
| [MANUAL.md](MANUAL.md) | Manual e tutorial: primeiro B-roll em 5 minutos, os oito passos com cada comando explicado, roteiro, clientes e receitas, memória, análise de mídia, plugins, como ler a resposta e o que fazer quando dá erro. |
| [GUIDE.md](GUIDE.md) | Guia completo: instalação e runtime, atualizar da 2.5 para a 2.6, perfil do workspace, fontes, navegador, Instagram, pastas do projeto e Storyboard. |
| [BRIEF.md](BRIEF.md) | O modelo do plano de b-rolls de um vídeo. |
| [RULES.md](RULES.md) | O modelo das regras do canal: formato, fontes preferidas, sites bloqueados e direitos. |
| [`.env.example.pt-BR`](../.env.example.pt-BR) | Todas as variáveis de configuração, comentadas. |
| [CHANGELOG.md](../CHANGELOG.md) | O que mudou em cada versão. |

## Referência técnica

| Documento | Para quê |
|---|---|
| [PLUGIN_DEV_QUICKSTART.md](PLUGIN_DEV_QUICKSTART.md) | O caminho curto para criar, testar e instalar um plugin. |
| [SDK.md](SDK.md) | A referência do SDK: manifesto, fontes, rotas, comandos, exportadores, resolvedores, instalação, marketplaces e confiança. |
| [CLI_CONTRACT.md](CLI_CONTRACT.md) | Contrato da CLI para scripts e agentes: stdout e stderr, códigos de saída, `error_code`, avisos, variáveis `GB_*` e deprecação. |
| [SCHEMAS.md](SCHEMAS.md) | O formato de cada arquivo que o getbrolls grava, com a versão e o layout do projeto. |
| [SECURITY.md](SECURITY.md) | Para onde os dados vão, rede, perfil, plugins oficiais e de terceiros, e como relatar uma vulnerabilidade. |
| [QUALITY.md](QUALITY.md) | Portões de qualidade, ensaios reais e limites conhecidos. |
| [CHECKLIST.md](CHECKLIST.md) · [CODE_STYLE.md](CODE_STYLE.md) | A ordem de toda mudança e o estilo de código. |
| [CONTRIBUTING.md](../CONTRIBUTING.md) · [AGENTS.md](../AGENTS.md) | Como contribuir e o mapa do repositório para agentes. |
