---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls]
---

# Captura de notícias e páginas pelo navegador

Workflow opcional do agente com [Playwright CLI oficial](https://github.com/microsoft/playwright-cli). Requer Node.js/npm/npx e navegador disponível. A skill não inclui navegador nem cookies. O plano usa listas de argumentos, não eval de conteúdo do usuário.

## Preparar

```sh
python3 scripts/gb.py rules --project ./video-01
python3 scripts/gb.py references --project ./video-01
python3 scripts/gb.py browser-plan --url https://www.nasa.gov/news/recently-published/ --project ./video-01
```

Leia primeiro as regras editoriais e referências aprovadas/rejeitadas. Priorize `preferred_domains` nas pesquisas do navegador e não use `blocked_domains`. Exemplos de queries: assunto + entidade + site preferido. Confira a URL real encontrada; não invente resultados.

Defina `GB_SKILL_DIR` com a pasta instalada. Os exemplos abaixo usam a CLI local instalada pelo INSTALL. `browser-plan` também pode emitir a forma equivalente via `npx`, que obtém a CLI do npm quando necessário.

O plano devolve comandos `open`, `resize`, `snapshot` e `screenshot`, caminho único e tipo habilitado para a captura. Execute em ordem e inspecione o snapshot antes de interações. Exemplo operacional:

```sh
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls open https://www.nasa.gov/news/recently-published/ --headed
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls resize 390 844
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls snapshot
# Depois de selecionar a notícia por uma referência do snapshot atual:
# ... click REF_REAL
# ... snapshot
# Use o caminho de captura fornecido por browser-plan:
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls screenshot --filename=./video-01/output/playwright/news.png
```

`resize` muda a área visível para layout móvel. Não é emulação completa de dispositivo/touch/user-agent. Para horizontal, configure viewport desktop no RULES. Full-page é configurável, mas prints longos não cabem automaticamente num insert 9:16: selecione trecho legível, mantendo a origem. Não distorça a página para preencher o frame.

## Inspeção e importação

- Confira manchete, autor, data da notícia, URL final e carregamento de imagens. Registre data de captura separada da publicação.
- Se conteúdo estiver atrás de login/paywall, registre indisponibilidade; não tente contornar. O agente só usa acessos autorizados pelo usuário.
- Tire novo snapshot após navegar ou alterar significativamente a página. Nunca reutilize referências obsoletas.
- Confira o screenshot visualmente antes de importar. Não transforme banner de erro/cookies em asset aprovado.

```sh
python3 scripts/gb.py resolve --file ./video-01/output/playwright/news.png --asset-type news_screenshot --source-url URL_REAL --title "Manchete real" --creator "Autor informado" --captured-at "2026-09-15T12:00:00-03:00" --shot news-01 --project ./video-01
python3 scripts/gb.py preview --candidate ID --narration "Fala do roteiro" --reason "Notícia comprova o evento citado" --project ./video-01
python3 scripts/gb.py review --project ./video-01
```

Substitua os metadados de exemplo pelos dados reais. Imagem estática não exige `--start/--end`. Se apenas `web_screenshot` estiver habilitado, use esse tipo. Revisão, decisão de direitos, fetch e referência seguem o fluxo normal.

Não execute `close-all` nem feche abas de outros projetos. Encerre somente a sessão criada para a captura quando terminar. Se o ambiente do agente exigir outro transporte de navegador, mantenha a mesma sequência e metadados usando suas ferramentas autorizadas.
