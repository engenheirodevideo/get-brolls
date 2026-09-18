"""Brief editorial do usuário: Markdown com um único bloco JSON, validado à mão.

Mesmo contrato de `rules.py`: nada de YAML, nada de execução de código, erros em
português dizendo o que fazer. O brief descreve o vídeo e os beats; cada beat vira
comando pronto (`search`/`resolve`/`preview`) e se liga ao candidato pelo `--shot`.
"""

import json
import os
import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "gb.py"

# O id do beat também é valor de `--shot`, então usa um alfabeto mais estreito que ele.
BEAT_ID_RE = re.compile(r"[a-z0-9-]{1,40}")
FORMATS = ("native", "reels", "horizontal")
INTENTS = ("literal", "illustrative")
POSTURES = ("per_item_evidence", "user_declaration")
SOURCES = (
    "youtube",
    "instagram",
    "tiktok",
    "pexels",
    "pixabay",
    "commons",
    "nasa",
    "local",
)
STOCK_SOURCES = ("pexels", "pixabay")
SEARCHABLE = ("youtube", "pexels", "pixabay", "commons", "nasa")
MIN_HINT_S = 0.5
MAX_HINT_S = 120


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
    raw = path.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\s*\n(.*?)\n```", raw, re.S)
    if len(blocks) != 1:
        raise ValueError(
            "O BRIEF.md precisa de exatamente um bloco ```json — apague os blocos extras "
            "ou rode `init-brief` numa pasta limpa para começar de um modelo."
        )
    try:
        return json.loads(blocks[0])
    except json.JSONDecodeError:
        raise ValueError(
            "O bloco json do BRIEF.md está com erro de digitação (vírgula ou aspas "
            "sobrando). Conserte essa linha e rode `brief --validate` de novo."
        ) from None


def _text(value, field, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(
            f'Em BRIEF.md, "{field}" tem que ser um texto entre aspas'
            + ("." if not required else " e não pode ficar vazio.")
        )
    return value


def _number(value, field, low, high, required=True):
    if value is None and not required:
        return None
    if type(value) not in (int, float) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(
            f'Em BRIEF.md, "{field}" tem que ser um número entre {low} e {high}' + ("." if required else " (ou null).")
        )
    return value


def _flag(value, field):
    if not isinstance(value, bool):
        raise ValueError(f'Em BRIEF.md, "{field}" tem que ser true ou false.')
    return value


def _choice(value, field, options):
    if value not in options:
        raise ValueError(f'Em BRIEF.md, "{field}" tem que ser ' + " ou ".join(f'"{o}"' for o in options) + ".")
    return value


def _sources(value, field):
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(v, str) or v not in SOURCES for v in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(
            f'Em BRIEF.md, "{field}" só aceita, sem repetir, uma lista destas fontes: ' + ", ".join(SOURCES) + "."
        )
    return list(value)


def _queries(value, field):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError(f'Em BRIEF.md, "{field}" tem que ser uma lista de frases de busca entre aspas.')
    return list(value)


def _dictionary(data, field):
    value = data.get(field)
    if not isinstance(value, dict):
        raise ValueError(
            f'Em BRIEF.md, "{field}" tem que ser um bloco entre chaves com as chaves descritas em docs/BRIEF.md.'
        )
    return value


def resolve_beat(defaults, beat, position):
    """Beat com os buracos preenchidos pelos defaults do brief; nada de campo extra."""
    where = f"beats[{position}]"
    identifier = _text(beat.get("id"), f"{where}.id") or ""
    if not BEAT_ID_RE.fullmatch(identifier):
        raise ValueError(
            f'Em BRIEF.md, o id do beat "{identifier}" não serve: use de 1 a 40 '
            "caracteres com letras minúsculas, números e hífen (ele também vira o "
            "valor de --shot)."
        )
    sources = (
        _sources(beat["allowed_sources"], f"{where}.allowed_sources")
        if beat.get("allowed_sources") is not None
        else list(defaults["allowed_sources"])
    )
    stock = _flag(beat["stock"], f"{where}.stock") if beat.get("stock") is not None else defaults["stock"]
    banks = [s for s in sources if s in STOCK_SOURCES]
    if stock and not banks:
        raise ValueError(
            f'O beat "{identifier}" está com "stock": true, mas nenhuma fonte de banco '
            "em allowed_sources. Acrescente pexels ou pixabay, ou marque stock como false."
        )
    if not stock and banks:
        raise ValueError(
            f'O beat "{identifier}" permite ' + " e ".join(banks) + ' com "stock": false. '
            "Marque stock como true se o material de banco pode entrar, ou tire essas "
            "fontes de allowed_sources."
        )
    return {
        "id": identifier,
        "narration": _text(beat.get("narration"), f"{where}.narration", required=False),
        "target": _text(beat.get("target"), f"{where}.target"),
        "intent": _choice(beat.get("intent", defaults["intent"]), f"{where}.intent", INTENTS),
        "allowed_sources": sources,
        "stock": stock,
        "duration_hint_s": _number(
            beat.get("duration_hint_s", defaults["duration_hint_s"]),
            f"{where}.duration_hint_s",
            MIN_HINT_S,
            MAX_HINT_S,
            required=False,
        ),
        "queries": _queries(beat.get("queries"), f"{where}.queries"),
        "notes": _text(beat.get("notes"), f"{where}.notes", required=False),
    }


def validate_brief(data, rules=None):
    """Devolve (brief normalizado, conflitos). Erro = brief inutilizável; conflito = aviso."""
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError('Em BRIEF.md, "version" tem que ser o número 1. Ajuste essa linha.')
    video = _dictionary(data, "video")
    delivery = _dictionary(video, "delivery")
    rights = _dictionary(data, "rights")
    defaults = _dictionary(data, "defaults")
    video = {
        "title": _text(video.get("title"), "video.title"),
        "objective": _text(video.get("objective"), "video.objective"),
        "audience": _text(video.get("audience"), "video.audience", required=False),
        "delivery": {
            "format": _choice(delivery.get("format"), "video.delivery.format", FORMATS),
            "duration_s": _number(delivery.get("duration_s"), "video.delivery.duration_s", 1, 36000, required=False),
            "platform": _text(delivery.get("platform"), "video.delivery.platform", required=False),
        },
    }
    rights = {
        "posture": _choice(rights.get("posture"), "rights.posture", POSTURES),
        "stock_allowed": _flag(rights.get("stock_allowed"), "rights.stock_allowed"),
        "notes": _text(rights.get("notes"), "rights.notes", required=False),
    }
    defaults = {
        "allowed_sources": _sources(defaults.get("allowed_sources"), "defaults.allowed_sources"),
        "intent": _choice(defaults.get("intent"), "defaults.intent", INTENTS),
        "duration_hint_s": _number(defaults.get("duration_hint_s"), "defaults.duration_hint_s", MIN_HINT_S, MAX_HINT_S),
        "stock": _flag(defaults.get("stock"), "defaults.stock"),
    }
    raw_beats = data.get("beats")
    if not isinstance(raw_beats, list) or not raw_beats or any(not isinstance(b, dict) for b in raw_beats):
        raise ValueError(
            'Em BRIEF.md, "beats" tem que ser uma lista com pelo menos um beat; cada beat precisa de "id" e "target".'
        )
    beats, seen = [], set()
    for position, raw in enumerate(raw_beats):
        resolved = resolve_beat(defaults, raw, position)
        if resolved["id"] in seen:
            raise ValueError(
                f'O id de beat "{resolved["id"]}" aparece repetido em BRIEF.md. Cada beat '
                "precisa de um id único, porque ele vira o --shot do candidato."
            )
        seen.add(resolved["id"])
        beats.append({"id": resolved["id"], "resolved": resolved})
    conflicts = []
    if not rights["stock_allowed"] and any(b["resolved"]["stock"] for b in beats):
        conflicts.append(
            'O brief diz "rights.stock_allowed": false, mas há beat com "stock": true. '
            "Confirme com a pessoa antes de buscar em banco."
        )
    if isinstance(rules, dict):
        target = rules.get("video_format")
        if target in FORMATS and target != video["delivery"]["format"]:
            conflicts.append(
                f'O brief entrega em "{video["delivery"]["format"]}" e o RULES.md está em '
                f'"{target}". Escolha um dos dois antes de coletar: '
                f"`init-rules --force --format {video['delivery']['format']}` alinha a "
                "regra ao brief, ou corrija video.delivery.format no BRIEF.md."
            )
    # RULES.md ilegível ou ausente não é declaração preenchida: a postura que transfere
    # responsabilidade para uma pessoa nunca passa por falta de arquivo para conferir.
    copyright_block = (rules or {}).get("copyright") or {}
    if rights["posture"] == "user_declaration" and any(
        not (copyright_block.get(key) or "").strip() for key in ("responsible_person", "declaration")
    ):
        raise ValueError(
            'O brief assume "user_declaration", mas o RULES.md não tem nome e declaração '
            'legíveis. Rode `init-rules --mode user_declaration --responsible "NOME" '
            '--declaration "frase" --force` antes de seguir.'
        )
    return (
        {"version": 1, "video": video, "rights": rights, "defaults": defaults, "beats": beats},
        conflicts,
    )


def _cli_prefix():
    return f'python3 "{CLI}"'


def beat_commands(project, beat):
    """search/resolve/inspect/preview prontos para este beat, com --shot, --intent e --narração.

    Beat sem fonte pesquisável por API (só instagram/tiktok/local) não ganha `search`:
    no lugar dele vai um `note` explicando que o caminho é `resolve --url/--file`.
    """
    project = shlex.quote(str(Path(project).expanduser().resolve()))
    prefix = f"{_cli_prefix()} "
    provider = next((s for s in beat["allowed_sources"] if s in SEARCHABLE), None)
    query = beat["queries"][0] if beat["queries"] else beat["target"]
    origin = "--file ARQUIVO" if beat["allowed_sources"] == ["local"] else "--url URL_PUBLICA"
    narration = f" --narration {shlex.quote(beat['narration'])}" if beat.get("narration") else ""
    commands = {}
    if provider:
        commands["search"] = (
            prefix
            + f"search --project {project} --provider {provider} "
            + f"--query {shlex.quote(query)} --intent {beat['intent']} --shot {beat['id']}"
        )
    commands["resolve"] = prefix + f"resolve --project {project} {origin} --shot {beat['id']}"
    # Sem --start/--end: o intervalo real sai do que a pessoa viu na fonte, não de um
    # palpite do brief; `duration_hint_s` fica em `resolved` como sugestão.
    # Analisar vem antes de pré-visualizar: a fonte diz a duração e onde está o assunto.
    commands["inspect"] = (
        prefix
        + f"inspect --project {project} --candidate ID --query "
        + shlex.quote(beat.get("narration") or beat["target"])
    )
    commands["preview"] = prefix + f"preview --project {project} --candidate ID" + narration
    if not provider:
        # Instagram, TikTok e material próprio não têm busca por API: entram por URL/arquivo.
        commands["note"] = (
            "Nenhuma fonte deste beat é pesquisável por API ("
            + ", ".join(beat["allowed_sources"])
            + "): descubra a URL no navegador e registre com o resolve acima."
        )
    return commands


def beat_progress(beats, items):
    """Candidatos já registrados por beat: o vínculo é `c["shot"] == beat.id`."""
    return {beat["id"]: [c["id"] for c in items if c.get("shot") == beat["id"]] for beat in beats}
