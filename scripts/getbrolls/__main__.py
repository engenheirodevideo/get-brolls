"""`python -m getbrolls`: o mesmo ponto de entrada do comando `getbrolls`."""

from getbrolls.cli import entrypoint

if __name__ == "__main__":
    raise SystemExit(entrypoint())
