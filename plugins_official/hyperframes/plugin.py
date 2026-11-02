"""Ponte oficial do getbrolls para o HyperFrames.

Duas funções puras: `package` escreve o pedido que vai no pacote (quem grava é o core) e `readback` diz em
que pé está a pasta do projeto pelos arquivos que o leitor do core listou. Nada aqui toca disco, rede ou
processo (`tests/test_official_plugins_purity.py`). Nesta versão o pedido é só o LEIA-ME; o BRIEF e a
leitura completa da volta chegam com a entrega ao HyperFrames.
"""

LEIA_ME = """# Pacote para o HyperFrames

Os caminhos deste pacote são relativos a esta pasta. O roteiro já foi revisado por uma pessoa: preserve a
fala revisada e não publique nem renderize sem o ok dela. Quando o projeto existir, peça à pessoa que rode o
readback do getbrolls.

Paths in this package are relative to this folder. The script was reviewed by a person: keep the reviewed
speech, and do not publish or render without their approval. When the project exists, ask the person to run
the getbrolls readback.
"""


def package(_handoff):
    """O pedido à skill do HyperFrames, em `hyperframes/LEIA-ME.md`."""
    return {"files": {"hyperframes/LEIA-ME.md": LEIA_ME}, "notes": []}


def readback(view):
    """`waiting` sem arquivo na pasta do projeto, `project` com `hyperframes.json` e `index.html`, senão `broken`."""
    names = {entry["rel"] for entry in view.get("entries", []) if entry.get("kind") == "file"}
    if not names:
        state, problems = "waiting", []
    elif {"hyperframes.json", "index.html"} <= names:
        state, problems = "project", []
    else:
        state, problems = "broken", ["A pasta do projeto não tem hyperframes.json e index.html."]
    return {
        "state": state,
        "artifacts": [],
        "renders": [],
        "scenes": {"total": 0, "seen": None, "method": "count"},
        "problems": problems,
        "env_detail": {},
    }


def register(api):
    """Registra a ponte `hyperframes`."""
    api.environment(
        "hyperframes", package, readback, "Ponte para o HyperFrames: pedido de intenção e volta por arquivos"
    )
