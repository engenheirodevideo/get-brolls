---
type: eval-run
status: template
created: 2026-09-16
updated: 2026-09-16
tags: [get-brolls, eval, blind-tests, template]
---

# Rodada cega — <versão> — <agente/modelo> — <AAAA-MM-DD>

> Copie este arquivo para `eval/runs/AAAA-MM-DD-<versão>-<agente>.md`, troque `status: template` por `status: current` e apague as instruções entre colchetes angulares.

## Identificação

| Campo | Valor |
|---|---|
| Versão testada | <2.3.x + commit/branch> |
| Agente/modelo executor | <nome do agente e modelo> |
| Juiz | <quem pontuou> |
| Amostragem humana | <quem conferiu e quantos beats> |
| Data | <AAAA-MM-DD> |
| Casos | <ids do corpus ou "roteiro avulso"> |
| Escopo | <rodada completa (14) | smoke (3) | fora de ciclo> |
| Sistema | <SO, Python, forma de instalação> |

## Linha do tempo por fase

| Fase | Marco | Tempo |
|---|---|---|
| Instalação | <clone/instalador concluído> | <mm:ss> |
| Preparação | <projeto criado, regras lidas> | <mm:ss> |
| Primeira prévia | <primeiro GIF/contact sheet legível> | <T+mm:ss> |
| Storyboard | <review.html publicado e servido> | <T+mm:ss> |
| Parada | <entrega ao humano> | <T+mm:ss> |
| **Total** | <até a parada na revisão humana> | <mm:ss> |

## Veredito por beat

Legenda: ⭐ literal e correto · 🟡 parcial · ⚠️ corrigido depois de retry · ❌ errado ou ausente.

| # | Beat (fala) | Tipo entregue | Fonte | Intervalo | Veredito | Observação |
|---|---|---|---|---|---|---|
| 1 | <fala do roteiro> | <vídeo/imagem/print> | <origem real> | <start–end> | <⭐/🟡/⚠️/❌> | <o que aconteceu> |

## Métricas da rubrica

| Métrica | Meta | Resultado |
|---|---|---|
| Beats com nota ≥ 0.75 | — | <n/N — %> |
| Reach literal | ≥ 80% | <%> |
| Stock sem pedido | 0 | <n> |
| Origem registrada | 100% | <%> |
| Previews corretos na 1ª tentativa | ≥ 90% | <n/N> |

Custo de operação: <nº de comandos> comandos, <nº> aprovações pedidas ao humano, <nº> retries de intervalo.

Notas por eixo (Reach / Literalidade / Preview / Disciplina): <médias da rodada>.

## Fricções ranqueadas

1. **<fricção mais cara>** — <o que aconteceu, quanto custou, o que resolveria>
2. **<segunda>** — <…>
3. **<terceira>** — <…>

## Ambiente vs comportamento

| Ocorrência | Classe | Consequência |
|---|---|---|
| <o que falhou> | <ambiente/comportamento> | <registro datado ou regressão do produto> |

<Escreva aqui, em uma frase, o que a rodada prova e o que ela não prova. Falha de rede não é conclusão sobre a fonte; ensaio ao vivo é registro datado, não promessa futura nem aprovação editorial.>

## Estado final do projeto

```
<saída de: python3 scripts/gb.py status --project <projeto>>
```

<Confirme explicitamente: nada aprovado pelo agente, nenhum corte final coletado, `credits.md` no estado esperado.>
