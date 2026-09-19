"""User-editable, local declarative rules. No YAML dependency or code evaluation."""

import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from . import logs
from .brief import read_json_block
from .models import invalidate_approval
from .queue import validate_pacing_block

ROOT = Path(__file__).resolve().parents[2]
TYPES = {"video", "image", "news_screenshot", "web_screenshot"}

log = logs.get("rules")


def read_rules_block(path):
    """Único bloco ```json de um RULES.md, já validado como objeto."""
    return read_json_block(
        path,
        missing=(
            "O RULES.md precisa de exatamente um bloco ```json — apague os blocos "
            "extras ou rode `init-rules --force` para gerar um arquivo limpo."
        ),
        syntax=(
            "O bloco json do RULES.md está com erro de digitação (vírgula ou aspas "
            "sobrando). Rode `init-rules --force` para gerar um arquivo limpo."
        ),
        not_object=(
            "O bloco json do RULES.md tem que ser um objeto entre chaves. Rode "
            "`init-rules --force` para gerar um arquivo limpo."
        ),
    )


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
            logs.event(log, logging.INFO, "rules_key_not_inherited", rule=key, layer=label)
    return data


def rules_layers(project):
    """Camadas na ordem geral → específica, com os avisos do que foi ignorado."""
    layers, warnings = [], []
    project_path = Path(project) / "RULES.md"
    template_base = not project_path.exists()
    if template_base:
        # Sem arquivo no projeto, o modelo da skill é só o piso: quem está por cima
        # (global, GB_RULES_FILE) continua valendo mais que ele.
        template = ROOT / "docs" / "RULES.md"
        layers.append((template, read_rules_block(template)))
        project_path = None
    global_path = home_dir() / "RULES.md"
    if global_path.exists():
        # Uma camada global corrompida some, mas as restrições dela (blocked_domains,
        # asset_types...) somem junto — a pessoa continua achando que valem. Falha
        # travada: melhor parar o comando do que deixar de aplicar uma regra de
        # segurança sem avisar. Só o arquivo FALTANDO é inofensivo (o piso da skill
        # cobre isso); um arquivo presente e ilegível não pode ser tratado como ausente.
        try:
            data = read_rules_block(global_path)
        except ValueError as e:
            raise ValueError(
                f"O RULES.md global ({global_path}) não pôde ser lido: {e} Conserte "
                "esse arquivo ou apague-o para usar apenas as regras deste projeto."
            ) from None
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
    logs.event(
        log,
        logging.DEBUG,
        "rules_layers",
        **{"global": global_path.exists()},
        env_file=bool(middle),
        project=project_path is not None,
        template_base=template_base,
    )
    return layers, warnings


def load_rules(project):  # noqa: C901, PLR0912 - existing size; validator with one check per RULES.md field
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
        if type(browser.get(key)) is not int or not 240 <= browser[key] <= 3840:  # noqa: PLR2004 - matches the "entre 240 e 3840" message below
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


def _host(url):
    """Host only, never the full URL: safe to log."""
    hostname = urlsplit(url or "").hostname
    return hostname.lower() if hostname else None


def allowed(c, rules):
    if c.get("asset_type", "video") not in rules["asset_types"]:
        logs.event(
            log,
            logging.INFO,
            "rule_block",
            candidate=c.get("id"),
            rule="asset_type",
            provider=c.get("provider"),
            host=_host(c.get("source_url")),
        )
        return False
    if domain_matches(c.get("source_url"), rules["blocked_domains"]):
        logs.event(
            log,
            logging.INFO,
            "rule_block",
            candidate=c.get("id"),
            rule="blocked_domain",
            provider=c.get("provider"),
            host=_host(c.get("source_url")),
        )
        return False
    return True


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
        if abs(w / h - (9 / 16 if target == "reels" else 16 / 9)) < 0.025  # noqa: PLR2004 - aspect-ratio tolerance
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
    invalidated_ids = set(invalidated)
    changed = []
    for c in ledger.data["items"]:
        new = format_report(c, rules)
        old = c.get("format", {}).get("target", "native")
        if old != new["target"]:
            invalidate_approval(c, bump_revision=True)
            c["format"] = new
            changed.append(c)
            logs.event(
                log,
                logging.INFO,
                "format_invalidation",
                candidate=c["id"],
                **{"from": old, "to": new["target"]},
                confirmed=c["id"] in invalidated_ids,
            )
        else:
            c["format"] = new
    if changed:
        ledger.save_many("format_changed", changed)
