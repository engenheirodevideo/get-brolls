"""Nomes que saíram na 2.6.0: cada um recusa com o caminho novo, sem alias nem rota dupla.

O que a versão de testes da 2.6 chamava de outro jeito (comando, ação, flag, chave ou pasta) nunca
funciona pelo nome antigo: a mensagem diz o nome novo e, para o que está em disco, o passo manual que
converte (o `migrate` só adota o layout 1; ele não move pastas). Um comando novo que esta versão ainda
não tem nunca é citado como se existisse. O evento `renamed_refused` leva só o motivo, nunca o que a
pessoa digitou.
"""

from typing import NamedTuple

from . import _paths, logs
from .errors import UsageError

_log = logs.get("renamed")


class Rename(NamedTuple):
    """Um nome que saiu: o antigo, o novo, o motivo do log e (no parse) a sugestão do argparse.

    É o que o gancho `GbArgumentParser.renamed` de um subcomando devolve; registrar outro nome
    que saiu é só devolver mais um `Rename` dele.
    """

    old: str
    new: str
    reason: str
    tail: str = ""
    suggestion: str | None = None


def init_template() -> Rename:
    """`init --template` (a flag da versão de testes) vira `init --recipe`: o mesmo `Rename` no parse e na execução."""
    return Rename(
        "init --template",
        "init --recipe RECEITA",
        "init_template",
        tail=(
            " Um template da versão de testes não vira receita sozinho: veja as receitas do cliente com "
            "`recipe --action list` e crie o projeto com `init --recipe RECEITA`."
        ),
        suggestion="--recipe",
    )


def message(old: str, new: str, tail: str = "") -> str:
    """ "`<old>` saiu: agora é `<new>`.<tail>": a mensagem, igual para quem recusa no parse e na execução."""
    return f"`{old}` saiu: agora é `{new}`.{tail}"


def unavailable_message(old: str, tail: str) -> str:
    """ "`<old>` saiu e não tem substituto nesta versão.<tail>"."""
    return f"`{old}` saiu e não tem substituto nesta versão.{tail}"


def message_en(old: str, new: str) -> str:
    """A mesma recusa em inglês, para o `error_en` das ferramentas."""
    return f"`{old}` was renamed: it is now `{new}`."


def usage(old: str, new: str, *, reason: str, tail: str = "") -> UsageError:
    """`UsageError` (saída 2) "`<old>` saiu: agora é `<new>`.<tail>", já registrada em `renamed_refused`."""
    return logs.refusal(_log, "renamed_refused", UsageError(message(old, new, tail)), reason=reason)


def unavailable(old: str, *, reason: str, tail: str) -> UsageError:
    """`UsageError` (saída 2) de um nome que saiu sem substituto nesta versão, registrada em `renamed_refused`."""
    return logs.refusal(_log, "renamed_refused", UsageError(unavailable_message(old, tail)), reason=reason)


def refuse(found: Rename) -> UsageError:
    """`usage` de um `Rename`, já registrada em `renamed_refused`."""
    return usage(found.old, found.new, reason=found.reason, tail=found.tail)


def refuse_at_parse(found: Rename) -> UsageError:
    """`refuse` de antes do log abrir (o parse): como o `command_refused` do `.env`, só o espelho do stderr.

    Recusa nenhuma abre o log nem cria `$GB_HOME/logs/`; a linha só aparece com `GB_LOG_STDERR=1`.
    """
    logs.configure(None, read_only=True)
    try:
        return refuse(found)
    finally:
        logs.shutdown()


# (`found`, `where`) já avisados neste processo: um comando dá o aviso uma vez por forma antiga e por
# projeto ou cliente, não a cada componente.
_SEEN: set[tuple[str, str]] = set()


def adopt_hint(*scope: str) -> str:
    """A frase que manda adotar o layout 1: "Para adotar o layout 1 sem mover nada: <migrate --action plan …>."."""
    return "Para adotar o layout 1 sem mover nada: " + _paths.cli_hint("migrate", "--action", "plan", *scope) + "."


def old_shape(found: str, fix: str, *, where: str = "", text: str | None = None) -> None:
    """Aviso `OLD_SHAPE`, uma vez por processo, por `found` e por `where` (a pasta do projeto ou do cliente).

    Só conta como dado o aviso que chegou a um comando em andamento: sem evento ativo nada é
    gravado e o próximo chamado ainda avisa. `text` troca o texto inteiro do aviso (o de um arquivo que
    segue sendo lido, só com uma chave sobrando).
    """
    from . import runtime  # tardio: o runtime traz logs e http

    key = (found, where)
    if key in _SEEN or runtime.ACTIVE.get() is None:
        return
    runtime.record_warning(
        "OLD_SHAPE", text or f"{found} é da forma antiga (versão de testes da 2.6) e não é lido. {fix}"
    )
    _SEEN.add(key)


def reset_old_shape_warnings() -> None:
    """Esquece os avisos `OLD_SHAPE` já dados (testes no mesmo processo)."""
    _SEEN.clear()
