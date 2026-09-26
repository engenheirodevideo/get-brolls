"""Plano de cena: o roteiro lido contra as pastas do projeto.

Por cena, diz que componentes ela pede (e se existem), que beats ela gera, onde
cada camada entra na fala (âncora) e uma impressão digital estável do conteúdo.
É a única leitura que o `sync` e os exporters (subprojeto C) usam: plugin nenhum
varre pasta sozinho. Nada aqui levanta por causa de um nome de componente: a
linha vira `invalid` e o problema vai para `problems`.
"""

import hashlib
import json

from . import assets
from .roteiro import ASPECT_TO_FORMAT, canonical, fold, spoken_words, take_problem

BRAND_WORDS = ("logo", "marca", "cta")
# Lado esquerdo do SPLIT é sempre `-a`, o direito sempre `-b`: o id não muda quando
# um lado vira apresentador ou volta a ser b-roll.
SIDES = ("a", "b")
_LAYER_KIND = {"SFX": "sfx", "MUSICA": "musica", "COMP": "composicao"}
# Versão da forma do plano (exporters) e da entrada dos hashes: mudar a forma sobe o número.
# A forma 2 acrescenta `words`, `layout.full_role` e `layout.slots`; o hash não muda.
PLAN_VERSION = 2
HASH_VERSION = 1
META_KEYS = ("aspecto", "legenda", "duracao_alvo_s", "genero", "tema", "cliente", "direcao")
# Papel da vaga única de `[FULL: x]` por `full_role`.
_FULL_SLOT = {"beat": "broll", "marca": "brand", "cartela": "card"}


def digest(value):
    """sha256 de um JSON canônico (chaves ordenadas, sem espaço): o mesmo em qualquer máquina."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def scene_fingerprint(scene, project):
    """O que a pessoa revisou numa cena, sem id nem formatação: título, diretivas, fala e notas.

    Entra também o papel do `[FULL: x]` (`full_role`): ele depende de `assets/marca`,
    fora do texto, e trocar beat por marca aposenta um beat — a revisão tem que cair.
    """
    return {
        "v": HASH_VERSION,
        "title": " ".join(scene.title.split()),
        "layout": {"kind": scene.layout.kind, "args": list(scene.layout.args), "quoted": list(scene.layout.quoted)},
        "layers": [
            {"kind": d.kind, "args": list(d.args), "quoted": list(d.quoted), "at": d.word_offset} for d in scene.layers
        ],
        "extensions": [
            {"plugin": d.plugin, "name": d.name, "args": list(d.args), "quoted": list(d.quoted), "at": d.word_offset}
            for d in scene.extensions
        ],
        "speech": " ".join(scene.speech.split()),
        "notes": list(scene.notes),
        "full": full_role(project, scene.layout),
    }


def _presenter(side):
    """(é apresentador, take, descrição, erro do take) de um lado de SPLIT: `A-ROLL[: take]` ou `UGC: descrição`.

    O take do lado passa pela mesma gramática do take do layout (`roteiro.take_problem`).
    """
    head, _, rest = side.partition(":")
    kind = canonical(head)
    if kind == "A-ROLL":
        take = fold(rest) or None
        return True, take, None, take_problem(take) if take else None
    if kind == "UGC":
        return True, None, rest.strip() or None, None
    return False, None, None, None


def _is_brand(project, target):
    """FULL é componente de marca (sem beat) quando é palavra de marca ou arquivo de `assets/marca`."""
    if fold(target) in BRAND_WORDS:
        return True
    if not assets.valid_name(target):
        return False  # "cidade à noite, 1998" não é nome de arquivo: é alvo de b-roll
    try:
        return assets.resolve(project, "marca", target)["status"] == "found"
    except ValueError:
        return True  # existe arquivo de marca com esse nome, ambíguo ou fora da pasta: a linha do componente explica


def full_role(project, layout):
    """Papel de `[FULL: x]`: "cartela" (texto entre aspas), "marca" (componente, sem beat) ou "beat";
    None fora de FULL."""
    if layout.kind != "FULL":
        return None
    if layout.quoted[0]:
        return "cartela"
    return "marca" if _is_brand(project, layout.args[0]) else "beat"


def _aroll_name(scene_id, take, side=None):
    """`aroll/cNN[-lado][-take].<ext>`; sem id ainda, sem nome (o sync dá o id).

    O lado só entra quando os dois lados do SPLIT são apresentador: senão os dois
    cairiam no mesmo arquivo.
    """
    if not scene_id:
        return None
    return "-".join(part for part in (scene_id, side, take) if part)


def _component(  # noqa: PLR0913 - one keyword per row field the caller knows
    project, directive, kind, name, *, prompt=None, side=None, take=None, error=None
):  # pylint: disable=too-many-arguments,too-many-positional-arguments  # one keyword per row field the caller knows
    """Linha de componente; `side` ("a"/"b") só em apresentador de SPLIT e `take` só em A-ROLL; None no resto."""
    row = {
        "directive": directive.kind, "kind": kind, "name": name, "line": directive.line, "prompt": prompt,
        "side": side, "take": take, "status": "pending", "path": None, "origin": None, "license": None,
        "warnings": [], "error": None,
    }  # fmt: skip
    if error is not None:
        return {**row, "status": "invalid", "error": error}
    if name is None:
        return row
    try:
        found = assets.resolve(project, kind, name)
    except ValueError as exc:
        return {**row, "status": "invalid", "error": str(exc)}
    return {
        **row,
        "status": found["status"],
        "path": found["path"],
        "origin": found["origin"],
        "license": found["license"],
        "warnings": found["warnings"],
    }


def _layout_components(project, scene):
    layout, scene_id = scene.layout, scene.scene_id
    if layout.kind == "A-ROLL":
        take = layout.args[0] if layout.args else None
        return [_component(project, layout, "aroll", _aroll_name(scene_id, take), take=take)]
    if layout.kind == "UGC":
        return [_component(project, layout, "aroll", _aroll_name(scene_id, None), prompt=layout.args[0])]
    if layout.kind == "SPLIT":
        presenters = []
        for slot, side, quoted in zip(SIDES, layout.args, layout.quoted, strict=True):
            presenter, take, prompt, problem = _presenter(side)
            if presenter and not quoted:
                presenters.append((slot, take, prompt, problem))
        both = len(presenters) == len(SIDES)
        return [
            _component(
                project, layout, "aroll", _aroll_name(scene_id, take, slot if both else None),
                prompt=prompt, side=slot, take=take, error=problem,
            )
            for slot, take, prompt, problem in presenters
        ]  # fmt: skip
    if full_role(project, layout) == "marca":
        return [_component(project, layout, "marca", layout.args[0])]
    return []


def _components(project, scene):
    rows = _layout_components(project, scene)
    for layer in scene.layers:
        if layer.kind == "LETTERING":
            if len(layer.args) > 1:
                rows.append(_component(project, layer, "lettering", layer.args[1]))
            continue
        rows.append(_component(project, layer, _LAYER_KIND[layer.kind], layer.args[0]))
    return rows


def _beat_targets(project, scene):
    """[(id do beat, alvo)] da cena; id None enquanto a cena não tem id."""
    layout, scene_id = scene.layout, scene.scene_id
    if layout.kind == "BROLL":
        return [(scene_id, layout.args[0])]
    if layout.kind == "FULL":
        if full_role(project, layout) != "beat":
            return []  # cartela de texto ou marca: componente, não busca
        return [(scene_id, layout.args[0])]
    if layout.kind == "SPLIT":
        return [
            (f"{scene_id}-{slot}" if scene_id else None, side)
            for slot, side, quoted in zip(SIDES, layout.args, layout.quoted, strict=True)
            if not quoted and not _presenter(side)[0]
        ]
    return []


def _slot(slot, role, text, **extra):
    """Uma vaga do layout: onde a mídia entra, e o que a preenche (beat, apresentador ou componente)."""
    row = {"slot": slot, "role": role, "text": text, "beat_id": None, "take": None, "prompt": None, "component": None}
    return {**row, **extra}


def _presenter_slot(slot, text, rows):
    """Vaga de apresentador; `component` = índice da linha `aroll` do mesmo lado em `components`."""
    _, take, prompt, _ = _presenter(text)
    side = slot if slot in SIDES else None
    index = next((i for i, r in enumerate(rows) if r["kind"] == "aroll" and r["side"] == side), None)
    return _slot(slot, "presenter", text, take=take, prompt=prompt, component=index)


def slots(project, scene, components):
    """Vagas de mídia do layout: `a`/`b` no SPLIT (esquerda/topo, direita/baixo), `main` no resto.

    Mesma regra de `_beat_targets`: lado entre aspas é cartela, apresentador é
    `presenter`, o resto é b-roll com o beat `cNN-a|b`. `component` aponta a linha de
    `components` que preenche a vaga (apresentador ou marca); None no resto.
    """
    layout, scene_id = scene.layout, scene.scene_id
    kind = layout.kind
    if kind == "SPLIT":
        rows = []
        for slot, side, quoted in zip(SIDES, layout.args, layout.quoted, strict=True):
            if quoted:
                rows.append(_slot(slot, "card", side))
            elif _presenter(side)[0]:
                rows.append(_presenter_slot(slot, side, components))
            else:
                rows.append(_slot(slot, "broll", side, beat_id=f"{scene_id}-{slot}" if scene_id else None))
        return rows
    if kind == "A-ROLL":
        text = f"A-ROLL: {layout.args[0]}" if layout.args else "A-ROLL"
        return [_presenter_slot("main", text, components)]
    if kind == "UGC":
        return [_slot("main", "presenter", f"UGC: {layout.args[0]}", prompt=layout.args[0], component=0)]
    if kind == "BROLL":
        return [_slot("main", "broll", layout.args[0], beat_id=scene_id)]
    role = _FULL_SLOT[full_role(project, layout) or "beat"]  # só FULL chega aqui: o papel nunca é None
    extra = {"beat_id": scene_id} if role == "broll" else {"component": 0} if role == "brand" else {}
    return [_slot("main", role, layout.args[0], **extra)]


def _placed(directive):
    label = f"{directive.plugin}:{directive.name}" if directive.kind == "EXT" else directive.kind
    return {
        "directive": label,
        "line": directive.line,
        "anchor": directive.anchor,
        "word_offset": directive.word_offset,
    }


def _scene_row(project, scene, components, beat_ids):
    layout = scene.layout
    return {
        "id": scene.scene_id,
        "title": scene.title,
        "line": scene.line,
        "layout": {
            "kind": layout.kind,
            "args": list(layout.args),
            "quoted": list(layout.quoted),
            "full_role": full_role(project, layout),
            "slots": slots(project, scene, components),
        },
        "layers": [
            {"kind": d.kind, "args": list(d.args), "quoted": list(d.quoted), **_placed(d)} for d in scene.layers
        ],
        "extensions": [
            {"plugin": d.plugin, "name": d.name, "args": list(d.args), "quoted": list(d.quoted), **_placed(d)}
            for d in scene.extensions
        ],
        "anchors": sorted((_placed(d) for d in (*scene.layers, *scene.extensions)), key=lambda row: row["line"]),
        "speech": scene.speech,
        "speech_clean": scene.speech_clean,
        "notes": list(scene.notes),
        "duration_s": scene.duration_s,
        "words": spoken_words(scene.speech),
        "content_hash": digest(scene_fingerprint(scene, project)),
        "components": components,
        "beats": beat_ids,
    }


def scene_plan(project, doc):
    """Plano completo: versão, meta, cenas, beats (na ordem das cenas), duração total, avisos e problemas."""
    scenes, beats, problems, warnings = [], [], [], list(doc.warnings)
    for scene in doc.scenes:
        label = scene.scene_id or f"linha {scene.line}"
        beat_ids = []
        for beat_id, target in _beat_targets(project, scene):
            beat_ids.append(beat_id)
            beats.append({
                "id": beat_id, "target": target, "narration": scene.speech_clean or None,
                "duration_hint_s": scene.duration_s, "scene": label,
            })  # fmt: skip
        components = _components(project, scene)
        for row in components:
            if row["status"] == "invalid":
                subject = f'{row["directive"]} "{row["name"]}"' if row["name"] else row["directive"]
                problems.append(f"linha {row['line']}: {subject}: {row['error']}")
            elif row["status"] == "pending" and row["kind"] != "aroll":
                warnings.append(f'{label}: {row["directive"]} "{row["name"]}" pendente (não achei em {row["kind"]})')
            warnings.extend(f"{label}: {w}" for w in row["warnings"])
        scenes.append(_scene_row(project, scene, components, beat_ids))
    total = round(sum(s.duration_s for s in doc.scenes), 1)
    return {
        "plan_version": PLAN_VERSION, "meta": {key: doc.meta.get(key) for key in META_KEYS},
        "scenes": scenes, "beats": beats, "total_s": total, "warnings": warnings, "problems": problems,
    }  # fmt: skip


def _format_label(value):
    """`em "reels"`; sem valor, `com o formato não definido` (nunca "None" na mensagem)."""
    return f'em "{value}"' if value else "com o formato não definido"


def aspect_problems(meta, rules, brief_data=None):
    """Divergências do `aspecto` contra o `video_format` efetivo (todas as camadas) e o BRIEF.md."""
    expected = ASPECT_TO_FORMAT[meta["aspecto"]]
    problems = []
    video_format = (rules or {}).get("video_format")
    if video_format != expected:
        problems.append(
            f"O roteiro está em {meta['aspecto']} ({expected}) e o RULES.md em vigor "
            f"está {_format_label(video_format)}. Rode `init-rules --format {expected} "
            "--force --project <projeto>` ou ajuste o aspecto do roteiro."
        )
    if brief_data is not None:
        delivered = ((brief_data.get("video") or {}).get("delivery") or {}).get("format")
        if delivered != expected:
            problems.append(
                f"O roteiro está em {meta['aspecto']} ({expected}) e o BRIEF.md entrega {_format_label(delivered)}. "
                f'Troque "video.delivery.format" para "{expected}" ou ajuste o aspecto do roteiro.'
            )
    return problems
