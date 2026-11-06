#!/usr/bin/env python3
"""Gera `scripts/getbrolls/sdk/official_pins.py`: o `folder_digest` de cada plugin oficial.

Os plugins oficiais moram em `plugins/bridges/` (pontes para ferramentas externas) e em `plugins/getbrolls/`
(plugins feitos para o getbrolls); um id só pode estar numa delas.

`python3 scripts/gen_official_pins.py` reescreve o arquivo; `--check` não escreve e sai 1 quando ele não
bate com as pastas (cadeia de lint e job `quality` da CI). O número é o de `loader.official_digest`, o mesmo
que o loader confere antes de rodar a ponte e que as saídas levam como procedência. `--root` é só para
teste: aponta para uma cópia, nunca para a árvore real.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from getbrolls.sdk import loader  # noqa: E402  # pylint: disable=wrong-import-position

TARGET = Path("scripts") / "getbrolls" / "sdk" / "official_pins.py"
HEADER = '''"""Pin de cada plugin oficial: o `folder_digest` da pasta em `plugins/bridges/` ou `plugins/getbrolls/`.

Gerado por `scripts/gen_official_pins.py`; não edite à mão. O `--check` dele (cadeia de lint e job `quality`
da CI) falha quando uma pasta mudou e este arquivo não foi gerado de novo.
"""
'''


FOLDERS_LABEL = " e ".join(f"{folder}/" for folder in loader.OFFICIAL_DIRS)


class RepeatedIdError(Exception):
    """O mesmo id de plugin oficial em mais de uma pasta oficial."""


def official_folders(root: Path) -> list[Path]:
    """A pasta de cada plugin oficial sob `root`, em ordem de id; `RepeatedIdError` se um id se repete."""
    folders = [
        path
        for name in loader.OFFICIAL_DIRS
        if (root / name).is_dir()
        for path in (root / name).iterdir()
        if path.is_dir()
    ]
    names = [folder.name for folder in folders]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise RepeatedIdError(f"id oficial em mais de uma pasta ({FOLDERS_LABEL}): {', '.join(repeated)}")
    return sorted(folders, key=lambda folder: folder.name)


def render(root: Path) -> str:
    """O texto de `official_pins.py` para as pastas oficiais de `root`, em ordem de id."""
    folders = official_folders(root)
    rows = "".join(f'    "{folder.name}": "{loader.official_digest(folder)}",\n' for folder in folders)
    return f"{HEADER}\nPINS = {{\n{rows}}}\n"


def main(argv: list[str] | None = None) -> int:
    """Gera (ou, com `--check`, só confere) o `official_pins.py`; devolve o código de saída."""
    parser = argparse.ArgumentParser(description="Gera o pin dos plugins oficiais (official_pins.py).")
    parser.add_argument("--check", action="store_true", help="Só confere; sai 1 se o arquivo estiver velho")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    target = args.root / TARGET
    try:
        text = render(args.root)
    except RepeatedIdError as exc:
        print(f"{exc}.")
        return 1
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current != text:
            print(f"official_pins.py não confere com {FOLDERS_LABEL}: rode python3 scripts/gen_official_pins.py.")
            return 1
        print(f"official_pins.py confere com {FOLDERS_LABEL}.")
        return 0
    target.write_text(text, encoding="utf-8", newline="\n")
    print("official_pins.py gerado de novo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
