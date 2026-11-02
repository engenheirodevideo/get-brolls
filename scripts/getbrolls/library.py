"""Biblioteca global de aprendizados: ponteiro editorial, nunca licença nem aprovação.

O que mora aqui atravessa projetos porque é memória de trabalho — que busca
acertou, que fonte falhou, que trecho já serviu e por quê. Direito de uso e
aprovação humana **não** atravessam: continuam por projeto, por revisão e por
intervalo, e toda resposta daqui repete isso em `rights_not_transferable`.

Também não atravessa um cliente: o que foi guardado a partir de um projeto de cliente
(`client` do `project.json`) leva o `client` na entrada, e só volta para projetos do mesmo
cliente. Entrada sem cliente é da própria pessoa e vale em qualquer projeto; entrada de outro
cliente nunca aparece (`search`, `hints`, contadores de fonte).
"""

# pylint: disable=missing-function-docstring,too-many-locals
# Legado: ocorrências pré-existentes (corpo idêntico ao código anterior à 2.6.0).

import contextlib
import errno
import hashlib
import json
import logging
import os
import stat
import time
import unicodedata
import uuid
from pathlib import Path

from . import effects, json_read, labels, layout, logs, roteiro_frontmatter, versioning
from ._paths import strict_exists
from .errors import LockedError
from .memory import load_references
from .models import now
from .rules import home_dir
from .sdk import jsonschema, schemas

_log = logs.get(__name__.rsplit(".", 1)[-1])


def empty_index():
    """Biblioteca vazia, na versão corrente do schema (cada chamada devolve um objeto novo)."""
    return {
        "schema_version": schemas.version("library_index"),
        "assets": [],
        "queries": [],
        "providers": {},
        "preferences": [],
    }


OFF_NOTE = (
    "Biblioteca desligada por GB_LIBRARY=off: nada foi lido nem gravado em "
    "~/.getbrolls/library. Apague a variável para voltar a usá-la."
)


def enabled():
    return (os.environ.get("GB_LIBRARY") or "").strip().lower() != "off"


def library_dir():
    return home_dir() / "library"


def index_path():
    return library_dir() / "index.json"


def _roteiro_names_a_client(project):
    """O ROTEIRO.md do getbrolls tem uma linha `cliente:` com valor que o parser descartou."""
    if not roteiro_frontmatter.is_roteiro(project):
        return False
    try:
        text = roteiro_frontmatter.roteiro_path(project).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return False
    lines = text.replace("\r\n", "\n").split("\n")
    meta, _, errors = roteiro_frontmatter.parse_frontmatter(lines)
    return "cliente" not in meta and any(
        0 < number <= len(lines) and roteiro_frontmatter.frontmatter_key(lines[number - 1]) == "cliente"
        for number, _ in errors
    )


def project_client(project):
    """`(slug, problema)` do cliente que o `project.json` de `project` declara.

    Sem projeto, sem `project.json` ou sem `client` nele, o slug é `None`: contexto da
    própria pessoa. `problema` é o motivo quando o arquivo não pôde ser lido (o slug é
    `None` e quem grava recusa, em vez de gravar uma entrada que valeria para todos).
    Um projeto de layout 0 (sem `project.json`) tem o cliente no `cliente` do ROTEIRO.md, como
    `migrate` e `export` o leem; um `cliente` que não é slug é `problema`, nunca contexto da pessoa.
    """
    if project is None:
        return None, None
    found = layout.info(project)
    if found.problem:
        return None, found.problem
    if found.doc is None:
        from .migrate import _roteiro_client  # local: migrate importa clients e runtime

        declared = _roteiro_client(Path(project))
        if declared is None and _roteiro_names_a_client(Path(project)):
            return (
                None,
                'o "cliente" do ROTEIRO.md não é um slug válido; rode `roteiro --action check --project PROJETO`',
            )
    else:
        declared = found.doc.get("client")
    try:
        return layout.check_client(declared, "client"), None
    except ValueError as error:
        return None, str(error)


def _recording_client(project):
    """O cliente sob o qual uma entrada nova é gravada; recusa quando não dá para saber."""
    client, problem = project_client(project)
    if problem:
        raise ValueError(
            f"O cliente deste projeto não pôde ser lido do project.json ou do ROTEIRO.md ({problem}). Conserte-o "
            "antes de guardar na biblioteca: sem ele não há como saber de qual cliente é a entrada."
        )
    return client


def _visible(item, client):
    """A entrada é da própria pessoa (sem cliente) ou do cliente de quem pergunta."""
    return item.get("client") in (None, client)


def _scope(client):
    """O campo `scope` da resposta: só aparece dentro de um projeto de cliente."""
    return {"scope": f"client:{client}"} if client else {}


def answer(**extra):
    """Toda resposta da biblioteca diz, na cara, que direito não viaja com ela."""
    return {"rights_not_transferable": True, "enabled": enabled(), **extra}


def _off(**extra):
    return {**answer(**extra), "note": OFF_NOTE}


def load_index():
    path = index_path()
    if not enabled() or not strict_exists(path):
        return empty_index()
    try:
        data = json_read.read(path)
    except json_read.JsonReadError as exc:
        if exc.reason == "not_regular":
            # Pasta ou FIFO no lugar do índice é E/S (IO_ERROR), como a leitura direta respondia: nada de apagar.
            raise OSError(errno.EINVAL, "não é um arquivo comum", str(path)) from None
        raise ValueError(
            f"{labels.shown(path)} está com JSON inválido. Preserve o arquivo e conserte-o, ou "
            "apague-o para começar uma biblioteca nova; nenhum projeto depende dele."
        ) from None
    current = schemas.version("library_index")
    invalid = (
        f"{labels.shown(path)} não é uma biblioteca da versão {current}. Preserve o "
        "arquivo e conserte-o, ou apague-o para começar uma biblioteca nova."
    )
    if not isinstance(data, dict):
        raise ValueError(invalid)  # noqa: TRY004 - contrato da CLI: dado inválido é ValueError (INVALID_DATA)
    versioning.read_version(data, labels.shown(path), current, invalid=invalid)
    for key, value in empty_index().items():
        data.setdefault(key, value)
    if jsonschema.errors(data, schemas.load("library_index")):
        raise ValueError(invalid)
    return data


def _write_private(path, text):
    """Escrita atômica com 0600: a biblioteca é pessoal e nunca fica meio gravada.

    O temporário leva pid e um sufixo aleatório: duas escritas simultâneas nunca
    disputam o mesmo arquivo nem apagam o `.tmp` uma da outra.
    """
    effects.mkdir(path.parent, parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    with effects.op("fs.write", path):
        try:
            with open(  # noqa: PTH123 - wraps an os.open() fd (explicit flags/mode), no Path equivalent
                os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
                "w",
                encoding="utf-8",
                newline="\n",
            ) as f:
                effects.make_private(temp)
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            temp.chmod(0o600)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def save_index(data):
    _write_private(
        index_path(), json.dumps(versioning.stamp(data, schemas.version("library_index")), ensure_ascii=False, indent=2)
    )


# Trava de diretório: um arquivo criado com O_EXCL, que funciona igual no Windows.
LOCK_TIMEOUT_S = 10.0
STALE_LOCK_S = 60.0
# Uma sobra `index.lock.stale.*` mais velha que isso nenhum processo trataria como trava nova: quem renomeia
# só devolve ao nome uma trava de até STALE_LOCK_S, e a folga cobre a janela dele e o relógio de outra máquina.
STALE_GRAVE_S = 2 * STALE_LOCK_S
LOCK_NAME = "index.lock"
MAX_QUERIES = 500


def _create_lock(path):
    """Cria a trava do índice com O_EXCL e devolve o `(st_dev, st_ino)` dela; `FileExistsError` se já existe."""
    # effect-exempt: O_EXCL lock file of the library index; a lock that stays busy logs library_lock
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        created = os.fstat(fd)
    finally:
        os.close(fd)
    return created.st_dev, created.st_ino


def _recreate_lock(path, stamp):
    """Devolve a trava (arquivo vazio) ao nome sem hardlink, com a idade de `stamp`: O_EXCL não sobrescreve.

    `FileExistsError` quer dizer que o nome já está tomado; qualquer outro `OSError` também sobe, e o nome fica vazio.
    """
    _create_lock(path)
    with contextlib.suppress(OSError):
        os.utime(path, (stamp.st_atime, stamp.st_mtime))


def _restore_fresh(grave, path, stamp):
    """Põe de volta no nome a trava nova de outro processo, tirada por engano; devolve `(erro, de_lado)`.

    Com `link` a trava volta com o mesmo inode. Sem ele (exFAT, algumas pastas de rede) é recriada com O_EXCL
    e a mesma idade: o dono dela solta outro inode e não a apaga, então ela fica sem dono até ficar velha, como
    a original ficaria (limite conhecido sem hardlink; só disponibilidade, a exclusão vale). Se um terceiro processo
    já pegou o nome (`FileExistsError`), a trava do outro fica de lado no `grave`, nunca apagada. Qualquer outra falha
    deixa o nome vazio com o dono ainda lá dentro: `de_lado` é verdadeiro e quem chamou não pode criar a trava.
    """
    try:
        effects.link(grave, path)
    except OSError as error:
        try:
            _recreate_lock(path, stamp)
        except FileExistsError:
            logs.event(_log, logging.WARNING, "library_lock", fresh_lock_kept=True, reason=labels.reason(error))
            return None, False
        except OSError as failed:
            logs.event(_log, logging.WARNING, "library_lock", fresh_lock_aside=True, reason=labels.reason(failed))
            return failed, True
        logs.event(_log, logging.WARNING, "library_lock", fresh_lock_restored=True, reason=labels.reason(error))
    with contextlib.suppress(OSError):
        effects.unlink(grave)
    return None, False


def _take_over_stale(path):
    """Tira do caminho uma trava velha sem nunca apagar uma trava nova; devolve `(erro, de_lado)`.

    A trava é renomeada para um nome só deste processo e só então reexaminada, uma vez: se outro processo
    a trocou por uma trava nova entre o exame e a renomeação, ela volta ao lugar (`_restore_fresh`) em vez de
    apagada. `erro` é o `OSError` que impediu a troca: quem falha em renomear não gira, segue para o prazo.
    `de_lado` diz que a trava nova de outro processo não pôde voltar ao nome: entrar agora quebraria a exclusão.
    """
    grave = path.with_name(f"{path.name}.stale.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    try:
        effects.rename(path, grave)
    except FileNotFoundError:
        return None, False  # outro processo já a tirou
    except OSError as error:
        return error, False
    try:
        stamp = grave.stat()
    # lenient: a taken-over lock it cannot stat is treated as the stale one it was taken for
    except OSError:
        stamp = None
    if stamp is not None and time.time() - stamp.st_mtime <= STALE_LOCK_S:
        return _restore_fresh(grave, path, stamp)
    with contextlib.suppress(OSError):
        effects.unlink(grave)
    return None, False


def _sweep_graves(folder):
    """Apaga as sobras `index.lock.stale.*` mais velhas que STALE_GRAVE_S; só roda com a trava na mão.

    Uma sobra fica quando um processo morre entre renomear e apagar, ou quando a trava nova de outro ficou de
    lado. Uma recente pode estar no meio da troca de outro processo e fica; uma velha, esse outro processo
    também a apagaria. Só arquivo comum, nunca pasta nem link; quem não sai fica para a próxima vez.
    """
    try:
        graves = list(folder.glob(f"{LOCK_NAME}.stale.*"))
    except OSError:
        return  # a pasta não deu para listar: a escrita segue, as sobras ficam para a próxima vez
    swept = 0
    now_s = time.time()
    for grave in graves:
        try:
            seen = grave.lstat()
        # lenient: a leftover that vanished or cannot be read is left for the next sweep
        except OSError:
            continue
        if not stat.S_ISREG(seen.st_mode) or now_s - seen.st_mtime <= STALE_GRAVE_S:
            continue
        try:
            effects.unlink(grave)
        except OSError:
            continue
        swept += 1
    if swept:
        logs.event(_log, logging.INFO, "library_lock", stale_swept=swept)


def _lock_identity(path):
    """`(st_dev, st_ino)` do arquivo no nome, lido por `fstat` como na criação (no Windows o `stat` diverge)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        seen = os.fstat(fd)
    finally:
        os.close(fd)
    return seen.st_dev, seen.st_ino


def _release_lock(path, owned):
    """Apaga a trava só se ainda for a que este processo criou (`st_dev`/`st_ino`), nunca a de outro pelo nome."""
    try:
        found = _lock_identity(path)
    except OSError:
        return  # sumiu ou não dá para identificar: fica, e expira em STALE_LOCK_S como qualquer outra
    if found != owned:
        logs.event(_log, logging.WARNING, "library_lock", foreign_lock_kept=True)
        return
    with contextlib.suppress(OSError):
        effects.unlink(path)


def _is_stale(path):
    """A trava no nome passou de STALE_LOCK_S; uma que não dá para examinar conta como nova (acaba em LOCKED)."""
    try:
        return time.time() - path.stat().st_mtime > STALE_LOCK_S
    # lenient: stale-lock probe: a lock it cannot stat counts as fresh, so the wait ends in LOCKED
    except OSError:
        return False


@contextlib.contextmanager
def _locked():
    """Serializa ler → mudar → gravar; sem isso duas escritas perdem uma entrada."""
    path = library_dir() / LOCK_NAME
    effects.mkdir(path.parent, parents=True, exist_ok=True)
    started = time.monotonic()
    deadline = started + LOCK_TIMEOUT_S
    blocked, aside = None, False
    while True:
        # Com a trava nova de outro processo de lado e o nome vazio, criar a trava aqui entraria junto com ele.
        if not aside:
            try:
                owned = _create_lock(path)
                break
            except FileExistsError:
                if _is_stale(path):
                    # Sobrou de um processo que morreu no meio: ninguém a renova.
                    blocked, aside = _take_over_stale(path)
                    if blocked is None:
                        continue
        if time.monotonic() > deadline:
            logs.event(
                _log,
                logging.WARNING,
                "library_lock",
                waited_ms=round((time.monotonic() - started) * 1000),
                timeout=True,
            )
            raise LockedError(
                f"{labels.shown(path)} está travado há tempo demais por outro getbrolls. "
                f"Espere a outra execução terminar ou apague esse arquivo.{_locked_detail(blocked, aside)}"
            ) from None
        time.sleep(0.02)
    logs.event(
        _log,
        logging.DEBUG,
        "library_lock",
        waited_ms=round((time.monotonic() - started) * 1000),
        timeout=False,
    )
    try:
        _sweep_graves(path.parent)
        yield
    finally:
        _release_lock(path, owned)


def _locked_detail(blocked, aside):
    """O motivo, no fim da mensagem de LOCKED, quando a troca de uma trava velha falhou."""
    if blocked is None:
        return ""
    if aside:
        return f" A trava de outra execução não pôde voltar ao lugar: {labels.reason(blocked)}."
    return f" Não foi possível remover a trava velha: {labels.reason(blocked)}."


def _update(mutate):
    """Aplica uma mudança no índice inteiro sob trava, e devolve o que ela retornar."""
    with _locked():
        data = load_index()
        result = mutate(data)
        save_index(data)
    return result


def _write_note(text):
    """Nota longa vira arquivo; o índice guarda só o caminho relativo."""
    digest = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]
    relative = f"notes/{digest}.md"
    _write_private(library_dir() / relative, text.strip() + "\n")
    return relative


def _normalise(text):
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(c if c.isalnum() else " " for c in text)


def _tokens(text):
    return {t for t in _normalise(text).split() if t}


def _count_provider(data, provider, outcome, at, client):
    """Soma ao contador gravado da fonte. Só a própria pessoa escreve aqui: o contador de um cliente
    se refaz das buscas dele (`_provider_counts`), e assim nenhuma consulta de cliente sobrevive a
    ela no contador quando o teto de buscas a descarta."""
    if client is not None:
        return
    counts = data["providers"].setdefault(provider, {"outcomes": {"hit": 0, "miss": 0}, "last_at": None})
    counts["outcomes"][outcome] = counts["outcomes"].get(outcome, 0) + 1
    counts["last_at"] = at


# pylint: disable-next=too-many-arguments
def learn_query(  # noqa: PLR0913 - the four fields of a search record, `auto`, and the project that names its client
    query, provider, outcome, note=None, auto=False, *, project=None
):
    """Registra que uma busca acertou ou não numa fonte (sob o cliente do projeto, se ele tiver um)."""
    query = (query or "").strip()
    if not query:
        raise ValueError("Informe a busca real em --query.")
    if outcome not in ("hit", "miss"):
        raise ValueError("--outcome aceita hit ou miss.")
    provider = (provider or "").strip()
    if not provider:
        raise ValueError("Informe a fonte em --provider.")
    if not enabled():
        result = _off(entry=None)
        logs.event(_log, logging.INFO, "library", action="learn", entries=0, disabled=True)
        return result
    client = _recording_client(project)
    note_path = _write_note(note) if (note or "").strip() else None

    def mutate(data):
        at = now()
        same = next(
            (
                q
                for q in data["queries"]
                if (q["query"], q["provider"], q["outcome"], bool(q.get("auto")), q.get("client"))
                == (query, provider, outcome, bool(auto), client)
            ),
            None,
        )
        if same is not None:
            # A mesma busca repetida é um contador, não uma linha nova: senão a
            # biblioteca cresce sem parar e cada `search` reescreve o índice inteiro.
            same["count"] = same.get("count", 1) + 1
            same["at"] = at
            same["note"] = note_path or same.get("note")
            entry = same
        else:
            entry = {
                "query": query,
                "provider": provider,
                "outcome": outcome,
                "count": 1,
                "at": at,
                "note": note_path,
            }
            if auto:
                # Marcado porque ninguém digitou: veio de uma falha real de provedor.
                entry["auto"] = True
            if client:
                entry["client"] = client
            data["queries"].append(entry)
        if len(data["queries"]) > MAX_QUERIES:
            # Teto: o que ninguém digitou sai primeiro, do mais antigo para o mais novo.
            order = sorted(
                range(len(data["queries"])),
                key=lambda i: (
                    not data["queries"][i].get("auto"),
                    data["queries"][i].get("at") or "",
                ),
            )
            drop = set(order[: len(data["queries"]) - MAX_QUERIES])
            data["queries"] = [q for i, q in enumerate(data["queries"]) if i not in drop]
        _count_provider(data, provider, outcome, at, client)
        return entry

    result = answer(entry=_update(mutate))
    logs.event(_log, logging.INFO, "library", action="learn", entries=1, disabled=False)
    return result


def learn_preference(text, by=None, project=None):
    """Guarda uma preferência editorial dita por uma pessoa (sob o cliente do projeto, se ele tiver um)."""
    text = (text or "").strip()
    if len(text) < 5:  # noqa: PLR2004 - matches the "pelo menos 5 caracteres" message below
        raise ValueError("Escreva a preferência como ela foi dita, com pelo menos 5 caracteres.")
    if not enabled():
        result = _off(entry=None)
        logs.event(_log, logging.INFO, "library", action="learn", entries=0, disabled=True)
        return result
    entry = {"text": text, "by": (by or "").strip() or None, "at": now()}
    client = _recording_client(project)
    if client:
        entry["client"] = client

    def mutate(data):
        data["preferences"].append(entry)
        return entry

    result = answer(entry=_update(mutate))
    logs.event(_log, logging.INFO, "library", action="learn", entries=1, disabled=False)
    return result


def asset_id(source_url, clip_signature):
    return hashlib.sha256(json.dumps([source_url, clip_signature], sort_keys=True).encode()).hexdigest()[:16]


def learn_from_candidate(project, ident, shot=None):
    """Copia o ponteiro editorial de um candidato já memorizado no projeto.

    A fonte é `references.json` (a decisão humana já registrada por `remember`)
    mais o candidato do ledger. Nada de `rights.evidence`, nada de `approval`:
    o que vai para a biblioteca não permite coletar nada em lugar nenhum.
    """
    from .ledger import Ledger
    from .review import project_id

    ledger = Ledger(project)
    c = ledger.get(ident)
    items = load_references(ledger.root).get("items", [])
    references = [r for r in items if r.get("id") == ident]
    if not references:
        raise ValueError(
            "Este candidato ainda não tem decisão registrada na memória do projeto. "
            "Rode `remember --candidate " + ident + " --decision approved --reason "
            '"…" --by NOME --project PROJETO` (ou `--decision rejected`) antes de guardá-lo na biblioteca.'
        )
    reference = references[-1]
    if not enabled():
        result = _off(entry=None)
        logs.event(_log, logging.INFO, "library", action="learn", entries=0, disabled=True)
        return result
    from .models import signature

    client = _recording_client(project)
    entry = {
        "asset_id": asset_id(c.get("source_url"), signature(c)),
        "source_url": c.get("source_url"),
        "provider": c.get("provider"),
        "title": c.get("title"),
        "creator": c.get("creator", {}).get("name"),
        "license_name": c.get("rights", {}).get("license_name"),
        "license_url": c.get("rights", {}).get("license_url"),
        # Só o nome da base declarada no projeto: é pista de onde procurar a
        # condição de uso, nunca a prova dela.
        "rights_basis": c.get("rights", {}).get("basis"),
        "clip": {
            "start_s": c["segment"]["start_s"],
            "end_s": c["segment"]["end_s"],
            "signature": signature(c),
        },
        "tags": sorted(_tokens(c.get("title")) | _tokens(c.get("narration")))[:12],
        "decision": reference["decision"],
        "reason": reference["reason"],
        "by": reference["by"],
        "at": now(),
        "used_by": [],
    }
    if client:
        entry["client"] = client
    use = {"project_id": project_id(ledger), "shot": shot or c.get("shot"), "at": now()}

    def mutate(data):
        for existing in data["assets"]:
            if existing["asset_id"] == entry["asset_id"] and existing.get("client") == client:
                existing["used_by"].append(use)
                existing["decision"] = entry["decision"]
                existing["reason"] = entry["reason"]
                existing["by"] = entry["by"]
                existing["at"] = entry["at"]
                return existing
        entry["used_by"].append(use)
        data["assets"].append(entry)
        return entry

    result = answer(entry=_update(mutate))
    logs.event(_log, logging.INFO, "library", action="learn", entries=1, disabled=False)
    return result


def _score(term_tokens, text):
    found = term_tokens & _tokens(text)
    return len(found) / len(term_tokens) if term_tokens else 0.0


def _provider_counts(data, named, client):
    """Contadores das fontes `named`: os gravados (só da própria pessoa) e, num projeto de cliente,
    mais os das buscas visíveis desse cliente (`data["queries"]` já vem filtrado)."""
    counted = {
        name: {"outcomes": dict(counts["outcomes"]), "last_at": counts["last_at"]}
        for name, counts in data["providers"].items()
        if name in named
    }
    for q in data["queries"] if client else ():
        if q.get("client") == client and q.get("provider") in named:
            mine = counted.setdefault(q["provider"], {"outcomes": {"hit": 0, "miss": 0}, "last_at": None})
            mine["outcomes"][q["outcome"]] = mine["outcomes"].get(q["outcome"], 0) + q.get("count", 1)
            mine["last_at"] = max(mine["last_at"] or "", q.get("at") or "") or None
    return counted


def search(term, limit=5, project=None):
    """Procura assets, buscas e preferências que já falaram desse assunto.

    Responde só pelo contexto de quem pergunta: dentro de um projeto de cliente, o que foi guardado
    sob esse cliente ou sem cliente; fora de um, só o que foi guardado sem cliente. A resposta diz
    `scope: "client:<slug>"` quando há cliente; sem a chave, o contexto é o da própria pessoa.
    """
    term = (term or "").strip()
    if not term:
        raise ValueError("Informe o que procurar em --search.")
    # Um project.json ilegível lê como contexto da própria pessoa: o que é de cliente fica escondido.
    client = project_client(project)[0]
    if not enabled():
        result = _off(**_scope(client), assets=[], queries=[], providers={}, preferences=[])
        logs.event(_log, logging.INFO, "library", action="search", entries=0, disabled=True)
        return result
    wanted = _tokens(term)
    data = load_index()
    # A cópia sem cliente de um trecho que o cliente também guardou fica à sombra da dele.
    own = {a.get("asset_id") for a in data["assets"] if client and a.get("client") == client}
    data["assets"] = [
        a for a in data["assets"] if _visible(a, client) and (a.get("client") or a.get("asset_id") not in own)
    ]
    data["queries"] = [q for q in data["queries"] if _visible(q, client)]
    data["preferences"] = [p for p in data["preferences"] if _visible(p, client)]
    assets = sorted(
        (
            (
                a,
                _score(
                    wanted,
                    " ".join(
                        filter(None, [a.get("title"), a.get("reason"), a.get("source_url"), *(a.get("tags") or [])])
                    ),
                ),
            )
            for a in data["assets"]
        ),
        key=lambda pair: -pair[1],
    )
    queries = sorted(
        ((q, _score(wanted, q.get("query"))) for q in data["queries"]),
        key=lambda pair: -pair[1],
    )
    preferences = sorted(
        ((p, _score(wanted, p.get("text"))) for p in data["preferences"]),
        key=lambda pair: -pair[1],
    )

    def keep(pairs):
        return [dict(item, score=round(score, 3)) for item, score in pairs if score][:limit]

    found_assets, found_queries = keep(assets), keep(queries)
    found_preferences = keep(preferences)
    named = {item.get("provider") for item in found_assets + found_queries}
    result = answer(
        **_scope(client),
        term=term,
        assets=found_assets,
        queries=found_queries,
        preferences=found_preferences,
        providers=_provider_counts(data, named, client),
    )
    logs.event(
        _log,
        logging.INFO,
        "library",
        action="search",
        entries=len(found_assets) + len(found_queries) + len(found_preferences),
        disabled=False,
    )
    return result


def hints(query, limit=5, project=None):
    """Pistas curtas para anexar a `search`; silenciosas quando não há biblioteca.

    Sem `reason` e sem `by`: esses textos foram escritos noutro projeto, às vezes
    sobre outro cliente. Quem quiser lê-los pede de propósito, em `library --search`. As pistas
    seguem o mesmo escopo de `search`: nunca trazem entrada de outro cliente.
    """
    if not enabled():
        logs.event(_log, logging.INFO, "library", action="hints", entries=0, disabled=True)
        return []
    try:
        found = search(query, limit=limit, project=project)
    except (ValueError, OSError) as error:
        # Non-fatal (hints are advisory), but a corrupted index must not look
        # identical to "nothing found" — surface it on the same warnings channel
        # every other command uses, naming the file so the person can act on it.
        from .runtime import record_warning

        record_warning(
            "LIBRARY_INDEX_UNREADABLE",
            f"Biblioteca de aprendizados ({labels.shown(index_path())}) não pôde ser lida; pistas ignoradas nesta "
            f"busca: {labels.reason(error)}",
        )
        return []
    out = [
        {
            "kind": "asset",
            "source_url": a.get("source_url"),
            "provider": a.get("provider"),
            "title": a.get("title"),
            "decision": a.get("decision"),
            "rights_not_transferable": True,
        }
        for a in found["assets"]
    ]
    out.extend(
        {
            "kind": "query",
            "query": q.get("query"),
            "provider": q.get("provider"),
            "outcome": q.get("outcome"),
            "rights_not_transferable": True,
        }
        for q in found["queries"]
    )
    result = out[:limit]
    logs.event(_log, logging.INFO, "library", action="hints", entries=len(result), disabled=False)
    return result
