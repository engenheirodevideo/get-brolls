"""Local manifest with recoverable writes and derived candidate snapshots."""

# pylint: disable=missing-function-docstring,too-many-branches,missing-class-docstring
# Legado: ocorrências pré-existentes (corpo idêntico ao código anterior à 2.6.0).

import hashlib
import json
import os
import uuid
from pathlib import Path

from . import versioning
from .models import id_stem, now


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_write(path, text):
    """Grava `text` inteiro de uma vez (temporário + troca), em UTF-8 e com o `\\n` como está.

    Sem `newline="\\n"`, o Windows trocaria cada `\\n` por `\\r\\n`: o arquivo sairia diferente do
    macOS e do Linux, e o ROTEIRO.md revisado voltaria com o CRLF que a revisão tirou."""
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


_INVALID_MANIFEST = (
    "manifest.json inválido ou incompatível. Preserve o arquivo e restaure uma cópia válida; "
    "nenhum dado foi reiniciado."
)


def validate_manifest(data):  # noqa: C901, PLR0912 - existing size; validator with one check per manifest field
    # Manifesto anterior ao campo carrega (ausente = 1); o de versão mais nova sai com a
    # frase própria, e qualquer outro valor com a mensagem genérica.
    if isinstance(data, dict):
        versioning.read_version(data, "manifest.json", invalid=_INVALID_MANIFEST)
    try:
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ValueError
        seen = set()
        for c in data["items"]:
            if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"] or c["id"] in seen:
                raise ValueError
            seen.add(c["id"])
            for field in ("provider", "source_id", "title", "state"):
                if not isinstance(c.get(field), str):
                    raise ValueError
            # Older manifests on disk predate this field: only refuse a value that is
            # present and unrecognized, never its absence. Same strictness as the
            # top-level check above: `True == 1` and `1.0 == 1` must not pass as the
            # int `1`.
            if "schema_version" in c and (type(c["schema_version"]) is not int or c["schema_version"] != 1):
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
                    raise ValueError
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
                (self.root / directory).mkdir(parents=True, exist_ok=True)
            self._make_clips_dir()
        self.path = self.root / "manifest.json"
        self.pending = self.root / ".pending-transaction.json"
        self.recovered = self.pending.exists()
        if self.recovered and recover:
            self._finish_transaction()
            from .runtime import record_commit, record_warning

            record_commit()

            record_warning(
                "RECOVERED_WRITE",
                "Gravação interrompida recuperada; manifesto, candidatos e eventos sincronizados.",
            )
        try:
            self.data = (
                versioning.stamp(validate_manifest(json.loads(self.path.read_text(encoding="utf-8"))))
                if self.path.exists()
                else {"schema_version": 1, "items": []}
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

    def save(self, operation, c=None):
        self.save_many(operation, [c] if c else [])

    def save_many(self, operation, candidates):
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
        atomic_write(
            self.pending,
            json.dumps({"data": self.data, "events": events}, ensure_ascii=False, indent=2),
        )
        from .runtime import record_commit

        record_commit()  # Journal is durable, even if completing snapshots fails.
        self._finish_transaction()

    def _make_clips_dir(self):
        """Cria a pasta de clipes finais do layout (`brolls/clips/` no 0, `broll/` no 1).

        Pasta de clipes que não dá para usar (link, `project.json` quebrado) não trava
        o comando aqui: quem grava o clipe (`fetch`) é que recusa, dizendo o motivo.
        """
        from . import layout

        try:
            folder = layout.clips_dir(self.root.parent)
        except ValueError:
            return
        folder.mkdir(parents=True, exist_ok=True)

    def _finish_transaction(self):
        try:
            transaction = json.loads(self.pending.read_text(encoding="utf-8"))
            data = validate_manifest(transaction["data"])
            events = transaction["events"]
            if not isinstance(events, list) or any(
                not isinstance(e, dict) or not isinstance(e.get("transaction"), str) for e in events
            ):
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise ValueError(
                "Journal de recuperação inválido. Preserve .pending-transaction.json e restaure "
                "o projeto antes de continuar."
            ) from None
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2))
        for c in data["items"]:
            path = self.root / "candidates" / (id_stem(c["id"]) + ".json")
            atomic_write(path, json.dumps(c, ensure_ascii=False, indent=2))
        event_path = self.root / "events.jsonl"
        prior = event_path.read_text(encoding="utf-8") if event_path.exists() else ""
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
        self.pending.unlink()


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
