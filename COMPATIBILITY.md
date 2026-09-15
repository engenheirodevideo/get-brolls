---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls]
---

# Compatibilidade

- Python 3.11+, FFmpeg/ffprobe; yt-dlp[default]/EJS e runtime JS para fontes sociais. Navegador/Playwright e curl no processo Instagram.
- Storyboard V2 usa navegador moderno com JavaScript, Blob e localStorage. Se armazenamento local falhar, exporte o JSON antes de fechar.
- Caminhos de prévias são relativos: compartilhe `brolls/` completo.
- CLI preserva schema do manifest v1 e adiciona `project_id`, metadados de prévia e revisão. Exportação de revisão usa `templateVersion: 2`.
- Artefatos do protótipo V2 anterior não têm assinatura de fonte/intervalo; não podem ser importados. Regenere o storyboard com `review`.
- `--shot` permite múltiplos inserts da mesma fonte; sem ele, a resolução deduplica por fonte.
- Helpers originais e coletor Instagram incluídos na 2.3.4. Review usa GIF/imagens, sem player remoto incorporado.
- Projeto local confiável, uso serial. Comandos simultâneos no mesmo projeto são recusados. Não há colaboração multiusuário; evidências remotas por fonte estão em QA e API-STATUS.

Versão 2.3: asset_type/image, RULES e biblioteca de referências por projeto. APIs pesquisam vídeos; imagens e screenshots entram por arquivo local. Regras usam JSON embutido em Markdown; nenhum parser YAML externo é necessário.

2.3.1 centraliza a assinatura incluindo o escopo da prévia e arquivos de contexto. Regenere o storyboard de versões anteriores e solicite nova revisão; JSON antigo pode ser recusado como desatualizado. Suporte da trava local: macOS/Linux (`fcntl`).

## Agentes — revisão 2.3.2

Entrada no padrão Agent Skills (`name`, `description`, `license`, `metadata`). Metadados operacionais do vault ficam em `metadata`, sem campos próprios no nível superior do SKILL.md. Os demais documentos mantêm seu frontmatter operacional.

Codex e Claude Code usam a mesma pasta, com destinos e invocações descritos em INSTALL.md. `agents/openai.yaml` é opcional e específico do Codex. Não há dependência de hooks, MCP, permissões preaprovadas ou sintaxe de interpolação exclusiva do Claude. Validação estrutural não equivale a teste de descoberta em uma sessão nativa de cada produto.

## Migração para 2.3.4

A prévia remota agora pode adquirir um trecho e guardar `local_start_s` junto ao hash da fonte. A assinatura inclui esse offset. Preserve o projeto e as decisões antigas como histórico, gere nova prévia/review e solicite nova decisão quando a revisão anterior for recusada por assinatura desatualizada. Não edite hashes/assinaturas para forçar uma aprovação antiga.

Projetos que já possuíam arquivo local continuam usando esse arquivo. Preserve os caminhos dos originais e `.getbrolls-sources/` para regenerar prévias; compartilhar só `brolls/` permite visualizar o storyboard, não continuar toda a edição em outro computador.

A suíte atual passou em macOS/Python 3.14.6. A matriz CI de Python 3.11/3.12/3.13 no Ubuntu está configurada, mas sua execução remota não foi verificada nesta rodada. Windows nativo não é suportado pelo uso de `fcntl`; WSL é um ambiente Linux separado e ainda precisa de validação própria. Versões de dependências ensaiadas estão em INSTALL.
