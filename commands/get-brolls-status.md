---
name: get-brolls-status
description: Diz em que pé está a coleta de B-rolls do projeto e qual é o próximo passo, sem alterar nada.
---

# Onde está a coleta

Use quando a pessoa perguntar "e aí, como está?", quando você retomar um projeto parado ou quando não souber qual é o próximo passo. O comando é **somente leitura**: não escreve nada no projeto.

1. Descubra com o usuário a pasta do projeto (a que guarda `brolls/`).

2. Rode o resumo:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" status --project <projeto>
```

No Windows, use `python` no lugar de `python3`.

3. Leia o objeto `summary` do JSON. Ele traz `line` (a etapa atual), `stages` (o que já foi feito por etapa), `brief` (beats cobertos e faltando) e `do`, que é o próximo passo já pronto.

4. Responda ao usuário em poucas linhas e **repasse `summary.do.for_human` sem parafrasear**: essa frase já foi escrita na língua da pessoa e já diz o que fazer. Reescrever é como o passo se perde. Se `do.command` existir, você pode executá-lo; se `do.blocking_human` for `true`, **pare e espere a pessoa** — é revisão humana, condição de uso por item ou conflito de formato, e nenhum deles se resolve sozinho.

5. Não invente estado: cite apenas o que o `status` devolveu. Se ele apontar que falta `BRIEF.md`, chame `/get-brolls-brief`. Se apontar ferramenta faltando, chame `/get-brolls-setup`.
