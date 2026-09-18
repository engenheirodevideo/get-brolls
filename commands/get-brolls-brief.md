---
name: get-brolls-brief
description: Entrevista o usuário em até 7 perguntas, escreve o BRIEF.md do vídeo e valida antes de qualquer busca.
---

# Fazer o brief do vídeo

Use quando alguém pedir b-roll e o projeto ainda não tiver `BRIEF.md`. Leia `${CLAUDE_PLUGIN_ROOT}/references/interview.md` antes de perguntar qualquer coisa: as sete perguntas, a ordem, os critérios de parada e os defaults estão lá, e não são para improvisar.

1. Descubra a pasta do projeto com o usuário (a que guarda ou vai guardar `brolls/`). Se não houver `RULES.md`, crie com `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" init-rules --project <projeto>`.

2. Conduza a entrevista: **uma pergunta por mensagem**, teto de sete, parando quando 1, 3 e 7 estiverem respondidas. Dois "tanto faz" seguidos encerram a entrevista — aplique os defaults e siga.

3. Crie o modelo e preencha o bloco JSON com o que você ouviu:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" init-brief --project <projeto>
```

Edite apenas o bloco ```json do `BRIEF.md`, mantendo a prosa. Cada beat precisa de `id` (minúsculas, números e hífen — ele vira o `--shot`) e de `target`. Nunca invente narração, link, licença ou nome de responsável.

4. Valide antes de buscar qualquer coisa:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" brief --validate --project <projeto>
```

Corrija o que a mensagem apontar e repita até passar. Só então colete.

5. Devolva o brief ao usuário em poucas linhas, no tom de `${CLAUDE_PLUGIN_ROOT}/references/templates-de-resposta.md`: o que você entendeu, quantos beats, o que assumiu por default e o que ainda falta dele. Peça correção, não aprovação formal.

6. Para coletar, peça o comando pronto de cada beat com `brief --beat ID --project <projeto>` e use `--shot <id>` em todo `resolve`. A decisão humana continua vindo do Storyboard (`review` + `import-review --by NOME`) ou da fala no chat (`approve --candidate ID --by NOME --channel chat --statement "frase"`).
