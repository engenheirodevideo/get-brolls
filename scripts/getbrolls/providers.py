"""Discovery adapters: yt-dlp for YouTube and official stock APIs."""

import html
import os
import re
from urllib.parse import parse_qs, quote, urlsplit
from .http import get_json, public_url, ProviderError
from .models import candidate

KEYS = {
    "pexels": "PEXELS_API_KEY",
    "pixabay": "PIXABAY_API_KEY",
}


def capabilities():
    result = {}
    for name in (
        "youtube",
        "instagram",
        "tiktok",
        "pexels",
        "pixabay",
        "commons",
        "nasa",
        "local",
    ):
        search_ok = name in ("youtube", "pexels", "pixabay", "commons", "nasa")
        key = KEYS.get(name)
        result[name] = {
            "search": search_ok,
            "resolve_url": name in ("youtube", "instagram", "tiktok"),
            "account_library": False,
            "embed": False,
            "seek": "local" if name == "local" else "unsupported",
            "download": True,
            "transport": "browser-cdn-pairs / yt-dlp" if name == "instagram" else "yt-dlp" if name in ("youtube", "tiktok") else name,
            "configured": not key or bool(os.environ.get(key)),
            "env_key": key,
        }
    return result


def _key(provider):
    key = os.environ.get(KEYS[provider])
    if not key:
        raise ProviderError(
            "Configure %s para pesquisar em %s" % (KEYS[provider], provider)
        )
    return key


def _base(provider, ident, title, url):
    return candidate(provider, str(ident), title, public_url(url))


def _poster(item, url):
    item["preview"]["poster_url"] = public_url(url)


def _media(item, url, width=None, height=None, duration=None):
    item["media"].update({"width": width, "height": height, "duration_s": duration})
    item["media_url"] = public_url(url)
    if item["media_url"]:
        item["acquisition"].update(
            {
                "status": "available",
                "method": "https",
                "evidence": [item["source_url"]] if item["source_url"] else [],
            }
        )
    return item


def _license(item, name, url, creator=None):
    item["rights"].update(
        {
            "license_name": name,
            "license_url": url,
            "status": "unknown",
            "evidence": [url] if url else [],
            "attribution": creator,
        }
    )


def _text(raw):
    return html.unescape(re.sub("<[^>]+>", "", str(raw or ""))).strip()


def search(provider, query, limit=8):
    if not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ProviderError("Limite deve estar entre 1 e 50")
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ProviderError("Consulta deve ter entre 1 e 500 caracteres")
    fn = {
        "pexels": _pexels,
        "pixabay": _pixabay,
        "youtube": _youtube,
        "commons": _commons,
        "nasa": _nasa,
    }.get(provider)
    if not fn:
        raise ProviderError(
            "Busca indisponível nesta fonte; forneça URL ou arquivo local"
        )
    items = fn(query.strip(), limit)
    for item in items:
        item["query"] = query.strip()
        item["match"]["kind"] = (
            "illustrative" if provider in ("pexels", "pixabay") else "literal"
        )
    return items[:limit]


def _pexels(query, limit):
    data = get_json(
        "https://api.pexels.com/v1/videos/search",
        {"query": query, "per_page": limit},
        {"Authorization": _key("pexels")},
    )
    return _pexels_rows(data)


def _pexels_rows(data):
    out = []
    for row in data.get("videos", []):
        item = _base("pexels", row["id"], "Pexels · %s" % row["id"], row.get("url"))
        user = row.get("user") or {}
        item["creator"] = {"name": user.get("name"), "url": public_url(user.get("url"))}
        _license(
            item, "Pexels License", "https://www.pexels.com/license/", user.get("name")
        )
        _poster(item, row.get("image"))
        files = [
            v
            for v in row.get("video_files", [])
            if v.get("file_type") == "video/mp4" and public_url(v.get("link"))
        ]
        fitting = [
            v for v in files if max(v.get("width") or 0, v.get("height") or 0) <= 1920
        ]
        file = max(
            fitting or files,
            key=lambda v: (v.get("width") or 0) * (v.get("height") or 0),
            default={},
        )
        out.append(
            _media(
                item,
                file.get("link"),
                file.get("width"),
                file.get("height"),
                row.get("duration"),
            )
        )
    return out


def _pixabay(query, limit):
    data = get_json(
        "https://pixabay.com/api/videos/",
        {"key": _key("pixabay"), "q": query, "per_page": max(3, limit)},
        cache_ttl=86400,
    )
    return _pixabay_rows(data)


def _pixabay_rows(data):
    out = []
    for row in data.get("hits", []):
        item = _base(
            "pixabay",
            row["id"],
            row.get("tags") or "Pixabay · %s" % row["id"],
            row.get("pageURL"),
        )
        item["creator"]["name"] = row.get("user")
        _license(
            item,
            "Pixabay Content License",
            "https://pixabay.com/service/license-summary/",
            row.get("user"),
        )
        variants = [
            v for v in row.get("videos", {}).values() if public_url(v.get("url"))
        ]
        fitting = [
            v
            for v in variants
            if max(v.get("width") or 0, v.get("height") or 0) <= 1920
        ]
        v = max(
            fitting or variants,
            key=lambda v: (v.get("width") or 0) * (v.get("height") or 0),
            default={},
        )
        _poster(item, v.get("thumbnail"))
        out.append(
            _media(
                item, v.get("url"), v.get("width"), v.get("height"), row.get("duration")
            )
        )
    return out


def _youtube(query, limit):
    from . import social
    out = []
    for row in social.search(query, limit):
        ident = row.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", ident):
            continue
        item = resolve("https://www.youtube.com/watch?v=" + ident)
        item["title"] = row.get("title") or item["title"]
        item["creator"]["name"] = row.get("channel") or row.get("uploader")
        item["media"]["duration_s"] = row.get("duration")
        thumbs = row.get("thumbnails") or []
        _poster(item, thumbs[-1].get("url") if thumbs else None)
        out.append(item)
    return out


def _commons(query, limit):
    data = get_json(
        "https://commons.wikimedia.org/w/api.php",
        {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": query + " filetype:video",
            "gsrnamespace": 6,
            "gsrlimit": limit,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
        },
    )
    out = []
    for row in data.get("query", {}).get("pages", {}).values():
        info = (row.get("imageinfo") or [{}])[0]
        if not info.get("mime", "").startswith("video/"):
            continue
        item = _base("commons", row["pageid"], row["title"], info.get("descriptionurl"))
        metadata = info.get("extmetadata", {})
        field = lambda key: _text(metadata.get(key, {}).get("value")) or None
        item["creator"]["name"] = field("Artist")
        _license(
            item,
            field("LicenseShortName"),
            public_url(field("LicenseUrl")),
            field("Attribution") or field("Artist"),
        )
        _poster(item, info.get("thumburl"))
        out.append(_media(item, info.get("url"), info.get("width"), info.get("height")))
    return out


def _nasa(query, limit):
    data = get_json(
        "https://images-api.nasa.gov/search",
        {"q": query, "media_type": "video", "page_size": limit},
    )
    out = []
    for row in data.get("collection", {}).get("items", []):
        if len(out) >= limit:
            # Avoid an unbounded N+1 of /asset lookups: once we have `limit` valid items,
            # further rows (even if present in the page) don't need a network round-trip.
            break
        meta = (row.get("data") or [{}])[0]
        ident = meta.get("nasa_id")
        if not ident or meta.get("media_type") != "video":
            continue
        item = _base(
            "nasa",
            ident,
            meta.get("title") or ident,
            "https://images.nasa.gov/details/" + quote(ident, safe=""),
        )
        item["creator"]["name"] = meta.get("secondary_creator") or meta.get("center")
        _license(
            item,
            "Verificar condições NASA e autoria do item",
            "https://www.nasa.gov/nasa-brand-center/images-and-media/",
            item["creator"]["name"],
        )
        _poster(
            item,
            next(
                (
                    v.get("href")
                    for v in row.get("links", [])
                    if v.get("rel") == "preview"
                ),
                None,
            ),
        )
        assets = get_json(
            "https://images-api.nasa.gov/asset/" + quote(ident, safe=""),
            cache_ttl=86400,
        )
        urls = [
            v.get("href")
            for v in assets.get("collection", {}).get("items", [])
            if public_url(v.get("href"))
            and urlsplit(v["href"]).path.lower().endswith(".mp4")
        ]
        urls.sort(key=lambda u: ("~orig" in u, "~medium" not in u, len(u)))
        out.append(_media(item, urls[0] if urls else None))
    return out


def resolve(url):
    if not public_url(url):
        raise ProviderError("Forneça URL pública HTTPS sem credenciais")
    p = urlsplit(url)
    host = p.hostname.lower()
    path = p.path.strip("/")
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"):
        ident = (
            path
            if host == "youtu.be"
            else (parse_qs(p.query).get("v") or [None])[0]
            if path == "watch"
            else path.split("/")[1]
            if path.startswith(("shorts/", "embed/")) and len(path.split("/")) == 2
            else None
        )
        if not ident or not re.fullmatch(r"[A-Za-z0-9_-]{11}", ident):
            raise ProviderError("URL de vídeo YouTube inválida")
        item = _base(
            "youtube",
            ident,
            "YouTube · " + ident,
            "https://www.youtube.com/watch?v=" + ident,
        )
        item["preview"].update(
            {
                "embed_url": "https://www.youtube-nocookie.com/embed/" + ident,
                "seek_mode": "native",
            }
        )
    elif host in ("instagram.com", "www.instagram.com"):
        match = re.fullmatch(r"(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", path)
        if not match:
            raise ProviderError("Forneça URL completa do post/reel Instagram")
        item = _base(
            "instagram",
            match[1],
            "Instagram · " + match[1],
            "https://www.instagram.com/" + path + "/",
        )
    elif host in ("tiktok.com", "www.tiktok.com", "m.tiktok.com"):
        match = re.fullmatch(r"@([A-Za-z0-9_.-]+)/video/(\d+)", path)
        if not match:
            raise ProviderError(
                "Forneça URL completa TikTok @usuario/video/ID; links curtos não são expandidos"
            )
        item = _base(
            "tiktok", match[2], "TikTok · " + match[2], "https://www.tiktok.com/" + path
        )
    else:
        raise ProviderError(
            "Fonte de URL não suportada; use busca do banco ou original local"
        )
    item["state"] = "candidate"
    item["acquisition"].update({"status": "available", "method": "yt-dlp"})
    return item


def refresh(item):
    """Refresh public stock file URLs without changing selection or approval."""
    import copy

    name = item["provider"]
    ident = str(item["source_id"])
    current = copy.deepcopy(item)
    if name == "pexels":
        if not ident.isdigit():
            raise ProviderError("ID Pexels inválido")
        row = get_json(
            "https://api.pexels.com/v1/videos/videos/" + ident,
            headers={"Authorization": _key(name)},
        )
        rows = _pexels_rows({"videos": [row]})
    elif name == "pixabay":
        if not ident.isdigit():
            raise ProviderError("ID Pixabay inválido")
        data = get_json(
            "https://pixabay.com/api/videos/",
            {"key": _key(name), "id": ident},
            cache_ttl=86400,
        )
        rows = _pixabay_rows(data)
    elif name == "nasa":
        data = get_json("https://images-api.nasa.gov/asset/" + quote(ident, safe=""))
        urls = [
            v.get("href")
            for v in data.get("collection", {}).get("items", [])
            if public_url(v.get("href"))
            and urlsplit(v["href"]).path.lower().endswith(".mp4")
        ]
        urls.sort(key=lambda u: ("~orig" in u, "~medium" not in u, len(u)))
        if not urls:
            raise ProviderError("Arquivo do provedor não está mais disponível")
        current["media_url"] = urls[0]
        return current
    elif name == "commons":
        data = get_json(
            "https://commons.wikimedia.org/w/api.php",
            {
                "action": "query",
                "format": "json",
                "pageids": ident,
                "prop": "imageinfo",
                "iiprop": "url|mime",
            },
        )
        pages = data.get("query", {}).get("pages", {})
        info = (pages.get(ident, {}).get("imageinfo") or [{}])[0]
        media_url = (
            public_url(info.get("url"))
            if info.get("mime", "").startswith("video/")
            else None
        )
        if not media_url:
            raise ProviderError("Arquivo do provedor não está mais disponível")
        current["media_url"] = media_url
        return current
    else:
        return current
    match = next((v for v in rows if v["source_id"] == ident), None)
    if not match or not match.get("media_url"):
        raise ProviderError("Arquivo do provedor não está mais disponível")
    current["media_url"] = match["media_url"]
    return current
