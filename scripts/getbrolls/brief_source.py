"""Leitura crua do BRIEF.md: caminho, bloco json e ids aposentados.

Módulo folha: não importa `.brief` nem nada do `.sdk`, só (tardiamente)
`.roteiro_frontmatter` para `is_roteiro` — nunca `.roteiro` direto, que importa
`sdk.loader` (via `enabled_plugins`) e reabriria o ciclo. Existe para que
`sdk.contracts` leia o brief sem reabrir o ciclo `brief <-> sdk.contracts`
(contracts importava `.brief` direto). `brief.py` reexporta tudo daqui para manter o
caminho de import de sempre.
"""

import json
import os
import re
from pathlib import Path

# O id do beat também é valor de `--shot`, então usa um alfabeto mais estreito que ele.
BEAT_ID_RE = re.compile(r"[a-z0-9-]{1,40}")

_FENCE = "```json"
_CLOSE = "\n```"
_SPACES = re.compile(r"\s*")


def json_block_spans(raw):
    """`[(início, fim)]` do miolo de cada bloco ```json de `raw`, em tempo linear.

    Mesma leitura de `re.findall(r"```json\\s*\\n(.*?)\\n```", raw, re.DOTALL)`: o miolo começa
    depois da última quebra de linha do espaço que segue o ```json e vai até o primeiro `\\n```` que
    vem depois. A regex voltava atrás a cada quebra de linha e ficava quadrática num fence sem fecho.
    """
    spans, pos = [], 0
    while (start := raw.find(_FENCE, pos)) != -1:
        after = start + len(_FENCE)
        spaces = _SPACES.match(raw, after)  # `\s*` sempre casa (até vazio)
        end_space = spaces.end() if spaces else after
        last = raw.rfind("\n", after, end_space)
        if last == -1:
            pos = start + 1
            continue
        close = raw.find(_CLOSE, last + 1)
        if close != -1:
            spans.append((last + 1, close))
            pos = close + len(_CLOSE)
            continue
        # Sem fecho adiante: só resta o fence colado na última quebra (miolo = espaço antes dela).
        before = raw.rfind("\n", after, last)
        if before != -1 and raw.startswith("```", last + 1):
            spans.append((before + 1, last))
            pos = last + len(_CLOSE)
            continue
        break  # nenhum `\n```` depois daqui: nenhum outro bloco fecha
    return spans


def read_json_block(path, missing, syntax, not_object=None):
    """Lê `path` e devolve o único bloco ```json nele, já decodificado.

    `missing` e `syntax` são as mensagens (já prontas, em português) para os
    casos de zero/mais de um bloco e de JSON malformado, respectivamente. Se
    `not_object` for informado, o resultado também precisa ser um objeto
    (dict), senão essa mensagem é levantada.
    """
    raw = Path(path).read_text(encoding="utf-8")
    blocks = [raw[begin:end] for begin, end in json_block_spans(raw)]
    if len(blocks) != 1:
        raise ValueError(missing)
    try:
        value = json.loads(blocks[0])
    except json.JSONDecodeError:
        raise ValueError(syntax) from None
    if not_object is not None and not isinstance(value, dict):
        raise ValueError(not_object)
    return value


def brief_path(project):
    """Arquivo que vale para este projeto: GB_BRIEF_FILE vence o BRIEF.md da pasta."""
    override = os.environ.get("GB_BRIEF_FILE")
    path = Path(override) if override else Path(project) / "BRIEF.md"
    # Caminho absoluto: a resposta é lida de outra pasta que não a do projeto.
    return path.expanduser().resolve()


def load_brief(project):
    """Lê o BRIEF.md do projeto e devolve o JSON cru, sem validar o conteúdo."""
    path = brief_path(project)
    if not path.exists():
        if os.environ.get("GB_BRIEF_FILE"):
            raise ValueError(
                "GB_BRIEF_FILE aponta para um arquivo que não existe: corrija o caminho "
                "ou apague essa variável para usar o BRIEF.md da pasta do trabalho."
            )
        raise ValueError(
            "Este projeto ainda não tem BRIEF.md. Rode `/get-brolls-brief` para fazer a "
            "entrevista, ou `init-brief --project ...` para criar o modelo e preencher."
        )
    return read_json_block(
        path,
        missing=(
            "O BRIEF.md precisa de exatamente um bloco ```json — apague os blocos extras "
            "ou rode `init-brief` numa pasta limpa para começar de um modelo."
        ),
        syntax=(
            "O bloco json do BRIEF.md está com erro de digitação (vírgula ou aspas "
            "sobrando). Conserte essa linha e rode `brief --validate` de novo."
        ),
    )


def retired_beat_ids(project):
    """Ids dos beats aposentados (`"retired": true`) lidos do bloco json cru do BRIEF.md.

    Não depende da validação completa nem do RULES.md: um brief que a postura de
    direitos deixa inválido continua com os aposentados fora de `entrega/`, da busca e
    do `resolve`. Vazio quando o brief falta ou o json não é legível; id fora do
    formato de beat não conta. `deliver` e `status` usam isto para deixar esses clipes
    fora de `entrega/` sem tratá-los como pendência. Sem ROTEIRO.md do get-brolls
    (`type: roteiro`), nada é aposentado: vazio.
    """
    from .roteiro_frontmatter import is_roteiro

    if project is None or not is_roteiro(project):
        return frozenset()
    try:
        raw = load_brief(project)
    except (ValueError, OSError):
        return frozenset()
    beats = raw.get("beats") if isinstance(raw, dict) else None
    if not isinstance(beats, list):
        return frozenset()
    return frozenset(
        b["id"]
        for b in beats
        if isinstance(b, dict)
        and b.get("retired") is True
        and isinstance(b.get("id"), str)
        and BEAT_ID_RE.fullmatch(b["id"])
    )
