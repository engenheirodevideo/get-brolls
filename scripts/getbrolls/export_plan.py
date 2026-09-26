"""Plano de export: o roteiro revisado e a mídia do projeto num JSON que o exporter lê sem tocar em disco.

Entrada: o plano de cena (`roteiro_plan.scene_plan`), os candidatos do manifesto e a
pasta numerada escolhida pelo core. Saída: `(plano, fontes)`. O plano leva ids
lógicos de mídia (`clip:…`, `aroll:…`, `asset:…`, `plugin:…`), tempo global por cena e
por camada e nunca um caminho absoluto; `fontes` fica no core (caminho real,
`st_dev`/`st_ino`, método previsto) e nunca vai para o plugin. Todo texto do plano é
dado não confiável: o exporter escapa no formato dele.

O resolvedor de mídia de plugin é injetado (`resolve_media(kind, name, extensions)
-> (hit | None, avisos)`): só `sfx`/`musica` pendentes no projeto e na biblioteca
pessoal chegam a ele, e sem ele nada de plugin é consultado.
"""

import hashlib
import json
import re
import stat
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from . import __version__, assets, delivery
from .export_voice import probe_voice, timed_words
from .roteiro import END_ANCHOR, fold
from .roteiro_plan import PLAN_VERSION
from .runtime import one_line

EXPORT_VERSION = 1
OUT_DIR_RE = re.compile(r"exports/([a-z][a-z0-9_]{1,31})/([0-9]{3,})")
# `clip:<candidato>`, `aroll:<nome>`, `asset:<tipo>:<nome>`, `plugin:<id>:<tipo>:<nome>`: sem barra, espaço nem controle.
MEDIA_ID_RE = re.compile(r"(?:clip|aroll|asset|plugin):[^\s/\\\x00-\x1f\x7f]+")
MEDIA_ID_MAX = 200
CREDIT_MAX = 300
RESOLVABLE = ("sfx", "musica")
SLOT_KEYS = ("slot", "role", "text", "beat_id", "take", "prompt")
_LAYER_COMPONENT = {"SFX": "sfx", "MUSICA": "musica", "COMP": "composicao", "LETTERING": "lettering"}
_EXPECTED_VIDEO = "(" + "|".join(ext.lstrip(".") for ext in assets.VIDEO) + ")"
_ID_HASH_CHARS = 12
# Caminho absoluto em texto de plugin (POSIX, `~/`, `C:\`, `\\servidor`, `file:`): vira `<caminho>`.
_ABS_PATH_RE = re.compile(r"file:/+[^\s\"'<>|]*|(?<![\w.~:/\\-])(?:~?/|[A-Za-z]:[\\/]|\\\\)[^\s\"'<>|]+")


def valid_media_id(value):
    return isinstance(value, str) and len(value) <= MEDIA_ID_MAX and MEDIA_ID_RE.fullmatch(value) is not None


def id_name(name):
    """Nome de componente dentro de um id de mídia: dobrado, com espaço e `%` escapados (`minha trilha` →
    `minha%20trilha`); letra não latina fica como está (a gramática aceita)."""
    folded = fold(unicodedata.normalize("NFC", name))
    return "".join("%25" if ch == "%" else (quote(ch) if ch.isspace() else ch) for ch in folded)


def named_id(prefix, name):
    """`prefix` + nome do componente; se passar do limite da gramática, o nome é cortado e ganha `~<hash>` estável."""
    part = id_name(name)
    if len(prefix) + len(part) <= MEDIA_ID_MAX:
        return prefix + part
    digest = hashlib.sha256(part.encode("utf-8")).hexdigest()[:_ID_HASH_CHARS]
    kept = part[: MEDIA_ID_MAX - len(prefix) - _ID_HASH_CHARS - 1]
    cut = kept.rfind("%", len(kept) - 2)
    if cut != -1:  # não deixa um `%XX` pela metade
        kept = kept[:cut]
    return f"{prefix}{kept}~{digest}"


def _scrub_paths(text):
    return _ABS_PATH_RE.sub("<caminho>", text)


def _media_kind(ext):
    if ext in assets.AUDIO:
        return "audio"
    return "image" if ext in assets.IMAGE else "video"


def _row(kind, source, **values):
    row = {
        "kind": kind, "source": source, "store": "getbrolls", "origin": None, "ext": None, "available": False,
        "problem": None, "has_audio": None, "sha256": None, "duration_s": None, "width": None, "height": None,
        "credit": None, "expected": None,
    }  # fmt: skip
    return {**row, **values}


def _credit(text):
    return one_line(text)[:CREDIT_MAX] if text else None


def _clip_credit(c):
    plugin = delivery.plugin_label(c)
    title = delivery.inert(c.get("title") or c["id"], plugin)
    url = c.get("source_url")
    where = delivery.inert(url, plugin) if url else "original local"
    return _credit(f"{title} — {c.get('provider') or 'fonte'} ({where})")


def _license_credit(info):
    if not info:
        return None
    return _credit(f"{info['credito']} — {info['licenca']} ({info['origem']})")


def at_seconds(placed, words, window):
    """Tempo global de uma camada/extensão: proporcional à posição na fala, nunca fora da cena."""
    start, duration = window
    if placed["anchor"] == END_ANCHOR:
        return round(start + duration, 3)
    if not words:
        return round(start, 3)
    return round(start + duration * min(placed["word_offset"] / words, 1), 3)


class _Collector:
    """Tabela de mídia e fontes do core, montada cena a cena; um id aparece uma vez."""

    def __init__(self, project, resolve_media):
        self.project = Path(project).expanduser().resolve()
        self.resolve_media = resolve_media
        self.media = {}
        self.sources = {}
        self.warnings = []
        self.resolved = {}

    def _linked_dir(self, relative):
        """Primeira pasta de `relative` (dentro do projeto) que é link ou junction, ou None."""
        # Import tardio, como em sdk.resolvers: o plano não carrega o SDK à toa.
        from .sdk.loader import _is_link

        here = self.project
        for part in Path(relative).parts:
            here /= part
            if _is_link(here):
                return here.relative_to(self.project).as_posix()
        return None

    def _link_warning(self, label, linked):
        self.warnings.append(f"{label}: a pasta {linked}/ é um link: o export não segue link; troque pela pasta real")

    def _source(self, media_id, path, method):
        info = path.lstat()
        self.sources[media_id] = {
            "path": str(path), "st_dev": info.st_dev, "st_ino": info.st_ino, "st_mtime_ns": info.st_mtime_ns,
            "st_size": info.st_size, "method": method,
        }  # fmt: skip

    def _entry(self, kind, name):
        """(arquivo sem seguir link, origem, pasta) do componente; a mesma busca de `assets.resolve`."""
        spec = assets.ASSET_KINDS[kind]
        key = fold(unicodedata.normalize("NFC", name))
        # Mesma busca de `assets.resolve` (raízes e agrupamento), mas devolvendo a entrada sem resolver o link.
        for origin, root in assets._roots(self.project, spec):
            matches = assets._entries(root, spec).get(key)
            if matches:
                return matches[0], origin, root
        return None, None, None

    def _where(self, origin, root, path):
        if origin == "project":
            return path.relative_to(self.project).as_posix()
        return f"biblioteca pessoal: {root.name}/{path.name}"

    # --- clipes -------------------------------------------------------------

    def _clip_problem(self, c, clips_root):
        rel = (c.get("output") or {}).get("path")
        if not isinstance(rel, str) or not rel.startswith("clips/"):
            return None, "caminho fora de clips/"
        path = self.project / "brolls" / rel
        linked = self._linked_dir(Path("brolls", rel).parent)
        if linked:
            return path, f"a pasta {linked}/ é um link"
        if path.is_symlink():
            return path, "é um link"
        try:
            real = path.resolve(strict=True)
            info = path.lstat()
        except OSError:
            return path, "o arquivo sumiu"
        if not real.is_relative_to(clips_root) or not stat.S_ISREG(info.st_mode) or info.st_size == 0:
            return path, "não é um arquivo de brolls/clips/"
        return path, None

    def clips_by_beat(self, items):
        """{beat: [ids de clipe]} na ordem do manifesto: primeiro os que existem, depois os que mudaram."""
        collected, _ = delivery.deliverable(self.project, items, log_event="export_skipped")
        clips_root = (self.project / "brolls" / "clips").resolve()
        good, changed = {}, {}
        for c in collected:
            media_id = f"clip:{c['id']}"
            shot = c.get("shot")
            if not valid_media_id(media_id):
                self.warnings.append(
                    f"{shot or 'sem beat'}: clipe {c['id']} com id fora do padrão: fica fora do export"
                )
                continue
            path, problem = self._clip_problem(c, clips_root)
            if problem or path is None:
                self.warnings.append(f"{shot or 'sem beat'}: clipe {c['id']} fora do export ({problem})")
                self.media[media_id] = _row(
                    "video", "clip", problem="link" if problem and problem.endswith("é um link") else "changed"
                )
                changed.setdefault(shot, []).append(media_id)
                continue
            ext = path.suffix.lower()
            output_media = c.get("output_media") or {}
            kind = "image" if c.get("asset_type") == "image" else _media_kind(ext)
            self.media[media_id] = _row(
                kind, "clip", ext=ext, available=True, sha256=(c.get("output") or {}).get("sha256"),
                duration_s=output_media.get("duration_s"), width=output_media.get("width"),
                height=output_media.get("height"), credit=_clip_credit(c),
            )  # fmt: skip
            self._source(media_id, path, "hardlink")
            good.setdefault(shot, []).append(media_id)
        return {beat: good.get(beat, []) + changed.get(beat, []) for beat in {*good, *changed}}

    # --- A-ROLL e narração --------------------------------------------------

    def aroll(self, name, label):
        """Id `aroll:<nome>`, sempre com linha na tabela (disponível ou não)."""
        media_id = f"aroll:{name}"
        if media_id not in self.media:
            linked = self._linked_dir("aroll")
            if linked:  # a pasta tem que ser real: nem o vídeo nem o sidecar são lidos através do link
                self._link_warning(label, linked)
                self.media[media_id] = _row("video", "aroll", problem="link")
            else:
                self._aroll_file(media_id, name, label)
        return media_id

    def _aroll_file(self, media_id, name, label):
        expected = f"aroll/{name}.{_EXPECTED_VIDEO}"
        try:
            found = assets.resolve(self.project, "aroll", name)
        except ValueError:
            self.warnings.append(
                f"{label}: aroll/{name}.* é ambíguo ou aponta para fora de aroll/: deixe um arquivo só"
            )
            self.media[media_id] = _row("video", "aroll", problem="unreadable")
            return
        entry, _, _ = self._entry("aroll", name) if found["status"] == "found" else (None, None, None)
        if entry is None:
            self.media[media_id] = _row("video", "aroll", problem="missing", expected=expected)
            return
        ext = entry.suffix.lower()
        if entry.is_symlink():
            self.warnings.append(f"{label}: aroll/{entry.name} é um link: o export não segue link; troque pelo arquivo")
            self.media[media_id] = _row("video", "aroll", ext=ext, problem="link")
            return
        try:
            info = probe_voice(entry)
        except ValueError:
            self.warnings.append(f"{label}: não consegui ler aroll/{entry.name} (ffprobe): confira o arquivo")
            self.media[media_id] = _row("video", "aroll", ext=ext, problem="unreadable")
            return
        self.media[media_id] = _row(
            "video", "aroll", origin="project", ext=ext, available=True, has_audio=info["has_audio"],
            duration_s=info["duration_s"], width=info["width"], height=info["height"],
        )  # fmt: skip
        self._source(media_id, entry, "clone")

    # --- componentes --------------------------------------------------------

    def asset(self, kind, row, label):
        """Id de um componente de mídia (`marca`/`sfx`/`musica`); None quando não há o que pôr no export."""
        name = row["name"]
        media_id = named_id(f"asset:{kind}:", name)
        if media_id in self.media:
            return media_id
        if row["status"] == "found":
            entry, origin, root = self._entry(kind, name)
            if entry is not None:
                return self._found_asset(media_id, (entry, origin, root), row, label)
        if row["status"] == "pending" and kind in RESOLVABLE and self.resolve_media is not None:
            return self.plugin(kind, name, label)
        if kind == "marca":
            expected = (
                f"assets/marca/{name}.<{'|'.join(e.lstrip('.') for e in assets.ASSET_KINDS['marca'].extensions)}>"
            )
            self.media[media_id] = _row("image", "asset", problem="missing", expected=expected)
            return media_id
        return None

    def _found_asset(self, media_id, located, row, label):
        entry, origin, root = located
        ext = entry.suffix.lower()
        media_kind = _media_kind(ext)
        # A pasta do projeto tem que ser real; a biblioteca pessoal pode morar num link (disco externo): lá é cópia.
        linked = self._linked_dir(entry.parent.relative_to(self.project)) if origin == "project" else None
        if linked:
            self._link_warning(label, linked)
            self.media[media_id] = _row(media_kind, "asset", origin=origin, ext=ext, problem="link")
            return media_id
        if entry.is_symlink():
            where = self._where(origin, root, entry)
            self.warnings.append(f"{label}: {where} é um link: o export não segue link; troque pelo arquivo")
            self.media[media_id] = _row(media_kind, "asset", origin=origin, ext=ext, problem="link")
            return media_id
        self.media[media_id] = _row(
            media_kind, "asset", origin=origin, ext=ext, available=True,
            has_audio=True if media_kind == "audio" else None, credit=_license_credit(row.get("license")),
        )  # fmt: skip
        self._source(media_id, entry, "clone")
        return media_id

    def plugin(self, kind, name, label):
        """Pergunta aos resolvedores de plugin (injetados); o acerto vira `plugin:<id>:<tipo>:<nome>`."""
        if self.resolve_media is None:
            return None
        key = (kind, id_name(name))
        if key not in self.resolved:  # uma pergunta por (tipo, nome); os avisos saem na primeira cena
            hit, warnings = self.resolve_media(kind, name, assets.ASSET_KINDS[kind].extensions)
            self.warnings.extend(dict.fromkeys(f"{label}: {_scrub_paths(w)}" for w in warnings))
            self.resolved[key] = hit
        hit = self.resolved[key]
        if hit is None:
            return None
        store = hit["store"]
        media_id = named_id(f"plugin:{store}:{kind}:", name)
        if not valid_media_id(media_id):
            self.warnings.append(f"{label}: resposta do plugin {store} ignorada (id fora do padrão)")
            return None
        if media_id not in self.media:
            license_text = hit.get("license")
            shown = delivery.inert(license_text, store) if license_text else "não informada"
            self.media[media_id] = _row(
                "audio", "plugin_store", store=store, ext=Path(hit["path"]).suffix.lower(), available=True,
                has_audio=True, credit=_credit(f"Licença informada pelo plugin {store}: {shown}"),
            )  # fmt: skip
            self.sources[media_id] = {
                "path": hit["path"], "st_dev": hit["st_dev"], "st_ino": hit["st_ino"], "st_mtime_ns": None,
                "st_size": hit["st_size"], "method": "plugin", "store": store,
            }  # fmt: skip
        return media_id

    # --- cenas --------------------------------------------------------------

    def _slot_media(self, scene, slot, clips):
        """(media_id, extras) de uma vaga; b-roll sem clipe e A-ROLL sem arquivo viram aviso."""
        label = scene["id"]
        if slot["role"] == "broll":
            found = clips.get(slot["beat_id"], [])
            if not any(self.media[m]["available"] for m in found):
                self.warnings.append(f"{label}: b-roll sem clipe coletado (beat {slot['beat_id']})")
            return (found[0] if found else None), found[1:]
        if slot["component"] is None:
            return None, []
        row = scene["components"][slot["component"]]
        if slot["role"] == "presenter":
            media_id = self.aroll(row["name"], label)
            if self.media[media_id]["problem"] == "missing":
                self.warnings.append(f"{label}: A-ROLL não gravado (grave {self.media[media_id]['expected']})")
            return media_id, []
        return self.asset("marca", row, label), []

    def _voices(self, scene, slots):
        label = scene["id"]
        presenters = [s["media_id"] for s in slots if s["role"] == "presenter" and s["media_id"]]
        if len(presenters) > 1:
            self.warnings.append(f"{label}: duas vozes na mesma cena: só o lado esquerdo tem som")
            return presenters[:1], presenters
        if presenters:
            return presenters, presenters
        if scene["words"] <= 0:
            return [], []
        narration = self.aroll(scene["id"], label)
        if self.media[narration]["problem"] == "missing":
            self.warnings.append(f"{label}: fala sem narração gravada (grave aroll/{scene['id']}.mp4|mov|m4v)")
        return [narration], [narration]

    def _layer(self, scene, layer, window):
        kind = layer["kind"]
        component = next(
            (r for r in scene["components"] if r["line"] == layer["line"] and r["directive"] == kind), None
        )
        name = layer["args"][0] if kind != "LETTERING" else (layer["args"][1] if len(layer["args"]) > 1 else None)
        media_id = None
        if component is not None and kind in ("SFX", "MUSICA"):
            media_id = self.asset(_LAYER_COMPONENT[kind], component, scene["id"])
            if media_id is None:
                self.warnings.append(f'{scene["id"]}: {kind} "{name}" pendente (não achei em {_LAYER_COMPONENT[kind]})')
        return {
            "kind": kind, "args": list(layer["args"]), "quoted": list(layer["quoted"]), "line": layer["line"],
            "anchor": layer["anchor"], "word_offset": layer["word_offset"],
            "at_s": at_seconds(layer, scene["words"], window), "text": layer["args"][0] if kind == "LETTERING" else None,
            "name": name, "media_id": media_id, "component_status": component["status"] if component else None,
        }  # fmt: skip

    def _extension(self, scene, ext, window, exporter):
        if ext["plugin"] != exporter:
            self.warnings.append(
                f"{scene['id']}: diretiva [{ext['plugin']}:{ext['name']}] não é tratada pelo exporter {exporter}"
            )
        return {
            "plugin": ext["plugin"], "name": ext["name"], "args": list(ext["args"]), "quoted": list(ext["quoted"]),
            "line": ext["line"], "anchor": ext["anchor"], "word_offset": ext["word_offset"],
            "at_s": at_seconds(ext, scene["words"], window),
        }  # fmt: skip

    def _words(self, scene, voice_ids, window):
        """(fonte da legenda, palavras globais) pela voz que toca; aviso de voz sem trilha de áudio."""
        label = scene["id"]
        voice = self.media[voice_ids[0]] if voice_ids else None
        if voice is None or not voice["available"]:
            return "estimate", None
        name = Path(self.sources[voice_ids[0]]["path"]).name
        if not voice["has_audio"]:
            self.warnings.append(f"{label}: aroll/{name} não tem trilha de áudio")
            return "estimate", None
        found, warnings = timed_words(self.sources[voice_ids[0]]["path"], voice["duration_s"], window, label)
        self.warnings.extend(warnings)
        return ("transcript", found) if found else ("estimate", None)

    def scene(self, scene, clips, start, exporter):
        slots = []
        for slot in scene["layout"]["slots"]:
            media_id, extra = self._slot_media(scene, slot, clips)
            slots.append({**{k: slot[k] for k in SLOT_KEYS}, "media_id": media_id, "extra_media_ids": extra})
        voice_ids, timed = self._voices(scene, slots)
        seen = [self.media[m]["duration_s"] for m in timed if self.media[m]["available"]]
        duration = round(max(seen), 3) if seen else scene["duration_s"]
        window = (round(start, 3), duration)
        source, words_timed = self._words(scene, voice_ids, window)
        return {
            "id": scene["id"], "title": scene["title"], "line": scene["line"], "start_s": window[0],
            "duration_s": duration, "duration_source": "aroll" if seen else "estimate",
            "estimate_s": scene["duration_s"], "words": scene["words"],
            "layout": {
                "kind": scene["layout"]["kind"], "args": list(scene["layout"]["args"]),
                "quoted": list(scene["layout"]["quoted"]), "full_role": scene["layout"]["full_role"], "slots": slots,
            },
            "voice_media_ids": voice_ids, "words_source": source, "words_timed": words_timed,
            "layers": [self._layer(scene, layer, window) for layer in scene["layers"]],
            "extensions": [self._extension(scene, ext, window, exporter) for ext in scene["extensions"]],
            "speech_clean": scene["speech_clean"], "notes": list(scene["notes"]), "content_hash": scene["content_hash"],
        }  # fmt: skip


def _timing(scenes):
    sources = {s["duration_source"] for s in scenes}
    if sources == {"aroll"}:
        return "aroll"
    return "mixed" if len(sources) > 1 else "estimate"


def check_refs(plan):
    """Problemas de consistência do plano: id de mídia fora da gramática ou referência sem linha em `media`."""
    media = plan.get("media") or {}
    problems = [f"id de mídia fora do padrão: {media_id!r}" for media_id in media if not valid_media_id(media_id)]
    for scene in plan.get("scenes") or []:
        refs = [*scene["voice_media_ids"], *(layer["media_id"] for layer in scene["layers"])]
        for slot in scene["layout"]["slots"]:
            refs += [slot["media_id"], *slot["extra_media_ids"]]
        problems += [f"{scene['id']}: mídia {ref!r} não está na tabela" for ref in refs if ref and ref not in media]
    return problems


def build(project, plan, items, out_dir, resolve_media=None):
    """(plano de export, fontes do core) para a pasta numerada `out_dir` (`exports/<exporter>/<NNN>`).

    `plan` é o `scene_plan` de um roteiro revisado e sincronizado (toda cena com id);
    `items` são os candidatos do manifesto (lidos sem recuperação).
    """
    match = OUT_DIR_RE.fullmatch(out_dir or "")
    if match is None:
        raise ValueError(f"pasta de export inválida: {out_dir!r}")
    exporter = match.group(1)
    collector = _Collector(project, resolve_media)
    clips = collector.clips_by_beat(items)
    scenes, cursor = [], 0.0
    for scene in plan["scenes"]:
        row = collector.scene(scene, clips, cursor, exporter)
        scenes.append(row)
        cursor = round(cursor + row["duration_s"], 3)
    result = {
        "export_version": EXPORT_VERSION, "exporter": exporter, "out_dir": out_dir,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "getbrolls_version": __version__,
        "plan_version": PLAN_VERSION, "meta": dict(plan["meta"]), "total_s": cursor, "timing": _timing(scenes),
        "scenes": scenes, "media": collector.media, "warnings": list(dict.fromkeys(collector.warnings)),
    }  # fmt: skip
    result = json.loads(json.dumps(result, ensure_ascii=False))
    problems = check_refs(result)
    if problems:
        raise ValueError("Plano de export inconsistente (bug): " + "; ".join(problems[:5]))
    return result, collector.sources
