"""Discovery adapters: yt-dlp for YouTube and official stock APIs."""

import html
import os
import re
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .http import ProviderError, encoded_url, get_json, public_url
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
            "transport": "browser-cdn-pairs / yt-dlp"
            if name == "instagram"
            else "yt-dlp"
            if name in ("youtube", "tiktok")
            else name,
            "configured": not key or bool(os.environ.get(key)),
            "env_key": key,
        }
    return result


def _key(provider):
    key = os.environ.get(KEYS[provider])
    if not key:
        raise ProviderError(f"Configure {KEYS[provider]} para pesquisar em {provider}")
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


MEDIA_CHOICES = ("image", "video", "any")
# Fontes que publicam foto e vídeo no mesmo acervo; nas outras `--media` não muda nada.
MEDIA_AWARE = ("nasa", "commons")


def search(provider, query, limit=8, media="any"):
    if not isinstance(limit, int) or not 1 <= limit <= 50:  # noqa: PLR2004 - matches the "entre 1 e 50" message below
        raise ProviderError("Limite deve estar entre 1 e 50")
    if not isinstance(query, str) or not query.strip() or len(query) > 500:  # noqa: PLR2004 - matches the "entre 1 e 500 caracteres" message below
        raise ProviderError("Consulta deve ter entre 1 e 500 caracteres")
    if media not in MEDIA_CHOICES:
        raise ProviderError("--media aceita image, video ou any")
    fn = {
        "pexels": _pexels,
        "pixabay": _pixabay,
        "youtube": _youtube,
        "commons": _commons,
        "nasa": _nasa,
    }.get(provider)
    if not fn:
        raise ProviderError("Busca indisponível nesta fonte; forneça URL ou arquivo local")
    # YouTube e os bancos só devolvem vídeo: pedir imagem ali não é erro do usuário,
    # é fonte errada — e quem escolhe a fonte é o beat, não esta função.
    items = fn(query.strip(), limit, media) if provider in MEDIA_AWARE else fn(query.strip(), limit)
    for item in items:
        item["query"] = query.strip()
        item["match"]["kind"] = "illustrative" if provider in ("pexels", "pixabay") else "literal"
    return items[:limit]


def _pick_largest(variants, cap=1920):
    """Maior variante cujo lado maior não passe de `cap`; sem isso, a maior de todas."""
    fitting = [v for v in variants if max(v.get("width") or 0, v.get("height") or 0) <= cap]
    return max(
        fitting or variants,
        key=lambda v: (v.get("width") or 0) * (v.get("height") or 0),
        default={},
    )


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
        item = _base("pexels", row["id"], f"Pexels · {row['id']}", row.get("url"))
        user = row.get("user") or {}
        item["creator"] = {"name": user.get("name"), "url": public_url(user.get("url"))}
        _license(item, "Pexels License", "https://www.pexels.com/license/", user.get("name"))
        _poster(item, row.get("image"))
        files = [
            v for v in row.get("video_files", []) if v.get("file_type") == "video/mp4" and public_url(v.get("link"))
        ]
        file = _pick_largest(files)
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
            row.get("tags") or f"Pixabay · {row['id']}",
            row.get("pageURL"),
        )
        item["creator"]["name"] = row.get("user")
        _license(
            item,
            "Pixabay Content License",
            "https://pixabay.com/service/license-summary/",
            row.get("user"),
        )
        variants = [v for v in row.get("videos", {}).values() if public_url(v.get("url"))]
        v = _pick_largest(variants)
        _poster(item, v.get("thumbnail"))
        out.append(_media(item, v.get("url"), v.get("width"), v.get("height"), row.get("duration")))
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


def _commons(query, limit, media="any"):
    kinds = {"image": "bitmap", "video": "video"}.get(media)
    data = get_json(
        "https://commons.wikimedia.org/w/api.php",
        {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": query + (f" filetype:{kinds}" if kinds else " filetype:video|bitmap"),
            "gsrnamespace": 6,
            "gsrlimit": limit,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
        },
    )
    wanted = {"image": ("image/",), "video": ("video/",)}.get(media, ("video/", "image/"))
    out = []
    for row in data.get("query", {}).get("pages", {}).values():
        info = (row.get("imageinfo") or [{}])[0]
        if not info.get("mime", "").startswith(wanted):
            continue
        out.append(_commons_item(row, info))
    return out


def _commons_item(row, info):
    """Um arquivo do Commons vira candidato: mesma leitura na busca e na página dele."""
    item = _base("commons", row["pageid"], row["title"], info.get("descriptionurl"))
    metadata = info.get("extmetadata", {})

    def field(key, metadata=metadata):
        return _text(metadata.get(key, {}).get("value")) or None

    item["creator"]["name"] = field("Artist")
    _license(
        item,
        field("LicenseShortName"),
        public_url(field("LicenseUrl")),
        field("Attribution") or field("Artist"),
    )
    mime = str(info.get("mime") or "")
    if mime.startswith("image/"):
        item["media"]["kind"] = "image"
        item["asset_type"] = "image"
    elif mime.startswith("video/"):
        item["media"]["kind"] = "video"
    _poster(item, info.get("thumburl"))
    return _media(item, info.get("url"), info.get("width"), info.get("height"))


def _commons_file(title):
    """`commons.wikimedia.org/wiki/File:…` vira candidato pela mesma API pública da busca.

    Recusar a página enquanto `search --provider commons` existe deixava quem já tem o
    link do arquivo sem rota nenhuma — e o Commons é onde mora a foto histórica literal.
    """
    data = get_json(
        "https://commons.wikimedia.org/w/api.php",
        {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
        },
        cache_ttl=86400,
    )
    pages = list((data.get("query") or {}).get("pages", {}).values())
    row = pages[0] if pages else {}
    info = (row.get("imageinfo") or [{}])[0]
    if row.get("missing") is not None or not info.get("url") or not row.get("pageid"):
        raise ProviderError(f"O Commons não tem o arquivo {title!r}; confira o endereço da página.")
    mime = str(info.get("mime") or "")
    if not mime.startswith(("video/", "image/")):
        raise ProviderError(f"O arquivo {title!r} não é vídeo nem imagem; o Commons também guarda som e documento.")
    return _commons_item(row, info)


def _nasa(query, limit, media="any"):
    media_type = {"image": "image", "video": "video"}.get(media, "image,video")
    data = get_json(
        "https://images-api.nasa.gov/search",
        {"q": query, "media_type": media_type, "page_size": limit},
    )
    accepted = {"image": ("image",), "video": ("video",)}.get(media, ("image", "video"))
    out = []
    for row in data.get("collection", {}).get("items", []):
        if len(out) >= limit:
            # Avoid an unbounded N+1 of /asset lookups: once we have `limit` valid items,
            # further rows (even if present in the page) don't need a network round-trip.
            break
        meta = (row.get("data") or [{}])[0]
        ident = meta.get("nasa_id")
        kind = meta.get("media_type")
        if not ident or kind not in accepted:
            continue
        item = _base(
            "nasa",
            ident,
            meta.get("title") or ident,
            "https://images.nasa.gov/details/" + quote(ident, safe=""),
        )
        item["creator"]["name"] = meta.get("secondary_creator") or meta.get("center")
        # Sem isto o relatório da busca mostrava `media.kind: None` para todo item da NASA.
        item["media"]["kind"] = kind
        if kind == "image":
            item["asset_type"] = "image"
        _license(
            item,
            "Verificar condições NASA e autoria do item",
            "https://www.nasa.gov/nasa-brand-center/images-and-media/",
            item["creator"]["name"],
        )
        _poster(
            item,
            encoded_url(
                next(
                    (v.get("href") for v in row.get("links", []) if v.get("rel") == "preview"),
                    None,
                )
            ),
        )
        suffixes = (".mp4",) if kind == "video" else NASA_IMAGE_SUFFIXES
        urls = _nasa_asset_urls(ident, suffixes)
        out.append(_media(item, urls[0] if urls else None))
    return out


# Arquivos de imagem que o acervo da NASA publica para um mesmo item.
NASA_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff")


def _nasa_asset_urls(ident, suffixes):
    """Arquivos públicos deste item, do mais completo para o mais leve."""
    data = get_json("https://images-api.nasa.gov/asset/" + quote(ident, safe=""), cache_ttl=86400)
    urls = [
        encoded_url(v["href"])
        for v in data.get("collection", {}).get("items", [])
        if public_url(v.get("href")) and urlsplit(v["href"]).path.lower().endswith(suffixes)
    ]
    urls.sort(key=lambda u: ("~orig" in u, "~medium" not in u, len(u)))
    return urls


def _nasa_details(ident):
    """`images.nasa.gov/details/<id>` vira candidato pela mesma API pública da busca.

    Recusar esta página enquanto `search --provider nasa` existe deixava o beat de
    foto estática sem rota nenhuma: a pessoa tem o link do item e não consegue usá-lo.
    """
    data = get_json("https://images-api.nasa.gov/search", {"nasa_id": ident}, cache_ttl=86400)
    rows = data.get("collection", {}).get("items", [])
    meta = (rows[0].get("data") or [{}])[0] if rows else {}
    if not meta.get("nasa_id"):
        raise ProviderError(f"O acervo da NASA não tem item com o id {ident!r}; confira o endereço da página.")
    nasa_id = str(meta["nasa_id"])
    video = meta.get("media_type") == "video"
    item = _base(
        "nasa",
        nasa_id,
        meta.get("title") or nasa_id,
        "https://images.nasa.gov/details/" + quote(nasa_id, safe=""),
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
        encoded_url(next((v.get("href") for v in (rows[0].get("links") or []) if v.get("rel") == "preview"), None)),
    )
    item["media"]["kind"] = "video" if video else "image"
    if not video:
        item["asset_type"] = "image"
    urls = _nasa_asset_urls(nasa_id, (".mp4",) if video else NASA_IMAGE_SUFFIXES)
    _media(item, urls[0] if urls else None)
    item["state"] = "candidate"
    return item


def resolve(url):  # noqa: C901 - existing size; one branch per recognized source host/URL shape
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
            if path.startswith(("shorts/", "embed/")) and len(path.split("/")) == 2  # noqa: PLR2004 - caminho "shorts/<id>" ou "embed/<id>": exatamente 2 partes
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
    elif host in ("images.nasa.gov", "www.images.nasa.gov"):
        match = re.fullmatch(r"details/(.+)", path)
        if not match:
            raise ProviderError("Forneça a URL completa do item: https://images.nasa.gov/details/<id>")
        return _nasa_details(unquote(match[1]))
    elif host in ("commons.wikimedia.org", "commons.m.wikimedia.org"):
        match = re.fullmatch(r"wiki/(File:.+)", unquote(path))
        if not match:
            raise ProviderError("Forneça a URL completa do arquivo: https://commons.wikimedia.org/wiki/File:<nome>")
        return _commons_file(match[1].replace("_", " "))
    elif host in ("tiktok.com", "www.tiktok.com", "m.tiktok.com"):
        match = re.fullmatch(r"@([A-Za-z0-9_.-]+)/video/(\d+)", path)
        if not match:
            raise ProviderError("Forneça URL completa TikTok @usuario/video/ID; links curtos não são expandidos")
        item = _base("tiktok", match[2], "TikTok · " + match[2], "https://www.tiktok.com/" + path)
    else:
        raise ProviderError(
            "Fonte de URL não suportada; use busca do banco (`search --provider ...`) ou original local"
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
        image = (item.get("media") or {}).get("kind") == "image"
        urls = _nasa_asset_urls(ident, NASA_IMAGE_SUFFIXES if image else (".mp4",))
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
        media_url = public_url(info.get("url")) if info.get("mime", "").startswith("video/") else None
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
