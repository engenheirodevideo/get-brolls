#!/usr/bin/env python3
"""Gera `scripts/getbrolls/sdk/official_pins.py`: o `folder_digest` de cada plugin em `plugins_official/`.

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
HEADER = '''"""Pin de cada plugin oficial: o `folder_digest` da pasta em `plugins_official/` (arquivo gerado).

Gerado por `scripts/gen_official_pins.py`; não edite à mão. O `--check` dele (cadeia de lint e job `quality`
da CI) falha quando uma pasta mudou e este arquivo não foi gerado de novo.
"""
'''


def render(root: Path) -> str:
    """O texto de `official_pins.py` para as pastas de `root/plugins_official/`, em ordem de id."""
    folders = sorted(path for path in (root / loader.OFFICIAL_DIR).iterdir() if path.is_dir())
    rows = "".join(f'    "{folder.name}": "{loader.official_digest(folder)}",\n' for folder in folders)
    return f"{HEADER}\nPINS = {{\n{rows}}}\n"


def main(argv: list[str] | None = None) -> int:
    """Gera (ou, com `--check`, só confere) o `official_pins.py`; devolve o código de saída."""
    parser = argparse.ArgumentParser(description="Gera o pin dos plugins oficiais (official_pins.py).")
    parser.add_argument("--check", action="store_true", help="Só confere; sai 1 se o arquivo estiver velho")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    target = args.root / TARGET
    text = render(args.root)
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current != text:
            print("official_pins.py não confere com plugins_official/: rode python3 scripts/gen_official_pins.py.")
            return 1
        print("official_pins.py confere com plugins_official/.")
        return 0
    target.write_text(text, encoding="utf-8", newline="\n")
    print("official_pins.py gerado de novo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
