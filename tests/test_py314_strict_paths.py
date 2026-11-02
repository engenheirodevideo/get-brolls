"""Uma pasta sem permissão é erro de E/S, não ausência, em qualquer Python.

Desde o 3.14, `Path.exists()`/`is_dir()`/`is_file()`/`is_symlink()` respondem `False` a
qualquer `OSError`; até o 3.13 eles relançavam o EACCES. Onde a resposta decide o que o
comando diz, grava ou apaga, o getbrolls usa `_paths.strict_*`: o `PermissionError` sobe
até a CLI como `IO_ERROR` (saída `1`) em vez de virar "não existe", lista vazia com saída
`0`, padrão silencioso ou gravação por cima. Cada teste tranca uma pasta (`chmod 000`) e
chama, por baixo dela, o ponto que decide.
"""

import errno
import os
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import _existence_guard

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths as _test_paths  # também põe `scripts/` no sys.path

from getbrolls import (
    _paths,
    acquisition,
    analysis,
    assets,
    bootstrap,
    brief_source,
    clients,
    commands,
    config,
    delivery,
    delivery_sweep,
    env_loader,
    export_folder,
    export_gates,
    export_plan,
    install_report,
    layout,
    library,
    media,
    memory,
    migrate,
    project_init,
    queue,
    recipe_init,
    recipes,
    review,
    roteiro,
    roteiro_ids,
    roteiro_sync,
    rules,
    runtime_parts,
    serve,
)
from getbrolls.sdk import cli as sdk_cli
from getbrolls.sdk import files as sdk_files
from getbrolls.sdk import git_source, loader, marketplace, remove, scaffold

# pylint: disable=protected-access
# Os pontos que decidem são, em boa parte, internos de cada módulo: aqui eles são chamados
# direto, sob uma pasta trancada, sem montar o comando inteiro.

POSIX = os.name != "nt"
ROOT_USER = POSIX and os.geteuid() == 0
NEEDS_EACCES = unittest.skipUnless(POSIX and not ROOT_USER, "permissões POSIX, sem root")


class Locked(unittest.TestCase):
    """Uma pasta só do teste e, dentro dela, `trancada/` sem permissão nenhuma."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-py314-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.locked = self.base / "trancada"
        self.locked.mkdir()
        self.locked.chmod(0)
        self.addCleanup(self.locked.chmod, 0o700)
        # Um projeto, um arquivo e um `$GB_HOME` por baixo da pasta trancada.
        self.project = self.locked / "projeto"
        self.home = self.locked / "gbhome"

    def assert_denied(self, calls):
        for label, call in calls.items():
            with self.subTest(call=label), self.assertRaises(PermissionError):
                call()

    def locked_home(self):
        return patch.dict(os.environ, {"GB_HOME": str(self.home)})


@NEEDS_EACCES
class StrictLexistsTests(Locked):
    def test_strict_lexists_raises_on_a_locked_folder_and_sees_a_broken_link(self):
        with self.assertRaises(PermissionError):
            _paths.strict_lexists(self.locked / "x")
        self.assertFalse(_paths.strict_lexists(self.base / "nao-existe"))
        self.assertFalse(_paths.strict_lexists(self.base / "x\0y"))
        broken = self.base / "link-quebrado"
        broken.symlink_to(self.base / "sumiu")
        self.assertTrue(_paths.strict_lexists(broken))
        self.assertFalse(_paths.strict_exists(broken))

    def test_a_name_too_long_is_an_io_error_as_on_python_3_11(self):
        # O 3.11 só tomava por ausência ENOENT, ENOTDIR, EBADF e ELOOP: um nome longo demais
        # relançava (IO_ERROR na CLI). As `strict_*` mantêm isso; o 3.14 diria `False`.
        too_long = self.base / ("a" * 300)
        for check in (_paths.strict_exists, _paths.strict_is_dir, _paths.strict_is_file, _paths.strict_lexists):
            with self.subTest(check=check.__name__), self.assertRaises(OSError) as caught:
                check(too_long)
            self.assertEqual(errno.ENAMETOOLONG, caught.exception.errno)


@NEEDS_EACCES
class ProjectLayoutTests(Locked):
    def test_a_locked_project_is_an_io_error_for_layout_init_and_migrate(self):
        self.assert_denied(
            {
                # Antes: `None` (layout 0), e os clipes iam para a pasta antiga.
                "layout.load_project": lambda: layout.load_project(self.project),
                # Antes: passava calado, e o init seguia para gravar.
                "project_init._refuse_existing": lambda: project_init._refuse_existing(self.project, str(self.project)),
                "recipe_init.plan_instance": lambda: recipe_init.plan_instance(self.project, "reels@1", "acme", "Tema"),
                # Antes: "não é uma pasta" (INVALID_DATA).
                "migrate.run": lambda: migrate.run(SimpleNamespace(project=str(self.project))),
            }
        )


@NEEDS_EACCES
class ProjectStateTests(Locked):
    def test_locked_project_state_is_never_read_as_empty(self):
        brolls = self.project / "brolls"
        with self.locked_home():
            self.assert_denied(
                {
                    # Antes: 0 eventos, 0 referências, sem fila, sem decisões: saída 0.
                    "commands.status_journal": lambda: commands.status_journal(brolls),
                    "commands.status_references": lambda: commands.status_references(brolls),
                    "memory.load_references": lambda: memory.load_references(brolls),
                    "queue.load": lambda: queue.load(brolls / "queue.json"),
                    "queue.hint": lambda: queue.hint(self.project),
                    "queue.record_cooldown": lambda: queue.record_cooldown(self.project, "youtube", "429"),
                    "review.latest_review_file": lambda: review.latest_review_file(brolls),
                    # Antes: biblioteca vazia, depois gravada por cima do índice de verdade.
                    "library.load_index": library.load_index,
                }
            )

    def test_a_local_file_in_a_locked_folder_is_not_reported_missing(self):
        args = SimpleNamespace(file=str(self.locked / "clipe.mp4"), url=None)
        # Antes: "Arquivo local inexistente." (INVALID_DATA).
        self.assert_denied({"commands._resolve_build_candidate": lambda: commands._resolve_build_candidate(args)})


@NEEDS_EACCES
class SettingsFilesTests(Locked):
    def test_rules_brief_env_and_pins_in_a_locked_folder_are_io_errors(self):
        target = str(self.locked / "arquivo")
        cases = {
            # Antes: o RULES.md global sumia calado, e as restrições dele junto.
            "rules.rules_layers (global)": ({"GB_HOME": str(self.home)}, lambda: rules.rules_layers(self.base)),
            # Antes: "aponta para um arquivo que não existe".
            "rules.rules_layers (GB_RULES_FILE)": ({"GB_RULES_FILE": target}, lambda: rules.rules_layers(self.base)),
            "brief_source.load_brief": ({"GB_BRIEF_FILE": target}, lambda: brief_source.load_brief(self.base)),
            "config.executable_override": (
                {"GB_FFMPEG_PATH": target},
                lambda: config.executable_override("GB_FFMPEG_PATH"),
            ),
            "config.venv_override": ({"GB_VENV_PATH": target}, config.venv_override),
            "media.find_font": ({"GB_FONT_FILE": target}, media.find_font),
            # Antes: nenhuma chave carregada, calado.
            "env_loader.load_env": ({}, lambda: env_loader.load_env(target)),
            "rules.video_format_unchosen": ({}, lambda: rules.video_format_unchosen(self.project, {})),
            "rules.seed_project_rules": ({}, lambda: rules.seed_project_rules(self.project, "reels")),
        }
        for label, (env, call) in cases.items():
            with self.subTest(call=label), patch.dict(os.environ, env), self.assertRaises(PermissionError):
                call()


@NEEDS_EACCES
class RoteiroTests(Locked):
    def test_out_of_sync_never_raises_and_sends_a_locked_project_to_the_sync(self):
        # A escada do `status`/`brief`/`deliver` só pergunta; o `sync`, que confere com
        # `strict_*`, é quem mostra o IO_ERROR. Antes: o 3.11 levantava aqui, apesar do
        # docstring ("nunca levanta"), e o 3.14 já respondia "fora de sincronia".
        self.assertTrue(roteiro_sync.out_of_sync(self.project, []))

    def test_a_locked_project_is_an_io_error_for_roteiro_and_export_gates(self):
        self.assert_denied(
            {
                # Antes: "ROTEIRO.md não existe; crie com new".
                "roteiro.load_text": lambda: roteiro.load_text(self.project),
                "roteiro.path_problem": lambda: roteiro.path_problem(self.project / "ROTEIRO.md"),
                # Antes: estado vazio, e o sync renumerava as cenas a partir de 1.
                "roteiro_ids.read_state": lambda: roteiro_ids.read_state(self.project),
                # Antes: lia estado velho em vez de recusar a gravação pendente.
                "roteiro_sync.ensure_no_pending": lambda: roteiro_sync.ensure_no_pending(self.project),
                "roteiro_sync._ledger": lambda: roteiro_sync._ledger(self.project, False),
                # Antes: export sem clipes, ou "fora de sincronia".
                "export_gates._manifest": lambda: export_gates._manifest(self.project),
                "export_gates._in_sync": lambda: export_gates._in_sync(self.project, []),
            }
        )


@NEEDS_EACCES
class DeliveryExportTests(Locked):
    def test_a_locked_project_is_an_io_error_for_deliver_and_export(self):
        self.assert_denied(
            {
                # Antes: os beats do BRIEF.md ficavam de fora da entrega.
                "delivery._brief_beats": lambda: delivery._brief_beats(self.project, rules={}),
                # Antes: nada varrido nem relatado.
                "delivery_sweep.sweep": lambda: delivery_sweep.sweep(self.project / "entrega", set(), True),
                # Antes: numeração recomeçava em 1.
                "export_folder.next_number": lambda: export_folder.next_number(self.locked / "exports"),
                # Antes: `ok: true` sem arquivos, saída 0.
                "analysis.check_all": lambda: analysis.check_all(self.project),
            }
        )


@NEEDS_EACCES
class ClientsTests(Locked):
    def test_a_locked_home_or_root_is_an_io_error_for_clients_and_recipes(self):
        with self.locked_home():
            self.assert_denied(
                {
                    # Antes: registro vazio, depois gravado por cima do de verdade.
                    "clients.load_registry": clients.load_registry,
                    "recipes.list_recipes": recipes.list_recipes,
                }
            )
        # Antes: "--root tem que ser uma pasta existente".
        self.assert_denied({"clients._checked_base": lambda: clients._checked_base(str(self.locked / "raiz"))})


@NEEDS_EACCES
class RecipesTests(Locked):
    def test_a_locked_recipe_version_is_an_io_error_not_a_missing_file(self):
        self.assert_denied(
            {
                # Antes (3.14): "não é um arquivo comum", como se o json não existisse.
                "recipes._read_json": lambda: recipes._read_json(
                    self.locked / recipes.RECIPE_FILE, recipes.RECIPE_FILE, recipes.FAMILY
                ),
                # Antes (3.14): "getbrolls-recipe.sha256 não existe".
                "recipes._seal_check": lambda: recipes._seal_check(self.locked, b""),
                # Antes (3.11 e 3.14): `os.walk` engolia o erro e a versão parecia íntegra.
                "recipes._tree_problems": lambda: recipes._tree_problems(self.locked, {"files": []}, set()),
                "recipes._versions": lambda: recipes._versions(self.locked),
            }
        )


@NEEDS_EACCES
class PluginsTests(Locked):
    def test_a_locked_home_is_an_io_error_for_the_plugin_state(self):
        with self.locked_home():
            self.assert_denied(
                {
                    # Antes: todo plugin desligado, lista vazia com saída 0.
                    "loader.read_state": loader.read_state,
                    "loader.entries": loader.entries,
                    "marketplace.read_state": marketplace.read_state,
                }
            )

    def test_a_locked_plugin_folder_is_an_io_error(self):
        folder = self.locked / "plugin"
        self.assert_denied(
            {
                # Antes: "--path tem que ser a pasta do plugin" (USAGE_ERROR).
                "sdk.cli._check": lambda: sdk_cli._check(SimpleNamespace(path=str(folder))),
                # Antes: a pasta local virava URL e era recusada como URL.
                "git_source.parse_source": lambda: git_source.parse_source(str(folder)),
                # Antes: "não instalado".
                "remove._plugin_row": lambda: remove._plugin_row("plugin", folder),
                "scaffold.new": lambda: scaffold.new("meu_plugin", "provider", parent=self.locked),
            }
        )


@NEEDS_EACCES
class ServeTests(Locked):
    def test_a_locked_project_is_an_io_error_for_serve(self):
        # Antes: "Storyboard não encontrado; gere-o com review".
        self.assert_denied(
            {
                "serve.start": lambda: serve.start(self.project),
                "serve.start_background": lambda: serve.start_background(self.project),
            }
        )


@NEEDS_EACCES
class MarketplaceCacheTests(Locked):
    def setUp(self):
        super().setUp()
        self.open_home = self.base / "home"
        patcher = patch.dict(os.environ, {"GB_HOME": str(self.open_home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cache = marketplace.cache_path("demo")
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text("{}", encoding="utf-8")

    def lock(self, folder):
        folder.chmod(0)
        self.addCleanup(folder.chmod, 0o700)

    def test_a_locked_cache_folder_is_an_io_error_not_a_missing_cache(self):
        self.lock(self.cache.parent)
        pin = SimpleNamespace(name="demo", index_sha256="x", commit="0" * 40)
        # Antes: "o índice em cache sumiu; rode marketplace-update" (INVALID_DATA).
        self.assert_denied({"marketplace._read_cache": lambda: marketplace._read_cache(pin)})

    def test_a_cache_left_behind_a_locked_folder_is_reported_as_left_over(self):
        folder = self.cache.parent
        self.lock(folder.parent)
        # Antes: `None` ("saiu inteira") com a pasta ainda lá; o `remove` dizia `leftover: None`.
        self.assertEqual("PermissionError", marketplace._delete_cache(folder))
        folder.parent.chmod(0o700)
        self.assertTrue(folder.is_dir())


@NEEDS_EACCES
class ReadOnlyFolderTests(unittest.TestCase):
    """Uma pasta `r--` (0444): listar funciona, mas o `stat` de cada entrada dá EACCES.

    Até o 3.13 a conferência de cada entrada levantava; no 3.14 ela diz `False`, e as
    entradas sumiam caladas da resposta (plugins, clipes, componentes, decisões)."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-py314-ronly-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def read_only(self, folder, *names):
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            (folder / name).write_bytes(b"x")
        folder.chmod(0o444)
        self.addCleanup(folder.chmod, 0o700)
        return folder

    def test_entries_of_a_read_only_folder_are_an_io_error_where_they_decide(self):
        home = self.base / "home"
        project = self.base / "projeto"
        entrega = project / "entrega"
        with patch.dict(os.environ, {"GB_HOME": str(home)}):
            plugins = loader.plugins_root()
            (plugins / "demo").mkdir(parents=True)
            self.read_only(plugins)
            self.read_only(project / "brolls" / "clips", "a.mp4")
            self.read_only(entrega, "x.mp4")
            components = self.read_only(self.base / "marca", "logo.png")
            reviews_root = self.base / "brolls"
            self.read_only(reviews_root / "reviews", "a.json")
            calls = {
                # Antes: lista de plugins vazia com saída 0.
                "loader.entries": loader.entries,
                # Antes: o plano dizia 0 clipes a adotar.
                "migrate._legacy_clips": lambda: migrate._legacy_clips(project),
                # Antes: cada entrada passava por arquivo, e o sweep decidia apagar por isso.
                "delivery_sweep.sweep": lambda: delivery_sweep.sweep(entrega, set(), True),
                # Antes: nenhum componente do cliente.
                "assets.component_entries": lambda: assets.component_entries(components, assets.ASSET_KINDS["marca"]),
                # Antes: nenhuma decisão de revisão.
                "review.latest_review_file": lambda: review.latest_review_file(reviews_root),
            }
            for label, call in calls.items():
                with self.subTest(call=label), self.assertRaises(PermissionError):
                    call()

    def test_freezing_a_read_only_subfolder_warns_instead_of_skipping_silently(self):
        version = self.base / "versao"
        self.read_only(version / "sub", "a.json")
        warnings = []
        recipes._freeze_files(version, warnings)
        # Antes (3.14): o arquivo ficava gravável e nenhum aviso dizia isso. Um aviso por caminho: a passada
        # das pastas não repete o do arquivo que a passada dos arquivos não conseguiu ver.
        self.assertEqual(["sub/a.json: não consegui deixar somente leitura."], warnings)


@NEEDS_EACCES
class ClipCopiesTests(Locked):
    def test_a_clip_copy_linked_into_a_locked_folder_is_an_io_error_not_a_conflict(self):
        project = self.base / "projeto-aberto"
        project.mkdir()
        layout.write_project(project, layout.new_project_doc())
        (project / "broll").mkdir()
        legacy = project / "brolls" / "clips"
        legacy.mkdir(parents=True)
        (legacy / "a.mp4").write_bytes(b"antiga")
        (project / "broll" / "a.mp4").symlink_to(self.locked / "a.mp4")
        # Antes (3.14): a cópia que não dá para ler sumia do desempate e o erro dizia que
        # "nenhuma cópia bate com o sha256 registrado".
        self.assert_denied({"layout.clip_file": lambda: layout.clip_file(project, "clips/a.mp4", sha256="0" * 64)})


@NEEDS_EACCES
class PluginFilesTests(Locked):
    def test_a_plugin_route_file_in_a_locked_folder_is_an_io_error(self):
        # Antes (3.14): "a rota devolveu um arquivo fora da pasta de trabalho".
        self.assert_denied(
            {
                "acquisition.verified_route_file": lambda: acquisition.verified_route_file(
                    self.locked / "clipe.mp4", self.base, "plugin", "rota"
                )
            }
        )

    def test_a_read_only_plugin_subfolder_is_an_io_error_for_the_file_list(self):
        plugin = self.base / "plugin"
        (plugin / "sub").mkdir(parents=True)
        (plugin / "sub" / "dados.json").write_text("{}", encoding="utf-8")
        (plugin / "sub").chmod(0o444)
        self.addCleanup((plugin / "sub").chmod, 0o700)
        # Antes (3.14): o arquivo sumia da lista que o hash e a cópia do plugin usam.
        self.assert_denied({"sdk.files.counted_files": lambda: list(sdk_files.counted_files(plugin))})

    def test_a_locked_file_the_hash_discards_does_not_fail_the_file_list(self):
        plugin = self.base / "plugin"
        (plugin / ".git").mkdir(parents=True)
        (plugin / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (plugin / "plugin.py").write_text("", encoding="utf-8")
        (plugin / ".git").chmod(0o444)
        self.addCleanup((plugin / ".git").chmod, 0o700)
        # O `.git` do topo nunca entra no hash: um arquivo dele que não abre não derruba a lista.
        self.assertEqual(["plugin.py"], [rel.as_posix() for rel, _path in sdk_files.counted_files(plugin)])


@NEEDS_EACCES
class DiagnosticsTests(Locked):
    """Diagnóstico não diz "não existe" do que só não dá para ver: diz o motivo."""

    def test_setup_where_reports_a_runtime_folder_it_cannot_see(self):
        with patch.dict(os.environ, {"GB_RUNTIME_DIR": str(self.locked / "runtime")}):
            info = bootstrap.where("venv")
        # Antes: `exists: false`, como se o setup ainda não tivesse instalado nada ali.
        self.assertIsNone(info["exists"])
        self.assertEqual("sem_permissao", info["problem"])

    def locked_data_root(self):
        """Pasta de dados com `assets/` trancada e o resto faltando."""
        root = self.base / "dados"
        (root / "assets").mkdir(parents=True)
        (root / "assets").chmod(0)
        self.addCleanup((root / "assets").chmod, 0o700)
        unreadable = [entry for entry in _paths.REQUIRED_DATA if entry.startswith("assets/")]
        missing = [entry for entry in _paths.REQUIRED_DATA if entry not in unreadable]
        return root, missing, unreadable

    def test_doctor_lists_unreadable_data_files_apart_from_missing_ones(self):
        root, missing, unreadable = self.locked_data_root()
        with patch.object(_paths, "data_root", return_value=root):
            report = install_report.install_report()
            summary = commands.doctor_summary({}, (), report["data_missing"], report["data_unreadable"])
            step = bootstrap._data_step()
        # Antes: os arquivos de `assets/` saíam em `data_missing` ("Faltam: …"), como se a
        # instalação estivesse incompleta, e não sem permissão de leitura.
        self.assertEqual(missing, report["data_missing"])
        self.assertEqual(unreadable, report["data_unreadable"])
        rows = {entry["item"]: entry["note"] for entry in summary["missing"] if entry["item"].startswith("dados")}
        # Um `item` por linha: quem indexa `summary.missing` pelo item não perde nenhuma.
        self.assertEqual(len(summary["missing"]), len({entry["item"] for entry in summary["missing"]}))
        self.assertEqual(
            {
                "dados da instalação": "Faltam: " + ", ".join(missing),
                "dados da instalação (sem permissão)": "Sem permissão de leitura: " + ", ".join(unreadable),
            },
            rows,
        )
        self.assertFalse(step["ok"])
        self.assertEqual(
            "Faltam: " + ", ".join(missing) + ". Sem permissão de leitura: " + ", ".join(unreadable), step["note"]
        )

    def test_an_intact_install_has_no_unreadable_data(self):
        self.assertEqual([], install_report.install_report()["data_unreadable"])

    def test_setup_where_reports_a_runtime_part_whose_contents_it_cannot_open(self):
        runtime_dir = self.base / "runtime"
        (runtime_dir / ".venv" / "bin").mkdir(parents=True)
        with patch.dict(os.environ, {"GB_RUNTIME_DIR": str(runtime_dir)}):
            runtime_parts.write_marker("venv", runtime_dir, "ready")
            (runtime_dir / ".venv").chmod(0)
            self.addCleanup((runtime_dir / ".venv").chmod, 0o700)
            info = bootstrap.where("venv")
        # Antes: `{"error": …}` no 3.11 e `managed: false, problem: null` no 3.14.
        self.assertIs(True, info["exists"])
        self.assertEqual("sem_permissao", info["problem"])
        self.assertIsNone(info["managed"])
        self.assertEqual({"path": None, "source": None, "executable": None}, info["in_use"])

    def test_setup_where_keeps_false_for_a_folder_that_is_really_absent(self):
        with patch.dict(os.environ, {"GB_RUNTIME_DIR": str(self.base / "runtime")}):
            info = bootstrap.where("venv")
        self.assertIs(False, info["exists"])
        self.assertIsNone(info["problem"])


@NEEDS_EACCES
class ExportPlanTests(Locked):
    def test_a_clip_in_a_locked_clip_folder_is_an_io_error_not_a_vanished_file(self):
        project = self.base / "projeto-aberto"
        clips = project / "broll"
        clips.mkdir(parents=True)
        (clips / "a.mp4").write_bytes(b"x")
        clips.chmod(0)
        self.addCleanup(clips.chmod, 0o700)
        collector = export_plan._Collector(project, None)
        # Antes (3.14): "o arquivo sumiu"; o 3.11 já levantava o EACCES.
        self.assert_denied(
            {"export_plan._clip_file_problem": lambda: collector._clip_file_problem(clips / "a.mp4", clips)}
        )


# A varredura mora em `_existence_guard` (o docstring dele diz o que conta como conferência e
# como o marcador `# lenient: <motivo>` funciona); aqui ficam a regra para o pacote e as provas.
PACKAGE = _test_paths.ROOT / "scripts" / "getbrolls"
REASON = "the folder below was created by this very call"


class ExistenceCheckGuardTests(unittest.TestCase):
    def test_no_module_has_a_bare_existence_check(self):
        problems = []
        official = _test_paths.ROOT / "plugins_official"
        for path in [*sorted(PACKAGE.rglob("*.py")), *sorted(official.rglob("*.py"))]:
            name = path.relative_to(_test_paths.ROOT).as_posix()
            found = _existence_guard.bare_probes(path.read_text(encoding="utf-8"))
            problems += [f"{name}:{row}: {problem}: {code}" for row, code, problem in found]
        self.assertEqual(
            [],
            problems,
            "Use `_paths.strict_*` (uma pasta sem permissão vira IO_ERROR) ou, se ausência for "
            "mesmo a resposta certa, explique com `# lenient: <motivo>` na linha ou logo acima.",
        )

    def guard(self, body):
        source = textwrap.dedent(body).replace("REASON", REASON)
        return [(row, problem) for row, _code, problem in _existence_guard.bare_probes(source)]

    def test_a_bare_check_is_flagged_and_a_marked_one_is_not(self):
        source = """\
            def f(path):
                if path.exists():  # lenient: REASON
                    return 1
                # lenient(2): REASON
                return path.is_dir() or path.is_file()
            def g(path):
                return path.is_symlink()
            """
        self.assertEqual([(7, "sem marcador")], self.guard(source))

    def test_a_marker_inside_a_string_does_not_count(self):
        source = """\
            def f(path):
                return path.exists(), "# lenient: REASON"
            """
        self.assertEqual([(2, "sem marcador")], self.guard(source))

    def test_a_marker_covers_one_line_only(self):
        source = """\
            def f(path, other):
                # lenient: REASON
                first = path.exists()
                second = other.exists()
                return (
                    first
                    and path.is_file()  # lenient: REASON
                    and other.is_file()
                )
            """
        self.assertEqual([(4, "sem marcador"), (8, "sem marcador")], self.guard(source))

    def test_a_marker_covers_one_check_unless_it_names_the_count(self):
        source = """\
            def f(path):
                a = path.exists() or path.is_symlink()  # lenient: REASON
                b = path.is_dir() or path.is_file()  # lenient(2): REASON
                c = path.is_mount()  # lenient(2): REASON
                return a, b, c
            """
        self.assertEqual([(2, "marcador cobre 1 de 2"), (4, "marcador cobre 2 de 1")], self.guard(source))

    def test_a_vague_short_or_padded_reason_does_not_count(self):
        source = """\
            def f(path):
                a = path.exists()  # lenient: ok
                b = path.is_dir()  # lenient: TODO
                c = path.is_file()  # lenient: best effort
                d = path.is_symlink()  # lenient:
                e = path.is_mount()  # lenient: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
                g = path.is_fifo()  # lenient: padding padding padding padding padding padding
                h = path.is_junction()  # lenient: this is a check that we do here
                i = path.exists()  # lenient: optional cache, a miss refetches
                return a, b, c, d, e, g, h, i
            """
        self.assertEqual([(row, "motivo vago") for row in range(2, 9)], self.guard(source))

    def test_aliases_of_os_path_and_getattr_are_flagged(self):
        source = """\
            import os
            import os.path as osp
            from os import path as p
            from os.path import isdir, lexists as there
            def f(x):
                return (
                    os.path.islink(x),
                    osp.isfile(x),
                    p.exists(x),
                    isdir(x),
                    there(x),
                    getattr(x, "exists")(),
                    list(filter(os.path.isdir, [x])),
                )
            """
        self.assertEqual([(row, "sem marcador") for row in range(7, 14)], self.guard(source))

    def test_other_path_predicates_and_indirect_calls_are_flagged(self):
        source = """\
            import importlib
            import operator
            def f(x, name):
                return (
                    x.is_mount(),
                    x.is_junction(),
                    x.is_fifo(),
                    getattr(x, name)(),
                    operator.methodcaller("is_dir")(x),
                    __import__("os").path.isdir(x),
                    importlib.import_module("os.path").isdir(x),
                    nt._path_isdir(x),
                    getattr(x, "name"),
                )
            """
        self.assertEqual([(row, "sem marcador") for row in range(5, 13)], self.guard(source))

    def test_a_hand_made_check_with_stat_is_flagged_unless_it_raises(self):
        source = """\
            import os
            def there(path):
                try:
                    os.stat(path)
                except OSError:
                    return False
                return True
            def strict(path):
                try:
                    return path.lstat()
                except OSError as exc:
                    if exc.errno == 2:
                        return None
                    raise
            def other(path):
                try:
                    return path.stat()
                except ValueError:
                    return None
            """
        self.assertEqual([(5, "sem marcador")], self.guard(source))

    def test_a_marker_without_a_check_is_flagged(self):
        source = """\
            def f(path):
                # lenient: REASON
                return path.stat()
            """
        self.assertEqual([(3, "marcador sem conferência")], self.guard(source))


if __name__ == "__main__":
    unittest.main()
