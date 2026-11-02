"""Receitas do getbrolls, os arquivos: caminhos seguros de componente, cópia com sha256 e recusa de link.

Tudo o que `recipes` e `recipe_init` leem ou gravam de uma versão passa por aqui; o módulo `recipes` re-exporta
estes nomes.
"""

import hashlib
import re
import unicodedata

from . import assets, effects, labels, logs, refs, runtime
from ._paths import strict_is_file
from .roteiro_frontmatter import fold
from .sdk.files import is_link

_log = logs.get("recipes")

ASSETS = "assets"
TEXT_MAX = 200
_CHUNK = 1024 * 1024
_COMPONENT_DEPTH = 3  # assets/<tipo>/<arquivo>
_KIND_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")


def _shown(path):
    """Caminho para a saída (`path` de `seal_project`/`show`): a pasta pessoal vira `~`."""
    return runtime.scrub_home(str(path))


def _nfc(text):
    """O texto em NFC: o selo guarda nomes NFC, e o disco (HFS+, SMB, zip) pode guardar NFD."""
    return unicodedata.normalize("NFC", text)


def _place(path, *, project=None, client_root=None):
    """Caminho para uma mensagem: `<projeto>/…`, `<cliente>/…` (a pasta que quem chama já tem)
    ou outra marca de `labels.shown`; nunca o caminho da máquina. Não lê o registro de clientes:
    uma mensagem de erro não pode depender de outro arquivo nem trocar o erro por outro."""
    return labels.shown(path, project=project, extra=[(client_root, "<cliente>")])


def _refused(action, reason, exc, **fields):
    """Registra a recusa (`recipe_refused`: ação, motivo e `error_code`) e devolve `exc` para levantar.

    Nunca o slug da receita, o cliente nem um caminho: o nome de uma receita costuma carregar o do cliente.
    """
    return logs.refusal(_log, "recipe_refused", exc, action=action, reason=reason, **fields)


def _check_text(value, flag):
    """Texto opcional de uma linha (`--title`, `--by`); `None` fica `None`."""
    if value is None:
        return None
    text = _nfc(value).strip() if isinstance(value, str) else ""
    if not text or len(text) > TEXT_MAX or any(unicodedata.category(char) == "Cc" for char in text):
        raise ValueError(f"{flag} tem que ser texto de uma linha, de 1 a {TEXT_MAX} caracteres.")
    return text


def check_slug(slug):
    """Slug de receita válido (a regra de slug de cliente, sem ponto); `ValueError` senão."""
    refs.recipe_ref(refs.PERSONAL_OWNER, slug, 1)
    return slug


def refuse_link(path, *, project=None, client_root=None):
    """`path` como veio; `ValueError` quando é link: o getbrolls nunca segue link numa receita."""
    if is_link(path):
        raise _refused(
            "lookup",
            "link",
            ValueError(
                f"{_place(path, project=project, client_root=client_root)} é um link; o getbrolls não lê nem grava "
                "receitas através de links."
            ),
        )
    return path


def _file_name(name, path):
    """Nome do arquivo na receita: o do componente, que continua achado pelo mesmo nome."""
    if fold(_nfc(path.stem)) == fold(_nfc(name)):
        return _nfc(path.name)
    return f"{_nfc(name).strip()}{path.suffix.lower()}"


def copy_hashed(source, target, expected=None, *, project=None, client_root=None):
    """Copia `source` para `target` novo (nunca sobrescreve) e devolve o sha256 dos bytes copiados.

    Com `expected`, os bytes que passaram precisam ter esse sha256: senão o arquivo novo é
    apagado e `ValueError` diz qual mudou.
    """
    digest = hashlib.sha256()
    try:
        with (
            effects.op("fs.copy", target, fallback=(FileExistsError,)),
            source.open("rb") as reader,
            target.open("xb") as writer,
        ):
            while chunk := reader.read(_CHUNK):
                digest.update(chunk)
                writer.write(chunk)
    except FileExistsError:
        raise _refused(
            "copy",
            "exists",
            ValueError(f"{_place(target, project=project, client_root=client_root)} já existe; nada foi sobrescrito."),
        ) from None
    except BaseException:
        effects.unlink(target, missing_ok=True)
        raise
    value = digest.hexdigest()
    if expected is not None and value != expected:
        effects.unlink(target, missing_ok=True)
        raise _refused(
            "copy",
            "changed_while_copying",
            ValueError(f"{source.name} mudou durante a cópia (sha256 diferente da receita); nada foi copiado."),
        )
    return value


def _kind_folder(kind):
    """Pasta do tipo em `assets/`: a do tipo conhecido; tipo que esta versão não conhece usa o próprio nome."""
    spec = assets.ASSET_KINDS.get(kind)
    return spec.folder.removeprefix("assets/") if spec else kind


def safe_relative(rel, kind=None):
    """Caminho relativo seguro de componente (`assets/<tipo>/<arquivo>`); `ValueError` senão.

    O arquivo nunca começa com `.` nem termina em `.licenca.json` (licença nunca viaja na
    receita). A pasta é a de um tipo (`[a-z][a-z0-9_]*`), nunca a do A-ROLL, que é só do
    projeto; com `kind`, tem que ser a pasta desse tipo (`sfx` só em `assets/sfx/`).
    """
    from .analysis_contract import Invalid, relative

    unsafe = ValueError(f'Caminho de componente inseguro na receita: "{rel}".')
    try:
        parts = relative(rel)
    except Invalid:
        raise unsafe from None
    if len(parts) != _COMPONENT_DEPTH or parts[0] != ASSETS or not _KIND_RE.fullmatch(parts[1]):
        raise unsafe
    spec = assets.ASSET_KINDS.get(parts[1])
    if spec is not None and not spec.personal:
        raise unsafe
    name = parts[2]
    if name.startswith(".") or name.lower().endswith(assets.LICENSE_SUFFIX):
        raise unsafe
    if kind is not None and parts[1] != _kind_folder(kind):
        raise unsafe
    return parts


def _on_disk(parent, part):
    """`parent/part` com o nome como o disco o guarda: o selo diz NFC, e HFS+, SMB e zip guardam NFD."""
    try:
        names = [entry.name for entry in parent.iterdir()]
    except OSError:  # the strict test that follows says what is wrong with the folder
        names = []
    found = [name for name in names if _nfc(name) == part]
    if len(found) > 1:
        raise ValueError(f"dois nomes iguais em NFC: {part}")
    return parent / (found[0] if found else part)


def component_path(folder, rel, kind=None):
    """Arquivo `rel` de `assets/` dentro da versão, conferido antes de qualquer leitura.

    `ValueError` quando o caminho é inseguro (`..`, absoluto, `\\`, `:`, fora de `assets/<tipo>/`),
    quando algum pedaço do caminho é link ou quando não é arquivo.
    """
    parts = safe_relative(rel, kind)
    here = folder
    for part in parts:
        here = _on_disk(here, part)
        if is_link(here):
            raise ValueError(f"{rel} é um link; a receita só aceita arquivos comuns.")
    if not strict_is_file(here):
        raise ValueError(f"{rel} não existe na receita.")
    return here


def sha256_file(path):
    """sha256 dos bytes de `path`, lido em blocos."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
