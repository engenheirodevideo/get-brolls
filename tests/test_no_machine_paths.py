"""Guarda: nenhuma saída da CLI leva caminho desta máquina fora dos campos que o contrato deixa absolutos.

Roda a matriz do ouro (`fluxo` e `layout0`) e uma matriz dos comandos da 2.6 (cliente, template,
`init`, componentes, análise, roteiro, export, plugins, perfil, runtime), com recusas, num ambiente
fechado onde a pasta pessoal, o `$GB_HOME`, o projeto e a pasta temporária são pastas irmãs: nenhuma
dentro da outra, então `~` não esconde nada. A normalização do ouro troca a pasta da rodada (nas
duas grafias, crua e escapada em JSON) por `<T>`; qualquer `<T>` em stdout, stderr, campo JSON ou
linha acrescentada a um log é um caminho da máquina. Só os campos de `ALLOWED_FIELDS` e `ERROR_FIELDS` — dados que a
máquina usa e o contrato lista (`docs/CLI_CONTRACT.md`, "Sem caminho da máquina") — podem levá-lo.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import unittest.mock
import urllib.parse
from collections.abc import Set as AbstractSet
from pathlib import Path

import _golden
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import CLI_ARGV, ROOT

from getbrolls import labels, recipes

MACHINE = "<T>"

# Os campos que ficam absolutos, exatos por comando (`stdout.json.` + caminho, `[]` no lugar dos índices):
# o arquivo ou a pasta que a máquina (ou a pessoa) abre ou passa de volta, e o que a pessoa digitou (as
# linhas de comando sugeridas repetem o `--project`). É a tabela de `docs/CLI_CONTRACT.md`
# ("Campos que ficam absolutos"): `test_the_allowlist_is_the_contract_table` prende uma à outra.
ALLOWED_FIELDS = frozenset(
    {
        ("approve", "stdout.json.local_path"),
        ("assets list", "stdout.json.assets[].path"),
        ("brief", "stdout.json.brief"),
        ("brief", "stdout.json.beats[].commands.inspect"),
        ("brief", "stdout.json.beats[].commands.preview"),
        ("brief", "stdout.json.beats[].commands.resolve"),
        ("brief", "stdout.json.beats[].commands.search"),
        ("browser-plan", "stdout.json.output"),
        ("browser-plan", "stdout.json.cwd"),
        ("client add", "stdout.json.client.root"),
        ("client list", "stdout.json.clients[].root"),
        ("client show", "stdout.json.client.root"),
        ("deliver", "stdout.json.delivery"),
        ("deliver", "stdout.json.readme"),
        ("doctor", "stdout.json.install.app_log"),
        ("doctor", "stdout.json.install.env_file.path"),
        ("doctor", "stdout.json.install.env_file.searched[]"),
        ("doctor", "stdout.json.install.gb_home"),
        ("doctor", "stdout.json.install.profile.trust_file"),
        ("doctor", "stdout.json.install.profile.values.cache_dir.value"),
        ("doctor", "stdout.json.install.profile.values.home.value"),
        ("doctor", "stdout.json.install.profile.values.runtime_dir.value"),
        ("doctor", "stdout.json.install.runtime.tools.path"),
        ("doctor", "stdout.json.install.runtime.venv.path"),
        ("doctor", "stdout.json.venv.path"),
        ("doctor", "stdout.json.venv.python"),
        ("fetch", "stdout.json.local_path"),
        ("import-review", "stdout.json.file"),
        ("import-review", "stdout.json.review"),
        ("init", "stdout.json.project"),
        ("init", "stdout.json.summary.do.command"),
        ("init-brief", "stdout.json.brief"),
        ("init-rules", "stdout.json.rules"),
        ("migrate plan", "stdout.json.project"),
        ("permit", "stdout.json.local_path"),
        ("preview", "stdout.json.files.contact_sheet"),
        ("preview", "stdout.json.files.gif"),
        ("preview", "stdout.json.files.poster"),
        ("preview", "stdout.json.files.review"),
        ("preview", "stdout.json.local_path"),
        ("profile show", "stdout.json.env_file.path"),
        ("profile show", "stdout.json.env_file.searched[]"),
        ("profile show", "stdout.json.profile.trust_file"),
        ("profile show", "stdout.json.profile.values.cache_dir.value"),
        ("profile show", "stdout.json.profile.values.home.value"),
        ("profile show", "stdout.json.profile.values.runtime_dir.value"),
        ("queue add", "stdout.json.queue"),
        ("queue mark", "stdout.json.queue"),
        ("queue next", "stdout.json.queue"),
        ("queue resume", "stdout.json.queue"),
        ("queue status", "stdout.json.queue"),
        ("recipe show", "stdout.json.path"),
        ("reject", "stdout.json.local_path"),
        ("resolve", "stdout.json.local_path"),
        ("review", "stdout.json.review"),
        ("roteiro check", "stdout.json.scenes[].components[].path"),
        ("roteiro new", "stdout.json.roteiro"),
        ("roteiro new", "stdout.json.rules"),
        ("roteiro new", "stdout.json.summary.do.command"),
        ("rules", "stdout.json.sources.asset_types"),
        ("rules", "stdout.json.sources.blocked_domains"),
        ("rules", "stdout.json.sources.browser"),
        ("rules", "stdout.json.sources.copyright"),
        ("rules", "stdout.json.sources.editorial_rules"),
        ("rules", "stdout.json.sources.preferred_domains"),
        ("rules", "stdout.json.sources.preferred_providers"),
        ("rules", "stdout.json.sources.version"),
        ("rules", "stdout.json.sources.video_format"),
        ("setup", "stdout.json.app_log"),
        ("setup", "stdout.json.gb_home"),
        ("setup", "stdout.json.tools.in_use.path"),
        ("setup", "stdout.json.tools.path"),
        ("setup", "stdout.json.tools.root"),
        ("setup", "stdout.json.venv.in_use.path"),
        ("setup", "stdout.json.venv.path"),
        ("setup", "stdout.json.venv.root"),
        ("status", "stdout.json.log"),
        ("status", "stdout.json.project"),
        ("status", "stdout.json.review_page"),
        ("status", "stdout.json.summary.do.command"),
    }
)
# No erro de qualquer comando (`stderr.json.`): o diagnostics.jsonl e o getbrolls.log que guardaram o evento.
ERROR_FIELDS = frozenset(
    {
        "log",
        "app_log",
    }
)
_TOKEN = re.compile(re.escape(MACHINE) + r"""[^\s'"`),;:\]}]*""")


def _typed_echo(text, argv):
    """Todo caminho da máquina em `text` é exatamente um argumento que a pessoa digitou neste caso."""
    tokens = [token.rstrip(".") for token in _TOKEN.findall(text)]
    return bool(tokens) and all(token in argv for token in tokens)


def command_of(record):
    """O comando de um caso, como a tabela do contrato o escreve: `status`, `client add`, `queue next`…"""
    argv: list[str] = [str(arg) for arg in record.get("argv") or ()]
    if not argv:
        return ""
    if "--action" in argv[:-1]:
        return f"{argv[0]} {argv[argv.index('--action') + 1]}"
    return argv[0]


def allowed(command, where):
    """O lugar `where` (com índices) é um campo que o contrato deixa absoluto para `command`?"""
    plain = re.sub(r"\[\d+\]", "[]", where)
    if plain.startswith("stderr.json.") and plain.removeprefix("stderr.json.") in ERROR_FIELDS:
        return True
    return (command, plain) in ALLOWED_FIELDS


def encoded_roots(tmp):
    """As formas da pasta da rodada que a normalização do ouro não troca por `<T>`: URL (com e sem `/`
    codificada, codificada duas vezes, `file://`) e JSON com `\\/`, `\\\\/` e `\\u002f`, nas duas grafias; a
    comparação ignora maiúsculas (o `%2f` minúsculo também conta)."""
    real = os.path.realpath(tmp)
    forms = set()
    for spelling in (str(tmp), real, real.removeprefix("/private")):
        quoted = urllib.parse.quote(spelling, safe="")
        forms |= {urllib.parse.quote(spelling), quoted, urllib.parse.quote(quoted, safe="")}
        # A pasta do Windows não tem `/`: o JSON que escapa a barra parte da grafia com `/` (`as_posix`).
        forms |= {Path(spelling).as_posix().replace("/", sep) for sep in ("\\/", "\\\\/", "\\u002f")}
    forms.discard(str(tmp))
    forms.discard(real)
    return {form.lower() for form in forms}


def _leaks(text, argv, encoded):
    if MACHINE in text and not _typed_echo(text, argv):
        return True
    seen = text.lower()
    return any(form in seen for form in encoded)


def _walk(value, where, record, encoded, found):
    """Acrescenta a `found` cada texto de `value` com caminho da máquina fora dos campos permitidos."""
    if allowed(command_of(record), where):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _walk(item, f"{where}.{key}", record, encoded, found)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, f"{where}[{index}]", record, encoded, found)
    elif isinstance(value, str) and _leaks(value, set(record.get("argv") or ()), encoded):
        found.append((where, value))


def machine_hits(record, encoded: AbstractSet[str] = frozenset()):
    """`(caso, onde, texto)` de cada caminho da máquina fora dos campos permitidos num registro do ouro.

    `encoded` são as formas codificadas da pasta da rodada (`encoded_roots`), procuradas em todo texto."""
    found = []
    argv = set(record.get("argv") or ())
    for stream in ("stdout", "stderr"):
        _walk(record[stream], stream, record, encoded, found)
    for log, lines in record["appends"].items():
        found += [(f"log {Path(log).name}", line) for line in lines if _leaks(line, argv, encoded)]
    return [(record["case"], where, text[:300]) for where, text in found]


def extra_cases(tmp, _state):
    """Comandos da 2.6, com recusas, num projeto de layout 1 e numa pasta de cliente."""
    work = tmp / "work"
    p, v2, clients_root = str(work / "proj"), str(work / "v2"), str(work / "Clientes")
    yield "capabilities", ["capabilities"], {}
    yield "setup_where", ["setup", "--where"], {"bare": True}
    yield "profile_show", ["profile", "--action", "show", "--json"], {}
    yield "client_add", ["client", "--action", "add", "--slug", "acme", "--root", clients_root], {}
    yield "client_add_again", ["client", "--action", "add", "--slug", "acme", "--root", clients_root], {}
    yield "client_list", ["client", "--action", "list"], {}
    yield "client_show", ["client", "--action", "show", "--slug", "acme"], {}
    yield "init", ["init", "--project", v2, "--client", "acme"], {}
    yield "init_again", ["init", "--project", v2, "--client", "acme"], {}
    yield "status_v2", ["status", "--project", v2], {}
    yield "roteiro_new", ["roteiro", "--action", "new", "--genero", "reels", "--tema", "Teste", "--project", v2], {}
    yield "roteiro_check", ["roteiro", "--action", "check", "--project", v2], {}
    yield "roteiro_plan", ["roteiro", "--action", "plan", "--project", v2], {}
    yield "assets_list", ["assets", "--action", "list", "--project", v2], {}
    yield "assets_where", ["assets", "--action", "where", "--kind", "sfx", "--name", "whoosh", "--project", v2], {}
    # A fila parada e retomada: o `--expect` é o `at` que o `status` mostrou, lido do disco entre os casos.
    q = str(work / "qproj")
    yield "queue_add_q", ["queue", "--action", "add", "--provider", "instagram", *REELS, "--project", q], {}
    yield "queue_next_q", ["queue", "--action", "next", "--project", q], {}
    yield (
        "queue_mark_q",
        ["queue", "--action", "mark", "--id", "instagram:ABC123xyz", "--failed", "--reason", "login", "--project", q],
        {},
    )
    yield "queue_next_stopped_q", ["queue", "--action", "next", "--project", q], {}
    yield "queue_status_stopped_q", ["queue", "--action", "status", "--project", q], {}
    stopped_at = json.loads((work / "qproj" / "work" / "queue.json").read_text(encoding="utf-8"))["providers"][
        "instagram"
    ]["stopped"]["at"]
    yield (
        "queue_resume_q",
        [
            *("queue", "--action", "resume", "--provider", "instagram", "--by", "Bruno"),
            *("--phrase", "voltei a logar", "--expect", stopped_at, "--project", q),
        ],
        {},
    )
    yield "analysis_register", ["analysis", "--action", "register", "--path", "aroll/c01.mp4", "--project", v2], {}
    # Uma ferramenta que falha e diz o caminho no stderr (o ffprobe numa mídia quebrada): o erro leva a marca.
    yield "resolve_broken_media", ["resolve", "--file", "v2/aroll/quebrado.mp4", "--project", v2], {}
    yield "analysis_list", ["analysis", "--action", "list", "--project", v2], {}
    yield "analysis_check", ["analysis", "--action", "check", "--project", v2], {}
    yield "template_freeze", ["template", "--action", "freeze", "--from", v2, "--slug", "reels-acme"], {}
    yield "recipe_list", ["recipe", "--action", "list"], {}
    yield "recipe_show", ["recipe", "--action", "show", "--ref", REF], {}
    # `init --recipe`: por `--project` (o comando sugerido repete o que a pessoa digitou) e por `--client --slug`.
    yield (
        "init_recipe_project",
        ["init", "--recipe", REF, "--client", "acme", "--project", str(work / "v4"), "--tema", "T"],
        {},
    )
    yield (
        "init_recipe_slug",
        ["init", "--recipe", "reels-acme@1", "--client", "acme", "--slug", "rx", "--tema", "T"],
        {},
    )
    yield "export_dry", ["export", "--to", "meu_exporter", "--dry-run", "--project", v2], {}
    yield "migrate_plan", ["migrate", "--action", "plan", "--project", p], {}
    yield "plugins_list", ["plugins", "--action", "list"], {}
    yield "plugins_missing", ["plugins", "--action", "install", "--source", str(work / "sem-plugin")], {}
    yield "x_list", ["x", "--list"], {}
    yield "status_missing_v3", ["status", "--project", str(work / "v3")], {}
    # `--project` com `~` (a pasta pessoal da rodada): a recusa devolve `~/…` como digitado, nunca a pasta.
    yield "status_tilde", ["status", "--project", "~/sem-projeto"], {}
    yield "review_tilde", ["review", "--project", "~/sem-projeto"], {}
    yield "assets_ambiguous", ["assets", "--action", "where", "--kind", "marca", "--name", "logo", "--project", v2], {}
    yield "roteiro_check_ambiguous", ["roteiro", "--action", "check", "--project", v2], {}


REF = "cat:getbrolls/recipe/acme.reels-acme@1"
REELS = ("https://www.instagram.com/reel/ABC123xyz/", "https://www.instagram.com/reel/DEF456uvw/")


def prepare_extra(tmp):
    """O que a matriz da 2.6 encontra em disco: a pasta de clientes, o logo duplicado do cliente e uma mídia."""
    work = tmp / "work"
    (work / "proj").mkdir(parents=True, exist_ok=True)
    (work / "Clientes").mkdir(parents=True, exist_ok=True)
    (work / "media").mkdir(parents=True, exist_ok=True)
    (work / "sem-plugin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(_golden.MEDIA, work / "media" / "original.mp4")


def _seal_recipe(tmp):
    """A receita `reels-acme` do cliente `acme`, selada do projeto `v2` (que já tem o roteiro)."""
    with unittest.mock.patch.dict(os.environ, {"GB_HOME": str(tmp / "gbhome")}):
        recipes.seal_project(tmp / "work" / "v2", "reels-acme", client="acme", title="Reels")


def after_case(tmp, case):
    """Arruma o disco entre casos: a mídia do A-ROLL e o logo do cliente depois do `init`; no fim, um
    segundo logo, que deixa o nome ambíguo."""
    marca = tmp / "work" / "Clientes" / "acme" / "assets" / "marca"
    if case == "init":
        v2 = tmp / "work" / "v2"
        (v2 / "aroll").mkdir(parents=True, exist_ok=True)
        shutil.copy2(_golden.MEDIA, v2 / "aroll" / "c01.mp4")
        (v2 / "aroll" / "quebrado.mp4").write_bytes(b"isto nao e um mp4")
        (marca / "logo.svg").write_bytes(b"<svg/>")
    if case == "roteiro_new":
        _seal_recipe(tmp)
    if case == "status_missing_v3":
        (marca / "logo.png").write_bytes(b"png")


def record_extra(tmp):
    prepare_extra(tmp)
    _golden.plant_log_salt(tmp / "gbhome")
    executables = {"<PY>": sys.executable, "<FFMPEG>": _golden.ffmpeg_pin(tmp), "<FFPROBE>": shutil.which("ffprobe")}
    roots = _golden.Roots(tmp, ROOT, executables)
    run = _golden.Run(
        list(CLI_ARGV), tmp, _golden.Normalizer(roots, _golden.version_of(ROOT), today=_golden.run_date(tmp))
    )
    records = []
    for case, argv, options in extra_cases(tmp, {}):
        records.append(_golden._record_case(run, case, argv, options))  # pylint: disable=protected-access
        after_case(tmp, case)
    return records


class NoMachinePathTests(unittest.TestCase):
    maxDiff = None

    def run_dir(self):
        raw = tempfile.mkdtemp(prefix="gb-guarda-")
        self.addCleanup(shutil.rmtree, raw, ignore_errors=True)
        tmp = Path(raw)
        # As raízes da rodada são irmãs: nenhuma dentro da outra, nem da pasta temporária dela.
        for name in ("userhome", "gbhome", "tmp", "work"):
            (tmp / name).mkdir(parents=True, exist_ok=True)
        return tmp

    def assert_clean(self, records, tmp):
        encoded = encoded_roots(tmp)
        hits = [hit for record in records for hit in machine_hits(record, encoded)]
        self.assertEqual([], hits, "\n".join(f"{case} | {where} | {text}" for case, where, text in hits))

    def test_the_golden_matrix_leaks_no_machine_path(self):
        for scenario in ("fluxo", "layout0"):
            with self.subTest(scenario=scenario):
                tmp = self.run_dir()
                self.assert_clean(_golden.record_scenario(list(CLI_ARGV), ROOT, scenario, tmp), tmp)

    def test_the_2_6_matrix_leaks_no_machine_path(self):
        tmp = self.run_dir()
        records = record_extra(tmp)
        # A matriz anda de verdade: o cliente, o projeto e o template existem no fim.
        self.assertTrue((tmp / "work" / "Clientes" / "acme" / "client.json").is_file())
        self.assertTrue((tmp / "work" / "v2" / "project.json").is_file())
        self.assert_clean(records, tmp)

    def test_the_allowlist_is_the_contract_table(self):
        # A guarda não deixa passar campo que o contrato não lista: pôr `message` ou `warnings` aqui sem
        # mudar a tabela de docs/CLI_CONTRACT.md (e o contrato com a pessoa) falha.
        text = (ROOT / "docs" / "CLI_CONTRACT.md").read_text(encoding="utf-8")
        table = text.split("<!-- campos-absolutos -->", 1)[1].split("<!-- /campos-absolutos -->", 1)[0]
        fields, errors = set(), set()
        for line in table.splitlines()[3:]:
            command, cells = (cell.strip() for cell in line.strip("|").split("|"))
            names = re.findall(r"`([^`]+)`", cells)
            if command == "qualquer comando, no erro":
                errors |= {name.removeprefix("stderr.") for name in names}
            else:
                fields |= {(command.strip("`"), "stdout.json." + name) for name in names}
        self.assertEqual(set(ERROR_FIELDS), errors)
        self.assertEqual(set(ALLOWED_FIELDS), fields)

    def test_the_guard_sees_a_machine_path_in_a_message(self):
        record = {
            "case": "sonda",
            "argv": ["status"],
            "stdout": {"json": {"summary": {"line": f"Gravei em {MACHINE}/work/proj/x."}, "project": f"{MACHINE}/p"}},
            "stderr": {"text": [f"aviso: {MACHINE}/gbhome/plugins.json"]},
            "appends": {"work/proj/brolls/getbrolls.log": [f"level=INFO event=x note={MACHINE}/y"]},
        }
        self.assertEqual(
            ["stdout.json.summary.line", "stderr.text[0]", "log getbrolls.log"],
            [where for _, where, _ in machine_hits(record)],
        )

    def test_the_guard_sees_an_encoded_run_folder(self):
        tmp = self.run_dir()
        quoted = urllib.parse.quote(str(tmp / "work"), safe="")
        record = {
            "case": "sonda",
            "stdout": {"json": {"q": f"a={quoted}", "url": f"file://{MACHINE}/work/x"}},
            "stderr": {"json": {"error": tmp.as_posix().replace("/", "\\/") + "\\/x"}},
            "appends": {},
        }
        self.assertEqual(
            ["stdout.json.q", "stdout.json.url", "stderr.json.error"],
            [where for _, where, _ in machine_hits(record, encoded_roots(tmp))],
        )


class FreeTextPathTests(unittest.TestCase):
    """Texto livre (mensagem, linha de log, diagnostics.jsonl): o caminho fora de toda pasta conhecida vira só o
    hash do nome, com espaço no nome, em Windows, codificado, colado a uma opção ou entre colchetes. Só cadeias
    de texto: rodam em qualquer sistema."""

    HASH = re.escape(labels.OUTSIDE) + r"/#[0-9a-f]{8}"

    def assert_hidden(self, text, pattern, *gone):
        for run in (labels.outside, labels.for_log):
            with self.subTest(run=run.__name__, text=text):
                shown = run(text)
                self.assertRegex(shown, pattern)
                for word in gone:
                    self.assertNotIn(word, shown)

    def test_a_path_with_spaces_goes_whole(self):
        for text, tail, gone in (
            ("/Volumes/Disco 1/x.mp4", r"\.mp4", ("Disco", "x.mp4")),
            ("/Volumes/pessoa/Meus Vídeos/c.mp4", r"\.mp4", ("Meus", "Vídeos", "pessoa")),
            ("/Volumes/Externo/outra area/none.json", r"\.json", ("outra", "area", "none")),
            ("/Volumes/Externo/Meu vídeo final.mp4", r"\.mp4", ("Meu", "final")),
        ):
            self.assert_hidden(text, "^" + self.HASH + tail + "$", *gone)
            # The text after the path goes with it: the extension is no longer the end of the name.
            self.assert_hidden(f"falhou em {text} de novo", "^falhou em " + self.HASH + "$", *gone, "novo")
            self.assert_hidden(f"'{text}'", "^'" + self.HASH + tail + "'$", *gone)
            self.assert_hidden(f"[Errno 2] No such file or directory: '{text}'", ": '" + self.HASH + tail + "'$", *gone)

    def test_the_text_after_an_unanchored_path_goes_with_it(self):
        # The old contract kept the prose after a path (up to six words were guessed to belong to the name). The
        # name of a path outside every folder is not known, so the path now runs to the end of its line: losing
        # the prose is accepted, leaking a part of the path is not.
        for text, head in (
            ("falhou em /Volumes/Externo/pasta porque o disco encheu", "falhou em "),
            ("falhou em /Volumes/Externo/pasta, e outro/ou isto", "falhou em "),
            ("falhou em /Volumes/Externo/a.mp4 não achei", "falhou em "),
            ("falhou em /Volumes/Externo/pasta. Veja mais/abaixo", "falhou em "),
            ("falhou em /Volumes/Externo/pasta (de novo) ok", "falhou em "),
        ):
            self.assert_hidden(text, "^" + re.escape(head) + self.HASH + r"(?:\.[a-z0-9]+)?$", "Externo", "ok")

    def test_the_prose_before_and_the_next_line_stay(self):
        for run in (labels.outside, labels.for_log):
            with self.subTest(run=run.__name__):
                shown = run("Erro: copiando /Volumes/Externo/Meus Vídeos/a b.mp4 falhou\nsegunda linha /tmp-x")
                self.assertRegex(shown, "^Erro: copiando " + self.HASH + r"(?:\.[a-z0-9]+)?\nsegunda linha /tmp-x$")

    def test_no_part_of_an_unanchored_path_leaks(self):
        nbsp, em_space = "\u00a0", "\u2003"
        for text, head, tail in (
            ("/Volumes/pessoa/Meus Videos/clip final.mp4 falhou", "", ""),
            ("C:\\Users\\pessoa\\My Videos\\a b.mp4", "", ""),
            ("/Volumes/pessoa/A/B C/D E F.mp4", "", ""),
            ("Pasta /Volumes/pessoa/Meus Videos não existe", "Pasta ", ""),
            ("/Volumes/pessoa/a b c d e f g h i.mp4", "", ""),
            (f"/Volumes/pessoa/Meus{nbsp}Videos/clip{em_space}final.mp4", "", ""),
            (f"erro em /Volumes/pessoa/a{em_space}b/c d{nbsp}e.mp4 falhou", "erro em ", ""),
            ("'/Volumes/pessoa/It\\'s mine/a.mp4'", "'", "'"),
            ('"/Volumes/pessoa/it\\"s mine/a.mp4" depois', '"', '" depois'),
            ('{"p": "/Volumes/pessoa/it\\"s mine/a.mp4"}', '{"p": "', '"}'),
            ("'/Volumes/pessoa/a b/c.mp4 falhou", "'", ""),
            ("(/Volumes/pessoa/a (1) b.mp4) depois", "(", ") depois"),
            ("[/Volumes/pessoa/Meus Videos/a.mp4], depois", "[", "], depois"),
            ("\u201c/Volumes/pessoa/Meus Videos/a.mp4\u201d fim", "\u201c", "\u201d fim"),
            ("\\Users\\pessoa\\a.mp4", "", ""),
            ("erro em \\Users\\pessoa\\Meus Videos\\a b.mp4 falhou", "erro em ", ""),
            ("'\\Users\\pessoa\\Meus Videos\\a b.mp4'", "'", "'"),
            ("file:///Volumes/pessoa/Meus Videos/a b.mp4 falhou", "", ""),
            ("-i /Volumes/pessoa/Meus Videos/a.mov -o /Volumes/pessoa/out dir/b.mp4", "-i ", ""),
        ):
            for run in (labels.outside, labels.for_log, lambda value: labels.message_of(ValueError(value))):
                with self.subTest(text=text):
                    shown = run(text)
                    pattern = "^" + re.escape(head) + self.HASH + r"(?:\.[a-z0-9]+)?" + re.escape(tail) + "$"
                    self.assertRegex(shown, pattern)
                    for word in ("pessoa", "Meus", "Videos", "clip", "final", "mine", "Volumes", "falhou", "(1)"):
                        self.assertNotIn(word, shown)

    def test_a_single_backslash_needs_two_names_to_be_a_path(self):
        for kept in (
            "a regex \\n\\d+ e \\w+\\.mp4 sobra",
            "texto \\u00e9\\u00e3 e \\x1b\\x00 fim",
            "linha\\ncom\\tescapes",
            "\\Users sozinho",
        ):
            with self.subTest(kept=kept):
                self.assertEqual(kept, labels.outside(kept))

    def test_a_path_from_an_os_error_goes_by_its_own_name(self):
        name = "/Volumes/Externo/o'brien area/x.json"
        typed = tempfile.gettempdir()
        for exc, project in (
            (FileNotFoundError(2, "No such file or directory", name), None),
            (OSError(18, "Invalid cross-device link", name, None, "/Volumes/Outro disco/y z.json"), None),
            (FileNotFoundError(2, "No such file or directory", name), typed),  # a typed project elsewhere
        ):
            with self.subTest(exc=repr(exc), project=project):
                if project is not None:
                    labels.note_typed_project(project, project)
                    self.addCleanup(labels.forget_typed_project)
                out = labels.message_of(exc)
                for word in ("Externo", "brien", "Outro", "disco", "x.json", "y z"):
                    self.assertNotIn(word, out)
                self.assertRegex(out, self.HASH)

    def test_windows_paths_go_with_every_prefix(self):
        for text, gone in (
            ("C:\\Users\\pessoa\\a.mp4", ("pessoa",)),
            ("c:/Volumes/pessoa/a.mp4", ("pessoa",)),
            ("\\\\servidor\\dados\\a.mp4", ("servidor", "dados")),
            ("\\\\?\\C:\\Users\\pessoa\\a.mp4", ("pessoa", "Users")),
            ("\\\\?\\UNC\\servidor\\dados\\a.mp4", ("servidor", "dados")),
            ("\\\\.\\C:\\Users\\pessoa\\a.mp4", ("pessoa",)),
            ("C:\\Users\\pessoa\\Meus Vídeos\\c.mp4", ("pessoa", "Meus", "Vídeos")),
            ("\\\\?\\C:\\Users\\pessoa\\Meus Vídeos\\c.mp4", ("pessoa", "Meus", "Vídeos")),
        ):
            self.assert_hidden(text, "^" + self.HASH + r"\.mp4$", *gone)
            self.assert_hidden(f"erro em {text}.", "^erro em " + self.HASH + r"\.mp4\.$", *gone)

    def test_an_encoded_path_goes_whole(self):
        for text, tail, gone in (
            ("%2FUsers%2Fpessoa%2Fa.mp4", "$", ("pessoa", "Users")),
            ("%2fvolumes%2fExterno%2fx%20y.mp4&b=1", "&b=1$", ("Externo", "x%20y")),
            ("a=%2FUsers%2Fpessoa%2Fa.mp4", "$", ("pessoa",)),
            ("%252FUsers%252Fpessoa%252Fa.mp4", "$", ("pessoa",)),
            ("C%3A%5CUsers%5Cpessoa%5Ca.mp4", "$", ("pessoa",)),
            ("%5C%5Cservidor%5Cdados%5Ca.mp4", "$", ("servidor",)),
        ):
            self.assert_hidden(text, self.HASH + r"\.mp4" + tail, *gone)
        for kept in ("a%2Fb", "x=1%2F2", "https://h/a%2Fb/c", "100%25"):
            self.assertEqual(kept, labels.outside(kept))

    def test_a_root_in_any_case_goes_on_every_system(self):
        # O texto vem de qualquer lugar (URL codificada, Windows, log de outra máquina), não só deste disco:
        # as raízes conhecidas valem em qualquer caixa, o disco distinguindo `Users` de `USERS` ou não.
        for caseless in (False, True):
            with self.subTest(caseless=caseless), unittest.mock.patch.object(labels._paths, "_CASELESS", caseless):  # pylint: disable=protected-access
                labels._roots.cache_clear()  # pylint: disable=protected-access
                self.addCleanup(labels._roots.cache_clear)  # pylint: disable=protected-access
                for text in (
                    "/volumes/Externo/x.mp4",
                    "/VOLUMES/Externo/x.mp4",
                    "/USERS/pessoa/a.mp4",
                    "/uSeRs/pessoa/a.mp4",
                    "%2fvolumes%2fExterno%2fx.mp4",
                    "%2FUSERS%2Fpessoa%2Fa.mp4",
                    "C%3A%5Cusers%5Cpessoa%5Ca.mp4",
                ):
                    self.assert_hidden(text, "^" + self.HASH + r"\.mp4$", "pessoa", "Externo", "USERS", "volumes")
                for kept in ("a%2Fb", "x=1%2F2", "https://h/a%2Fb/c", "100%25", "/v1/search", "/review/save"):
                    self.assertEqual(kept, labels.outside(kept))

    def test_a_path_glued_to_an_option_goes(self):
        for text, head in (
            ("-o/Volumes/pessoa/a.mp4", "-o"),
            ("ffmpeg -i/Volumes/pessoa/a.mp4", "ffmpeg -i"),
            ("--out=/Volumes/pessoa/a.mp4", "--out="),
            ("-o=/Volumes/pessoa/a.mp4", "-o="),
            ("x=/Volumes/pessoa/a.mp4", "x="),
            ("-oC:\\Users\\pessoa\\a.mp4", "-o"),
        ):
            self.assert_hidden(text, "^" + re.escape(head) + self.HASH + r"\.mp4$", "pessoa")
        for kept in (
            "ssh://u@h/Volumes/pessoa/a.mp4",
            "https://h/Volumes/pessoa/a.mp4",
            "veja ftp://h/Volumes/x/y",
            "a-o/Volumes/x/y",
        ):
            self.assertEqual(kept, labels.outside(kept))

    def test_a_closing_mark_after_a_path_stays_out_of_the_name(self):
        for text, head, tail in (
            ("[/Volumes/pessoa/a.mp4]", "[", "]"),
            ("(/Volumes/pessoa/a.mp4)", "(", ")"),
            ("{/Volumes/pessoa/a.mp4}", "{", "}"),
            ("(/Volumes/pessoa/a.mp4);", "(", ");"),
            ("/Volumes/pessoa/a.mp4,", "", ","),
            ("/Volumes/pessoa/a.mp4;", "", ";"),
            ("/Volumes/pessoa/a.mp4:", "", ":"),
            ("'/Volumes/pessoa/a.mp4'", "'", "'"),
            ('"/Volumes/pessoa/a.mp4"', '"', '"'),
            ("[/Volumes/pessoa/Meus Vídeos/a.mp4]", "[", "]"),
            ("\u201c/Volumes/pessoa/Meus Vídeos/a.mp4\u201d", "\u201c", "\u201d"),
            ("\u2018/Volumes/pessoa/a.mp4\u2019", "\u2018", "\u2019"),
        ):
            self.assert_hidden(text, "^" + re.escape(head) + self.HASH + r"\.mp4" + re.escape(tail) + "$", "pessoa")

    def test_a_climb_out_of_a_label_is_an_unanchored_path(self):
        # The `..` leaves the folder the label stands for: what follows is the name of another folder.
        for text in (
            "<projeto>/../Clientes Reservados/filme.mp4",
            "<cliente>/pasta/../../Outra Pessoa/filme.mp4",
            "~/../Outra Pessoa/filme.mp4",
            "$TMPDIR/../Reservado/a.mp4",
            "$GB_HOME/../Reservado/a.mp4",
        ):
            self.assert_hidden(text, "^" + self.HASH + r"\.mp4$", "Reservad", "Outra", "Pessoa", "Clientes")
            self.assert_hidden(
                f"falhou em {text}: sem acesso", "^falhou em " + self.HASH + r"\.mp4: sem acesso$", "Reservad"
            )
        for kept in ("<projeto>/../", "<projeto>/a/../b/y.mp4", "~/a/b/../c.mp4", "<projeto>/./y.mp4"):
            with self.subTest(kept=kept):
                self.assertEqual(kept, labels.outside(kept))

    def test_the_reason_after_a_path_stays(self):
        for text, reason in (
            (
                "/Volumes/Disco/in.mp4: Invalid data found when processing input",
                "Invalid data found when processing input",
            ),
            ("Unable to open /Volumes/pessoa/cookies.txt: Permission denied", "Permission denied"),
            ("erro /Volumes/Disco/Meus Vídeos/a b.mp4:\tpermissão negada", "permissão negada"),
        ):
            for run in (labels.outside, labels.for_log, lambda value: labels.message_of(ValueError(value))):
                with self.subTest(text=text):
                    shown = run(text)
                    self.assertRegex(shown, r":\s" + re.escape(reason) + "$")
                    for word in ("Disco", "pessoa", "cookies", "Meus", "in.mp4"):
                        self.assertNotIn(word, shown)
        # A drive letter is not a colon followed by a blank, and a colon without a blank is part of the name.
        self.assert_hidden("C:\\Users\\pessoa\\a.mp4", "^" + self.HASH + r"\.mp4$", "pessoa")
        self.assert_hidden("/Volumes/pessoa/a:b.mp4 falhou", "^" + self.HASH + "$", "pessoa", "falhou")

    def test_a_credential_after_the_path_goes_even_when_the_text_was_not_redacted(self):
        # The path now ends at `: `, so what follows it is no longer swallowed with it.
        for text in (
            "/Volumes/pessoa/a b.mp4: password=hunter2 falhou",
            "/Volumes/pessoa/a b.mp4: senha: abc123",
            "erro em /Volumes/pessoa/a.mp4: token: abcdefghijkl",
        ):
            for run in (labels.outside, labels.for_log, labels.text):
                with self.subTest(text=text, run=run.__name__):
                    shown = run(text)
                    for word in ("hunter2", "abc123", "abcdefghijkl"):
                        self.assertNotIn(word, shown)

    def test_an_unanchored_path_after_a_colon_goes(self):
        for text, head in (
            ("PATH=/usr/bin:/Volumes/pessoa/bin", "PATH="),
            ("PATH=/bin:/Volumes/pessoa/bin", "PATH=/bin:"),
            ("path:/Volumes/pessoa/a b.mp4", "path:"),
            ("error:/Volumes/pessoa/a.mp4", "error:"),
            ("FILE:///Volumes/pessoa/a.mp4", ""),
            ("File://localhost/Volumes/pessoa/a.mp4", ""),
            ('{"p": "\\/Users\\/pessoa\\/a.mp4"}', '{"p": "'),
            ("//servidor/dados/a.mp4", ""),
            ("..\\..\\Users\\pessoa\\a.mp4", ""),
            ("../../Volumes/pessoa/a.mp4", ""),
        ):
            pattern = "^" + re.escape(head) + self.HASH + r"(?:\.[a-z0-9]+)?(?:\"\})?$"
            self.assert_hidden(text, pattern, "pessoa", "servidor", "dados")
        for kept in ("https://h/dados/a.mp4", "https://h:80/Volumes/x/y", "// um comentario", "a//b/c"):
            with self.subTest(kept=kept):
                self.assertEqual(kept, labels.outside(kept))

    def test_an_encoded_path_is_decoded_until_it_is_stable(self):
        text = "/Volumes/pessoa/a.mp4"
        for depth in range(1, 9):
            encoded = text
            for _round in range(depth):
                encoded = urllib.parse.quote(encoded, safe="")
            with self.subTest(depth=depth):
                self.assert_hidden(encoded, "^" + self.HASH + r"\.mp4$", "pessoa")
        # Past the cap it is left as it is, in bounded time.
        encoded = text
        for _round in range(12):
            encoded = urllib.parse.quote(encoded, safe="")
        self.assertNotIn("Volumes/pessoa", labels.outside(encoded))

    def test_a_rename_error_hides_both_names_whole(self):
        first, second = "/Volumes/Externo/pessoa/a.mp4", "/Volumes/Externo/pessoa/a.mp42"
        for exc in (
            OSError(18, "Invalid cross-device link", first, None, second),
            OSError(18, "Invalid cross-device link", second, None, first),
        ):
            with self.subTest(exc=repr(exc)):
                out = labels.message_of(exc)
                self.assertEqual(2, len(re.findall(self.HASH, out)), out)
                for word in ("Externo", "pessoa", "mp42"):
                    self.assertNotIn(word, out)

    def test_the_other_line_terminators_end_a_path(self):
        for end in ("\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            with self.subTest(end=repr(end)):
                pattern = "^em " + self.HASH + r"(?:\.[a-z0-9]+)?" + re.escape(end) + "segunda linha$"
                self.assert_hidden(f"em /Volumes/pessoa/a b.mp4 falhou{end}segunda linha", pattern, "pessoa", "falhou")

    def test_a_multi_word_first_folder_of_a_backslash_root_is_a_path(self):
        for text in ("\\Documents and Settings\\Ana Maria\\a.mp4", "\\Arquivos de Programas\\Ana\\a.mp4"):
            self.assert_hidden(text, "^" + self.HASH + r"\.mp4$", "Documents", "Ana", "Settings")
        self.assertEqual("a \\um dois tres quatro cinco\\x", labels.outside("a \\um dois tres quatro cinco\\x"))

    def test_a_backtick_closes_a_quoted_path(self):
        self.assert_hidden("`/Volumes/pessoa/a b.mp4` e depois", "^`" + self.HASH + r"\.mp4` e depois$", "pessoa")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    unittest.main()
