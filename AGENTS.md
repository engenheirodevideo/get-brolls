---
type: instructions
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, documentation]
---

# Instruções para agentes — Get B-rolls

## Escopo e entrada

Esta pasta contém a skill `get-brolls`, seus comandos, scripts customizados, interface de revisão e documentação. O processo e os helpers incorporados são de autoria de Bruno e fazem parte desta entrega. Leia [SKILL](SKILL.md) para executar uma coleta e [GUIDE](GUIDE.md#instalação) para preparar o ambiente. Nenhum produto externo de origem é necessário.

Use a pasta da skill como base para scripts e um `--project` explícito para a coleta. Fora desta pasta, execute o CLI pelo caminho absoluto. Não grave projetos/mídias dentro da fonte da skill. Mantenha esta entrega isolada de outras instalações do autor.

## Contratos do produto

- YouTube busca e baixa com yt-dlp/FFmpeg, **sem YouTube API key**. Pexels/Pixabay usam somente suas próprias chaves opcionais.
- Instagram preserva o fluxo navegador/Playwright → vídeo e áudio do mesmo Reel → configs temporários → coletor próprio → FFmpeg/ffprobe. Leia a seção [Instagram](GUIDE.md#instagram--navegadorplaywright-dois-streams-e-mp4). Use o navegador logado indicado pelo usuário quando disponível. O script não captura sozinho o navegador.
- TikTok usa URL completa descoberta no navegador e yt-dlp. Não declare busca global por palavra-chave implementada na CLI.
- Fonte literal nomeada tem prioridade quando a fala citar pessoa, produto ou fato. Prefira 1080p quando disponível; confirme dimensões reais, sem upscale para simular qualidade.
- `preview` pode obter mídia de trabalho antes da decisão editorial. `--reference-only` é uma escolha explícita. `fetch` publica o corte final depois da decisão humana e das condições de uso registradas.
- Não invente fala, autor, licença ou aprovação. `approve` registra decisão explícita já recebida. Preserve origem, hash, intervalo e contexto; mudanças relevantes invalidam revisão.
- Os helpers Bash usam VIDEO_ID do YouTube e não gravam automaticamente o ledger. Importe seus resultados no fluxo comum quando precisar do registro/revisão.

## Dependências e arquivos privados

Distribua instruções, comandos e código próprio. O destinatário instala bibliotecas oficiais conforme [GUIDE](GUIDE.md#instalação). Nunca copie `.venv/`, `.tools/`, `node_modules/`, bibliotecas, executáveis externos ou perfis de navegador para a fonte de distribuição.

Não grave chaves, cookies, URLs assinadas, configs CDN, originais ou projetos de clientes nos exemplos, logs públicos ou documentação. Mantenha os pares Instagram em pasta privada do projeto; relatórios mostram fonte pública e resultado técnico, sem assinatura CDN. Credenciais de Pexels/Pixabay pertencem ao ambiente do usuário ou ao `.env` privado.

Execute comandos do mesmo projeto serialmente. Preserve originais, eventos e journal. Se a gravação do estado terminou e a página falhou, regenere `review`; não apague o projeto para contornar erro.

## Manutenção

- A versão executável vem de `scripts/getbrolls/__init__.py`; mantenha `SKILL.md`, README, GUIDE, QUALITY e CHANGELOG coerentes quando houver mudança de versão. Revisão documental sem alteração de versão deve aparecer no changelog vigente.
- Atualize a seção correspondente de `GUIDE.md` junto com o código da rota afetada. O guia consolidado preserva tanto o processo de origem quanto as instruções atuais.
- Corrija a causa e adicione regressão quando houver bug. Não escreva testes que exijam retirar uma capacidade existente.
- Use mídia sintética e mocks em testes automatizados. Ensaios de rede ficam fora da fonte e registram URL pública, versão utilizada, resultado, dimensão/duração e limites.
- Valide comandos documentados com `--help`, links relativos e frontmatter. Markdown usa `type`, `status`, `created`, `updated`, `tags`; no SKILL esses campos ficam em `metadata`.
- Não execute empacotamento, publicação, push ou instalação pessoal da skill como parte automática de uma revisão. Faça essas ações somente quando incluídas no pedido do usuário. `dist/` contém snapshots antigos, não a versão atual desta pasta.

## Verificação

```sh
bash scripts/install.sh --check
python3 scripts/gb.py doctor
python3 -m unittest discover -s tests -v
```

`--check` valida pré-requisitos do instalador; não instala bibliotecas nem testa sessão/rede. `doctor` informa disponibilidade; `doctor --live` faz buscas/refresh limitados e pode consumir quota. Nenhum deles substitui teste de aquisição, prévia e decodificação da fonte afetada.

Consulte [QUALITY](QUALITY.md) antes de afirmar que algo foi validado. Distingua testes locais, ensaios reais e verificação de descoberta nativa do agente. Não transforme uma falha de URL/sessão em afirmação de indisponibilidade permanente da plataforma.
