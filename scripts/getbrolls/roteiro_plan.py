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
from .roteiro import ASPECT_TO_FORMAT, canonical, fold

BRAND_WORDS = ("logo", "marca", "cta")
# Lado esquerdo do SPLIT é sempre `-a`, o direito sempre `-b`: o id não muda quando
# um lado vira apresentador ou volta a ser b-roll.
SIDES = ("a", "b")
_LAYER_KIND = {"SFX": "sfx", "MUSICA": "musica", "COMP": "composicao"}


def digest(value):
    """sha256 de um JSON canônico (chaves ordenadas, sem espaço): o mesmo em qualquer máquina."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def scene_fingerprint(scene):
    """O que a pessoa revisou numa cena, sem id nem formatação: título, diretivas, fala e notas."""
    return {
        "title": " ".join(scene.title.split()),
        "layout": {"kind": scene.layout.kind, "args": list(scene.layout.args), "quoted": list(scene.layout.quoted)},
        "layers": [
            {"kind": d.kind, "args": list(d.args), "quoted": list(d.quoted), "at": d.word_offset} for d in scene.layers
        ],
        "extensions": [
            {"plugin": d.plugin, "name": d.name, "args": list(d.args), "at": d.word_offset} for d in scene.extensions
        ],
        "speech": " ".join(scene.speech.split()),
        "notes": list(scene.notes),
    }


def _presenter(side):
    """(é apresentador, take, descrição) de um lado de SPLIT: `A-ROLL[: take]` ou `UGC: descrição`."""
    head, _, rest = side.partition(":")
    kind = canonical(head)
    if kind == "A-ROLL":
        return True, fold(rest) or None, None
    if kind == "UGC":
        return True, None, rest.strip() or None
    return False, None, None


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


def _aroll_name(scene_id, take):
    """`aroll/cNN.<ext>` ou `aroll/cNN-<take>.<ext>`; sem id ainda, sem nome (o sync dá o id)."""
    if not scene_id:
        return None
    return f"{scene_id}-{take}" if take else scene_id


def _component(project, directive, kind, name, prompt=None):
    row = {
        "directive": directive.kind, "kind": kind, "name": name, "line": directive.line, "prompt": prompt,
        "status": "pending", "path": None, "origin": None, "license": None, "warnings": [], "error": None,
    }  # fmt: skip
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
        return [_component(project, layout, "aroll", _aroll_name(scene_id, take))]
    if layout.kind == "UGC":
        return [_component(project, layout, "aroll", _aroll_name(scene_id, None), prompt=layout.args[0])]
    if layout.kind == "SPLIT":
        rows = []
        for side, quoted in zip(layout.args, layout.quoted, strict=True):
            presenter, take, prompt = _presenter(side)
            if presenter and not quoted:
                rows.append(_component(project, layout, "aroll", _aroll_name(scene_id, take), prompt=prompt))
        return rows
    if layout.kind == "FULL" and not layout.quoted[0] and _is_brand(project, layout.args[0]):
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
        if layout.quoted[0] or _is_brand(project, layout.args[0]):
            return []  # cartela de texto ou marca: componente, não busca
        return [(scene_id, layout.args[0])]
    if layout.kind == "SPLIT":
        return [
            (f"{scene_id}-{slot}" if scene_id else None, side)
            for slot, side, quoted in zip(SIDES, layout.args, layout.quoted, strict=True)
            if not quoted and not _presenter(side)[0]
        ]
    return []


def _placed(directive):
    label = f"{directive.plugin}:{directive.name}" if directive.kind == "EXT" else directive.kind
    return {
        "directive": label,
        "line": directive.line,
        "anchor": directive.anchor,
        "word_offset": directive.word_offset,
    }


def _scene_row(scene, components, beat_ids):
    return {
        "id": scene.scene_id,
        "title": scene.title,
        "line": scene.line,
        "layout": {"kind": scene.layout.kind, "args": list(scene.layout.args), "quoted": list(scene.layout.quoted)},
        "layers": [
            {"kind": d.kind, "args": list(d.args), "quoted": list(d.quoted), **_placed(d)} for d in scene.layers
        ],
        "extensions": [
            {"plugin": d.plugin, "name": d.name, "args": list(d.args), **_placed(d)} for d in scene.extensions
        ],
        "anchors": sorted((_placed(d) for d in (*scene.layers, *scene.extensions)), key=lambda row: row["line"]),
        "speech": scene.speech,
        "speech_clean": scene.speech_clean,
        "notes": list(scene.notes),
        "duration_s": scene.duration_s,
        "content_hash": digest(scene_fingerprint(scene)),
        "components": components,
        "beats": beat_ids,
    }


def scene_plan(project, doc):
    """Plano completo: cenas, beats (na ordem das cenas), duração total, avisos e problemas."""
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
                problems.append(f'linha {row["line"]}: {row["directive"]} "{row["name"]}": {row["error"]}')
            elif row["status"] == "pending" and row["kind"] != "aroll":
                warnings.append(f'{label}: {row["directive"]} "{row["name"]}" pendente (não achei em {row["kind"]})')
            warnings.extend(f"{label}: {w}" for w in row["warnings"])
        scenes.append(_scene_row(scene, components, beat_ids))
    total = round(sum(s.duration_s for s in doc.scenes), 1)
    return {"scenes": scenes, "beats": beats, "total_s": total, "warnings": warnings, "problems": problems}


def aspect_problems(meta, rules, brief_data=None):
    """Divergências do `aspecto` contra o `video_format` efetivo (todas as camadas) e o BRIEF.md."""
    expected = ASPECT_TO_FORMAT[meta["aspecto"]]
    problems = []
    video_format = (rules or {}).get("video_format")
    if video_format != expected:
        problems.append(
            f'O roteiro está em {meta["aspecto"]} ({expected}) e o RULES.md em vigor está em "{video_format}". '
            f"Rode `init-rules --format {expected} --force --project <projeto>` ou ajuste o aspecto do roteiro."
        )
    if brief_data is not None:
        delivered = ((brief_data.get("video") or {}).get("delivery") or {}).get("format")
        if delivered != expected:
            problems.append(
                f'O roteiro está em {meta["aspecto"]} ({expected}) e o BRIEF.md entrega em "{delivered}". '
                f'Troque "video.delivery.format" para "{expected}" ou ajuste o aspecto do roteiro.'
            )
    return problems
