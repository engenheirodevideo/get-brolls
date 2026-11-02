"""Diferença entre duas gravações do ouro e a classificação pela lista fechada (`accepted.json`).

Uma diferença é `(caso, onde, tipo, era, ficou)`: `onde` é `exit`, um ponteiro dentro de
`stdout`/`stderr` (`stderr/json/error_code`), `files/<caminho>`, `appends/<caminho>` (um log) ou
`appends/<caminho>/<linha>/<campo>` (um diário `.jsonl`, campo a campo). Cada
item do `accepted.json` diz de que entrada da lista fechada ele é, a frase da "Migração" do
CHANGELOG que o explica e os padrões (glob) das diferenças que cobre; `case` é um padrão ou a
lista dos casos medidos, e a primeira regra que casa leva a diferença. O `diferencas_sha256` do
item prende os valores que ele leva (`digest`). O que nenhuma regra leva é defeito até o
mantenedor decidir.

    python tests/_golden_diff.py --old <v250> --new <expected> --accepted <json> --report <md> [--header <json>]
"""

import argparse
import difflib
import fnmatch
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"

# A lista fechada da 2.6: só estas entradas, e as que o mantenedor acrescentar com data.
CLOSED_LIST = {
    "saidas": "Saídas e error_code: 1 em erro de dados, 4 no doctor com pendência, LOCKED e USAGE_ERROR",
    "comando-brief": "/getbrolls:get-brolls-brief no degrau do brief",
    "versao": "--version",
    "doctor-ready-install": "ready e install do doctor",
    "trava-biblioteca": "Trava da biblioteca pessoal",
    "brief-md": "docs/BRIEF.md copiado pelo init-brief",
    "status-layout": "Chave layout do status, também no layout 0",
    "hint": "hint do erro só quando há o que fazer",
}
ADDED = "acrescimo"
_DATE = re.compile(r"^\d{4}-\d\d-\d\d$")
_HEADING = re.compile(r"^(#{1,6}) (.*?)[ \t]*$")
_VERSION_LEVEL = 2  # `## 2.6.0 — …`
CELL_MAX = 160


def load(folder):
    """{rodada/caso: registro} de uma pasta gravada por `_golden.py record`."""
    records = {}
    for path in sorted(Path(folder).glob("*/*.json")):
        records[f"{path.parent.name}/{path.stem}"] = json.loads(path.read_text(encoding="utf-8"))
    return records


def _leaves(value, prefix):
    if isinstance(value, dict) and value:
        for key in sorted(value):
            yield from _leaves(value[key], f"{prefix}/{key}")
    elif isinstance(value, list) and value and prefix.split("/")[-1] not in {"text", "lines"}:
        for index, item in enumerate(value):
            yield from _leaves(item, f"{prefix}/{index}")
    else:
        yield prefix, value


def flatten(record):
    """{onde: valor} de um registro: folhas de stdout/stderr e de cada linha de diário `.jsonl`.

    Arquivo e log entram como um valor só.
    """
    flat = {"exit": record["exit"]}
    for stream in ("stdout", "stderr"):
        if record[stream] is not None:
            flat.update(_leaves(record[stream], stream))
    for rel, entry in record["files"].items():
        flat[f"files/{rel}"] = entry.get("lines", entry)
    for rel, lines in record["appends"].items():
        if rel.endswith(".jsonl"):
            for index, line in enumerate(lines):
                flat.update(_leaves(_json_line(line), f"appends/{rel}/{index}"))
        else:
            flat[f"appends/{rel}"] = lines
    return flat


def _json_line(line):
    """A linha de um diário `.jsonl` como objeto, para comparar campo a campo; a que não é JSON fica texto."""
    try:
        return json.loads(line)
    except ValueError:
        return line


def diff(old, new):
    """Lista de diferenças entre duas gravações (dicionários de `load`)."""
    found = []
    for case in sorted(set(old) | set(new)):
        if case not in old or case not in new:
            found.append({"case": case, "where": "case", "kind": "added" if case in new else "removed"})
            continue
        before, after = flatten(old[case]), flatten(new[case])
        for where in sorted(set(before) | set(after)):
            if where not in after:
                found.append({"case": case, "where": where, "kind": "removed", "old": before[where]})
            elif where not in before:
                found.append({"case": case, "where": where, "kind": "added", "new": after[where]})
            elif before[where] != after[where]:
                found.append(
                    {"case": case, "where": where, "kind": "changed", "old": before[where], "new": after[where]}
                )
    return found


def _matches(rule, item):
    cases = rule.get("case", "*")
    cases = [cases] if isinstance(cases, str) else cases
    if not any(fnmatch.fnmatchcase(item["case"], case) for case in cases):
        return False
    if not fnmatch.fnmatchcase(item["where"], rule["where"]):
        return False
    if "kind" in rule and rule["kind"] != item["kind"]:
        return False
    return all(key not in rule or rule[key] == item.get(key) for key in ("old", "new"))


def classify(found, accepted):
    """({id do item: [diferenças]}, [sem regra], [itens que não levaram nada])."""
    taken = {entry["id"]: [] for entry in accepted["items"]}
    loose = []
    for item in found:
        owner = next(
            (entry["id"] for entry in accepted["items"] if any(_matches(rule, item) for rule in entry["match"])),
            None,
        )
        if owner is None:
            loose.append(item)
        else:
            taken[owner].append(item)
    stale = [entry_id for entry_id, items in taken.items() if not items]
    return taken, loose, stale


def digest(items):
    """sha256 das diferenças que um item leva, em qualquer ordem: o valor de cada uma entra junto."""
    ordered = sorted(items, key=lambda item: (item["case"], item["where"], item["kind"]))
    text = json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def drifted(taken, accepted):
    """Itens cujas diferenças não são mais as que o mantenedor ratificou (`diferencas_sha256`).

    A regra diz onde o item mexe; o sha prende o que ele leva ali. Outro valor no mesmo lugar, um
    campo a mais ou um caso a mais muda o sha e pede um novo ok.
    """
    return [entry["id"] for entry in accepted["items"] if entry.get("diferencas_sha256") != digest(taken[entry["id"]])]


def migration_section(text):
    """O texto de toda seção "Migração" da versão mais nova do CHANGELOG (a primeira `## `).

    Vale o título que é a linha inteira (`### Migração`, ou `#### Migração` dentro de uma
    subseção, como a das correções de uma revisão); cada seção vai até o próximo título do
    mesmo nível ou acima.
    """
    parts, level, version_seen = [], None, False
    for line in text.splitlines(keepends=True):
        heading = _HEADING.match(line)
        if heading is None:
            if level is not None:
                parts.append(line)
            continue
        depth = len(heading.group(1))
        if depth == _VERSION_LEVEL:
            if version_seen:
                break
            version_seen = True
        if level is not None and depth <= level:
            level = None
        if version_seen and level is None and heading.group(2) == "Migração":
            level = depth
        if level is not None:
            parts.append(line)
    return "".join(parts)


def pending(accepted):
    """Ids dos itens que aguardam o ok do mantenedor: `"ratificado": null` e o motivo em `pendente`."""
    return [entry["id"] for entry in accepted["items"] if entry.get("ratificado") is None and entry.get("pendente")]


def problems(accepted, changelog_text):
    """O que impede o `accepted.json` de valer: entrada fora da lista, sem data ou sem linha na Migração.

    Um item que aguarda o mantenedor fica sem data (`"ratificado": null`) e diz por que em
    `pendente` (`pending`); data e pendência juntas, ou nenhuma das duas, é problema."""
    section = migration_section(changelog_text)
    found = []
    for entry in accepted["items"]:
        if entry["lista"] not in CLOSED_LIST and entry["lista"] != ADDED:
            found.append(f"{entry['id']}: lista {entry['lista']!r} não existe")
        dated = bool(_DATE.match(entry.get("ratificado") or ""))
        if dated and entry.get("pendente"):
            found.append(f"{entry['id']}: tem data e pendência ao mesmo tempo; tire o pendente")
        elif not dated and not (entry.get("ratificado") is None and entry.get("pendente")):
            found.append(f"{entry['id']}: falta a data em que o mantenedor ratificou")
        if entry["changelog"] not in section:
            found.append(f"{entry['id']}: a frase não está na Migração do CHANGELOG: {entry['changelog']!r}")
    return found


def _cell(value):
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    text = text.replace("|", "\\|").replace("\n", " ⏎ ")
    return text if len(text) <= CELL_MAX else text[: CELL_MAX - 1] + "…"


def _rows(items):
    lines = ["| caso | onde | tipo | v2.5.0 | agora |", "|---|---|---|---|---|"]
    for item in items:
        if isinstance(item.get("old"), list) or isinstance(item.get("new"), list):
            old, new = "(ver diff abaixo)", "(ver diff abaixo)"
        else:
            old, new = _cell(item.get("old", "—")), _cell(item.get("new", "—"))
        lines.append(f"| {item['case']} | `{item['where']}` | {item['kind']} | {old} | {new} |")
    return lines


def _group_key(item):
    """Mesmo lugar e mesmo tipo de mudança, com o índice de lista trocado por N."""
    return (re.sub(r"/\d+(?=/|$)", "/N", item["where"]), item["kind"])


def _text_diffs(items):
    lines = []
    for item in items:
        old, new = item.get("old"), item.get("new")
        if not isinstance(old, list) and not isinstance(new, list):
            continue
        old = old if isinstance(old, list) else [] if old is None else [json.dumps(old, ensure_ascii=False)]
        new = new if isinstance(new, list) else [] if new is None else [json.dumps(new, ensure_ascii=False)]
        body = difflib.unified_diff(old, new, "v2.5.0", "agora", lineterm="", n=1)
        lines += [f"#### {item['case']} · `{item['where']}`", "", "```diff", *body, "```", ""]
    return lines


def _drift_section(taken, accepted):
    moved = drifted(taken, accepted)
    if not moved:
        return []
    return [
        "## Itens que mudaram depois do ok",
        "",
        "O item leva outras diferenças que as ratificadas: confira as tabelas acima e peça um novo ok.",
        "",
        *(f"- {entry_id}: `{digest(taken[entry_id])}`" for entry_id in moved),
        "",
    ]


def report(found, accepted, header):
    """O relatório em PT-BR que vai ao mantenedor junto da decisão sobre a fronteira da 2.5."""
    taken, loose, stale = classify(found, accepted)
    groups = {}
    for item in loose:
        groups.setdefault(_group_key(item), []).append(item)
    out = [
        "# Diferença do ouro contra a v2.5.0",
        "",
        *(f"- {key}: {value}" for key, value in header.items()),
        f"- diferenças: {len(found)} · na lista fechada: {len(found) - len(loose)} · fora da lista: {len(loose)}",
        "",
        "## Na lista fechada",
        "",
    ]
    for entry in accepted["items"]:
        label = CLOSED_LIST.get(entry["lista"], "acréscimo ratificado")
        out += [f"### {entry['id']} — {label}", "", f"Migração: {entry['changelog']}", ""]
        out += [*_rows(taken[entry["id"]]), ""]
    out += [
        "## Fora da lista",
        "",
        "Cada grupo é defeito (volta ao byte da v2.5.0) ou entra na lista com o seu ok.",
        "",
    ]
    for number, (key, items) in enumerate(sorted(groups.items()), 1):
        cases = sorted({item["case"] for item in items})
        out += [f"### F{number}. `{key[0]}` ({key[1]}, {len(cases)} caso(s))", "", *_rows(items), ""]
    if stale:
        out += ["## Regras sem diferença", "", *(f"- {entry_id}" for entry_id in stale), ""]
    out += _drift_section(taken, accepted)
    out += ["## Arquivos e diários que mudaram", "", *_text_diffs(found)]
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compara duas gravações do ouro e escreve o relatório.")
    parser.add_argument("--old", required=True, help="Gravação da v2.5.0")
    parser.add_argument("--new", required=True, help="Gravação da base")
    parser.add_argument("--accepted", required=True, help="accepted.json com as regras da lista fechada")
    parser.add_argument("--report", required=True, help="Arquivo .md do relatório")
    parser.add_argument("--header", help="JSON com as linhas do cabeçalho (base, tag, ffmpeg, Python, SO)")
    args = parser.parse_args(argv)
    found = diff(load(args.old), load(args.new))
    accepted = json.loads(Path(args.accepted).read_text(encoding="utf-8"))
    header = json.loads(Path(args.header).read_text(encoding="utf-8")) if args.header else {}
    Path(args.report).write_text(report(found, accepted, header), encoding="utf-8", newline="\n")
    taken, loose, stale = classify(found, accepted)
    moved = drifted(taken, accepted)
    sys.stdout.write(json.dumps({"diffs": len(found), "loose": len(loose), "stale": stale, "drifted": moved}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
