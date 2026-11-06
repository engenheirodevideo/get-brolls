"""Marca de gravação de cada ação da CLI (§2 do contrato): o que ela pode gravar e com que ok.

`L` só lê; `0` grava só em `$GB_HOME/private`; `G` grava sem ok (comando explícito, área do
getbrolls, nunca sobrescreve); `OK` pede prévia e `--yes --expect`; `OK1` pede o ok só na
condição que o contrato diz; `P` é portão humano (`--by --expect --channel --statement`).
Uma marca por ação: a chave é `(comando, ação)`, com `None` para o comando sem `--action` e a
flag de modo para o `setup` (`--check`, `--where`). A tabela é o contrato; o teste confere que
ela cobre a superfície do parser, nem mais nem menos.
"""

import argparse

LEVELS = ("L", "0", "G", "OK", "OK1", "P")
# Flags de modo: cada uma é uma ação com a própria marca (`setup --check|--where` × `setup`).
# `--dry-run` é `L` no sentido do contrato: não grava dado nem estado. A trava, o `getbrolls.log` e o
# `diagnostics.jsonl` do projeto continuam sendo escritos, como em qualquer comando que não está em
# `command_policy.READ_ONLY_*`. `serve --background|--stop` grava `brolls/.serve.pid` e `.serve.log`.
MODE_FLAGS = {
    "setup": ("--check", "--where"),
    "serve": ("--background", "--stop"),
    "deliver": ("--dry-run",),
    "search": ("--dry-run",),
    "export": ("--dry-run",),
}

MARKS: dict[tuple[str, str | None], str] = {
    ("analysis", "check"): "L",
    ("analysis", "list"): "L",
    ("analysis", "register"): "G",
    ("approve", None): "P",
    ("assets", "list"): "L",
    ("assets", "where"): "L",
    ("brief", None): "L",
    # O PRD §2 K lista o `browser-plan` como só leitura; a marca segue o comportamento real: ele toma a trava
    # e grava `brolls/` (`.command.lock`, `manifest.json`, `events.jsonl`, `getbrolls.log`, `candidates/`,
    # `previews/`, `clips/`) e `output/playwright/`. Por isso também não está em `command_policy.READ_ONLY_COMMANDS`.
    ("browser-plan", None): "G",
    ("capabilities", None): "L",
    ("client", "add"): "OK1",
    ("client", "list"): "L",
    ("client", "remove"): "G",
    ("client", "show"): "L",
    ("deliver", None): "G",
    ("deliver", "--dry-run"): "L",
    ("doctor", None): "L",
    ("environment", "list"): "L",
    ("export", None): "G",
    ("export", "--dry-run"): "L",
    ("fetch", None): "G",
    ("import-review", None): "P",
    ("init", None): "G",
    ("init-brief", None): "G",
    ("init-rules", None): "G",
    ("inspect", None): "G",
    ("learn", None): "G",
    ("library", None): "L",
    ("migrate", "apply"): "OK",
    ("migrate", "plan"): "L",
    ("permit", None): "P",
    # Executa o `register()` do plugin (código de terceiros, sem sandbox): o core não prova que é só leitura.
    ("plugins", "check"): "G",
    ("plugins", "disable"): "G",
    ("plugins", "enable"): "OK",
    ("plugins", "install"): "OK",
    ("plugins", "list"): "L",
    ("plugins", "marketplace-add"): "G",
    ("plugins", "marketplace-list"): "L",
    ("plugins", "marketplace-remove"): "G",
    ("plugins", "marketplace-update"): "G",
    ("plugins", "new"): "G",
    ("plugins", "remove"): "OK",
    ("plugins", "search"): "L",
    ("plugins", "update"): "OK",
    ("preview", None): "G",
    ("profile", "show"): "L",
    ("profile", "trust"): "OK",
    ("profile", "untrust"): "G",
    ("providers", None): "L",
    ("queue", "add"): "G",
    ("queue", "mark"): "G",
    ("queue", "next"): "G",
    # Portão humano (`--by --phrase --expect`): tira `stopped` de `work/queue.json` e grava quem e com que frase.
    ("queue", "resume"): "P",
    ("queue", "status"): "L",
    ("recipe", "list"): "L",
    ("recipe", "show"): "L",
    ("references", None): "L",
    ("reject", None): "G",
    ("remember", None): "G",
    ("resolve", None): "G",
    ("review", None): "G",
    ("roteiro", "check"): "L",
    ("roteiro", "new"): "G",
    ("roteiro", "plan"): "L",
    ("roteiro", "review"): "P",
    ("roteiro", "sync"): "OK1",
    ("rules", None): "L",
    ("search", None): "G",
    ("search", "--dry-run"): "L",
    # `serve` em primeiro plano só serve arquivos (as decisões que a pessoa salva na página vão para
    # `reviews/` e só entram no projeto pelo `import-review`, que é `P`); o modo em segundo plano e o
    # `--stop` gravam e apagam `brolls/.serve.pid` e `.serve.log`.
    ("serve", None): "L",
    ("serve", "--background"): "G",
    ("serve", "--stop"): "G",
    ("setup", None): "OK",
    ("setup", "--check"): "L",
    ("setup", "--where"): "L",
    ("status", None): "L",
    ("template", "freeze"): "L",
    ("template", "list"): "L",
    ("template", "show"): "L",
    ("verify", None): "G",
    ("x", None): "G",
}


def actions(parser: argparse.ArgumentParser) -> set[tuple[str, str | None]]:
    """`(comando, ação)` de cada ação da superfície: as escolhas de `--action`, os modos e o comando puro."""
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))  # pylint: disable=protected-access
    found: set[tuple[str, str | None]] = set()
    for name, subparser in sub.choices.items():
        choices = next(
            (a.choices for a in subparser._actions if "--action" in a.option_strings),  # pylint: disable=protected-access
            None,
        )
        if choices:
            found.update((name, str(choice)) for choice in choices)
        else:
            found.add((name, None))
        found.update((name, flag) for flag in MODE_FLAGS.get(name, ()))
    return found
