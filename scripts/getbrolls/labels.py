"""Caminhos da máquina em texto para a pessoa: cada pasta conhecida vira a marca dela.

Uma mensagem, um aviso ou uma linha de log nunca leva o caminho absoluto desta máquina
(`docs/CLI_CONTRACT.md`, "Sem caminho da máquina"). A pasta é dita pela marca: `<projeto>/…`
(o projeto do comando), `<cliente>/…` (quando quem chama sabe a pasta do cliente),
`$GB_HOME/…`, `$GB_CACHE_DIR/…`, `$GB_RUNTIME_DIR/…`, `$TMPDIR/…` e `~/…`. Fora de todas,
sobra `<pasta fora do projeto>/<nome>`, nunca o caminho inteiro. O caminho que a pessoa
digitou e o comando devolve como veio não passa por aqui.
"""

import contextlib
import errno
import functools
import os
import re
import tempfile
import urllib.parse
from pathlib import Path, PurePath

from . import _paths, _tokens, redaction

OUTSIDE = "<pasta fora do projeto>"
# O `--project` como a pessoa o digitou e a pasta que ele vale (`note_typed_project`): o erro de E/S de um
# arquivo dentro dele (ou de uma pasta acima, como a pasta travada) o devolve nas palavras dela (`as_typed`).
_TYPED: list[tuple[str, tuple[Path, ...]]] = []
# Marca do caminho digitado durante a redação: sem barra, nenhum padrão de pasta a toca.
_TYPED_MARK = "\x00digitado\x00"
# O `--project` digitado só como caminho inteiro e sem nada dentro dele (`em /x/proj;`, `'~/x'`): nunca
# `/x/proj2` nem `/x/proj/brolls`, que são outros caminhos e vão pela marca.
_TYPED_END = r"(?=$|[\s;,'\")\]:]|\.(?:$|\s))"
# Teto da entrada da redação (64 KiB): um texto maior sai inteiro como `<texto omitido: N caracteres>`. Nunca
# é cortado antes de redigido: um caminho ou um token cortado no meio não casaria com o padrão e sobraria
# cru. O corte de tamanho de quem chama (o valor de log, a mensagem) vem DEPOIS da redação.
INPUT_MAX = 65_536


def anchors(project=None, extra=()):
    """`(pasta, marca)` desta máquina, para `_paths.label`: `extra` primeiro, depois o projeto
    (`project`, ou o do comando em curso), `$GB_HOME`, o cache, um `$GB_RUNTIME_DIR` explícito,
    a pasta temporária e a pasta pessoal. Nunca levanta; uma pasta que não dá para saber fica de fora."""
    from . import logs  # local: logs imports this module

    found = [(root, tag) for root, tag in extra if root is not None]
    project = project if project is not None else logs.project_root()
    if project is not None:
        found.append((Path(project).expanduser(), "<projeto>"))
        found.extend((spelling, "<projeto>") for spelling in _typed_spellings(project))
    with contextlib.suppress(OSError, RuntimeError, ValueError):
        found.append((_paths.gb_home(), "$GB_HOME"))
    with contextlib.suppress(OSError, RuntimeError, ValueError):
        from .config import cache_root  # local: config is heavy and imports runtime

        found.append((cache_root(), "$GB_CACHE_DIR"))
    runtime_dir = os.environ.get("GB_RUNTIME_DIR")
    if runtime_dir:
        found.append((Path(runtime_dir), "$GB_RUNTIME_DIR"))
    with contextlib.suppress(OSError, RuntimeError, ValueError):
        found.append((Path(tempfile.gettempdir()), "$TMPDIR"))
    with contextlib.suppress(OSError, RuntimeError, ValueError):
        found.append((Path.home(), "~"))
    return found


def _typed_spellings(project):
    """Como a pessoa escreveu o `--project` (`note_typed_project`) quando é a mesma pasta que `project`, comparada
    pelo caminho de verdade: um link para a pasta, ou, no Windows, o nome curto (`RUNNER~1`) de uma pasta de nome
    longo. Só a grafia lexical conta aqui; a resolvida é a que `project` já traz. Nunca levanta."""
    typed = _TYPED[:1]  # one read: another thread may forget the typed project between a test and an index
    if not typed:
        return ()
    try:
        held = os.path.normcase(os.path.realpath(Path(project).expanduser()))
        return tuple(
            spelling
            for spelling in typed[0][1]
            if os.path.normcase(os.path.realpath(spelling)) == held and str(spelling) != str(project)
        )
    except (OSError, RuntimeError, ValueError):
        return ()


def shown(path, *, project=None, extra=()) -> str:
    """`path` para uma mensagem: a marca da pasta mais funda que o guarda, ou
    `<pasta fora do projeto>/#<hash>.<ext>` (`_hidden_name`). Nunca o caminho absoluto; nunca levanta."""
    if not isinstance(path, (str, os.PathLike)) or not str(path):
        return str(path)  # nothing that names a folder (`None`): as it came
    found = _paths.label(path, anchors(project, extra))
    if found is not None:
        return found
    name = _hidden_name(path)
    return f"{OUTSIDE}/{name}" if name else OUTSIDE


def text(value, *, project=None, extra=()) -> str:
    """`value` (texto livre: o motivo de um `OSError`, uma linha de log) com cada grafia das
    pastas de `anchors` trocada pela marca, só como caminho inteiro; o caminho absoluto que nenhuma
    guardou (a irmã `Projeto 2` de `Projeto`, uma pasta de outro disco) sai por `outside`, com o
    hash do nome: nada do que passa por aqui leva o caminho da máquina. O caminho que a pessoa
    digitou e a mensagem devolve como veio não passa por aqui."""
    result = bounded(value)
    if result != str(value):
        return result  # omitido inteiro: nada a redigir
    result = _tokens.hide_labelled(_tokens.hide(result))  # credentials first: no folder or path rule ever sees one
    if "/" not in result and "\\" not in result and "%" not in result:
        return result  # no path in it, not even URL-encoded (once or twice): the common case of a log value
    ordered = sorted(anchors(project, extra), key=lambda pair: len(_real_parts(pair[0])), reverse=True)
    for root, tag in ordered:
        result = _paths.replace_folder(result, root, tag)
    return outside(result)


def note_typed_project(typed, project) -> None:
    """Guarda o `--project` digitado (`~/x`, `proj`) e a pasta que ele vale, como escrita e resolvida (no macOS
    `/var/…` é `/private/var/…`), para `as_typed`."""
    written = _paths._lexical(Path(project).expanduser())  # pylint: disable=protected-access
    _TYPED[:] = [(str(typed), tuple(dict.fromkeys((written, Path(os.path.realpath(written))))))]


def forget_typed_project() -> None:
    """Esquece o `--project` digitado (outro comando, ou testes)."""
    _TYPED.clear()


def as_typed(path) -> str | None:
    """`path` nas palavras da pessoa, quando é o `--project` digitado, um arquivo dentro dele (o digitado mais
    o resto) ou uma pasta acima dele (o digitado sem os últimos nomes): `None` fora disso. Só compara o texto
    dos caminhos (nada de disco): uma pasta travada acima do projeto também sai como a pessoa a escreveu."""
    if not _TYPED or not isinstance(path, (str, os.PathLike)) or not str(path):
        return None
    typed, roots = _TYPED[0]
    try:
        target = _paths._lexical(path)  # pylint: disable=protected-access
    except (TypeError, ValueError):
        return None
    for root in roots:
        if target == root or root in target.parents:
            return str(Path(typed, *target.relative_to(root).parts)) if target != root else typed
        if target in root.parents:
            above = PurePath(typed.rstrip("/\\"))
            for _level in range(len(root.parts) - len(target.parts)):
                if above.parent == above or str(above.parent) == ".":
                    return None  # above what the person typed: `proj` has no folder above it
                above = above.parent
            return str(above)
    return None


def message_of(exc, limit=None, *, project=None) -> str:
    """O texto de um erro para a mensagem do comando: redigido (`redaction.redact`), com as pastas pela marca e o
    arquivo de um `OSError` dentro do `--project` (ou acima dele) como a pessoa o digitou; só então o corte."""
    # Credentials first, on the whole text (and on the typed path it gives back): then the typed path, the labels
    # and the cut.
    value = redaction.redact(exc, limit=None)
    typed = as_typed(exc.filename) if isinstance(exc, OSError) and exc.filename else None
    if isinstance(exc, OSError) and typed is None:
        value = _known_names(exc, value, project)  # before the typed project: that one is given back as typed
    name = redaction.redact(exc.filename, limit=None) if typed is not None else ""
    if typed is not None and name in value:
        value = value.replace(name, _TYPED_MARK)
    elif _TYPED:
        # The refusal that repeats the `--project` (`Projeto não encontrado em /x/proj;`) keeps it as typed.
        typed = _TYPED[0][0]
        value = re.sub(r"(?<![\w/\\.~-])" + re.escape(typed) + _TYPED_END, lambda _found: _TYPED_MARK, value)
    else:
        typed = None
    echoed = redaction.redact(typed, limit=None) if typed is not None else _TYPED_MARK
    result = for_log(value, project=project).replace(_TYPED_MARK, echoed)
    return result if limit is None else result[:limit]


def _known_names(exc, value, project):
    """`value` (o texto de `exc`) com cada caminho que o próprio erro carrega (`filename`, `filename2`) trocado
    pela marca dele, pelo texto exato do caminho: um nome com espaço ou aspas não depende do padrão de texto livre.
    O mais longo primeiro, e só como nome inteiro: `a.mp4` não é o começo de `a.mp42`."""
    marks = {}
    for name in (exc.filename, exc.filename2):
        if not isinstance(name, (str, os.PathLike)):
            continue
        written = redaction.redact(os.fspath(name), limit=None)
        if len(written) < _MIN_NAMES or not ("/" in written or "\\" in written):
            continue
        label = shown(name, project=project)
        for spelling in (written, written.replace("\\", "\\\\")):  # as written and as `repr` shows it
            marks[spelling] = label
    for spelling in sorted(marks, key=len, reverse=True):
        value = re.sub(re.escape(spelling) + r"(?!\w)", lambda _found, label=marks[spelling]: label, value)
    return value


def bounded(value, limit=INPUT_MAX) -> str:
    """`value` como veio até `limit` caracteres; além disso, nada dele: `<texto omitido: N caracteres>`."""
    result = str(value)
    return result if len(result) <= limit else f"<texto omitido: {len(result)} caracteres>"


_DRIVE_LETTER = re.compile(r"[A-Za-z]:")


def _hidden_name(path) -> str:
    """O nome de um arquivo ou pasta fora de toda pasta conhecida, sem dizê-lo: o nome de um arquivo de
    cliente já é dado do cliente. Fica o hash com a chave da instalação e a extensão conhecida, como no
    `event=op` (`#3fa2c1d9.mp4`): dá para reconhecer o mesmo arquivo de novo, não para ler o nome."""
    from . import effects  # local: effects imports logs, which imports this module

    name = re.split(r"[/\\]", str(path).rstrip("/\\"))[-1]
    if _DRIVE_LETTER.fullmatch(name):
        return ""  # `C:\` is the root of a disk, as `/` is: no name
    # A chave que já existe; nunca a cria (um comando que só lê não grava nada): `effects.message_key`.
    return effects._hashed(name, key=effects.message_key()) if name else ""  # pylint: disable=protected-access


# Um caminho absoluto que sobrou num texto de log: POSIX (`/a/b`, nunca depois de um nome, de `/`, de `>` ou de
# `$`: URL, caminho relativo e marca ficam; depois de uma opção curta, `-o/a/b`, ou de um `:` que não abre `//`
# (`erro:/a/b`, `PATH=/bin:/a/b`), sim), com `//servidor/pasta` (dois nomes), Windows (`C:\\a\\b`, `C:/a/b`),
# UNC (`\\\\servidor\\pasta`), com prefixo estendido (`\\\\?\\C:\\a`, `\\\\?\\UNC\\s\\p`, `\\\\.\\C:\\a`), da raiz
# com uma barra invertida só (`\Users\x\a.mp4`: dois nomes ou mais, o primeiro, de até quatro palavras, com duas
# letras ou mais e não um escape `\u00e9`/`\x1b`: `\n`, `\d+` e `\w+\.mp4` são texto), escapado em JSON
# (`\/Users\/x`), subindo de pastas (`..\..\Users\x`), `file://` (maiúsculas ou minúsculas) ou a subida para fora
# de uma marca (`<projeto>/../Outro/a.mp4`, `~/../Outro`, `$TMPDIR/../Outro`): a marca só vale para dentro da pasta
# dela, então o que vem depois do `..` é o nome de outra pasta. Um só quantificador sobre uma classe: linear, sem
# nada para voltar atrás; onde o caminho acaba é `_Ends`.
_LABEL_HEAD = r"(?:<[^<>\s/]{1,40}>|\$[A-Z_]{2,20}|~)"
_START = (
    r"(?:/|[A-Za-z]:[\\/]|\\\\(?=\w|[?.]\\)|\\/(?=[\w$.-])"
    r"|\\(?=(?![ux][0-9A-Fa-f]{2,8}\\)[\w$.-]{2,}(?: [\w$.-]+){0,3}\\[\w$.~-])"
    r"|(?:\.\.[\\/])+(?=[\w$.-]{2,}[\\/][\w$.~-])"
    r"|" + _LABEL_HEAD + r"(?=(?:/[^\s/\\'\"`<>|]{1,255}){0,8}/\.\.(?:[/\\]|$|[\s'\"`<>|,;:)\]}]))"
    ")"
)
_ABSOLUTE = re.compile(
    r"\b(?i:file)://(?:localhost)?(?=/)[^\s'\"`\u201c\u201d\u2018\u2019<>|]+"
    r"|(?:(?<![\w:/.~\->\]$%\\])|(?<=(?<![\w-])-[A-Za-z])|(?<=(?<![\w-])-[A-Za-z]{2})|(?<=:)(?=/[^/]))"
    + _START
    + r"[^\s'\"`\u201c\u201d\u2018\u2019<>|:]+"
)
# Um caminho codificado como URL (`%2FUsers%2Fx%2Fa.mp4`, uma ou duas vezes): a corrida de caracteres de URL
# que tem uma barra codificada. Só começa no começo da corrida: linear.
_ENCODED = re.compile(r"(?<![\w%.~+-])(?=[\w.~%+-]*%(?:25)*(?:2[Ff]|5[Cc]))[\w.~%+-]+")
_TRAILING = ",;:()[]{}."
# Onde acaba um caminho de texto livre fora de toda pasta conhecida (`_Ends`): o nome pode ter espaço e o texto
# não diz onde ele acaba, então o caminho vale do começo até o fim da linha, ou até o fecho de quem o abriu
# (aspas, colchete, parêntese ou chave logo antes dele). Sem quem o abra, vale só até um `: ` ou um `; ` (dois
# pontos ou ponto e vírgula e um branco): é onde as mensagens põem o motivo (`/x/a.mp4: Invalid data found`) ou
# o resto da frase (`em /x/proj; nenhum arquivo foi criado`), e o que vem depois fica, desde que não haja barra
# depois dele, na mesma linha: com barra (`Cliente; Parte 1; Final/a.mp4`) o nome da pasta continua e o caminho
# também. Um `C:\` não é isso, nem um `:` ou `;` colado ao nome. Perder a frase depois do caminho é aceito;
# deixar sobrar um pedaço do nome, não. Quem sabe o nome (o `filename` de um `OSError`) o troca pelo texto exato
# antes disto.
_LINE_END = re.compile(r"[\r\n\x0b\x0c\x1c-\x1e\x85\u2028\u2029]")
_REASON = re.compile(r"[:;]\s")
_CLOSERS = {
    "'": "'",
    '"': '"',
    "`": "`",
    "\u201c": "\u201d",
    "\u2018": "\u2019",
    "(": ")",
    "[": "]",
    "{": "}",
    "<": ">",
}
_QUOTES = frozenset("'\"`\u201c\u2018")
# Um nome só a partir da raiz (`/4`, `/tmp`) diz pouco e quase nunca é caminho: o caminho tem dois ou mais.
_MIN_NAMES = 2
# Quantas vezes uma corrida de URL é decodificada (`%2525252F` é uma barra codificada quatro vezes).
_DECODE_ROUNDS = 8
# A primeira pasta de um caminho POSIX de verdade: o que existe na raiz deste disco mais as raízes de
# costume em outro sistema. `/v1/search` (uma rota) e `/review/save` (uma rota de servidor) não são disco.
_KNOWN_ROOTS = frozenset(
    {"Applications", "Library", "Network", "System", "Users", "Volumes", "cygdrive", "etc", "home", "media"}
    | {"mnt", "net", "opt", "private", "root", "run", "srv", "tmp", "usr", "var", "workspace", "workspaces"}
)


@functools.lru_cache(maxsize=1)
def _roots() -> frozenset[str]:
    """`_KNOWN_ROOTS` e os nomes da raiz deste disco, sempre sem diferença de maiúsculas."""
    # O texto vem de qualquer sistema (URL codificada, Windows, log de outra máquina), não só deste disco.
    try:
        here = {child.name for child in Path(os.sep).iterdir()}
    except OSError:
        here = set()  # a root it cannot list: only the usual names
    names = _KNOWN_ROOTS | here
    return frozenset(name.casefold() for name in names)


def _climbs_out(rest) -> bool:
    """`rest` (o que vem depois de `<marca>/`) sobe acima da pasta da marca: mais `..` do que nomes até ali."""
    depth = 0
    for part in re.split(r"[/\\]", rest):
        if part == "..":
            depth -= 1
            if depth < 0:
                return True
        elif part not in ("", "."):
            depth += 1
    return False


def _a_path(core) -> bool:
    """`core` é caminho de disco: `file://`, UNC, `//servidor/pasta`, uma subida para fora de uma marca ou dois
    nomes ou mais a partir da raiz (`/Users/x`, `C:\\a\\b`), com a primeira pasta POSIX em `_roots`. `/4`,
    `/tmp` sozinho e `/v1/search` ficam."""
    if core.casefold().startswith(("file:", "\\\\")):
        return True
    head = re.match(_LABEL_HEAD + "/", core)
    if head is not None:
        return _climbs_out(core[head.end() :])
    core = core.replace("\\/", "/") if core.startswith("\\/") else core  # a JSON-escaped slash
    core = re.sub(r"^(?:\.\.[\\/])+", "", core)  # a climb from where the text stands: the root is what counts
    names = [name for name in re.split(r"[/\\]", core[2:] if core[1:2] == ":" else core) if name]
    windows = core[1:2] == ":" or core[0] == "\\"
    # `C:\Program Files\x` has one name in its first word: a drive or a backslash root is a path from the first name.
    if len(names) < (1 if windows else _MIN_NAMES):
        return False
    if windows:
        return True
    if core.startswith("//"):
        return True  # `//servidor/pasta`: a network share, whatever the server is called
    return names[0].casefold() in _roots()


def _decoded_path(run):
    """A corrida de URL `run` (`%2FUsers%2Fx%2Fa.mp4`) como o caminho que ela esconde, decodificada até ficar
    estável (no máximo `_DECODE_ROUNDS` vezes); `None` quando o que sai não é um caminho de disco."""
    decoded = run
    for _round in range(_DECODE_ROUNDS):
        again = urllib.parse.unquote(decoded)
        if again == decoded:
            break
        decoded = again
    if not (re.match(_START, decoded) or decoded.casefold().startswith("file://")):
        return None
    return decoded if _a_path(decoded.rstrip(_TRAILING)) else None


def _marked(path) -> str | None:
    """O caminho `path` (sem a pontuação do fim) como `<pasta fora do projeto>/<nome>`, ou `None` quando não é
    caminho de disco: uma barra solta, uma rota ou uma fração."""
    name = re.split(r"[/\\]", path.rstrip("/\\"))[-1]
    if not name or not any(char.isalnum() for char in name) or not _a_path(path):
        return None
    return f"{OUTSIDE}/{_hidden_name(path)}"


class _Ends:  # pylint: disable=too-few-public-methods
    """Onde acaba cada caminho de texto livre de um mesmo texto (`end`). O fim da linha e o próximo `: ` ou `; ` são
    guardados: os começos chegam em ordem, então cada um é procurado uma vez só por texto (linear, mesmo com
    milhares de caminhos na mesma linha)."""

    def __init__(self, text_):
        self.text = text_
        self._line = -1
        self._reason = -1
        self._separators_line = -1
        self._separator = -1

    def _next(self, pattern, start, known):
        if known >= start:
            return known
        found = pattern.search(self.text, start)
        return found.start() if found else len(self.text)

    def _last_separator(self, start):
        """Onde está a última barra (`/` ou `\\`) da linha atual, procurada a partir de `start` (-1 sem barra).
        Guardada por linha: os começos chegam em ordem, então cada linha é varrida uma vez só."""
        if self._separators_line != self._line:
            self._separators_line = self._line
            self._separator = max(self.text.rfind("/", start, self._line), self.text.rfind("\\", start, self._line))
        return self._separator

    def _reason_end(self, start):
        """O primeiro `: ` ou `; ` depois de `start` sem nenhuma barra depois dele, até o fim da linha: é a frase do
        motivo. Com barra em qualquer ponto depois, na mesma linha, o nome da pasta ainda pode não ter acabado
        (`Cliente; Parte 1; Final/a.mp4`) e o caminho segue; sem terminador assim, vai até o fim da linha.
        Fica de fora o que nenhuma regra separa: um nome de arquivo com `; ` e sem barra depois
        (`Clip; take 2.mp4: erro`) é igual a uma frase."""
        position = start
        while True:
            self._reason = self._next(_REASON, position, self._reason)
            if self._reason >= self._line:
                return self._line
            after = self._reason + 2
            if self._last_separator(after) < after:
                return self._reason
            position = after

    def end(self, start):
        """Onde acaba o caminho que começa em `start`: no fecho de quem o abriu (a aspa, com a barra invertida que
        escapa; o colchete, o parêntese ou a chave, contando os que abrem dentro) ou, sem fecho, no fim da linha;
        sem quem abra, no primeiro `: ` ou `; ` sem barra depois dele na linha (`_reason_end`) ou no fim da linha."""
        self._line = self._next(_LINE_END, start, self._line)
        opener = self.text[start - 1] if start else ""
        closer = _CLOSERS.get(opener)
        if closer is None:
            return self._reason_end(start)
        depth, position = 0, start
        while position < self._line:
            char = self.text[position]
            if char == "\\" and opener in _QUOTES:
                position += 2
                continue
            if char == closer:
                if depth == 0:
                    return position
                depth -= 1
            elif char == opener != closer:
                depth += 1
            position += 1
        return self._line


def outside(value) -> str:
    """`value` (uma linha de log ou do diagnostics.jsonl, já com `text`) com cada caminho absoluto que
    nenhuma pasta conhecida guardou trocado por `<pasta fora do projeto>/<nome>`: no log, nem a
    pasta de fora fica (`docs/CLI_CONTRACT.md`). A pontuação do fim volta depois do nome. A troca pode
    alongar o texto (`/a` vira `<pasta fora do projeto>/#…`): o resultado passa de novo pelo teto."""

    def encoded(found):
        decoded = _decoded_path(found.group(0))
        label = _marked(decoded.rstrip(_TRAILING)) if decoded is not None else None
        return found.group(0) if label is None else label

    result = bounded(value)
    if result != str(value):
        return result
    result = _tokens.hide_labelled(_tokens.hide(result))
    if "/" not in result and "\\" not in result and "%" not in result:
        return bounded(result)  # no path in it, not even URL-encoded: the common case of a log value
    result = _ENCODED.sub(encoded, result)
    ends = _Ends(result)
    pieces, position = [], 0
    for found in _ABSOLUTE.finditer(result):
        start = found.start()
        if start < position:
            continue  # inside a path that already ran to its end
        if _marked(found.group(0).rstrip(_TRAILING)) is None:
            continue  # a route, a fraction, a lone root: nothing that names a disk
        end = ends.end(start)
        path = result[start:end]
        core = path.rstrip(_TRAILING)
        pieces.append(result[position:start] + (_marked(core) or OUTSIDE) + path[len(core) :])
        position = end
    pieces.append(result[position:])
    return bounded("".join(pieces))


def for_log(value, limit=None, *, project=None) -> str:
    """`value` para o log, o diagnóstico ou a mensagem: as pastas pela marca (`text`, com `project` ou o projeto
    do comando em curso), o que sobrou de fora pelo hash (`outside`) e só então o corte em `limit`: um corte
    antes deixaria o começo de um caminho cru."""
    result = outside(text(value, project=project))
    return result if limit is None else result[:limit]


def labelled(value, *, project=None):
    """`value` (um JSON: dict, lista, texto) com `text` e depois `outside` aplicados a cada texto dentro dele."""
    if isinstance(value, dict):
        return {key: labelled(item, project=project) for key, item in value.items()}
    if isinstance(value, list):
        return [labelled(item, project=project) for item in value]
    return outside(text(value, project=project)) if isinstance(value, str) else value


def reason(exc, *, project=None) -> str:
    """O motivo de um erro para uma mensagem: de um `OSError`, só o texto do sistema
    (`strerror`), que não repete o caminho do arquivo; de outro erro, `text(exc)`."""
    if isinstance(exc, OSError):
        return exc.strerror or type(exc).__name__
    return text(exc, project=project)


# Motivo em português de cada `errno` conhecido; os outros caem no texto do sistema.
OS_REASONS = {
    errno.EACCES: "sem permissão",
    errno.EPERM: "sem permissão",
    errno.EROFS: "o disco está somente leitura",
    errno.ENOSPC: "o disco está cheio",
}
_OS_REASONS_MORE = {
    errno.EEXIST: "o arquivo já existe",
    errno.ENAMETOOLONG: "o nome é longo demais",
}
# O que fazer, conforme o `errno`; o resto pede só para conferir e repetir.
_OS_ADVICE = {
    errno.EACCES: "Confira as permissões",
    errno.EPERM: "Confira as permissões",
    errno.ENOSPC: "Libere espaço em disco",
    errno.EROFS: "Use um disco que aceite gravação",
    errno.ENAMETOOLONG: "Use um nome mais curto",
    errno.EEXIST: "Escolha um nome que ainda não exista",
}


def os_message(exc, *, project=None) -> str:
    """A mensagem de um `OSError` do sistema (permissão, nome longo, disco): onde e o motivo em português, sem o
    `[Errno N]` nem o caminho da máquina. O arquivo sai como a pessoa digitou o `--project` ou pela marca da pasta."""
    detail = OS_REASONS.get(exc.errno) or _OS_REASONS_MORE.get(exc.errno) or exc.strerror or type(exc).__name__
    advice = _OS_ADVICE.get(exc.errno, "Confira o caminho e as permissões")
    if exc.filename and isinstance(exc.filename, (str, os.PathLike)):
        where = as_typed(exc.filename) or shown(exc.filename, project=project)
        return f"Não consegui acessar {where} ({detail}). {advice} e repita."
    return f"Erro de E/S ({detail}). {advice} e repita."


def _real_parts(path):
    try:
        return Path(path).resolve().parts
    except (OSError, RuntimeError, ValueError):
        return Path(path).parts
