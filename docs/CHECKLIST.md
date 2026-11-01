---
type: documentation
status: current
created: 2026-09-27
updated: 2026-09-27
tags: [get-brolls, checklist, quality, process]
---

# Checklist de mudança

Siga este checklist na ordem, a cada mudança no getbrolls: correção, recurso, doc ou dependência. Marque cada item. Um item que não vale para a mudança fica marcado com "n/a" e o motivo. Quando o checklist deixar passar um erro, acrescente aqui o item que teria pego esse erro.

Referências: [CODE_STYLE](CODE_STYLE.md) (estilo e revisão), [CONTRIBUTING](../CONTRIBUTING.md) (ferramentas, dependências e release), [QUALITY](QUALITY.md) (o que conta como evidência) e [SECURITY](SECURITY.md).

## 1. Antes de começar: discovery → spec → plano → TDD
Uma feature é resolvida inteira, sempre nesta ordem:
- [ ] Trabalhe numa branch própria a partir da branch-alvo combinada, nunca direto na `main`.
- [ ] Rode `git status`: a árvore precisa estar limpa, e a branch sincronizada com o remoto.
- [ ] **Discovery**: leia o código e a doc que a feature toca. Liste o que ela toca (CLI, JSON de saída, arquivos do projeto, contrato do plano de export, API do SDK, docs), o que já existe para reaproveitar e as restrições.
- [ ] **Spec**: escreva o que a feature faz, para quem, o contrato (comandos, flags, JSON, arquivos), os casos de borda e o que fica fora. Passe por crítica adversarial e ajuste.
- [ ] **Plano**:
  - Quebre a feature em tarefas **completas**, cada uma do tamanho que um implementador resolve numa sessão, sem passos miúdos que gastam contexto à toa.
  - Cada tarefa traz o arquivo e a linha exatos a mudar, o código ou a assinatura esperados, os testes a escrever e o comando que prova que ficou pronta. Quem implementa só escreve, sem refazer a descoberta.
  - Marque a ordem e as dependências entre as tarefas.
- [ ] **TDD** em cada tarefa: primeiro o teste que falha, depois o código, e aí o portão da seção 3.

## 2. Enquanto escreve
- [ ] Siga o plano: se a tarefa pedir mudança fora do que o plano previu, pare e corrija o plano primeiro, para não haver retrabalho.
- [ ] TDD: primeiro o teste que falha (vermelho), depois o código que o faz passar (verde). Bug corrigido ganha teste de regressão.
- [ ] O código segue o [CODE_STYLE](CODE_STYLE.md): stdlib, 120 colunas, docstring de uma linha em pt-BR e supressão só na linha, com a regra e o motivo.
- [ ] Mensagens para a pessoa em pt-BR, dizendo o que aconteceu e o próximo passo, sem caminho absoluto da máquina, token ou texto de exceção em inglês.
- [ ] API pública congelada: nomes e assinaturas do SDK, flags da CLI, campos JSON e nomes de arquivo gerados continuam iguais. Mudança só aditiva.
- [ ] O plano de export segue a política de evolução do [SDK](SDK.md#evolução-do-plano-de-export).
- [ ] Aprovação continua sendo da pessoa: o agente nunca aprova, autoriza nem publica por ela.
- [ ] Portabilidade:
  - `encoding="utf-8"` em toda leitura e escrita de texto;
  - LF ao gravar;
  - caminhos com `pathlib`, sem supor `/`;
  - arquivo somente leitura tratado no Windows;
  - `os.name` e `sys.platform` testados nos dois ramos.
- [ ] Processo externo só com lista de argv, sem `shell=True`, com timeout quando pode travar.
- [ ] Nada de material interno no repositório: ids de revisão, nome de onda ou rodada, caminhos de máquina, planejamento.

## 3. Portões locais (todos verdes, nesta ordem)
- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] `pyright`
- [ ] `pylint scripts examples`: 0 mensagens.
- [ ] `PYTHONPATH=scripts:tests pylint --rcfile tests/pylintrc tests`: 0 mensagens.
- [ ] `python3 scripts/gen_skill_mirror.py --check`
- [ ] `python3 scripts/check_anchors.py`
- [ ] `python3 -m unittest discover -s tests`: anote o total de testes.
- [ ] Os mesmos portões também em Python 3.11, que é a versão em que o CI faz o lint (um `.venv` em 3.11 reproduz o CI). `bash scripts/check.sh` roda os itens acima em sequência.
- [ ] Sem regressão: projetos que não usam a novidade continuam com a mesma saída da CLI, os mesmos arquivos e os mesmos logs. Compare antes e depois.

## 4. Docs e versão
- [ ] Atualize GUIDE, MANUAL, SDK e `references/` junto com o código afetado.
- [ ] `SKILL.md` com no máximo 900 palavras e o espelho regenerado (`python3 scripts/gen_skill_mirror.py`), nunca editado à mão.
- [ ] Linha no CHANGELOG para quem usa: o que muda, os limites e se exige migração.
- [ ] Os comandos citados na doc conferem com o `--help` real.
- [ ] Release: `python3 scripts/bump_version.py X.Y.Z --date AAAA-MM-DD`, depois `--check`, depois `bash scripts/preflight.sh --version X.Y.Z` com `PREFLIGHT OK`.

## 5. Segurança
- [ ] `gitleaks git --log-opts="<base>..HEAD"` e `gitleaks dir .`: só valores falsos de teste.
- [ ] A mudança toca caminhos, arquivos, rede, subprocesso, plugin ou dados de terceiros? Faça uma revisão de segurança dedicada e corrija o que ela achar antes de seguir.

## 6. Revisão
- [ ] Code review independente contra o checklist do [CODE_STYLE](CODE_STYLE.md#checklist-de-revisão).
- [ ] QA adversarial: teste o comportamento de verdade, casos de borda e entrada hostil.
- [ ] Achados: uma rodada de correção e revisão focada. Só siga com aprovação.

## 7. Commit e envio
- [ ] Commits pequenos, com mensagem convencional em inglês, sem id interno nem caminho de máquina.
- [ ] Push só com o ok do mantenedor, só da branch, nunca direto na `main`.
- [ ] CI verde nos 3 sistemas (macOS, Windows e Ubuntu) e no job `quality`. Leia o log de qualquer falha antes de corrigir.
- [ ] Merge, tag e release são decisões separadas do mantenedor.

## 8. Depois
- [ ] Registre o que deu errado e que o checklist não pegou.
- [ ] Acrescente o item que faltou a este arquivo.
