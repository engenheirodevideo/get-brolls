"""Pastas de staging do `install`/`update`/`remove` em `plugins/`: nomes, varredura das sobras e o que esconde delas.

Toda pasta de staging começa com ponto (`.install-*`, `.old-*`, `.removed-*`), então o `loader` a ignora enquanto
existe. A idade vem do epoch no NOME da pasta, nunca do `st_mtime`. A varredura devolve ao lugar o único exemplar
de um plugin deixado por um `update` interrompido e apaga o resto, sem nunca abortar quem a chamou.
"""

import contextlib
import re
import time
import uuid

from .. import effects, runtime
from . import loader
from .contracts import NAME_RE
from .manifest import read_manifest

# `.old-*`/`.install-*` mais nova que isso pode ser um `install`/`update` concorrente
# ainda em andamento (não terminou de rodar o próprio `finally`); só mexemos em
# quem já passou desse prazo.
STALE_STAGING_MAX_AGE_S = 3600


# O epoch de criação vai no NOME da pasta de staging, não é lido do `st_mtime`
# dela: `os.replace`/`shutil.copytree` preservam timestamp do conteúdo de
# origem (um plugin instalado há muito tempo, movido ou copiado agora,
# continua com o `st_mtime` antigo do próprio conteúdo) — uma pasta de staging
# recém-criada podia "parecer" velha o bastante pra a varredura mexer nela no
# meio de um `install`/`update` concorrente, apagando o staging de outro
# processo ainda rodando (`FileNotFoundError` cru quando esse processo, mais
# tarde, tentasse a própria troca). O nome é a única fonte de verdade da idade.
_INSTALL_STAGING_RE = re.compile(r"^\.install-(\d+)-[0-9a-f]{32}$")
_OLD_STAGING_RE = re.compile(r"^\.old-(\d+)-(.+)-[0-9a-f]{32}$")
# Pasta de um `plugins --action remove` (trocada de lugar antes de apagar). Nunca
# reusa o prefixo `.old-*`: esse a varredura devolve ao lugar quando o plugin sumiu,
# e um plugin removido de propósito voltaria sozinho. Resto `.removed-*` é sempre apagado.
_REMOVED_STAGING_RE = re.compile(r"^\.removed-(\d+)-[0-9a-f]{32}$")


# Só `.install-*`: o `.old-*` de uma troca que falhou no meio é citado de propósito
# (é onde o conteúdo anterior ficou).
_STAGING_NAME_RE = re.compile(r"\.install-\d+-[0-9a-f]{32}")


def hide_staging_names(text):
    """`text` sem o nome de uma pasta de staging (`.install-<epoch>-<hash>`…): "Plugin
    .install-…: x" vira "Plugin da origem: x"; o nome é detalhe interno, não algo que a
    pessoa encontre ou possa corrigir."""
    text = re.sub(r"Plugin " + _STAGING_NAME_RE.pattern + ":", "Plugin da origem:", str(text))
    return _STAGING_NAME_RE.sub("(pasta temporária)", text)


@contextlib.contextmanager
def _staging_names_hidden():
    """Reescreve o `ValueError` que sair do bloco sem o nome da pasta de staging."""
    try:
        yield
    except ValueError as exc:
        clean = hide_staging_names(exc)
        if clean == str(exc):
            raise
        try:
            replacement = type(exc)(clean)
        except TypeError:
            replacement = ValueError(clean)
        raise replacement from exc


def _new_install_staging_name():
    return f".install-{int(time.time())}-{uuid.uuid4().hex}"


def new_removed_staging_name():
    """Nome da pasta para onde o `remove` tira o plugin antes de apagá-lo."""
    return f".removed-{int(time.time())}-{uuid.uuid4().hex}"


def _new_old_staging_name(plugin_id):
    return f".old-{int(time.time())}-{plugin_id}-{uuid.uuid4().hex}"


def _sweep_stale_install(entry, name, now):
    match = _INSTALL_STAGING_RE.match(name)
    if not match or now - int(match.group(1)) < STALE_STAGING_MAX_AGE_S:
        return  # nome não reconhecido (versão anterior/lixo) ou jovem demais: não mexe
    runtime.force_rmtree(entry)


def _sweep_stale_removed(entry, name, now):
    match = _REMOVED_STAGING_RE.match(name)
    if not match or now - int(match.group(1)) < STALE_STAGING_MAX_AGE_S:
        return  # nome não reconhecido ou jovem demais (um `remove` ainda apagando): não mexe
    runtime.force_rmtree(entry)


def _known_ids():
    """Ids que o `plugins.json` ainda guarda (pin, último pin ou origem); `None` se ilegível."""
    try:
        state = loader.read_state()
    except (ValueError, OSError):
        return None
    return {*state.get("enabled", {}), *state.get("last_pins", {}), *state.get("sources", {})}


def _sweep_stale_old(root, entry, name, now, known):
    match = _OLD_STAGING_RE.match(name)
    if not match:
        return  # nome não reconhecido: sem como saber a idade com confiança, não mexe
    epoch, plugin_id = int(match.group(1)), match.group(2)
    if now - epoch < STALE_STAGING_MAX_AGE_S:
        return
    if not NAME_RE.fullmatch(plugin_id):
        return  # nome corrompido/inesperado: nunca usa como caminho sem validar antes
    if known is None:
        return  # plugins.json ilegível: sem saber se o plugin foi removido, não mexe
    target = root / plugin_id
    try:
        # Só volta ao lugar o plugin que o plugins.json ainda conhece: um `remove --yes`
        # depois de um update interrompido tirou o id de lá, e o `.old-*` é só resto.
        # lenient: sweep of stale .old-* folders; best effort
        if not target.exists() and plugin_id in known and _restorable(entry, plugin_id):
            effects.replace(entry, target)
        else:
            runtime.force_rmtree(entry)
    except OSError:
        # Uma corrida com outro processo (a pasta já não existe mais, ou o
        # destino apareceu entre o `exists()` e o `replace()`) nunca pode
        # abortar quem chamou `install`/`update`: na pior hipótese essa pasta
        # de resto fica pra próxima varredura.
        pass


def _restorable(entry, plugin_id):
    """Um `.old-*` só volta ao lugar se for mesmo aquele plugin: manifesto legível
    e com o mesmo id do nome. Pasta vazia, parcial ou de outro plugin é
    resto — restaurá-la travaria um `install` futuro com um plugin quebrado."""
    try:
        return read_manifest(entry, require_folder_match=False)["id"] == plugin_id
    except (ValueError, OSError):
        return False


def _sweep_one_stale_entry(root, entry, now, known):
    # lenient: stale-entry sweep; an entry it cannot stat is left for a later sweep
    if not entry.is_dir():
        return
    name = entry.name
    if name.startswith(".install-"):
        _sweep_stale_install(entry, name, now)
    elif name.startswith(".old-"):
        _sweep_stale_old(root, entry, name, now, known)
    elif name.startswith(".removed-"):
        _sweep_stale_removed(entry, name, now)


def _sweep_stale_staging():
    """Uma pasta `.install-*`/`.old-*`/`.removed-*` que sobrou de um processo anterior morto
    no meio (sem chance de rodar o próprio `finally`) não deve ficar acumulando
    disco nem confundir uma leitura futura de `plugins/`. Duas ressalvas, e uma
    regra de nunca-quebrar (ver `_sweep_one_stale_entry`):

    - Uma pasta mais nova que `STALE_STAGING_MAX_AGE_S` (pelo epoch no NOME,
      não pelo `st_mtime`) pode ser de um `install`/`update` concorrente ainda
      em andamento — mexer nela agora corromperia esse processo em vez de
      limpar um resto de verdade.
    - Se `plugins/<id>` já não existe e sobrou um `.old-<epoch>-<id>-<uuid>`
      antigo, é porque o processo morreu bem entre as duas trocas do `update`
      (depois de retirar a pasta original, antes de pôr a nova no lugar):
      esse `.old-*` é a ÚNICA cópia que resta do plugin, e apagá-lo destruiria
      o plugin inteiro. Devolvemos ele ao lugar em vez de apagar — mas só se o
      `plugins.json` ainda guarda o id: sem ele, o plugin foi removido de
      propósito (`remove --yes`) e o `.old-*` é resto, apagado.

    Nenhum erro de sistema de arquivos numa entrada individual pode abortar
    quem chamou `install`/`update`: o pior caso aceitável é deixar aquela
    pasta de resto pra próxima varredura."""
    root = loader.plugins_root()
    # lenient: sweep of stale staging; best effort
    if not root.is_dir():
        return
    now = time.time()
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    known = _known_ids()
    for entry in entries:
        try:
            _sweep_one_stale_entry(root, entry, now, known)
        except OSError:
            continue


def _staging():
    root = loader.plugins_root()
    effects.mkdir(root, parents=True, exist_ok=True)
    # Começa com ponto: `loader.entries()` ignora a pasta enquanto ela existe.
    return root / _new_install_staging_name()
