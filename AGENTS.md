---
type: instructions
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, documentation]
---

# Instruções para agentes — Get B-rolls

## Mapa do repositório

Este arquivo é o índice central para agentes e mantenedores: tudo que um agente precisa encontrar está roteado abaixo. Cada assunto tem um destino único; não duplique a instrução aqui nem no destino.

| Preciso de | Destino |
|---|---|
| Operar a skill (coletar, prever, revisar, entregar) | [SKILL.md](SKILL.md) — contrato de operação e fonte canônica; no plugin do Claude Code a skill descoberta é o espelho [skills/get-brolls/SKILL.md](skills/get-brolls/SKILL.md). |
| Instalar no Codex | Clone o repositório, apresente-o pelo [agents/openai.yaml](agents/openai.yaml) e acione com `$get-brolls`. |
| Instalar no Claude Code como skill | Clone o repositório na pasta de skills do agente e acione com `/get-brolls`. |
| Instalar no Claude Code como plugin | `/plugin marketplace add engenheirodevideo/get-brolls`, acione com `/get-brolls:get-brolls` e prepare o ambiente com [`/get-brolls-setup`](commands/get-brolls-setup.md). |
| Instalar no Gemini CLI | [GEMINI.md](GEMINI.md) — o snippet de importação `@` que o usuário acrescenta ao próprio `GEMINI.md`. |
| Guia operacional (instalação, provedores, navegador, Storyboard, `status`) | [GUIDE.md](GUIDE.md) |
| Qualidade, evidências reais e limites conhecidos | [QUALITY.md](QUALITY.md) |
| Contribuir (fluxo de mudança, revisão, PR) | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Segurança, egress e dados privados | [SECURITY.md](SECURITY.md) |
| O que mudou em cada versão | [CHANGELOG.md](CHANGELOG.md) |
| Visão do produto e primeiro uso | [README.md](README.md) · [README.en.md](README.en.md) |
| Regras editoriais e configuração do projeto | [RULES.md](RULES.md) · [.env.example](.env.example) |

Os roteadores por agente ([CLAUDE.md](CLAUDE.md) e [GEMINI.md](GEMINI.md)) apontam para este mapa; as regras de manutenção do repositório continuam nas seções abaixo.

## Escopo e entrada

Esta pasta contém o produto **GET B-ROLLS — ENGENHEIRO DE VÍDEO**: skill, CLI, utilitários, interface de revisão e documentação. Leia [SKILL](SKILL.md) para executar uma coleta e [GUIDE](GUIDE.md#instalação) para preparar o ambiente. Nenhum outro repositório é necessário.

Use a pasta da skill como base para scripts e um `--project` explícito para a coleta. Fora desta pasta, execute o CLI pelo caminho absoluto. Não grave projetos/mídias dentro da fonte da skill. Mantenha esta entrega isolada de outras instalações do autor.

## Contratos do produto

- YouTube busca e baixa com yt-dlp/FFmpeg, **sem YouTube API key**. Pexels/Pixabay usam somente suas próprias chaves opcionais.
- Instagram preserva o fluxo navegador/Playwright → vídeo e áudio do mesmo Reel → configs temporários → coletor próprio → FFmpeg/ffprobe. Leia a seção [Instagram](GUIDE.md#instagram--navegadorplaywright-dois-streams-e-mp4). Use o navegador logado indicado pelo usuário quando disponível. O script não captura sozinho o navegador.
- TikTok usa URL completa descoberta no navegador e yt-dlp. Não declare busca global por palavra-chave implementada na CLI.
- Fonte literal nomeada tem prioridade quando a fala citar pessoa, produto ou fato. Prefira 1080p quando disponível; confirme dimensões reais, sem upscale para simular qualidade.
- `preview` pode obter mídia de trabalho antes da decisão editorial. `--reference-only` é uma escolha explícita. `fetch` publica o corte final depois da decisão humana e das condições de uso registradas.
- Não invente fala, autor, licença ou aprovação. `approve` registra decisão explícita já recebida. Preserve origem, hash, intervalo e contexto; mudanças relevantes invalidam revisão.
- Os helpers Bash usam VIDEO_ID do YouTube e não gravam automaticamente o ledger. São opcionais no macOS/Linux; no Windows nativo, use os comandos equivalentes da CLI principal. Importe os resultados dos helpers no fluxo comum quando precisar do registro/revisão.

## Dependências e arquivos privados

Distribua instruções, comandos e código próprio. O destinatário instala bibliotecas oficiais conforme [GUIDE](GUIDE.md#instalação). Nunca copie `.venv/`, `.tools/`, `node_modules/`, bibliotecas, executáveis externos ou perfis de navegador para a fonte de distribuição.

Não grave chaves, cookies, URLs assinadas, configs CDN, originais ou projetos de clientes nos exemplos, logs públicos ou documentação. Mantenha os pares Instagram em pasta privada do projeto; relatórios mostram fonte pública e resultado técnico, sem assinatura CDN. Credenciais de Pexels/Pixabay pertencem ao ambiente do usuário ou ao `.env` privado.

Execute comandos do mesmo projeto serialmente. Preserve originais, eventos e journal. Se a gravação do estado terminou e a página falhou, regenere `review`; não apague o projeto para contornar erro.

## Manutenção

- A versão executável vem de `scripts/getbrolls/__init__.py`; mantenha `SKILL.md`, `skills/get-brolls/SKILL.md`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, README, GUIDE, QUALITY e CHANGELOG coerentes quando houver mudança de versão. `SKILL.md` da raiz é a fonte canônica; o espelho em `skills/get-brolls/` mantém `description` idêntica e o mesmo conteúdo com caminhos `${CLAUDE_PLUGIN_ROOT}` (invariante coberto por teste). Revisão documental sem alteração de versão deve aparecer no changelog vigente.
- Atualize a seção correspondente de `GUIDE.md` junto com o código da rota afetada. O guia é a referência operacional única do produto.
- Corrija a causa e adicione regressão quando houver bug. Não escreva testes que exijam retirar uma capacidade existente.
- Use mídia sintética e mocks em testes automatizados. Ensaios de rede ficam fora da fonte e registram URL pública, versão utilizada, resultado, dimensão/duração e limites.
- Valide comandos documentados com `--help` e links relativos. Documentos operacionais que usam frontmatter mantêm `type`, `status`, `created`, `updated` e `tags`; no SKILL esses campos ficam em `metadata`.
- Não execute publicação, push ou instalação pessoal da skill como parte automática de uma revisão. Faça essas ações somente quando incluídas no pedido do usuário. A árvore do repositório é a fonte oficial da entrega.

## Verificação

```sh
bash scripts/install.sh --check
python3 scripts/gb.py doctor
python3 -m unittest discover -s tests -v
```

No Windows PowerShell, troque o primeiro comando por `powershell -ExecutionPolicy Bypass -File scripts/install.ps1 -Check` e use `python` nos dois seguintes.

`--check` valida pré-requisitos do instalador; não instala bibliotecas nem testa sessão/rede. `doctor` informa disponibilidade; `doctor --live` faz buscas/refresh limitados e pode consumir quota. Nenhum deles substitui teste de aquisição, prévia e decodificação da fonte afetada.

Consulte [QUALITY](QUALITY.md) antes de afirmar que algo foi validado. Distingua testes locais, ensaios reais e verificação de descoberta nativa do agente. Não transforme uma falha de URL/sessão em afirmação de indisponibilidade permanente da plataforma.
