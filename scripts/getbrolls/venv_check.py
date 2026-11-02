"""Confere a `.venv` que o runtime usa contra os pinos de `requirements-bootstrap.txt` e `requirements.txt`.

A venv em uso é a do runtime (`$GB_HOME/runtime/<sha>/.venv` ou `GB_RUNTIME_DIR`), a `.venv` antiga do
checkout enquanto o runtime compartilhado não existe, ou a de `GB_VENV_PATH`. Atualizar o código (git pull,
`/plugin update`) sem rodar o `setup`, ou um `setup` que falhou no meio, pode deixar em uso uma venv com as
versões antigas: o `doctor` precisa dizer isso em vez de "tudo certo". Só lê: roda o Python da venv para
listar os pacotes instalados e não instala nem corrige nada.
"""

import json
import re
import subprocess
from pathlib import Path

from . import _paths, effects
from .config import executable_override, venv_override
from .errors import DataRootError

PYTHON_LAYOUTS = ("Scripts/python.exe", "bin/python")
LIST_TIMEOUT_S = 30

# A versão que tira a raiz distrustada do pacote de certificados (GHSA-6q48-6c7w-9gw2).
CERTIFI_FLOOR = "2026.10.19"
CERTIFI_NOTE = (
    "o certifi desta venv ainda confia na raiz emSign ECC Root CA - G3 (GHSA-6q48-6c7w-9gw2); "
    "a correção só vale depois do `setup`"
)

PINNED_VENV_FIX = (
    "A venv de GB_VENV_PATH é sua e o `setup` não mexe nela: instale nela os pinos de requirements.txt "
    "(com --require-hashes) ou remova GB_VENV_PATH e rode {fix}"
)

# Pares [nome, versão] de tudo que o `importlib.metadata` do Python da venv enxerga, duplicados inclusive.
_LIST_SNIPPET = (
    "import importlib.metadata as m, json; "
    "print(json.dumps([[d.metadata['Name'], d.version] for d in m.distributions() if d.metadata['Name']]))"
)
_PAIR = 2  # cada linha da listagem é [nome, versão]
_PIN = re.compile(r"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;\\]+)\s*(;)?")


def normalize_name(name):
    """Nome canônico do PEP 503: `Charset_Normalizer` e `charset-normalizer` são o mesmo pacote."""
    return re.sub(r"[-_.]+", "-", name).lower()


def normalize_version(version):
    """`2026.07.22` e `2026.7.22` são a mesma versão: zeros à esquerda não contam."""
    return ".".join(str(int(part)) if part.isdigit() else part.lower() for part in version.split("."))


def _version_key(version):
    return tuple(int(part) if part.isdigit() else -1 for part in normalize_version(version).split("."))


def pinned_versions(root=None):
    """`{nome: versão}` dos arquivos de requisitos da venv (os dados da instalação, ou `root`); linhas com
    marcador (só PyPy) ficam de fora."""
    root = Path(root) if root is not None else _paths.data_root()
    pins = {}
    for name in _paths.PART_FILES["venv"]:
        path = root / name
        # lenient: a data file that is missing is reported by the doctor's own data check; no pins from it
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\\\n", " ")
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "-")):
                continue
            match = _PIN.match(line)
            if match and not match.group(3):
                pins[normalize_name(match.group(1))] = match.group(2)
    return pins


def venv_python(base):
    """Python da venv (layout do Windows ou do Unix), ou None quando não há nenhum."""
    for relative in PYTHON_LAYOUTS:
        candidate = Path(base) / relative
        # Link quebrado (o Python do sistema foi atualizado e sumiu) conta: a venv existe e está quebrada.
        # lenient(2): tool discovery in the venv: no Python there is the `absent` answer
        if candidate.is_file() or candidate.is_symlink():
            return candidate
    return None


def runtime_venv():
    """`(pasta, origem)` da venv que o runtime usaria: origem `GB_VENV_PATH`, `gb_home`, `GB_RUNTIME_DIR`,
    `profile` ou `checkout`; pasta None (origem `pinned` ou `skipped`) quando o yt-dlp não vem de venv nenhuma."""
    try:
        if executable_override("GB_YTDLP_PATH"):
            return None, "pinned"
        base = venv_override()
    except ValueError:
        # Pin inválido (GB_YTDLP_PATH ou GB_VENV_PATH) já entra em `missing` pelo próprio pino.
        return None, "skipped"
    if base is not None:
        return base, "GB_VENV_PATH"
    found = _paths.venv_dir()
    return found.path, found.source


def installed_versions(python):
    """`{nome: [versões]}` instalados na venv; levanta OSError/ValueError quando o Python dela não roda."""
    done = effects.run(
        [str(python), "-I", "-c", _LIST_SNIPPET],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=LIST_TIMEOUT_S,
        check=False,
    )
    if done.returncode != 0:
        tail = done.stderr.strip().splitlines()[-1:] or [""]
        raise ValueError(f"saiu com código {done.returncode}: {tail[0][:200]}".rstrip(": "))
    rows = json.loads(done.stdout)
    if not isinstance(rows, list) or not all(isinstance(row, list) and len(row) == _PAIR for row in rows):
        raise ValueError("a listagem de pacotes não veio no formato esperado")
    found = {}
    for name, version in rows:
        found.setdefault(normalize_name(str(name)), []).append(str(version))
    return found


def _differences(installed, pins):
    """As linhas de diferença entre o instalado e os pinos, na ordem dos nomes."""
    found = []
    for name, pinned in sorted(pins.items()):
        versions = installed.get(name)
        if not versions:
            found.append(f"{name} ausente (fixado {pinned})")
        elif len(versions) > 1:
            # Qual o `import` acha depende da ordem do disco: dois `.dist-info` nunca passam por "ok".
            found.append(f"{name} duplicado ({', '.join(sorted(versions))})")
        elif normalize_version(versions[0]) != normalize_version(pinned):
            found.append(f"{name} {versions[0]} (fixado {pinned})")
    return found


def venv_report(base=None, root=None):
    """Estado da venv: `ok`, `stale` (versão diferente ou pacote ausente), `absent`, `broken`, `pinned`,
    `skipped` (pin inválido, ou `reason` `install_data_missing` quando faltam os pinos); `source` diz de onde
    veio a venv (ver `runtime_venv`), None quando `base` foi dada."""
    source = None
    if base is None:
        base, source = runtime_venv()
    if base is None:
        return {"status": source, "path": None, "source": None, "python": None, "differences": []}
    report = {"status": "ok", "path": str(base), "source": source, "python": None, "differences": []}
    python = venv_python(base)
    if python is None:
        report["status"] = "absent"
        return report
    report["python"] = str(python)
    try:
        installed = installed_versions(python)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report["status"] = "broken"
        report["error"] = str(exc)
        return report
    try:
        pins = pinned_versions(root)
    except DataRootError:
        # Sem os dados da instalação não há pinos: o item "dados da instalação" do `doctor` é quem avisa.
        report["status"] = "skipped"
        report["reason"] = "install_data_missing"
        return report
    report["differences"] = _differences(installed, pins)
    certifi = installed.get("certifi") or []
    report["certifi_distrusted_root"] = not certifi or any(
        _version_key(version) < _version_key(CERTIFI_FLOOR) for version in certifi
    )
    if report["differences"]:
        report["status"] = "stale"
    return report


def missing_entries(report, fix):
    """Itens de `summary.missing` para a venv: vazio quando ela bate com os pinos, não existe ou não é usada.

    A venv de `GB_VENV_PATH` é da pessoa: o `setup` não mexe nela, então o conserto diz o que fazer com ela.
    """
    status = report["status"]
    if report.get("source") == "GB_VENV_PATH":
        fix = PINNED_VENV_FIX.format(fix=fix)
    # `absent` não entra: sem venv o yt-dlp vem do PATH (que vale) ou falta, e o item `yt-dlp` já diz o conserto.
    if status == "broken":
        note = f"O Python da venv {report['python']} não roda ({report.get('error')}); refaça a venv pelo `setup`"
    elif status == "stale":
        note = "A venv não bate com os pinos fixados: " + ", ".join(report["differences"])
        if report.get("certifi_distrusted_root"):
            note += f"; {CERTIFI_NOTE}"
    else:
        return []
    return [{"item": "venv", "fix": fix, "note": note}]
