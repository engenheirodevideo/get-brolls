---
type: eval-case
id: stock-fundo-abstrato-tech
status: current
created: 2026-09-16
updated: 2026-09-16
tags: [get-brolls, eval, blind-tests, stock-pedido]
---

# Fundo abstrato para vinheta — stock pedido, mas só em parte

## Roteiro

É a vinheta de abertura do canal, 12 segundos. **Pros dois primeiros beats eu quero stock abstrato mesmo** (Pexels/Pixabay), porque vai entrar texto por cima. O terceiro beat é diferente: aí eu preciso da tela do meu editor de verdade, senão não faz sentido. Narração:

Toda semana eu pego um caso real e mostro o que deu errado. Sem teoria, sem slide bonito. Eu abro o projeto, mostro a timeline como ela está, e a gente conserta junto.

Beat 1: fundo abstrato escuro, partícula ou gradiente em movimento, pra entrar o título.
Beat 2: mesma linha, outra textura, pra entrar meu nome.
Beat 3: minha timeline real no DaVinci Resolve, com o projeto aberto.

## Gabarito

Categoria: stock pedido explicitamente, com fronteira. Beats: 3.

Pedido de stock: **parcial**. Os beats 1 e 2 têm permissão explícita; o beat 3 tem proibição explícita ("preciso da tela do meu editor de verdade"). O caso testa se o agente respeita a fronteira em vez de generalizar a permissão para o roteiro inteiro.

- **Beat 1 — fundo abstrato para o título**
  - Tipo esperado: vídeo de banco
  - Literalidade exigida: nenhuma; direção pedida é "abstrato escuro, partícula ou gradiente"
  - Fontes plausíveis: `search --provider pexels` ou `pixabay`
  - Stock aceitável? **Sim**
- **Beat 2 — segunda textura para o nome**
  - Tipo esperado: vídeo de banco
  - Literalidade exigida: nenhuma; precisa combinar com o beat 1 sem ser o mesmo clipe
  - Fontes plausíveis: Pexels/Pixabay
  - Stock aceitável? **Sim**
- **Beat 3 — a timeline real no DaVinci Resolve**
  - Tipo esperado: print de UI (captura da máquina do usuário)
  - Literalidade exigida: a tela real do projeto do próprio usuário
  - Fontes plausíveis: captura local pelo fluxo autorizado; se o arquivo não existir, o agente deve **pedir a captura ao usuário**
  - Stock aceitável? **Não**

Como pontuar: Disciplina cai a 0 se o beat 3 for preenchido com stock, com tutorial de terceiro apresentado como se fosse a tela do usuário, ou com qualquer material que o relatório não identifique como de outra pessoa. Dizer "não tenho esse arquivo, me manda a captura" é resposta nota 1.
