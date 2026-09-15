---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls]
---

# Changelog

## 2.3.4 — 2026-09-15

- Corrige a regressão de escopo da distribuição 2.3.3: YouTube volta a buscar/baixar via yt-dlp sem API key; redes sociais deixam de ser classificadas como somente referência.
- Integra os seis helpers próprios de B-roll e o coletor próprio Instagram do fluxo original. Instagram mantém navegador/Playwright → dois streams CDN → curl → FFmpeg → verificação e hash de áudio.
- Instala dependências oficiais via PyPI e npm na máquina do destinatário. Valida Node 22+, Python e executáveis. Nenhuma biblioteca de runtime é distribuída com a skill.
- Preview remoto prepara mídia de trabalho, retém origem/autoria e intervalo absoluto. Fetch recorta os mesmos bytes revisados, sem deslocamento de tempo.
- Corrige reuso de parcial após falha curl; helpers encontram a venv e habilitam runtime JavaScript. Aceita URLs Instagram com nome do perfil.
- Reescreve instalação, skill, rotas, recuperação e evidências; mantém aprovação editorial humana e não inventa licença.
- Remove fontes tipográficas externas da distribuição; HTML usa fontes do sistema.
- Reteste real concluiu Instagram (captura no navegador, dois canais, merge/decodificação) e TikTok (trecho/GIF usando instalação limpa); QUALITY atualizado sem embutir mídias/configs.
- Consolida AGENTS.md com os contratos do produto, manutenção e verificação; revisa README, instalação, compatibilidade, segurança e guias contra a CLI atual.
- Entrega atual em pasta, sem gerar/atualizar ZIP.
- Versão 2.3.3 preservada como histórico; não representa a distribuição recuperada.

## 2.3.3 — 2026-09-15 (parecer de lançamento retirado)

- Downloads interrompidos por Ctrl-C removem o parcial criado e permitem nova tentativa.
- Regenerar uma prévia preserva rejeição/pendência; estado verificado depende de aprovação vigente.
- Testes reais autenticados de busca, refresh e download Pexels/Pixabay; GIF e storyboard produzidos com os dois originais.
- Fluxo de bancos documentado explicitamente: obtenção externa do original para GIF e coleta final aprovada são etapas diferentes.
- A revisão original classificou esta versão como beta local; o parecer foi retirado porque ela não preservava os transportes sociais do produto de origem. Correção na 2.3.4.

## 2.3.2 — organização da skill

- Frontmatter padronizado: propriedades operacionais preservadas em metadata.
- Entrada mais curta, referências carregadas por necessidade e caminhos absolutos para outro cwd.
- Instalação documentada para Codex e Claude Code, com uma cópia compartilhada.
- Metadados de apresentação Codex em agents/openai.yaml.
- Insert isolado não exige roteiro completo; fala ausente não é inventada e padrões de regras são identificados como padrões.
- Entrega desta revisão em pasta de repositório; empacotamento e publicação ficam para etapa posterior.

## 2.3.1 — 2026-09-15

- Estabilização: regras inválidas com erros orientados, importação repetida sem descarte silencioso de procedência e escrita recuperável de manifesto/candidatos/eventos.
- Logs operacionais locais, avisos de fallback, proteção contra comandos simultâneos e cópia de imagem validada antes de finalizar.
- CLI, execução, geração do storyboard e preparação de prévias separados em módulos legíveis.
- Print da pessoa opcional e estático; `GB_GIF_SCOPE=broll|full` escolhe insert ou composição previamente fornecida.
- Impressão padrão do navegador e botão usam os mesmos quadros estáticos.
- Versão centralizada e ZIP reproduzível; helpers históricos fora da fonte operacional.
- Validação não autenticada nesta rodada; APIs pendentes mantidas para etapa posterior.

## 2.3.0 — 2026-09-15

- RULES.md por projeto: tipos, formato, preferências/bloqueios, regras editoriais e declaração explícita do usuário sobre direitos.
- Memória de referências positivas/negativas com motivo e responsável.
- Imagens e screenshots locais no mesmo ciclo de revisão/entrega; fonte, autor e captura preservados.
- Plano Playwright para viewport móvel/desktop, snapshot e screenshot, com documentação de inspeção.
- Relatório de adequação Reels/horizontal, preservando proporção nativa.
- Auditoria ao vivo Commons/NASA e correção do transporte HTTPS de assets NASA; provedores com chave permanecem pendentes.

## 2.2.0 — 2026-09-15

- Configuração `.env`: GIF ou estático, largura, FPS, cores, limite de duração/tamanho e quantidade de frames.
- Poster/contact sheet e GIF nativo sem upscale; limite de bytes com fallback explícito para estático.
- Storyboard com revisão, sugestões, exportação/importação JSON e impressão estática; galeria estática e nenhum player incorporado.
- Identidade do projeto e verificação de assinatura de fonte/intervalo na importação.
- Inserts independentes com `resolve --shot`.
- README, licença MIT, avisos OFL, política de segurança, contribuição, CI e distribuição por allowlist sem mídia real/segredos/legado.

## 2.1.0 — 2026-09-15

- Router multi-fonte, ledger local, aprovação, evidência de permissão, corte e verificação.
- Storyboard e caso visual local de desenvolvimento. O caso real não integra a distribuição pública.
