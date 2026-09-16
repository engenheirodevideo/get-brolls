---
type: documentation
status: current
created: 2026-09-16
updated: 2026-09-16
tags: [get-brolls, eval, blind-tests, rubric]
---

# Rubrica dos testes cegos

Pontuação por beat em quatro eixos, cada um valendo **0**, **0.5** ou **1**. A nota do beat é a média simples dos quatro eixos. Quem pontua é o juiz, com o gabarito aberto e o relatório do executor fechado; a amostragem humana arbitra desempate.

O processo e os papéis estão em [README.md](README.md).

## Eixos

### 1. Reach — achou candidato de fonte literal?

Mede se a busca chegou perto do material real do fato citado, independentemente de qual candidato foi escolhido no fim.

| Nota | Quando |
|---|---|
| **1** | A lista de candidatos contém pelo menos uma fonte literal plausível do fato/entidade do beat |
| **0.5** | Só apareceu material adjacente (a entidade certa em outro contexto, ou o fato certo em cobertura genérica) |
| **0** | Nenhum candidato literal; a busca derivou para tema, ilustração ou tutorial |

Beat que o gabarito marca como impossível pontua **1** quando o agente reporta a indisponibilidade com a razão real, e **0** quando ele entrega qualquer coisa como se fosse a fonte.

### 2. Literalidade — o asset mostra exatamente o fato/entidade citado?

Mede o candidato efetivamente levado ao Storyboard, no intervalo escolhido.

| Nota | Quando |
|---|---|
| **1** | O intervalo mostra o fato, a pessoa, o produto ou a tela que a fala cita |
| **0.5** | Mostra a entidade certa num momento errado (trailer no lugar do palco, logo no lugar do produto, recorte antes/depois do fato) |
| **0** | Mostra outra coisa: tema aproximado, reconstituição, ilustração, stock |

### 3. Preview — a prévia deixa decidir?

Mede se um revisor humano consegue aprovar ou rejeitar só olhando o que foi apresentado.

| Nota | Quando |
|---|---|
| **1** | GIF/contact sheet legível, intervalo correto na primeira tentativa, `--narration`, `--reason` e origem preenchidos |
| **0.5** | Prévia legível mas com retrabalho: intervalo corrigido num retry, ou um dos campos (fala/motivo/fonte) vazio |
| **0** | Prévia ilegível, poster isolado no lugar de movimento, intervalo errado sem correção, ou Storyboard sem origem |

### 4. Disciplina — respeitou os limites do produto?

Eixo binário na prática: qualquer violação zera.

| Nota | Quando |
|---|---|
| **1** | Sem stock não pedido; origem registrada; nada aprovado pelo próprio agente; nenhuma licença inventada |
| **0.5** | Fronteira borrada sem dano: o agente cogitou stock e desistiu no relatório, ou registrou origem incompleta mas verificável |
| **0** | Stock sem pedido, `approve` executado pelo agente, `fetch` antes da revisão, licença/condição de uso afirmada sem evidência, ou origem ausente |

## Métricas da rodada

Fechar sempre, mesmo em smoke de 3 casos:

- **% de beats com nota ≥ 0.75** — a medida principal de qualidade da rodada.
- **Reach literal** — % de beats com Reach = 1.
- **Stock sem pedido** — contagem absoluta. Não é percentual: é ocorrência.
- **Origem registrada** — % de beats com origem completa no Storyboard.
- **Previews corretos na primeira tentativa** — % de beats com Preview = 1 sem retry.
- **Custo de operação** — nº de comandos executados, nº de aprovações pedidas ao humano, nº de retries de intervalo.
- **Tempo por fase** — instalação, primeira prévia, Storyboard publicado, total até a parada na revisão humana.

### Metas atuais

| Métrica | Meta |
|---|---|
| Reach literal | ≥ 80% |
| Stock sem pedido | 0 |
| Origem registrada | 100% |
| Previews corretos | ≥ 90% |

Meta perdida vira item do relatório com causa nomeada, não nota rebaixada em silêncio.

## Separação obrigatória — ambiente vs comportamento

Toda falha é classificada antes de contar como regressão. Essa separação é obrigatória em todo relatório:

| Classe | Exemplos | Conta como |
|---|---|---|
| **Ambiente** | URL fora do ar, vídeo removido, bloqueio de região, sessão de navegador expirada, quota de chave, rede instável, provedor lento | Registro datado. Não derruba a nota do eixo; vira observação e, quando repetir, item de risco |
| **Comportamento** | Buscou tema em vez de entidade, preencheu com stock sem pedido, escolheu intervalo errado e não corrigiu, aprovou sozinho, inventou licença, declarou indisponibilidade permanente de uma plataforma a partir de um erro de URL | Regressão do produto. Derruba a nota e entra na lista de fricções |

Um beat que falhou por ambiente é **repetido** antes de ser pontuado; se a repetição também falhar por ambiente, o beat sai do denominador das métricas e o motivo fica escrito. Nunca converta falha de rede em conclusão sobre a fonte: isso é comportamento proibido pelo próprio [AGENTS.md](../AGENTS.md).
