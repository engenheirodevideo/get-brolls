"""`template`: o comando da versão de testes da 2.6 para as receitas, que agora são `recipe`.

Toda ação recusa (sem alias, sem rota dupla). `list` e `show` apontam para o `recipe` que existe;
`freeze` não tem substituto nesta versão: a selagem da receita ainda não está na CLI. Os modelos de
etapa (briefing, roteiro, storyboard…) chegam a este comando depois.
"""

from typing import NoReturn

from . import renamed

_FREEZE_TAIL = (
    " A receita é selada por um passo que esta versão da CLI ainda não tem; hoje existem "
    "`recipe --action list` e `recipe --action show --ref RECEITA` (RECEITA no formato dono.slug@N)."
)


def run(args) -> NoReturn:
    """`template --action freeze|list|show`: recusa; `list` e `show` com o `recipe` que os substitui."""
    action = args.action
    old = f"template --action {action}"
    if action == "freeze":
        raise renamed.unavailable(old, reason="template_freeze", tail=_FREEZE_TAIL)
    if action == "list":
        raise renamed.usage(old, "recipe --action list", reason="template_list")
    raise renamed.usage(
        old,
        "recipe --action show --ref RECEITA",
        reason="template_show",
        tail=" RECEITA no formato dono.slug@N, como em cliente-exemplo.reels-split@1.",
    )
