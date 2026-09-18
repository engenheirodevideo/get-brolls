"""Wrapper único para invocar `scripts/gb.py` como subprocesso nos testes.

Reúne o padrão hoje reimplementado em `test_brief.py`, `test_approve_chat.py`,
`test_env_paths.py`, `test_init_rules_flags.py`, `test_library.py` e
`test_permit_declaration.py`: monta `[sys.executable, CLI, *args]`, roda com
`capture_output=True, text=True, encoding="utf-8"`, confere o código de saída
e devolve o JSON — de `stdout` quando o código esperado é 0 (sucesso), de
`stderr` para qualquer outro código (erro tratado pela CLI, que imprime JSON
de erro lá).

`env` funde variáveis no ambiente herdado, como em `test_env_paths.py` e
`test_library.py`. `project` é conveniência: quando informado, acrescenta
`--project <project>` ao fim dos argumentos, para quem não quer repetir o par
em toda chamada.
"""

import json
import os
import subprocess
import sys

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import CLI


def run_cli(*args, project=None, expect=0, env=None):
    command_args = list(args)
    if project is not None:
        command_args += ["--project", str(project)]

    environment = None
    if env is not None:
        environment = dict(os.environ)
        environment.update(env)

    done = subprocess.run(
        [sys.executable, str(CLI), *map(str, command_args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    assert done.returncode == expect, done.stderr or done.stdout
    return json.loads(done.stdout if expect == 0 else done.stderr)
