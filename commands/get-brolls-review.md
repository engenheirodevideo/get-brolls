---
name: get-brolls-review
description: Monta o Storyboard local, entrega a URL para a pessoa decidir e importa as decisões salvas.
---

# Mandar as prévias para revisão

Use quando os candidatos já têm prévia e alguém precisa aprovar. Vale principalmente quando quem revisa **não é** a pessoa que está no chat.

1. Gere o Storyboard com as prévias já existentes:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" review --project <projeto>
```

2. Suba a página em segundo plano, para não travar a conversa:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" serve --background --project <projeto>
```

3. Entregue ao usuário a URL que o comando devolveu (por padrão `http://127.0.0.1:8767/review.html`) e diga, em uma linha, o que ele faz lá: olhar cada trecho, clicar em **Aprovar**, **Pedir ajuste** ou **Reprovar**, e clicar em **Salvar decisões** no fim. Avise que abrir o arquivo direto do disco, sem esta URL, pode desligar o salvamento. Então **pare e espere** a pessoa dizer que salvou.

4. Quando ela avisar, importe:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" import-review --by "NOME DA PESSOA" --project <projeto>
```

Sem `--file`, ele pega sozinho o arquivo mais recente de `brolls/reviews/`. `--by` é o nome real de quem decidiu — nunca o seu, nunca "usuário".

5. A resposta traz o que foi aplicado e `skipped[]` com o motivo de cada item pulado (`stale_epoch` = o intervalo mudou depois da decisão; `signature_mismatch` = o trecho não é mais o mesmo). Repasse os pulados ao usuário e regenere a prévia dos que precisarem.

6. Pare o servidor quando terminar:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" serve --stop --project <projeto>
```

Se a pessoa que decide está no próprio chat, você não precisa do board: descreva os contact sheets, pergunte "aprova todos, ou quais?" e registre os IDs que você mostrou com `approve --candidate ID1 --candidate ID2 … --by NOME --channel chat --statement "frase exata"` (`--all` só quando mostrou todos). Silêncio nunca é aprovação.
