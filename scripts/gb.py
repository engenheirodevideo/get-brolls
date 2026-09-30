#!/usr/bin/env python3
"""GET B-ROLLS CLI. Run from any working directory.

The version guard runs before importing the package, which needs Python 3.11+: keep this
file syntax-trivial (no f-strings, walrus, match or new typing) so an older Python can
parse it and print the friendly message instead of a traceback.
"""

import sys

MIN_PYTHON = (3, 11)
EXIT_PREREQUISITE = 4


def python_too_old(version_info=None):
    """Friendly PT-BR message when the interpreter is older than MIN_PYTHON, else None."""
    version = sys.version_info if version_info is None else version_info
    if tuple(version[:2]) >= MIN_PYTHON:
        return None
    # `%` de propósito: sem f-string, o arquivo continua legível por qualquer Python 3.
    return "getbrolls precisa de Python %d.%d ou mais novo; você tem %d.%d." % (  # noqa: UP031  # pylint: disable=consider-using-f-string
        MIN_PYTHON[0],
        MIN_PYTHON[1],
        version[0],
        version[1],
    )


def main():
    """Guard the interpreter version, then run the real CLI."""
    message = python_too_old()
    if message:
        sys.stderr.write(message + "\n")
        return EXIT_PREREQUISITE
    from getbrolls.cli import entrypoint  # pylint: disable=import-outside-toplevel  # só depois do guarda

    return entrypoint()


if __name__ == "__main__":
    raise SystemExit(main())
