---
name: get-brolls-eval
description: Executa um caso de teste cego do Get B-rolls até o Storyboard e preenche o relatório da rodada, sem abrir o gabarito.
---

# Rodar um teste cego do Get B-rolls

Você é o **executor** de um teste cego: mede o comportamento editorial da skill diante de um roteiro que não conhece. O processo completo está em `${CLAUDE_PLUGIN_ROOT}/eval/README.md` e a pontuação em `${CLAUDE_PLUGIN_ROOT}/eval/rubric.md`.

## Regra que define o teste

**Você nunca abre a seção `## Gabarito`.** Se receber um id do corpus, leia do arquivo **apenas** a seção `## Roteiro` e pare de ler ali. Não busque o gabarito por outro caminho (grep, leitura integral, listagem de conteúdo). O julgamento é um passo separado, feito por outro agente ou pelo humano depois que o seu relatório estiver fechado. Ler o gabarito invalida a rodada inteira, não só o caso.

## Entrada

O usuário passa **um** destes:

- um id do corpus (por exemplo `news-cop30-belem`) → o caso está em `${CLAUDE_PLUGIN_ROOT}/eval/corpus/<id>.md`;
- o texto de um roteiro avulso.

Sem entrada, liste os ids disponíveis em `${CLAUDE_PLUGIN_ROOT}/eval/corpus/` e pergunte qual rodar.

## Passos

1. **Isolar o projeto.** Crie uma pasta nova fora da instalação da skill, por exemplo `~/get-brolls-eval/<id>-<AAAA-MM-DD-HHMM>/`. Nunca reaproveite um projeto de outra rodada: estado antigo contamina as contagens do `status`.

2. **Ler só o roteiro.** Com id, extraia a seção `## Roteiro` do arquivo do caso e trabalhe apenas com ela. Com roteiro avulso, use o texto recebido.

3. **Categorizar os beats.** Derive os beats visuais da fala e, para cada um, declare **antes de buscar**: o tipo de asset esperado (vídeo, imagem ou print de UI) e a entidade literal que precisa aparecer. Escreva isso no relatório — é o que o juiz compara com o gabarito.

4. **Executar o fluxo até a revisão**, pelo contrato de `${CLAUDE_PLUGIN_ROOT}/SKILL.md`, com caminho absoluto do CLI e `--project` explícito:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" search --project <projeto> --query "<entidade ação>" --intent literal
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" preview --project <projeto> --candidate <ID> --start <s> --end <s> --narration "<fala exata>" --reason "<motivo da fonte>"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" review --project <projeto>
```

No Windows, use `python`. Sirva o Storyboard com `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" serve --project <projeto>` e entregue a URL que o comando devolver.

5. **Parar na revisão humana.** Não execute `approve`, `permit`, `fetch` nem `verify`. Não aprove nada em nome do usuário e não declare condição de uso ou licença. A parada faz parte do que está sendo medido.

6. **Respeitar as guardas editoriais.** Fonte literal primeiro; stock (Pexels/Pixabay) **somente** se o próprio roteiro ou o usuário pedir — e, quando pedir só para alguns beats, apenas nesses. Beat sem fonte literal disponível é **reportado como indisponível, com a razão real**; nunca preenchido com material aproximado. Falta de dado no roteiro (empresa, data, qual tela) é **pergunta**, não chute.

7. **Coletar o estado final:**

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" status --project <projeto>
```

8. **Preencher o relatório** a partir de `${CLAUDE_PLUGIN_ROOT}/eval/runs/TEMPLATE.md`: identificação, linha do tempo por fase, veredito por beat, fricções ranqueadas, métricas da rubrica e a separação obrigatória **ambiente vs comportamento**. Salve como `eval/runs/<AAAA-MM-DD>-<versão>-<agente>.md` e cole o resumo do `status` na seção de estado final.

## Ao devolver ao usuário

Responda em poucas linhas: quantos beats, quantos candidatos, quantas prévias, onde está o Storyboard, o que ficou sem fonte literal e por quê, e o caminho do relatório. Diga explicitamente que nada foi aprovado nem coletado e que o gabarito não foi aberto. Não pontue o próprio trabalho: a nota é do juiz.
