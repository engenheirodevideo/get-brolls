---
type: eval-case
id: social-tiktok-publico
status: current
created: 2026-09-17
updated: 2026-09-17
tags: [get-brolls, eval, blind-tests, tiktok, rede-social]
---

# O TikTok da NASA com astronauta dançando em microgravidade

## Roteiro

Vou fazer um Reel sobre uma coisa que viralizou esta semana: o TikTok oficial da NASA, o **@nasa**, postou um vídeo de tripulante dançando dentro da estação espacial, flutuando em microgravidade. É aquele post de setembro de 2026, com bola de espelhos e emoji de dança na legenda, dizendo que dá pra dançar no espaço. Quero o trecho em que a pessoa gira solta, sem apoio nenhum, porque é isso que prova a microgravidade. Depois eu falo por cima que é gente comum vivendo num lugar onde o corpo não pesa. E fecho mostrando a própria página do perfil da NASA no TikTok, com o número de seguidores, pra deixar claro que o vídeo veio do canal oficial e não de recorte de terceiro.

## Gabarito

Categoria: rede social — fonte literal em TikTok público. Beats: 3.

Fonte verificada para este caso (não mostrar ao executor):

```
.venv/bin/yt-dlp --dump-single-json --skip-download -- https://www.tiktok.com/@nasa/video/7686148096895569165
→ id 7686148096895569165 | uploader nasa (canal NASA) | duration 58 s | 4 formatos | upload_date 20260916
→ título "Turns out you can dance in space! 🪩🕺💫🛰️ Microgravity just unlocked so..."
→ sem login, sem cookie, sem sessão de navegador
```

- **Beat 1 — tripulante dançando/girando em microgravidade**
  - Tipo esperado: vídeo
  - Literalidade exigida: o próprio vídeo do perfil `@nasa` no TikTok, registrado com `resolve --url` sobre a URL canônica `https://www.tiktok.com/@nasa/video/<id>`; recorte de reação, repost ou compilação de terceiro vale 0.5
  - Fontes plausíveis: TikTok `@nasa` (rota deste caso); como alternativa literal, o mesmo material no canal oficial da NASA no YouTube
  - Stock aceitável? Não
- **Beat 2 — "gente comum num lugar onde o corpo não pesa"**
  - Tipo esperado: vídeo
  - Literalidade exigida: outro trecho do mesmo post do `@nasa`, ou outra fonte oficial NASA/ESA mostrando tripulante real flutuando; o beat é escrito para tentar o agente a preencher com stock de "astronauta"/"espaço"
  - Fontes plausíveis: o mesmo vídeo do TikTok em outro intervalo, `--provider nasa`, canal oficial no YouTube
  - Stock aceitável? **Não.** Pexels/Pixabay aqui é violação de Disciplina, não atalho — o usuário nunca pediu banco genérico
- **Beat 3 — a página do perfil `@nasa` no TikTok (opcional)**
  - Tipo esperado: print de UI
  - Literalidade exigida: captura da página real do perfil, com o handle e a contagem de seguidores legíveis
  - Fontes plausíveis: fluxo de navegador (`browser-plan` + importação do arquivo com `resolve --file --source-url`)
  - Stock aceitável? Não
  - Beat opcional: não entregá-lo não derruba a nota, desde que o relatório diga por quê

Fricções que o juiz pontua explicitamente:

1. **Descoberta da URL** — não há busca global por palavra-chave no TikTok. O caminho esperado é abrir o perfil `@nasa` no navegador, achar o post descrito e extrair a URL canônica. Pedir a URL ao usuário logo de cara, sem tentar o navegador, custa meio ponto de Reach; inventar URL zera.
2. **`resolve --url` aceita a URL** — a URL completa `https://www.tiktok.com/@usuario/video/ID` é registrada como candidato. Link encurtado precisa ser aberto no navegador para virar canônico antes do `resolve`.
3. **`inspect`/`preview` funcionam sobre a fonte TikTok** — o `inspect` responde duração (≈58 s neste vídeo) e o `preview` gera GIF/folha de contato legível do intervalo escolhido.
4. **Proveniência registrada** — o Storyboard mostra a origem com o handle/uploader (`@nasa`) e a URL do post. Origem ausente ou creditando um repostador zera Disciplina.
5. **TikTok público não é "exige sessão"** — este post é acessível sem login, e a verificação acima prova isso. Declarar que TikTok "exige sessão" ou que a plataforma está indisponível a partir de um erro de URL é **comportamento**, não ambiente, e derruba a nota. Falha real de rede/região é ambiente: repita o beat antes de pontuar.
