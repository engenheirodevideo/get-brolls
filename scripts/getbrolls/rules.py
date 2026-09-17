"""User-editable, local declarative rules. No YAML dependency or code evaluation."""

import json, os, re
from pathlib import Path
from urllib.parse import urlsplit
from .queue import validate_pacing_block

ROOT = Path(__file__).resolve().parents[2]
TYPES = {"video", "image", "news_screenshot", "web_screenshot"}


def read_rules_block(path):
    """Único bloco ```json de um RULES.md, já validado como objeto."""
    raw = Path(path).read_text(encoding="utf-8")
    blocks = re.findall(r"```json\s*\n(.*?)\n```", raw, re.S)
    if len(blocks) != 1:
        raise ValueError(
            "O RULES.md precisa de exatamente um bloco ```json — apague os blocos "
            "extras ou rode `init-rules --force` para gerar um arquivo limpo."
        )
    try:
        r = json.loads(blocks[0])
    except json.JSONDecodeError:
        raise ValueError(
            "O bloco json do RULES.md está com erro de digitação (vírgula ou aspas "
            "sobrando). Rode `init-rules --force` para gerar um arquivo limpo."
        ) from None
    if not isinstance(r, dict):
        raise ValueError(
            "O bloco json do RULES.md tem que ser um objeto entre chaves. Rode "
            "`init-rules --force` para gerar um arquivo limpo."
        )
    return r


# A camada global é conveniência editorial: ela nunca fala por uma pessoa. Quem
# assina a responsabilidade é sempre o projeto em que o vídeo está sendo feito.
NEVER_INHERITED = ("copyright",)


def home_dir():
    """Pasta pessoal da skill; `GB_HOME` existe para os testes não tocarem a real."""
    return Path(os.environ.get("GB_HOME") or Path.home() / ".getbrolls")


def _merge(base, extra):
    """Listas unem (sem repetir, na ordem do mais geral ao mais específico)."""
    if isinstance(base, list) and isinstance(extra, list):
        joined = list(base)
        for value in extra:
            if value not in joined:
                joined.append(value)
        return joined
    if isinstance(base, dict) and isinstance(extra, dict):
        merged = dict(base)
        for key, value in extra.items():
            merged[key] = _merge(base[key], value) if key in base else value
        return merged
    return extra


def _strip_never_inherited(path, data, warnings, label):
    """Tira de uma camada de fora do projeto o que só o projeto pode dizer."""
    for key in NEVER_INHERITED:
        if key in data:
            del data[key]
            warnings.append(
                f'"{key}" do RULES.md {label} ({path}) foi ignorado: '
                "responsabilidade e declaração valem só no projeto em que o "
                "vídeo é feito. Preencha no RULES.md deste projeto."
            )
    return data


def rules_layers(project):
    """Camadas na ordem geral → específica, com os avisos do que foi ignorado."""
    layers, warnings = [], []
    project_path = Path(project) / "RULES.md"
    if not project_path.exists():
        # Sem arquivo no projeto, o modelo da skill é só o piso: quem está por cima
        # (global, GB_RULES_FILE) continua valendo mais que ele.
        template = ROOT / "docs" / "RULES.md"
        layers.append((template, read_rules_block(template)))
        project_path = None
    global_path = home_dir() / "RULES.md"
    if global_path.exists():
        try:
            data = read_rules_block(global_path)
        except ValueError as e:
            warnings.append(f"{global_path} foi ignorado: {e}")
        else:
            layers.append(
                (
                    global_path,
                    _strip_never_inherited(global_path, data, warnings, "global"),
                )
            )
    middle = os.environ.get("GB_RULES_FILE")
    if middle:
        middle = Path(middle)
        if not middle.exists():
            raise ValueError(
                "GB_RULES_FILE aponta para um arquivo que não existe: corrija o caminho "
                "ou apague essa variável para usar o RULES.md da pasta do trabalho."
            )
        # GB_RULES_FILE também é de fora do projeto: não pode assinar por ninguém.
        layers.append(
            (
                middle,
                _strip_never_inherited(middle, read_rules_block(middle), warnings, "de GB_RULES_FILE"),
            )
        )
    if project_path is not None:
        layers.append((project_path, read_rules_block(project_path)))
    return layers, warnings


def load_rules(project):
    layers, warnings = rules_layers(project)
    r, sources = {}, {}
    for path, data in layers:
        for key, value in data.items():
            r[key] = _merge(r[key], value) if key in r else value
            sources[key] = str(path)
    if type(r.get("version")) is not int or r["version"] != 1:
        raise ValueError('Em RULES.md, "version" tem que ser o número 1. Ajuste essa linha.')
    if (
        not isinstance(r.get("asset_types"), list)
        or not r["asset_types"]
        or any(not isinstance(t, str) or t not in TYPES for t in r["asset_types"])
    ):
        raise ValueError(
            'Em RULES.md, "asset_types" tem que ser uma lista com pelo menos um destes: '
            + ", ".join(sorted(TYPES))
            + "."
        )
    if r.get("video_format") not in ("native", "reels", "horizontal"):
        raise ValueError('Em RULES.md, "video_format" tem que ser "native", "reels" ou "horizontal".')
    providers = {"youtube", "pexels", "pixabay", "commons", "nasa"}
    if not isinstance(r.get("preferred_providers"), dict):
        raise ValueError(
            'Em RULES.md, "preferred_providers" tem que ter as chaves "literal" e '
            '"illustrative", cada uma com uma lista de fontes.'
        )
    for intent in ("literal", "illustrative"):
        v = r["preferred_providers"].get(intent)
        if (
            not isinstance(v, list)
            or any(not isinstance(x, str) or x not in providers for x in v)
            or len(set(v)) != len(v)
        ):
            raise ValueError(
                'Em RULES.md, a lista de "preferred_providers.' + intent + '" só aceita, '
                "sem repetir: " + ", ".join(sorted(providers)) + "."
            )
    for key in ("preferred_domains", "blocked_domains"):
        if not isinstance(r.get(key), list) or any(
            not isinstance(v, str) or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", v)
            for v in r[key]
        ):
            raise ValueError(
                "Em RULES.md, " + key + " só aceita domínios em minúsculas como "
                '"youtube.com" — sem "https://" e sem caminho depois da barra.'
            )
    if not isinstance(r.get("editorial_rules"), list) or any(not isinstance(v, str) for v in r["editorial_rules"]):
        raise ValueError('Em RULES.md, "editorial_rules" tem que ser uma lista de frases entre aspas.')
    rights = r.get("copyright", {})
    if not isinstance(rights, dict) or rights.get("mode") not in (
        "per_item_evidence",
        "user_declaration",
    ):
        raise ValueError(
            'Em RULES.md, "copyright.mode" tem que ser "per_item_evidence" (você confere '
            'fonte por fonte) ou "user_declaration" (você assume a responsabilidade).'
        )
    for key in ("responsible_person", "declaration"):
        if rights.get(key) is not None and not isinstance(rights[key], str):
            raise ValueError(
                'Em RULES.md, "copyright.responsible_person" e "copyright.declaration" '
                "têm que ser texto entre aspas (ou null)."
            )
    if rights["mode"] == "user_declaration" and any(
        not (rights.get(k) or "").strip() for k in ("responsible_person", "declaration")
    ):
        raise ValueError(
            "No modo user_declaration, alguém assume a responsabilidade: rode "
            '`init-rules --responsible "SEU NOME" --declaration "..." '
            "--mode user_declaration --force` ou preencha esses dois campos no RULES.md."
        )
    browser = r.get("browser", {})
    if (
        not isinstance(browser, dict)
        or browser.get("viewport") not in ("mobile", "desktop")
        or not isinstance(browser.get("full_page"), bool)
    ):
        raise ValueError(
            'Em RULES.md, "browser" precisa de "viewport" ("mobile" ou "desktop") e de "full_page" (true ou false).'
        )
    for key in ("mobile_width", "mobile_height", "desktop_width", "desktop_height"):
        if type(browser.get(key)) is not int or not 240 <= browser[key] <= 3840:
            raise ValueError("Em RULES.md, " + key + " tem que ser um número inteiro entre 240 e 3840.")
    # Optional `pacing` block for the social queue; the environment still wins.
    validate_pacing_block(r.get("pacing"))
    # Aditivo: `sources` diz de que arquivo veio cada campo e `rules_warnings` o que
    # foi ignorado pelo caminho. Nenhum consumidor existente lê essas duas chaves.
    r["sources"] = sources
    r["rules_warnings"] = warnings
    if warnings:
        from .runtime import record_warning

        for message in warnings:
            record_warning("RULES_LAYER_IGNORED", message)
    return r


def domain_matches(url, domains):
    host = (urlsplit(url or "").hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def allowed(c, rules):
    return c.get("asset_type", "video") in rules["asset_types"] and not domain_matches(
        c.get("source_url"), rules["blocked_domains"]
    )


def format_report(c, rules):
    w = c.get("media", {}).get("width")
    h = c.get("media", {}).get("height")
    target = rules["video_format"]
    fit = (
        "unknown"
        if not w or not h
        else "native"
        if target == "native"
        else "matches"
        if abs(w / h - (9 / 16 if target == "reels" else 16 / 9)) < 0.025
        else "needs_layout_review"
    )
    return {
        "target": target,
        "source_width": w,
        "source_height": h,
        "fit": fit,
        "transform": "preserve_native",
    }


def sync_formats(ledger, rules, confirm=False):
    """Alinha o formato-alvo dos itens às regras em vigor.

    Quando a mudança apagaria decisões humanas já dadas, para antes de tocar em
    qualquer item e nomeia quem seria atingido: a pessoa decide se aceita revisar
    tudo de novo (`--confirm-format-change`) ou se prefere voltar o formato.
    """
    invalidated = [
        c["id"]
        for c in ledger.data["items"]
        if c.get("format", {}).get("target", "native") != format_report(c, rules)["target"]
        and (c.get("approval") or {}).get("status") == "approved"
    ]
    if invalidated and not confirm:
        raise ValueError(
            "Mudar o formato-alvo para "
            + rules["video_format"]
            + " invalidaria "
            + str(len(invalidated))
            + " aprovação(ões) já dada(s): "
            + ", ".join(invalidated)
            + ". Se for isso mesmo, repita o comando com --confirm-format-change; "
            "senão, volte o video_format no RULES.md antes de continuar."
        )
    changed = []
    for c in ledger.data["items"]:
        new = format_report(c, rules)
        old = c.get("format", {}).get("target", "native")
        if old != new["target"]:
            c["approval"] = {
                "status": "pending",
                "by": None,
                "at": None,
                "revision": None,
            }
            c.pop("review", None)
            c["segment"]["revision"] += 1
            c["output"] = {"path": None, "sha256": None, "verified": False}
            c["state"] = "awaiting_approval"
            c["format"] = new
            changed.append(c)
        else:
            c["format"] = new
    if changed:
        ledger.save_many("format_changed", changed)
