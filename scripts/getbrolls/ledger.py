"""Local manifest with recoverable writes and derived candidate snapshots."""

# pylint: disable=missing-function-docstring,too-many-branches,missing-class-docstring
# Legado: ocorrências pré-existentes (corpo idêntico ao código anterior à 2.6.0).

import hashlib
import json
import uuid
from pathlib import Path

from . import effects, versioning
from ._paths import strict_exists, strict_is_file, strict_lexists
from .models import id_stem, now
from .sdk import jsonschema, schemas


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_write(path, text, *, unique=False):
    """Grava `text` inteiro de uma vez (temporário + troca), em UTF-8 e com o `\\n` como está.

    É `effects.write_atomic` (uma linha `fs.write` no getbrolls.log). Sem `newline="\\n"`, o
    Windows trocaria cada `\\n` por `\\r\\n`: o arquivo sairia diferente do macOS e do Linux. Quem
    quer o fim de linha original (o CRLF do ROTEIRO.md revisado) o devolve no `text`. `unique=True` dá ao
    temporário um nome só deste processo (`<nome>.<uuid>.tmp`): dois gravadores ao mesmo tempo
    nunca escrevem no mesmo temporário."""
    effects.write_atomic(path, text, unique=unique)


def _published_name(name):
    """`clips/<arquivo>` de um clipe final: uma pasta só, sem `..` e sem a marca do provisório."""
    from .layout import CLIPS_PREFIX, STAGED_CLIP_MARK

    tail = name[len(CLIPS_PREFIX) :] if isinstance(name, str) and name.startswith(CLIPS_PREFIX) else ""
    return (
        bool(tail)
        and tail not in (".", "..")
        and tail == Path(tail).name
        and "\\" not in tail
        and STAGED_CLIP_MARK not in tail
    )


_INVALID_MANIFEST = (
    "manifest.json inválido ou incompatível. Preserve o arquivo e restaure uma cópia válida; "
    "nenhum dado foi reiniciado."
)


_INVALID_JOURNAL = (
    "Journal de recuperação inválido. Preserve .pending-transaction.json e restaure o projeto antes de continuar."
)


def validate_manifest(data):  # noqa: C901, PLR0912 - existing size; validator with one check per manifest field
    # Manifesto anterior ao campo carrega (ausente = 1); o de versão mais nova sai com a
    # frase própria, e qualquer outro valor com a mensagem genérica.
    if isinstance(data, dict):
        versioning.read_version(data, "manifest.json", schemas.version("manifest"), invalid=_INVALID_MANIFEST)
    # Tipo errado levanta `TypeError` e valor errado, `ValueError`: o `except` abaixo
    # trata os dois (e `KeyError`) como a mesma recusa, com a mensagem de sempre.
    try:
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise TypeError
        # O schema do manifesto cobre só o topo; cada item segue sendo lido por este validador (2.5, edição à mão).
        if jsonschema.errors(data, schemas.load("manifest")):
            raise ValueError
        seen = set()
        for c in data["items"]:
            if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"] or c["id"] in seen:
                raise ValueError
            seen.add(c["id"])
            for field in ("provider", "source_id", "title", "state"):
                if not isinstance(c.get(field), str):
                    raise TypeError
            # Older manifests on disk predate this field: only refuse a value that is
            # present and unrecognized, never its absence. Same strictness as the
            # top-level check above: `True == 1` and `1.0 == 1` must not pass as the
            # int `1`.
            if "schema_version" in c and (
                type(c["schema_version"]) is not int or c["schema_version"] != schemas.version("candidate")
            ):
                raise ValueError
            for field in (
                "media",
                "preview",
                "segment",
                "rights",
                "acquisition",
                "approval",
                "output",
                "creator",
                "match",
            ):
                if not isinstance(c.get(field), dict):
                    raise TypeError
            for field in ("start_s", "end_s", "revision"):
                if field not in c["segment"]:
                    raise ValueError
            if type(c["segment"]["revision"]) is not int or c["segment"]["revision"] < 0:
                raise ValueError
            if not isinstance(c["rights"].get("evidence"), list) or any(
                not isinstance(v, str) for v in c["rights"]["evidence"]
            ):
                raise ValueError
            for field in ("path", "sha256", "verified"):
                if field not in c["output"]:
                    raise ValueError
            for value in [c["output"]["path"]] + [
                c["preview"].get(k)
                for k in (
                    "poster_path",
                    "gif_path",
                    "contact_sheet_path",
                    "context_path",
                )
            ]:
                if value is not None and (
                    not isinstance(value, str)
                    or not value.startswith(("previews/", "clips/"))
                    or ".." in Path(value).parts
                    or "\\" in value
                ):
                    raise ValueError
    except (ValueError, TypeError, KeyError):
        raise ValueError(_INVALID_MANIFEST) from None
    return data


class Ledger:
    def __init__(self, project, recover=True):
        """recover=False abre o manifesto sem criar pastas nem concluir pendências."""
        self.root = Path(project).resolve() / "brolls"
        if recover:
            for directory in ("candidates", "previews"):
                effects.mkdir(self.root / directory, parents=True, exist_ok=True)
            self._make_clips_dir()
        self.path = self.root / "manifest.json"
        self.pending = self.root / ".pending-transaction.json"
        self.recovered = strict_exists(self.pending)
        if self.recovered and recover:
            self._finish_transaction()
            from .runtime import record_recovered, record_warning

            # A gravação concluída aqui é a de um comando anterior, não a deste.
            record_recovered()
            record_warning(
                "RECOVERED_WRITE",
                "Gravação interrompida recuperada; manifesto, candidatos e eventos sincronizados.",
            )
        try:
            self.data = (
                versioning.stamp(
                    validate_manifest(json.loads(self.path.read_text(encoding="utf-8"))), schemas.version("manifest")
                )
                if strict_exists(self.path)
                else versioning.stamp({"items": []}, schemas.version("manifest"))
            )
        except json.JSONDecodeError:
            raise ValueError(
                "manifest.json contém JSON inválido. Preserve o arquivo e restaure uma cópia válida."
            ) from None

    def get(self, ident):
        for c in self.data["items"]:
            if c["id"] == ident:
                return c
        raise ValueError("Candidato não encontrado neste projeto.")

    def add(self, c):
        if any(x["id"] == c["id"] for x in self.data["items"]):
            old = self.get(c["id"])
            # An import rerun must not silently discard new provenance.
            for field in (
                "source_url",
                "title",
                "asset_type",
                "captured_at",
                "creator",
                "context_image_sha256",
                "full_preview_sha256",
            ):
                if c.get(field) != old.get(field):
                    raise ValueError(
                        "Este insert já existe com metadados diferentes. Use outro --shot para "
                        "registrar a nova origem/contexto."
                    )
            return old
        self.data["items"].append(c)
        return c

    def save(self, operation, c=None, *, publish=()):
        self.save_many(operation, [c] if c else [], publish=publish)

    def save_many(self, operation, candidates, *, publish=()):
        """Grava o journal e conclui a transação.

        `publish` são nomes de clipe (`clips/<arquivo>`) cujo corte provisório (`<nome>.staged<ext>`)
        recebe o nome final dentro da transação, depois do journal e antes do manifesto: um SIGKILL
        em qualquer ponto deixa a troca para quem abrir o projeto depois.
        """
        validate_manifest(self.data)
        ident = str(uuid.uuid4())
        events = [
            {
                "transaction": ident + ":" + str(i),
                "at": now(),
                "operation": operation,
                "id": c["id"] if c else None,
                "revision": c["segment"]["revision"] if c else None,
                "state": c["state"] if c else None,
            }
            for i, c in enumerate(candidates or [None])
        ]
        journal = versioning.stamp(
            {"data": self.data, "events": events, **({"publish": list(publish)} if publish else {})},
            schemas.version("pending_transaction"),
        )
        atomic_write(self.pending, json.dumps(journal, ensure_ascii=False, indent=2))
        from .runtime import record_commit

        record_commit()  # Journal is durable, even if completing snapshots fails.
        self._finish_transaction()

    def _make_clips_dir(self):
        """Cria a pasta de clipes finais do layout (`brolls/clips/` no 0, `broll/` no 1).

        Pasta de clipes que não dá para usar (link, `project.json` quebrado) não trava
        o comando aqui: quem grava o clipe (`fetch`) é que recusa, dizendo o motivo.
        """
        from . import layout, logs

        try:
            with logs.handled():  # recusa aqui não para o comando: só o `fetch` recusa de fato
                folder = layout.clips_dir(self.root.parent)
        except ValueError:
            return
        effects.mkdir(folder, parents=True, exist_ok=True)

    def _publish_clips(self, names):
        """Dá a cada corte provisório o nome final que o journal registrou.

        Refaz sem erro o que um SIGKILL deixou pela metade: sem provisório, a troca já foi feita
        (ou o clipe sumiu, e o `verify` derruba o item).
        """
        from . import layout

        for name in names:
            final = layout.clip_file(self.root.parent, name, for_write=True)
            staged = layout.staged_clip(final)
            if not strict_lexists(staged):
                continue
            if not strict_is_file(staged, follow_symlinks=False):  # link (vivo ou quebrado) ou outra coisa
                raise ValueError(
                    "Journal de recuperação recusado: o corte provisório em clips/ não é um arquivo "
                    "comum (é um link ou outra coisa). Preserve .pending-transaction.json, restaure "
                    "o projeto e rode o comando de novo."
                )
            effects.replace(staged, final)

    def _finish_transaction(self):
        try:
            transaction = json.loads(self.pending.read_text(encoding="utf-8"))
        except ValueError:
            raise ValueError(_INVALID_JOURNAL) from None
        # Journal de 2.5 não tem o campo (vale 1); o de versão mais nova sai com a frase própria e fica no lugar.
        if isinstance(transaction, dict):
            versioning.read_version(
                transaction,
                ".pending-transaction.json",
                schemas.version("pending_transaction"),
                invalid=_INVALID_JOURNAL,
            )
        try:
            # O schema já exige `events` como lista de objetos com `transaction` em texto.
            if jsonschema.errors(transaction, schemas.load("pending_transaction")):
                raise ValueError
            data = validate_manifest(transaction["data"])
            events = transaction["events"]
            publish = transaction.get("publish", [])
            if not isinstance(publish, list) or not all(_published_name(name) for name in publish):
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise ValueError(_INVALID_JOURNAL) from None
        # O nome final do clipe vem antes do manifesto e fora do `ledger.commit`: a troca grava a
        # própria linha `fs.replace`, e uma falha dela não entra no `count` do lote.
        self._publish_clips(publish)
        # Uma linha `ledger.commit` para o manifesto, os candidatos, os eventos e o fim do journal
        # (`count` = arquivos gravados e apagados): um projeto grande regrava cada candidato.
        with effects.batch("ledger.commit", self.path):
            atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2))
            for c in data["items"]:
                path = self.root / "candidates" / (id_stem(c["id"]) + ".json")
                atomic_write(path, json.dumps(c, ensure_ascii=False, indent=2))
            event_path = self.root / "events.jsonl"
            prior = event_path.read_text(encoding="utf-8") if strict_exists(event_path) else ""
            try:
                known = {json.loads(line).get("transaction") for line in prior.splitlines() if line.strip()}
            except (ValueError, AttributeError):
                raise ValueError(
                    "events.jsonl inválido. Histórico preservado; restaure o log para concluir a recuperação."
                ) from None
            extra = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events if e["transaction"] not in known)
            if extra:
                atomic_write(
                    event_path,
                    prior + ("\n" if prior and not prior.endswith("\n") else "") + extra,
                )
            effects.unlink(self.pending)


def existing_project_id(project):
    """Id do projeto: o do `project.json` quando válido, senão o `project_id` do manifesto
    se for um uuid válido; senão `None`.

    Nunca cria um `project_id` e nunca levanta: manifesto corrompido, ilegível ou sem
    o campo devolve `None`, porque isso é usado para logar e um log nunca pode quebrar
    o comando. Um valor que não é uuid (por exemplo, um id antigo escrito à mão) também
    devolve `None`, para nunca colocar dado arbitrário do manifesto no log.
    """
    from . import layout

    try:
        declared = layout.project_id(project)
    except (OSError, ValueError):
        declared = None
    if declared is not None:
        return declared
    try:
        project_id = Ledger(project, recover=False).data.get("project_id")
    except (OSError, ValueError):
        return None
    if not isinstance(project_id, str):
        return None
    try:
        uuid.UUID(project_id)
    except ValueError:
        return None
    return project_id
