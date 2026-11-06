"""Confere um link aberto de vídeo (rota `open_web`: `direct_stream` e `embedded_player`) antes de entregá-lo ao yt-dlp.

O `resolve` confere só o primeiro host. Depois o yt-dlp busca por
conta própria cada endereço da playlist: variantes, segmentos, chaves (`EXT-X-KEY`) e o init
(`EXT-X-MAP`), seguindo redirecionamento. Uma playlist hostil num host público faria a
máquina mandar GETs para a rede local. Aqui a playlist (e um nível de variantes) é lida pelo
`web_text.get_text`, com IP conferido e redirecionamento revalidado, e todo endereço que ela cita
precisa ser HTTPS público na 443. O `.mp4` é espiado (`web_text.peek`): só segue o que começa como
MP4 de verdade, porque HTML, XML ou HLS atrás de um nome `.mp4` também seriam lidos pelo yt-dlp.

A playlist é lida como o downloader nativo do yt-dlp a lê (o leitor de referência; o FFmpeg não lê nada
remoto): pelos bytes, em UTF-8, sem o charset que a resposta declara, com linha só em `\r\n`/`\r`/`\n` (as
outras quebras do `splitlines()` dele e o byte que não é UTF-8 recusam a playlist) e com os atributos
achados por `[A-Z0-9-]+=` (`yt_dlp.utils.parse_m3u8_attributes`). A que ele leria diferente (controle,
separador Unicode, `URI` repetido, aspas sem fechar, barra invertida entre aspas, nome de atributo fora de
`[A-Z0-9-]`, como `X_URI`, `#EXT-X-KEY`/`#EXT-X-MAP` sem o `:` que ele não exige, ou endereço que só ele
acharia) é recusada. Só o DRM de CDM na playlist (Widevine, FairPlay, PlayReady) para a rota, com
`suggested_route`; AES-128 segue.

O yt-dlp busca de novo o que foi conferido: a segunda busca passa pelo proxy de saída conferida
(`egress_proxy`), que aplica a mesma regra de destino a cada conexão (docs/SECURITY.md).
"""

import logging
import re
from urllib.parse import urljoin, urlsplit

from . import http, logs, route_hint, web_text
from .errors import ProviderError, RouteRefusal
from .page_video import DRM_REFUSAL

_log = logs.get("stream_check")

PLAYLIST_MAX_BYTES = 2 * 1024 * 1024
# Uma master real traz poucas variantes e rendições; acima disto, a leitura não é feita.
VARIANT_LIMIT = 16
# Hosts diferentes citados no conjunto (cada um custa uma resolução de DNS).
HOST_LIMIT = 8
PEEK_BYTES = 512
# Caixas que abrem um arquivo MP4/MOV (ISO BMFF): `ftyp` na quase totalidade, as outras em arquivos antigos.
MP4_BOXES = (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip", b"styp")
MP4_TYPES = ("video/", "application/mp4", "application/octet-stream", "binary/octet-stream")

# O downloader nativo do yt-dlp (o leitor de referência) quebra a playlist com `str.splitlines()`. Aqui só
# `\r\n`, `\r` e `\n` quebram linha, e as outras quebras do `splitlines()` (`\x0b`, `\x0c`, `\x1c`-`\x1e`,
# `\x85`, `\u2028`, `\u2029`) recusam a playlist (`_CONTROL`): as duas leituras nunca dão linhas diferentes.
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
# Controle C0 (menos tab e as quebras acima), DEL, controle C1, os separadores Unicode de linha e parágrafo e o
# U+FFFD que o `web_text` põe no lugar de um byte que não é UTF-8: o yt-dlp lê os bytes com
# `decode('utf-8', 'ignore')` e simplesmente descarta esse byte (`#\xffEXT-X-KEY:…` vira uma chave para ele).
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u2028\u2029\ufffd]")
# Endereço é ASCII visível: espaço, Unicode ou controle num endereço é recusado em vez de interpretado.
_URI_TEXT = re.compile(r"[\x21-\x7e]+")
# O atributo `URI` (não o `…-URI` de outro atributo), contado no texto cru da tag: dois na mesma tag são recusados.
_URI_NAME = re.compile(r"(?<![A-Za-z0-9-])URI\s*=", re.IGNORECASE | re.ASCII)
_PLAYLIST_TAGS = ("#EXT-X-MEDIA:", "#EXT-X-I-FRAME-STREAM-INF:")
_KEY_TAGS = ("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")
# As tags que o downloader nativo do yt-dlp acha pelo prefixo (`line.startswith('#EXT-X-KEY')`) e lê de
# `line[11:]`: o caractere 10 tem de ser o `:`.
_PREFIX_TAGS = ("#EXT-X-KEY", "#EXT-X-MAP")
_PREFIX_LENGTH = 10
# DRM de CDM: Widevine, FairPlay e PlayReady (o `KEYFORMAT` da tag de chave, ou o `skd://` do FairPlay). A
# chave AES-128 comum (`identity`, ou sem `KEYFORMAT`) e outro formato qualquer seguem para o yt-dlp.
CDM_KEYFORMATS = frozenset(
    {
        "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed",
        "com.apple.streamingkeydelivery",
        "com.microsoft.playready",
        "urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95",
    }
)

NEXT_STEP = "Use outro link do vídeo, ou peça o arquivo original e importe com `resolve --file`."
NOT_PUBLIC = (
    "A playlist do vídeo aponta para um endereço fora da internet pública (rede local ou privada, "
    "http sem s ou porta diferente de 443), e o getbrolls não baixa esse vídeo. " + NEXT_STEP
)
MALFORMED = (
    "A playlist do vídeo tem caractere de controle, endereço fora do ASCII, `URI` repetido, aspas sem "
    "fechar, barra invertida entre aspas, nome de atributo fora de A-Z, 0-9 e hífen ou tag de chave/init sem "
    "o `:`, e o getbrolls não a lê. " + NEXT_STEP
)


class _MalformedPlaylistError(ProviderError):
    """Playlist que o yt-dlp e o getbrolls leriam de jeitos diferentes (`MALFORMED`)."""


class _CdmDrmError(ProviderError):
    """Chave de DRM de CDM na playlist (`DRM_REFUSAL`)."""


def _refuse(host, reason, message):
    """Linha `stream_check` da recusa (só o host) e o erro pronto para levantar."""
    logs.event(_log, logging.WARNING, "stream_check", host=host, result="refused", reason=reason)
    return ProviderError(message)


def _host(url):
    return (urlsplit(url).hostname or "-").lower()


def _port_443(url):
    """A porta é a do HTTPS (`443` ou nenhuma); porta inválida conta como não."""
    try:
        return urlsplit(url).port in (None, 443)
    except ValueError:
        return False


# O leitor de referência é o downloader nativo do yt-dlp (`yt_dlp.utils.parse_m3u8_attributes`): ele acha os
# atributos com `re.findall` de `[A-Z0-9-]+=valor`, sem exigir que o nome comece depois de uma vírgula. Um nome
# como `X_URI` vira `URI` para ele e continuaria sendo outro nome para uma leitura por vírgulas.
_NAME_ALPHABET = re.compile(r"[A-Za-z0-9-]+")
# Cópia literal da regex de `parse_m3u8_attributes` do yt-dlp 2026.08.19 (`yt_dlp/utils/_utils.py`), com a
# âncora `(?:,|$)` e o `[^",]+`. Não edite: ao subir o yt-dlp, confira a função de novo e atualize a versão
# aqui (o teste diferencial compara com a cópia dela).
_NATIVE_ATTRIBUTE = re.compile(r'(?P<key>[A-Z0-9-]+)=(?P<val>"[^"]+"|[^",]+)(?:,|$)')
# `URI=` dentro de um valor entre aspas: um leitor que errasse uma aspa veria ali um atributo.
_QUOTED_URI_NAME = re.compile(r"URI\s*=", re.IGNORECASE | re.ASCII)


def _native_attributes(text):
    """O dicionário que o yt-dlp monta da lista de atributos (`parse_m3u8_attributes`): o último nome repetido vale."""
    info = {}
    for key, val in _NATIVE_ATTRIBUTE.findall(text):
        info[key] = val[1:-1] if val.startswith('"') else val
    return info


def _is_uri_name(name):
    return name == "URI" or name.endswith("-URI")


def _attributes(text):
    """`[(NOME, valor)]` da lista de atributos de uma tag, lida como o yt-dlp lê (`NOME=valor` separados por `,`).

    Valor entre aspas vai até a aspa seguinte; levantam `_MalformedPlaylistError` as aspas que não fecham,
    texto depois da aspa que fecha sem uma vírgula, `URI=` dentro das aspas e a barra invertida entre aspas
    (outro leitor de HLS a leria como escape de aspa ou de barra; recusar é mais simples que escolher um).
    """
    found, i, size = [], 0, len(text)
    while i < size:
        while i < size and text[i] in " \t,":
            i += 1
        equals = text.find("=", i)
        if i >= size or equals < 0:
            break
        name = text[i:equals].strip(" \t").upper()
        if not _NAME_ALPHABET.fullmatch(name):
            raise _MalformedPlaylistError(MALFORMED)
        i = equals + 1
        if i < size and text[i] == '"':
            close = text.find('"', i + 1)
            if close < 0 or "\\" in text[i + 1 : close] or _QUOTED_URI_NAME.search(text, i + 1, close):
                raise _MalformedPlaylistError(MALFORMED)
            # Depois da aspa que fecha, só a vírgula ou o fim: `URI="a"x` o yt-dlp não lê como `URI`.
            if close + 1 < size and text[close + 1] != ",":
                raise _MalformedPlaylistError(MALFORMED)
            found.append((name, text[i + 1 : close]))
            i = close + 1
        else:
            comma = text.find(",", i)
            comma = size if comma < 0 else comma
            found.append((name, text[i:comma].strip(" \t")))
            i = comma
    return found


def _is_cdm_key(attributes):
    """Tag de chave com DRM de CDM: `KEYFORMAT` Widevine/FairPlay/PlayReady ou `URI` `skd://` (FairPlay)."""
    for name, value in attributes:
        if name == "KEYFORMAT" and value.strip().lower() in CDM_KEYFORMATS:
            return True
        if name == "URI" and value.strip().lower().startswith("skd:"):
            return True
    return False


def _uri(text):
    """O endereço como o downloader nativo do yt-dlp o lê; fora do ASCII visível levanta `_MalformedPlaylistError`."""
    if not _URI_TEXT.fullmatch(text):
        raise _MalformedPlaylistError(MALFORMED)
    return text


def _tag_uris(line, upper):
    """Os endereços de uma tag `#EXT…`: os atributos `URI` e `…-URI`, com a chave de CDM recusada.

    O downloader nativo do yt-dlp acha `#EXT-X-KEY` e `#EXT-X-MAP` só pelo começo da linha, sem exigir o
    `:`, e lê os atributos de `line[11:]`: `#EXT-X-KEYXURI=…` é uma chave para ele. Essas tags sem o `:` no
    lugar são recusadas. Em toda linha `#EXT`, cada `URI=` do texto cru precisa ser um atributo `URI` achado
    aqui; o que sobra (num `#EXTINF`, numa tag sem `:`) é recusado.
    """
    raw_uris = len(_URI_NAME.findall(line))
    if raw_uris > 1 or (upper.startswith(_PREFIX_TAGS) and upper[_PREFIX_LENGTH : _PREFIX_LENGTH + 1] != ":"):
        raise _MalformedPlaylistError(MALFORMED)
    if upper.startswith("#EXTINF:") or ":" not in line:
        if raw_uris:
            raise _MalformedPlaylistError(MALFORMED)
        return []
    attributes = _attributes(line.partition(":")[2])
    if raw_uris != sum(1 for name, _ in attributes if name == "URI"):
        raise _MalformedPlaylistError(MALFORMED)
    if upper.startswith(_KEY_TAGS) and _is_cdm_key(attributes):
        raise _CdmDrmError(DRM_REFUSAL)
    uris = [_uri(value) for name, value in attributes if _is_uri_name(name)]
    _require_native_agreement(line.partition(":")[2], attributes)
    return uris


def _require_native_agreement(text, attributes):
    """Recusa a tag em que o yt-dlp acharia outros endereços que os conferidos.

    Os atributos `URI`/`…-URI` conferidos, comparados como dicionário (o último nome repetido vale, como no
    yt-dlp), têm de ser exatamente os de `parse_m3u8_attributes`, nome e valor; um nome de endereço repetido
    (`KEY-URI` duas vezes) também é recusado, porque cada leitor poderia ficar com um.
    """
    pairs = [(name, value) for name, value in attributes if _is_uri_name(name)]
    checked = dict(pairs)
    native = {key: value for key, value in _native_attributes(text).items() if _is_uri_name(key)}
    if len(checked) != len(pairs) or checked != native:
        raise _MalformedPlaylistError(MALFORMED)


def _entries(text):
    """`(uri, é_playlist)` de cada endereço citado: linhas soltas e os atributos `URI` das tags `#EXT`.

    Linhas em `\r\n`/`\r`/`\n`, as mesmas do `splitlines()` do yt-dlp (as outras quebras dele são recusadas).
    Levanta `_MalformedPlaylistError` na playlist com controle, separador Unicode, byte que não é UTF-8,
    endereço fora do ASCII, `URI` repetido ou aspas sem fechar, e `_CdmDrmError` numa chave
    de DRM de CDM. `é_playlist` marca variante e rendição, que o yt-dlp lê como outra playlist.
    """
    if _CONTROL.search(text):
        raise _MalformedPlaylistError(MALFORMED)
    after_stream_inf = False
    for raw in _LINE_BREAK.split(text):
        line = raw.strip(" \t")
        if not line:
            continue
        if not line.startswith("#"):
            yield _uri(line), after_stream_inf
            after_stream_inf = False
            continue
        upper = line.upper()
        if upper.startswith("#EXT-X-STREAM-INF:"):
            after_stream_inf = True
            continue
        if not upper.startswith("#EXT"):
            continue
        nested = upper.startswith(_PLAYLIST_TAGS)
        for uri in _tag_uris(line, upper):
            yield uri, nested


def _joined(base, uri):
    """`uri` contra `base`; o endereço que nem se monta (`https://[::1/`) volta vazio e é recusado como não público."""
    try:
        return urljoin(base, uri)
    except ValueError:
        return ""


class _Check:
    """Uma conferência: guarda os hosts já resolvidos para não repetir DNS."""

    def __init__(self, origin_host):
        self.origin_host = origin_host
        self.hosts = set()

    def address(self, url):
        """Recusa o endereço que não é HTTPS público na 443 (inclusive pelo DNS)."""
        if not http.public_url(url, allow_signed=True) or not _port_443(url):
            raise _refuse(self.origin_host, "uri_not_public", NOT_PUBLIC)
        host = _host(url)
        if host in self.hosts:
            return
        if len(self.hosts) >= HOST_LIMIT:
            raise _refuse(
                self.origin_host,
                "too_many_hosts",
                f"A playlist do vídeo cita mais de {HOST_LIMIT} hosts diferentes, e o getbrolls não confere tantos. "
                + NEXT_STEP,
            )
        try:
            http.public_destination(url)
        except ProviderError:
            raise _refuse(self.origin_host, "uri_not_public", NOT_PUBLIC) from None
        self.hosts.add(host)

    def playlist(self, url, nested):
        """Lê a playlist em `url`; devolve `(url_final, variantes)` depois de conferir cada endereço."""
        try:
            # Pelos bytes, em UTF-8, como o downloader nativo do yt-dlp: o charset declarado (`utf-16`,
            # `cp037`…) mudaria o texto conferido.
            final, text = web_text.get_text(url, PLAYLIST_MAX_BYTES, encoding="utf-8")
        except ProviderError as error:
            raise _refuse(
                self.origin_host, "playlist_unreadable", f"Não consegui ler a playlist do vídeo ({error}). {NEXT_STEP}"
            ) from None
        if not text.lstrip("﻿ \t\r\n").startswith("#EXTM3U"):
            raise _refuse(self.origin_host, "not_hls", f"O link .m3u8 não entrega uma playlist HLS. {NEXT_STEP}")
        self.address(final)
        variants = []
        try:
            for uri, is_playlist in _entries(text):
                target = _joined(final, uri)
                self.address(target)
                if is_playlist:
                    variants.append(target)
        except _CdmDrmError:
            logs.event(_log, logging.WARNING, "stream_check", host=self.origin_host, result="refused", reason="drm")
            raise RouteRefusal(DRM_REFUSAL, suggested_route=route_hint.drm()) from None
        except _MalformedPlaylistError:
            raise _refuse(self.origin_host, "malformed_playlist", MALFORMED) from None
        if variants and nested:
            raise _refuse(
                self.origin_host,
                "nested_master",
                f"Uma variante da playlist é outra playlist de variantes, e o getbrolls lê só um nível. {NEXT_STEP}",
            )
        return final, list(dict.fromkeys(variants))


def _vet_hls(url):
    check = _Check(_host(url))
    final, variants = check.playlist(url, nested=False)
    if len(variants) > VARIANT_LIMIT:
        raise _refuse(
            check.origin_host,
            "too_many_variants",
            f"A playlist do vídeo tem mais de {VARIANT_LIMIT} variantes, e o getbrolls não confere tantas. {NEXT_STEP}",
        )
    for variant in variants:
        check.playlist(variant, nested=True)
    logs.event(_log, logging.INFO, "stream_check", host=check.origin_host, result="ok", kind="hls")
    return final


def _looks_like_mp4(content_type, head):
    """Tipo de vídeo/binário e a primeira caixa ISO BMFF (bytes 4 a 8: tamanho de 4 bytes, depois o nome)."""
    return content_type.startswith(MP4_TYPES) and head[4:8] in MP4_BOXES


def _vet_mp4(url):
    host = _host(url)
    try:
        final, content_type, head = web_text.peek(url, PEEK_BYTES)
    except ProviderError as error:
        raise _refuse(host, "mp4_unreadable", f"Não consegui abrir o vídeo ({error}). {NEXT_STEP}") from None
    if not _looks_like_mp4(content_type, head):
        raise _refuse(host, "not_mp4", f"O link .mp4 não entrega um arquivo MP4. {NEXT_STEP}")
    logs.event(_log, logging.INFO, "stream_check", host=host, result="ok", kind="mp4")
    return final


def vet(url):
    """A URL que o yt-dlp pode receber (a final, depois dos redirecionamentos conferidos).

    `.m3u8`: a playlist e um nível de variantes são lidos e todo endereço citado precisa ser
    HTTPS público na 443. Outro caminho (o `.mp4`): o começo do arquivo precisa ser MP4.
    Levanta `ProviderError` com o próximo passo quando algo não passa.
    """
    if urlsplit(url).path.lower().endswith(".m3u8"):
        return _vet_hls(url)
    return _vet_mp4(url)
