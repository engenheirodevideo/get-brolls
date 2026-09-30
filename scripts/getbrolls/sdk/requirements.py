"""Confere o `requires` do manifesto contra esta instalação, sem instalar nem executar nada.

Pacotes Python são lidos dos metadados de distribuição (`importlib.metadata`), os
executáveis só procurados no `PATH` (`shutil.which`, nunca rodados) e o runtime
`python` comparado com o interpretador atual. Outros runtimes e os serviços ficam
como declarados. Faltar algo nunca impede o plugin de carregar: `doctor` e
`plugins --action check` mostram o que falta, com o comando para instalar.
"""

import platform
import re
import shutil
import sys
from importlib import metadata

from .. import _paths
from .manifest import requirement_parts, satisfies

_LEADING_VERSION_RE = re.compile(r"\d+(?:\.\d+){0,2}")


def leading_version(text: str) -> str:
    """Parte numérica inicial de uma versão (`"3.2.0rc1"` → `"3.2.0"`), para `satisfies`.

    Pré-release e sufixos locais não pesam: `3.2.0rc1` atende `>=3.1`. Sem dígito
    no começo, vale `"0"`."""
    match = _LEADING_VERSION_RE.match(text)
    return match[0] if match else "0"


def installed_version(dist: str) -> str | None:
    """Versão instalada da distribuição `dist` neste Python, ou `None`."""
    try:
        return metadata.version(dist)
    except (metadata.PackageNotFoundError, OSError, ValueError):
        return None


def _in_range(version: str, spec: str | None) -> bool:
    return spec is None or satisfies(leading_version(version), spec)


def install_hint(reqs: list[str]) -> str:
    """Comando para instalar `reqs` no Python desta instalação.

    Pacote (`uv tool`/`pipx`): acrescenta ao ambiente da ferramenta. Checkout: o
    `pip` do próprio interpretador que roda o get-brolls."""
    quoted = " ".join(f'"{req}"' for req in reqs)
    if _paths.origin() == "wheel":
        with_flags = " ".join(f'--with "{req}"' for req in reqs)
        return f"uv tool install getbrolls {with_flags} (ou: pipx inject getbrolls {quoted})"
    return f'"{sys.executable}" -m pip install {quoted}'


def report(manifest: dict) -> dict:
    """O que o `requires` de `manifest` pede e o que esta instalação tem.

    `ok` é falso quando falta um pacote Python (ou a versão não atende), um executável
    ou o runtime `python` não atende; runtimes não verificados (`ok: None`) e
    serviços não pesam. `hint` traz o comando para os pacotes Python que faltam."""
    requires = manifest.get("requires") or {}
    python_rows = []
    for req in requires.get("python", []):
        name, spec = requirement_parts(req)
        version = installed_version(name)
        python_rows.append(
            {
                "requirement": req,
                "name": name,
                "installed": version,
                "ok": version is not None and _in_range(version, spec),
            }
        )
    binaries = [{"name": name, "found": shutil.which(name) is not None} for name in requires.get("binaries", [])]
    runtimes = []
    for name, spec in requires.get("runtimes", {}).items():
        if name == "python":
            version = platform.python_version()
            runtimes.append({"name": name, "spec": spec, "version": version, "ok": _in_range(version, spec)})
        else:
            runtimes.append({"name": name, "spec": spec, "version": None, "ok": None})
    missing = [row["requirement"] for row in python_rows if not row["ok"]]
    ok = not missing and all(row["found"] for row in binaries) and all(row["ok"] is not False for row in runtimes)
    return {
        "ok": ok,
        "python": python_rows,
        "binaries": binaries,
        "runtimes": runtimes,
        "services": list(requires.get("services", [])),
        "hint": install_hint(missing) if missing else None,
    }


def missing_summary(rows: list[dict]) -> str | None:
    """Uma frase para o `summary` do `doctor` quando algum plugin tem requisito faltando, ou `None`."""
    lacking = [row for row in rows if isinstance(row.get("requires"), dict) and row["requires"].get("ok") is False]
    if not lacking:
        return None
    parts = []
    for row in lacking:
        info = row["requires"]
        names = [item["requirement"] for item in info["python"] if not item["ok"]]
        names += [item["name"] for item in info["binaries"] if not item["found"]]
        names += [f"{item['name']} {item['spec']}" for item in info["runtimes"] if item["ok"] is False]
        detail = f"{row['id']} ({', '.join(names)})"
        if info.get("hint"):
            detail += f" — instale com: {info['hint']}"
        parts.append(detail)
    return (
        "Falta o que estes plugins pedem em requires (o plugin carrega mesmo assim, mas pode falhar ao usar): "
        + "; ".join(parts)
        + "."
    )
