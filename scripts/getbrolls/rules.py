"""User-editable, local declarative rules. No YAML dependency or code evaluation."""

import json, os, re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
TYPES = {"video", "image", "news_screenshot", "web_screenshot"}


def load_rules(project):
    path = Path(os.environ.get("GB_RULES_FILE") or Path(project) / "RULES.md")
    if not path.exists():
        if os.environ.get("GB_RULES_FILE"):
            raise ValueError("GB_RULES_FILE não existe.")
        path = ROOT / "RULES.md"
    raw = path.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\s*\n(.*?)\n```", raw, re.S)
    if len(blocks) != 1:
        raise ValueError("RULES.md precisa de exatamente um bloco JSON.")
    try:
        r = json.loads(blocks[0])
    except json.JSONDecodeError:
        raise ValueError("JSON inválido em RULES.md.") from None
    if not isinstance(r, dict) or (
        type(r.get("version")) is not int or r["version"] != 1
    ):
        raise ValueError("Versão de RULES inválida.")
    if (
        not isinstance(r.get("asset_types"), list)
        or not r["asset_types"]
        or any(not isinstance(t, str) or t not in TYPES for t in r["asset_types"])
    ):
        raise ValueError("asset_types inválido.")
    if r.get("video_format") not in ("native", "reels", "horizontal"):
        raise ValueError("video_format inválido.")
    providers = {"youtube", "pexels", "pixabay", "commons", "nasa"}
    if not isinstance(r.get("preferred_providers"), dict):
        raise ValueError("preferred_providers inválido.")
    for intent in ("literal", "illustrative"):
        v = r["preferred_providers"].get(intent)
        if (
            not isinstance(v, list)
            or any(not isinstance(x, str) or x not in providers for x in v)
            or len(set(v)) != len(v)
        ):
            raise ValueError("Ordem de provedores inválida.")
    for key in ("preferred_domains", "blocked_domains"):
        if not isinstance(r.get(key), list) or any(
            not isinstance(v, str)
            or not re.fullmatch(
                r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", v
            )
            for v in r[key]
        ):
            raise ValueError(
                key + ": use domínios minúsculos, sem protocolo ou caminho."
            )
    if not isinstance(r.get("editorial_rules"), list) or any(
        not isinstance(v, str) for v in r["editorial_rules"]
    ):
        raise ValueError("editorial_rules inválido.")
    rights = r.get("copyright", {})
    if not isinstance(rights, dict) or rights.get("mode") not in (
        "per_item_evidence",
        "user_declaration",
    ):
        raise ValueError("Modo copyright inválido.")
    for key in ("responsible_person", "declaration"):
        if rights.get(key) is not None and not isinstance(rights[key], str):
            raise ValueError("Declaração inválida.")
    if rights["mode"] == "user_declaration" and any(
        not (rights.get(k) or "").strip() for k in ("responsible_person", "declaration")
    ):
        raise ValueError("Usuário deve preencher nome e declaração em RULES.md.")
    browser = r.get("browser", {})
    if (
        not isinstance(browser, dict)
        or browser.get("viewport") not in ("mobile", "desktop")
        or not isinstance(browser.get("full_page"), bool)
    ):
        raise ValueError("Configuração browser inválida.")
    for key in ("mobile_width", "mobile_height", "desktop_width", "desktop_height"):
        if type(browser.get(key)) is not int or not 240 <= browser[key] <= 3840:
            raise ValueError("Viewport fora de 240–3840 px.")
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


def sync_formats(ledger, rules):
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
