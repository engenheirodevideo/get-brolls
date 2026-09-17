---
type: reference
status: current
created: 2026-09-17
updated: 2026-09-17
tags: [get-brolls, copy, templates]
---

# Templates de resposta

Modelos prontos para o agente falar com a pessoa. Adapte os dados entre `<>`; não mude o
tom nem invente URLs, arquivos ou condições de uso que você não tem. Os termos usados aqui
seguem o [glossário](glossario.md).

---

## 1. Entrega do Storyboard

> **Sua página de escolhas está pronta:** <URL>
>
> Clique no link (ou cole no navegador). Você vai ver <N> trechos; em cada um, diz
> **Aprovar**, **Pedir ajuste** ou **Reprovar**. Dentro de "Pedir ajuste" tem uma
> caixinha pra dizer "não é esse vídeo, procure outro".
>
> Quando terminar, tem dois jeitos de me devolver isso — escolha um:
> • **Na página:** clique em **"Salvar decisões"** no fim da lista. Ela baixa um arquivo;
>   volte aqui e me diga onde salvou.
> • **Aqui na conversa:** se você aprovou tudo, é só me dizer **"aprovei todos"** que eu
>   registro por aqui, sem arquivo nenhum.
>
> Enquanto você estiver na página, eu fico parado esperando. Me avisa que eu continuo.

Só ofereça a rota do chat quando a pessoa for aprovar de fato: aprovação nunca é inferida
de silêncio. Se ela disser "aprovei todos", registre com
`approve --all --by "<NOME>" --channel chat --statement "<frase exata dela>"`.

---

## 2. Direitos de uso (o momento do `permit`)

> Antes de baixar, preciso de uma coisa sua. Eu sei **de onde** cada trecho veio, mas eu
> **não sei se você pode publicar** — isso muda por canal, por país e por tipo de vídeo, e
> quem responde por isso é quem publica.
>
> Abra o link do original (tá no card) e olhe a licença ou a descrição. Depois me diga,
> com suas palavras, o que você encontrou. Exemplos do que serve:
> • "Canal oficial da NASA, domínio público"
> • "Licença Creative Commons BY no próprio vídeo"
> • "Falei com o autor no direct e ele autorizou"
> • "É citação jornalística curta e eu vou creditar"
>
> Eu guardo isso junto do arquivo, num arquivo de créditos. Se você não tiver certeza de
> algum, me diga "esse eu pulo" — eu não baixo.

Nunca preencha a evidência por conta própria, nem resuma a frase da pessoa: grave o texto
literal dela.

---

## 3. "Onde estão meus arquivos?"

> 🎬 **Prontos: <N> arquivos.**
>
> `<caminho da pasta>/brolls/clips/`
>
> | Arquivo | O que é | Duração |
> |---|---|---|
> | `<arquivo>.mp4` | <descrição curta> | <N>s |
>
> É só arrastar pro seu editor. Os arquivos já vêm cortados no trecho que você aprovou.
>
> Na mesma pasta tem um **`credits.md`**: de onde saiu cada um, quem é o autor e o que
> você me disse sobre poder usar. Guarde junto do projeto — é sua prova se alguém
> perguntar.
>
> Quer mais trechos? Me chama de novo e diz "abre a pasta <nome>" que eu continuo de onde
> paramos.

---

## 4. Fonte indisponível

> <Fonte> não me deixou pegar esse trecho agora: <motivo em linguagem comum — o site pediu
> uma pausa / o vídeo saiu do ar / é um vídeo que só dá pra ver, não pra baixar>.
>
> Não é erro seu e não estraga nada do que já foi feito. Duas saídas:
> • eu procuro outro vídeo que mostre a mesma coisa (me diz e eu vou), ou
> • você me manda um link que você já tenha.
>
> Os <N> trechos que já peguei continuam aqui — pode ir revisando enquanto isso.

Quando for pausa obrigatória do site, diga o prazo concreto e o que fazer no intervalo.
Nunca insista na mesma fonte durante a pausa.

---

## 5. Status em 5 linhas

Repasse o que o `status` devolve, sem parafrasear o passo seguinte:

> **Pasta:** <nome da pasta do trabalho>
> **Trechos:** <N> encontrados · <N> com prévia pronta · <N> aprovados · <N> baixados
> **Agora:** <summary.do.for_human>
> **Esperando você:** <o que só você pode fazer — ou "nada, pode deixar comigo">
> **Se travar:** me diga "status" que eu releio tudo e te falo de novo.
