#!/usr/bin/env python3
"""Build public skill only: no installed dependencies, filled projects or secrets."""

from pathlib import Path
import zipfile
from getbrolls import __version__

ROOT = Path(__file__).resolve().parents[1]


def files():
    exact = [
        "SKILL.md",
        "AGENTS.md",
        "requirements.txt",
        "agents/openai.yaml",
        "RULES.md",
        "README.md",
        "INSTALL.md",
        "COMPATIBILITY.md",
        "CHANGELOG.md",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "SECURITY.md",
        "QA.md",
        "CONTRIBUTING.md",
        ".env.example",
        ".gitignore",
        "scripts/gb.py",
        "scripts/install.sh",
        "scripts/playwright.sh",
        "scripts/package_release.py",
        "assets/storyboard.css",
        "assets/storyboard.js",
        "assets/storyboard-template.html",
        "assets/brand-logo.png",
        "assets/review-v2.css",
        "assets/review-v2.js",
    ]
    result = [ROOT / p for p in exact]
    for directory, pattern in [
        ("scripts/getbrolls", "*.py"),
        ("scripts/broll", "*.sh"),
        ("scripts/instagram", "*.py"),
        ("tests", "test_*.py"),
        ("references", "*.md"),
        ("schemas", "*.json"),
        (".github/workflows", "*.yml"),
    ]:
        result.extend(
            p
            for p in (ROOT / directory).rglob(pattern)
            if p.is_file() and "__pycache__" not in p.parts
        )
    return sorted(set(result))


def main():
    target = ROOT / f"dist/get-brolls-{__version__}.zip"
    target.parent.mkdir(exist_ok=True)
    pending = target.with_suffix(".tmp")
    with zipfile.ZipFile(pending, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for path in files():
            if path.is_symlink():
                raise ValueError("Release refuses symlinks: " + str(path))
            info = zipfile.ZipInfo(str(Path("get-brolls") / path.relative_to(ROOT)))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if path.suffix == ".sh" else 0o100644) << 16
            out.writestr(info, path.read_bytes())
    pending.replace(target)
    print(str(target))
    print(f"{len(files())} files, {target.stat().st_size} bytes")


if __name__ == "__main__":
    main()
