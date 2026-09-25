"""Ids de cena: alocação durável, nunca reaproveitada, e readoção de cena que perdeu o comentário.

O próximo número vem do maior entre `brolls/roteiro-state.json` e todo `cNN(-x)`
já visto no roteiro, nos beats do BRIEF.md e no `shot` dos candidatos do
manifesto — um id que já apontou para material nunca volta para outra cena. O
estado também guarda a assinatura (título, layout, argumentos) de cada cena, para
readotar uma cena cujo comentário `<!-- cNN -->` sumiu; na dúvida, recusa.
"""

import json
from pathlib import Path

from .ledger import atomic_write
from .roteiro import SCENE_BEAT_RE

STATE_FILE = "roteiro-state.json"
MAX_SCENE = 999
_CEILING = f"O roteiro passou de c{MAX_SCENE}: divida o vídeo em dois projetos."
_BAD_STATE = (
    f"brolls/{STATE_FILE} ilegível: restaure a cópia ou apague o arquivo "
    "(os ids em uso são recalculados do roteiro, do BRIEF.md e do manifesto)."
)


def state_path(project):
    return Path(project).expanduser().resolve() / "brolls" / STATE_FILE


def read_state(project):
    """`{"next_id": N, "scenes": {id: assinatura}}`; arquivo ausente = estado vazio. Só lê."""
    path = state_path(project)
    if not path.is_file():
        return {"next_id": 1, "scenes": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(_BAD_STATE) from None
    if (
        not isinstance(data, dict)
        or type(data.get("next_id")) is not int
        or data["next_id"] < 1
        or not isinstance(data.get("scenes", {}), dict)
    ):
        raise ValueError(_BAD_STATE)
    return {"next_id": data["next_id"], "scenes": dict(data.get("scenes") or {})}


def write_state(project, state):
    path = state_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def signature(scene):
    """O que identifica uma cena sem o id: título, layout e argumentos do layout, exatos."""
    return {"title": scene.title, "layout": scene.layout.kind, "args": list(scene.layout.args)}


def _base(identifier):
    return identifier.split("-")[0]


def _scene_ids(values):
    return [v for v in values if isinstance(v, str) and SCENE_BEAT_RE.fullmatch(v)]


def highest_seen(doc, beats, items, state):
    """Maior número de cena já usado em qualquer lugar: roteiro, beats, manifesto ou estado."""
    seen = _scene_ids(
        [s.scene_id for s in doc.scenes]
        + [b.get("id") for b in beats]
        + [c.get("shot") for c in items]
        + list(state["scenes"])
    )
    return max((int(_base(i)[1:]) for i in seen), default=0)


def _readopt(missing, idless, state):
    """{linha: id} das cenas sem id que casam, sem ambiguidade, com uma cena que sumiu.

    `missing` são todos os ids do estado ausentes do roteiro, tenham ou não candidatos:
    readota só quando a assinatura é única nos dois sentidos (um id ↔ uma cena).
    """
    readopted = {}
    for identifier in missing:
        wanted = state["scenes"].get(identifier)
        if wanted is None:
            continue
        matches = [s for s in idless if signature(s) == wanted]
        rivals = [other for other in missing if other != identifier and state["scenes"].get(other) == wanted]
        if len(matches) == 1 and not rivals and matches[0].line not in readopted:
            readopted[matches[0].line] = identifier
    return readopted


def _already_retired(beats):
    """Ids de cena cujos beats no BRIEF.md estão todos aposentados: a saída já foi revisada num sync anterior.

    Só o id que some NESTE sync fica em risco; um aposentado antes nunca volta a travar cena nova.
    """
    status = {}
    for beat in beats:
        identifier = beat.get("id")
        if isinstance(identifier, str) and SCENE_BEAT_RE.fullmatch(identifier):
            base = _base(identifier)
            status[base] = status.get(base, True) and beat.get("retired") is True
    return {base for base, retired in status.items() if retired}


def plan_ids(doc, beats, items, state):
    """Decide o id de cada cena sem id: readota, recusa ou dá o próximo número nunca usado.

    Devolve `{"assign": {linha: id}, "readopted": {linha: id}, "refusal": str | None,
    "next_id": int}`. `assign` inclui as readoções; com `refusal`, `assign` vem vazio.
    """
    present = {s.scene_id for s in doc.scenes if s.scene_id}
    known = set(state["scenes"]) | {_base(b) for b in _scene_ids(b.get("id") for b in beats)}
    shots = set(_scene_ids(c.get("shot") for c in items))
    settled = _already_retired(beats)
    at_risk = sorted(i for i in known - present - settled if any(_base(s) == i for s in shots))
    idless = [s for s in doc.scenes if not s.scene_id]
    readopted = _readopt(sorted(set(state["scenes"]) - present), idless, state)
    remaining = [s for s in idless if s.line not in readopted]
    orphaned = [i for i in at_risk if i not in readopted.values()]
    next_id = max(state["next_id"], highest_seen(doc, beats, items, state) + 1)
    if remaining and orphaned:
        lines = ", ".join(str(s.line) for s in remaining)
        fresh = (
            f"dê a ela um id novo à mão: `<!-- c{next_id:02d} -->`."
            if next_id <= MAX_SCENE
            else f"não há id novo livre (o roteiro passou de c{MAX_SCENE}): divida o vídeo em dois projetos."
        )
        refusal = (
            f"Cena sem id (linha {lines}) e beat com candidatos que ficaria sem cena ({', '.join(orphaned)}). "
            f"Se é a mesma cena de antes, devolva o comentário ao título (ex.: `## Título <!-- {orphaned[0]} -->`). "
            f"Se a cena antiga saiu de propósito e esta é nova, {fresh}"
        )
        return {"assign": {}, "readopted": readopted, "refusal": refusal, "next_id": next_id}
    assign = dict(readopted)
    for scene in remaining:
        if next_id > MAX_SCENE:
            raise ValueError(_CEILING)
        assign[scene.line] = f"c{next_id:02d}"
        next_id += 1
    return {"assign": assign, "readopted": readopted, "refusal": None, "next_id": next_id}


def apply_ids(text, assign):
    """Acrescenta ` <!-- cNN -->` ao fim de cada título da lista; o resto do texto fica byte a byte."""
    lines = text.split("\n")
    for number, identifier in assign.items():
        lines[number - 1] = f"{lines[number - 1].rstrip()} <!-- {identifier} -->"
    return "\n".join(lines)


def next_state(state, doc, next_id):
    """Estado depois do sync: o maior número usado e a assinatura de toda cena que já teve id."""
    scenes = {**state["scenes"], **{s.scene_id: signature(s) for s in doc.scenes if s.scene_id}}
    return {"next_id": next_id, "scenes": scenes}
