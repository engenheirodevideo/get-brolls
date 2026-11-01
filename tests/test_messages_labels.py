"""Mensagens, avisos e linhas de log dizem a pasta pela marca, módulo por módulo.

Regra (`docs/CLI_CONTRACT.md`, "Sem caminho da máquina"): toda pasta desta máquina num texto para
a pessoa vira `<projeto>/…`, `<cliente>/…`, `$GB_HOME/…`, `~/…` ou `<pasta fora do projeto>/…`; só
o caminho que a pessoa digitou volta como ela o digitou. Cada teste provoca a falha de verdade
(um `chmod` negado, uma trava ocupada, um arquivo ilegível) com o projeto, o `$GB_HOME` e a pasta
pessoal em pastas irmãs, e confere que o texto não leva nenhuma grafia da pasta da rodada.
"""

import argparse
import errno
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import (
    brief_source,
    cli,
    clients,
    commands,
    delivery,
    delivery_files,
    delivery_sweep,
    effects,
    env_loader,
    fetch_commands,
    health,
    http,
    labels,
    library,
    logs,
    media,
    migrate,
    project_init,
    queue,
    reader,
    recipe_init,
    roteiro,
    rules,
    runtime,
    search_commands,
    serve,
    social,
)
from getbrolls import layout as layout_module
from getbrolls.sdk import install, loader, marketplace
from getbrolls.sdk import remove as plugin_remove


def denied(path):
    return PermissionError(errno.EACCES, "Permission denied", str(path))


class LabelCase(unittest.TestCase):
    """O projeto, o `$GB_HOME` e a pasta pessoal são irmãos; o projeto é o do comando em curso."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-msg-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "video"
        (self.project / "brolls").mkdir(parents=True)
        self.gbhome = self.tmp / "gbhome"
        self.gbhome.mkdir()
        person = self.tmp / "pessoa"
        person.mkdir()
        env = patch.dict(os.environ, {"GB_HOME": str(self.gbhome), "HOME": str(person), "USERPROFILE": str(person)})
        env.start()
        self.addCleanup(env.stop)
        current = patch("getbrolls.logs.project_root", return_value=self.project)
        current.start()
        self.addCleanup(current.stop)

    def assert_no_machine_path(self, text):
        real = os.path.realpath(self.tmp)
        for spelling in (str(self.tmp), real, real.removeprefix("/private")):
            self.assertNotIn(spelling, text)

    def file(self, rel, data=b"x"):
        path = self.project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class ClientFolderTests(LabelCase):
    """The folder a client is added to is named by its label, so the sentence after it is not swallowed."""

    def refusal(self, *args):
        with self.assertRaises(ValueError) as caught:
            clients.add(*args)
        return labels.message_of(caught.exception)

    def test_a_refused_client_folder_keeps_its_prose(self):
        # The bases sit outside every known folder (the temporary folder is another one): only a label keeps the prose.
        scratch = self.tmp / "scratch"
        scratch.mkdir()
        patched = patch.object(tempfile, "tempdir", str(scratch))
        patched.start()
        self.addCleanup(patched.stop)
        base = self.tmp / "bases"
        base.mkdir()
        other = self.tmp / "outro"
        other.mkdir()
        (base / "arquivo").write_text("x", encoding="utf-8")
        (base / "ligada").symlink_to(other, target_is_directory=True)
        (base / "sub").mkdir()
        (base / "sub" / "assets").symlink_to(other, target_is_directory=True)
        for slug, tail in (
            ("arquivo", " existe e não é uma pasta."),
            ("ligada", " é um link; o getbrolls não cria nada através de links."),
            ("sub", " é um link; o getbrolls não cria nada através de links."),
        ):
            with self.subTest(slug=slug):
                message = self.refusal(slug, str(base))
                self.assertTrue(message.endswith(tail), message)
                self.assertTrue(message.startswith(labels.OUTSIDE + "/#"), message)
                self.assert_no_machine_path(message)


class DeliveryTests(LabelCase):
    def warnings_of(self, module, call):
        with patch.object(module, "record_warning") as warn:
            call()
        return [args[1] for args, _ in warn.call_args_list]

    def test_a_delivered_file_that_cannot_be_frozen_is_named_under_the_project(self):
        path = self.file("entrega/01_abertura/clip.mp4")
        with patch.object(Path, "chmod", side_effect=denied(path)):
            warnings = self.warnings_of(delivery_files, lambda: delivery_files._freeze(path, "hardlink"))
        self.assertEqual(1, len(warnings), warnings)
        self.assertTrue(
            warnings[0].startswith(
                "Não consegui deixar <projeto>/entrega/01_abertura/clip.mp4 somente-leitura (Permission denied)"
            ),
            warnings,
        )
        self.assert_no_machine_path(warnings[0])

    def test_a_copy_that_cannot_be_made_editable_is_named_under_the_project(self):
        path = self.file("entrega/01_abertura/clip.mp4.part")
        path.chmod(0o444)
        self.addCleanup(path.chmod, 0o644)
        final = path.with_name("clip.mp4")
        with patch.object(Path, "chmod", side_effect=denied(path)):
            warnings = self.warnings_of(delivery_files, lambda: delivery_files._writable(path, shown=final))
        self.assertTrue(
            warnings[0].startswith(
                "Não consegui deixar a cópia <projeto>/entrega/01_abertura/clip.mp4 editável (Permission denied)"
            ),
            warnings,
        )
        self.assert_no_machine_path(warnings[0])

    def test_refusals_that_name_a_delivered_file_use_the_label_and_the_system_reason(self):
        dest = self.file("entrega/01_abertura/clip.mp4")
        src = self.file("brolls/clips/clip.mp4", b"y")
        cases = {
            "compare": lambda: delivery_files._compared(
                lambda *_: (_ for _ in ()).throw(delivery_files._CompareError("Permission denied")),
                dest,
                src,
            ),
            "edited": lambda: (_ for _ in ()).throw(delivery_files._edited(dest)),
            "make_room": lambda: delivery._make_room(dest, "o clipe novo"),
        }
        with patch.object(delivery_sweep, "thaw_unlink", side_effect=denied(dest)):
            for name, call in cases.items():
                with self.subTest(name=name), self.assertRaises((ValueError, OSError)) as ctx:
                    call()
                self.assertIn("<projeto>/entrega/01_abertura/clip.mp4", str(ctx.exception))
                self.assert_no_machine_path(str(ctx.exception))

    def test_a_comparison_that_fails_to_read_keeps_only_the_system_reason(self):
        missing = self.project / "entrega" / "sumiu.mp4"
        # A frase é a do sistema, da mesma chamada que o código sob teste faz (`stat`): "No such file..." no POSIX;
        # no Windows `open` e `stat` dão frases diferentes para uma pasta que falta.
        with self.assertRaises(OSError) as system:
            missing.stat()
        with self.assertRaises(delivery_files._CompareError) as ctx:
            delivery_files._same_bytes(missing, missing)
        self.assertEqual(system.exception.strerror, str(ctx.exception))
        self.assert_no_machine_path(str(ctx.exception))

    def test_link_or_copy_that_fails_both_ways_is_named_under_the_project(self):
        src = self.file("brolls/clips/clip.mp4")
        dest = self.project / "entrega" / "01_abertura" / "clip.mp4"
        dest.parent.mkdir(parents=True)
        with (
            patch.object(delivery.effects, "link", side_effect=OSError(errno.EXDEV, "cross-device")),
            patch.object(delivery_files, "_copy_whole", side_effect=denied(dest)),
            self.assertRaises(OSError) as ctx,
        ):
            delivery.link_or_copy(src, dest)
        self.assertIn("<projeto>/brolls/clips/clip.mp4 para <projeto>/entrega/01_abertura/clip.mp4", str(ctx.exception))
        self.assert_no_machine_path(str(ctx.exception))

    def test_sweep_warnings_are_named_under_the_project(self):
        src = self.file("brolls/clips/clip.mp4")
        with (
            patch.object(Path, "chmod", side_effect=denied(src)),
            patch.object(delivery_sweep, "record_warning") as warn,
        ):
            delivery_sweep._restore_mode(src, 0o444)
        message = warn.call_args.args[1]
        self.assertTrue(
            message.startswith("Não consegui devolver o modo somente-leitura de <projeto>/brolls/clips/clip.mp4 ("),
            message,
        )
        self.assert_no_machine_path(message)


class CommandsTests(LabelCase):
    def warnings_of(self, call, module=commands):
        with patch.object(module, "record_warning") as warn:
            call()
        return [args[1] for args, _ in warn.call_args_list]

    def test_review_and_deliver_lines_name_the_project_files_by_label(self):
        line = commands.with_summary("review", {"review": str(self.project / "brolls" / "review.html")})
        self.assertEqual({"line": "Gerei o Storyboard em <projeto>/brolls/review.html."}, line["summary"])

    def test_queue_with_unreadable_rules_keeps_only_the_reason(self):
        args = argparse.Namespace(project=str(self.project))
        with (
            patch("getbrolls.rules.load_rules", side_effect=denied(self.gbhome / "RULES.md")),
            patch.object(commands, "queue_execute", return_value={}),
        ):
            warnings = self.warnings_of(lambda: commands._execute_status_or_queue("queue", args))
        self.assertEqual(["RULES.md ignorado para o ritmo: Permission denied"], warnings)

    def test_a_search_miss_that_cannot_be_learned_keeps_only_the_reason(self):
        ctx = SimpleNamespace(dry_run=False, args=SimpleNamespace(project=None))
        with patch("getbrolls.library.learn_query", side_effect=denied(self.gbhome / "library" / "index.json")):
            warnings = self.warnings_of(
                lambda: search_commands._search_record_provider_failure(ctx, "youtube", "foguete", ValueError("x")),
                search_commands,
            )
        self.assertEqual("Permission denied", warnings[-1])

    def test_verify_that_cannot_rebuild_entrega_keeps_only_the_reason(self):
        args = argparse.Namespace(project=str(self.project))
        ledger = SimpleNamespace(data={"items": []})
        with patch.object(delivery, "build_delivery", side_effect=denied(self.project / "entrega" / "x")):
            warnings = self.warnings_of(lambda: commands._execute_verify(args, None, ledger))
        self.assertEqual(["Os arquivos estão íntegros, mas não consegui refazer entrega/: Permission denied"], warnings)

    def test_a_reference_poster_that_fails_keeps_only_the_reason(self):
        local = self.file("media/clip.mp4")
        ledger = SimpleNamespace(root=self.project / "brolls")
        candidate = {"id": "local:abc", "preview": {}, "local_path": str(local)}
        with patch("getbrolls.media.image_preview", side_effect=denied(local)):
            warnings = self.warnings_of(lambda: fetch_commands.reference_poster(ledger, candidate), fetch_commands)
        self.assertEqual(["Não consegui montar o cartaz estático desta referência (Permission denied)."], warnings)

    def test_status_of_a_missing_project_echoes_the_folder_as_typed(self):
        typed = str(self.tmp / "link-que-nao-existe" / "v3")
        with self.assertRaises(ValueError) as ctx:
            commands._execute_status_or_queue("status", argparse.Namespace(project=typed))
        self.assertEqual(f"Projeto não encontrado em {typed}; nenhum arquivo foi criado.", str(ctx.exception))


class LibraryTests(LabelCase):
    def index(self, text):
        path = self.gbhome / "library" / "index.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_broken_or_foreign_index_is_named_under_gb_home(self):
        for text, expected in (
            ("{", "$GB_HOME/library/index.json está com JSON inválido."),
            ("[]", "$GB_HOME/library/index.json não é uma biblioteca da versão"),
            ('{"schema_version": 99}', "$GB_HOME/library/index.json foi gravado por uma versão mais nova"),
        ):
            self.index(text)
            with self.subTest(text=text), self.assertRaises(ValueError) as ctx:
                library.load_index()
            self.assertIn(expected, str(ctx.exception))
            self.assert_no_machine_path(str(ctx.exception))

    def test_a_lock_held_too_long_is_named_under_gb_home(self):
        lock = self.gbhome / "library" / "index.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()
        with (
            patch.object(library, "LOCK_TIMEOUT_S", 0.0),
            self.assertRaises(library.LockedError) as ctx,
            library._locked(),
        ):
            pass
        self.assertTrue(str(ctx.exception).startswith("$GB_HOME/library/index.lock está travado"), ctx.exception)

    def test_hints_over_an_unreadable_index_warn_under_gb_home_with_the_reason_only(self):
        path = self.index("{")
        with patch("getbrolls.runtime.record_warning") as warn:
            self.assertEqual([], library.hints("foguete"))
        message = warn.call_args.args[1]
        self.assertTrue(
            message.startswith("Biblioteca de aprendizados ($GB_HOME/library/index.json) não pôde"), message
        )
        self.assert_no_machine_path(message)
        self.assertTrue(path.exists())


class ProjectFileTests(LabelCase):
    def test_a_global_rules_file_that_does_not_read_is_named_under_gb_home(self):
        (self.gbhome / "RULES.md").write_text("sem bloco json", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            rules.rules_layers(self.project)
        self.assertTrue(str(ctx.exception).startswith("O RULES.md global ($GB_HOME/RULES.md) não pôde ser lido"))
        self.assert_no_machine_path(str(ctx.exception))

    def test_a_key_the_global_rules_cannot_set_is_named_under_gb_home(self):
        (self.gbhome / "RULES.md").write_text('```json\n{"copyright": {"x": 1}}\n```\n', encoding="utf-8")
        _, warnings = rules.rules_layers(self.project)
        self.assertTrue(warnings[0].startswith('"copyright" do RULES.md global ($GB_HOME/RULES.md) foi ignorado'))
        self.assert_no_machine_path(warnings[0])

    def test_a_broken_queue_is_named_under_the_project(self):
        path = self.file("work/queue.json", b"{")
        for text in ("{", "[]", '{"schema_version": 99, "items": [], "providers": {}}'):
            path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ValueError) as ctx:
                queue.load(path)
            self.assertIn("<projeto>/work/queue.json", str(ctx.exception))
            self.assert_no_machine_path(str(ctx.exception))

    def test_a_roteiro_that_is_a_broken_link_or_a_folder_is_named_under_the_project(self):
        path = self.project / "ROTEIRO.md"
        path.mkdir()
        self.assertEqual(
            "<projeto>/ROTEIRO.md é uma pasta, não um arquivo: renomeie a pasta e repita.", roteiro.path_problem(path)
        )
        path.rmdir()
        try:
            path.symlink_to(self.tmp / "sumiu.md")
        except (OSError, NotImplementedError):
            self.skipTest("sem permissão para criar link simbólico")
        problem = roteiro.path_problem(path) or ""
        self.assertTrue(problem.startswith("<projeto>/ROTEIRO.md é um link quebrado: aponta para "), problem)
        self.assert_no_machine_path(problem)

    def test_a_brief_that_is_not_utf8_is_named_under_the_project(self):
        path = self.file("BRIEF.md", "café".encode("latin-1"))
        with self.assertRaises(ValueError) as ctx:
            brief_source.read_utf8(path)
        self.assertEqual(
            "<projeto>/BRIEF.md não está em UTF-8; salve o arquivo como UTF-8 e repita.", str(ctx.exception)
        )

    def test_migrate_of_something_that_is_not_a_folder_echoes_it_as_typed(self):
        typed = str(self.file("arquivo.txt"))
        with self.assertRaises(ValueError) as ctx:
            migrate.run(argparse.Namespace(project=typed, action="plan", client=None))
        self.assertEqual(f"{typed} não é uma pasta; aponte --project para a pasta do projeto.", str(ctx.exception))


class InstallationTests(LabelCase):
    def test_marketplace_state_cache_and_listing_are_named_under_gb_home(self):
        marketplace.state_path().write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            marketplace.read_state()
        self.assertTrue(str(ctx.exception).startswith("marketplaces.json inválido em $GB_HOME/marketplaces.json."))
        marketplace.state_path().unlink()
        self.assertEqual("$GB_HOME/marketplaces.json", marketplace.listing()["state"])
        (self.gbhome / "marketplaces").mkdir()
        (self.gbhome / "marketplaces" / "fora").symlink_to(self.tmp, target_is_directory=True)
        with self.assertRaises(ValueError) as ctx:
            marketplace._write_cache("fora", b"{}")
        self.assertTrue(str(ctx.exception).startswith("$GB_HOME/marketplaces/fora é um link"), ctx.exception)

    def test_a_plugin_that_is_not_there_is_looked_for_under_gb_home(self):
        with self.assertRaises(ValueError) as ctx:
            loader.find("sumido")
        self.assertEqual(
            "Plugin sumido não encontrado em $GB_HOME/plugins. Confira o nome da pasta.", str(ctx.exception)
        )
        with self.assertRaises(ValueError) as ctx:
            plugin_remove.remove("sumido", confirm=False)
        self.assertEqual("Plugin sumido não encontrado em $GB_HOME/plugins nem em plugins.json.", str(ctx.exception))

    def test_a_swap_that_cannot_roll_back_names_both_folders_under_gb_home(self):
        folder = self.gbhome / "plugins" / "demo"
        folder.mkdir(parents=True)
        staging = self.gbhome / "plugins" / ".staging-demo"
        staging.mkdir()
        calls = []

        def replace(source, target):
            calls.append((source, target))
            if len(calls) > 1:
                raise OSError(errno.EIO, "disco")

        with patch.object(install.effects, "replace", side_effect=replace), self.assertRaises(ValueError) as ctx:
            install._swap_in("demo", folder, staging)
        message = str(ctx.exception)
        self.assertIn("o conteúdo anterior pode estar em $GB_HOME/plugins/.old-", message)
        self.assertIn("não em $GB_HOME/plugins/demo.", message)
        self.assert_no_machine_path(message)

    def test_a_lock_file_that_is_a_link_is_named_under_gb_home(self):
        lock = self.gbhome / ".clients.lock"
        lock.symlink_to(self.tmp / "alvo")
        with self.assertRaises(ValueError) as ctx, runtime.exclusive_lock(lock, "ocupado"):
            pass
        self.assertEqual(".clients.lock em $GB_HOME é um link; apague o link e repita o comando.", str(ctx.exception))

    def test_an_ignored_env_gb_home_is_named_under_gb_home(self):
        env = self.gbhome / ".env"
        env.write_text("", encoding="utf-8")
        choice = SimpleNamespace(path=env, source="gb_home", ignored=False)
        with patch.object(env_loader, "_home_from_profile", return_value=True):
            env_loader._note_home_ignored([(1, "GB_HOME", "/outra")], env)  # o .env do $GB_HOME: a marca
        self.addCleanup(env_loader._HOME_IGNORED.clear)
        with (
            patch.object(env_loader, "load_env", return_value=set()),
            patch("getbrolls.runtime.record_warning") as warn,
        ):
            env_loader.load_env_choice(choice, warn=True)
        messages = [args[1] for args, _ in warn.call_args_list]
        self.assertTrue(any(m.startswith("GB_HOME do .env ($GB_HOME/.env) foi ignorado") for m in messages), messages)


class InitTests(LabelCase):
    def test_init_over_an_existing_project_echoes_the_folder_as_typed(self):
        (self.project / "project.json").write_text("{}", encoding="utf-8")
        typed = str(self.project)
        args = argparse.Namespace(project=typed, client=None, recipe=None, tema=None, canvas=None, fps=None)
        with self.assertRaises(ValueError) as ctx:
            project_init.run(args)
        self.assertEqual(f"project.json já existe em {typed}; este projeto já foi criado.", str(ctx.exception))

    def test_init_recipe_over_an_existing_project_names_it_by_label(self):
        (self.project / "ROTEIRO.md").write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            recipe_init.plan_instance(self.project, "reels@1", "acme", "Tema")
        self.assertEqual("ROTEIRO.md já existe em <projeto>; init --recipe não sobrescreve nada.", str(ctx.exception))


class CliAndServerTests(LabelCase):
    def test_an_io_error_before_the_command_names_the_file_by_label(self):
        env = self.gbhome / ".env"
        error = cli.EnvReadError(errno.EACCES, "Permission denied", str(env))
        self.assertEqual(
            "Não consegui ler $GB_HOME/.env (Permission denied). Confira as permissões do arquivo e repita.",
            cli.io_error_message(error),
        )
        other = denied(self.project / "brolls")
        self.assertEqual(
            "Não consegui acessar <projeto>/brolls (sem permissão). Confira as permissões e repita.",
            cli.io_error_message(other),
        )

    def test_an_io_error_in_or_above_the_typed_project_comes_back_as_typed(self):
        # The person sees which folder is locked, in their own words: the typed `--project` plus the rest
        # (or minus the folders above it), never a hash nor the machine path.
        labels.note_typed_project("travada/proj", self.tmp / "travada" / "proj")
        self.addCleanup(labels.forget_typed_project)
        for filename, shown in (
            (self.tmp / "travada" / "proj", "travada/proj"),
            (self.tmp / "travada" / "proj" / "brolls" / "x.json", str(Path("travada/proj", "brolls", "x.json"))),
            (self.tmp / "travada", "travada"),
        ):
            with self.subTest(shown=shown):
                self.assertEqual(
                    f"Não consegui acessar {shown} (sem permissão). Confira as permissões e repita.",
                    cli.io_error_message(denied(filename)),
                )
        # Above what the person typed, or outside it: the label, as for any other file.
        self.assertIn("$TMPDIR/", cli.io_error_message(denied(self.tmp)))
        self.assertIn("<pasta fora do projeto>/#", cli.io_error_message(denied("/Volumes/Externo/travada")))
        self.assertIn("$GB_HOME/x", cli.io_error_message(denied(self.gbhome / "x")))
        # The same in the error of a command (`audited`): the same message, never the raw `[Errno N]`.
        event = {}
        runtime._classify_audited_error(event, denied(self.tmp / "travada" / "proj" / "brolls"), None)
        shown = Path("travada/proj", "brolls")
        self.assertEqual(
            f"Não consegui acessar {shown} (sem permissão). Confira as permissões e repita.", event["message"]
        )

    def test_the_readers_unreadable_error_keeps_its_own_message_in_a_command_error(self):
        # `UnreadableError` has an errno, but its message already names the file and carries the hint:
        # `audited` must not swap it for the generic `os_message` sentence.
        event = {}
        runtime._classify_audited_error(
            event, reader.UnreadableError("proj/x.txt", "PermissionError", errno.EACCES), None
        )
        self.assertEqual("IO_ERROR", event["error_code"])
        self.assertIn("proj/x.txt", event["message"])
        self.assertIn("confira a permissão da pasta", event["message"])
        self.assertNotIn("Erro de E/S", event["message"])
        # Any other OSError with an errno keeps the generic sentence.
        plain = {}
        runtime._classify_audited_error(plain, PermissionError(errno.EACCES, "Permission denied"), None)
        self.assertEqual("IO_ERROR", plain["error_code"])
        self.assertEqual(labels.os_message(PermissionError(errno.EACCES, "Permission denied")), plain["message"])

    def test_a_refusal_that_repeats_the_typed_project_keeps_it_as_typed(self):
        typed = str(self.tmp / "sem-projeto")
        labels.note_typed_project(typed, typed)
        self.addCleanup(labels.forget_typed_project)
        event = {}
        runtime._classify_audited_error(event, ValueError(f"Projeto não encontrado em {typed}; nada criado."), None)
        self.assertEqual(f"Projeto não encontrado em {typed}; nada criado.", event["message"])
        # Only the whole typed path: a sibling or a file inside it goes by its label.
        for other in (typed + "2/x", typed + "/brolls/x.json"):
            with self.subTest(other=other):
                event = {}
                runtime._classify_audited_error(event, ValueError(f"falhou em {other}"), None)
                self.assert_no_machine_path(event["message"])

    @unittest.skipIf(os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0, "chmod 000 POSIX, sem root")
    def test_a_locked_folder_above_the_project_is_named_as_typed_and_writes_nothing(self):
        person = self.tmp / "pessoa"
        env = {"HOME": str(person), "USERPROFILE": str(person), "GB_HOME": str(self.gbhome)}
        locked = person / "travada"
        (locked / "proj").mkdir(parents=True)
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        for argv in (("status",), ("rules",)):
            with self.subTest(argv=argv):
                error = run_cli(*argv, "--project", "~/travada/proj", expect=1, env=env)
                # Before the command (`.env`, profile) or inside it (`audited`): either way, as typed.
                as_typed = r"(^Não consegui acessar |Permission denied: ')~/travada(/proj\S*)?[ ']"
                self.assertRegex(error["error"], as_typed)
                self.assert_no_machine_path(error["error"])
        # The error goes to `$GB_HOME/diagnostics.jsonl`, as always; the key of the hashes is not created.
        self.assertNotIn(".log-salt", [path.name for path in self.gbhome.iterdir()])

    def test_a_project_typed_with_tilde_comes_back_with_tilde(self):
        env = {"HOME": str(self.tmp / "pessoa"), "USERPROFILE": str(self.tmp / "pessoa"), "GB_HOME": str(self.gbhome)}
        cases = (
            (("status", "--project", "~/sem-projeto-gb"), "Projeto não encontrado em ~/sem-projeto-gb;"),
            (("review", "--project", "~/sem-projeto-gb"), "Projeto não encontrado em ~/sem-projeto-gb (a pasta"),
            (("migrate", "--action", "plan", "--project", "~/sem-projeto-gb"), "~/sem-projeto-gb não é uma pasta"),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv):
                error = run_cli(*argv, expect=1, env=env)
                self.assertIn(expected, error["error"])
                self.assert_no_machine_path(error["error"])
        (self.tmp / "pessoa" / "novo").mkdir(parents=True)
        (self.tmp / "pessoa" / "novo" / "project.json").write_text("{}", encoding="utf-8")
        error = run_cli("init", "--project", "~/novo", expect=1, env=env)
        self.assertEqual("project.json já existe em ~/novo; este projeto já foi criado.", error["error"])

    @unittest.skipIf(os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0, "chmod 000 POSIX, sem root")
    def test_an_env_file_the_person_typed_comes_back_as_typed(self):
        env = {"HOME": str(self.tmp / "pessoa"), "USERPROFILE": str(self.tmp / "pessoa"), "GB_HOME": str(self.gbhome)}
        locked = self.tmp / "travado.env"
        locked.write_text("GB_LOG_LEVEL=INFO\n", encoding="utf-8")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o600)
        for argv, extra in (
            ((f"--env-file={locked}", "providers"), {}),
            (("providers",), {"GB_ENV_FILE": str(locked)}),
        ):
            with self.subTest(argv=argv):
                error = run_cli(*argv, expect=1, env={**env, **extra})
                self.assertEqual(
                    f"Não consegui ler {locked} (Permission denied). Confira as permissões do arquivo e repita.",
                    error["error"],
                )
        latin = self.tmp / "latin.env"
        latin.write_bytes("GB_LOG_LEVEL=INFO # café\n".encode("utf-16"))
        error = run_cli("providers", expect=2, env={**env, "GB_ENV_FILE": str(latin)})
        self.assertEqual(f".env: {latin} não está em UTF-8. Salve o arquivo como UTF-8 e repita.", error["error"])

    def test_a_stage_on_a_missing_project_echoes_it_as_typed(self):
        typed = str(self.tmp / "link-que-nao-existe" / "v3")
        with self.assertRaises(ValueError) as ctx:
            runtime._refuse_missing_project(typed)
        self.assertTrue(str(ctx.exception).startswith(f"Projeto não encontrado em {typed} (a pasta não existe)"))

    def test_serve_without_a_storyboard_names_it_by_label(self):
        with self.assertRaises(ValueError) as ctx:
            serve.start_background(self.project, port=0)
        self.assertEqual(
            "Storyboard não encontrado em <projeto>/brolls/review.html. Gere-o antes com o comando `review`.",
            str(ctx.exception),
        )

    def test_a_download_that_cannot_be_written_names_the_file_by_label(self):
        target = self.project / "brolls" / "previews" / "x.mp4"

        class Full(io.BytesIO):
            def write(self, _data):
                raise OSError(errno.ENOSPC, "No space left on device", str(target))

        with self.assertRaises(http.ProviderError) as ctx:
            http._download_write_body(io.BytesIO(b"abc"), Full(), target, 10, ValueError)
        self.assertEqual(
            f"Falha ao gravar arquivo (errno {errno.ENOSPC}): <projeto>/brolls/previews/x.mp4", str(ctx.exception)
        )

    def test_a_project_file_that_does_not_read_is_a_layout_problem_without_its_path(self):
        if os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0:
            self.skipTest("chmod 000 POSIX, sem root")
        path = self.project / "project.json"
        path.write_text("{}", encoding="utf-8")
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        problem = layout_module.info(self.project).problem
        self.assertEqual("project.json: Permission denied", problem)


class HashKeyTests(LabelCase):
    """O hash do nome de fora numa mensagem usa a chave da instalação quando ela já existe; sem ela, uma chave
    só do processo, sem gravar nada (`docs/CLI_CONTRACT.md`)."""

    def test_an_outside_name_in_a_message_never_creates_the_key(self):
        first = labels.shown("/Volumes/Externo/Projetos/video.mp4")
        self.assertRegex(first, r"^<pasta fora do projeto>/#[0-9a-f]{8}\.mp4$")
        self.assertEqual(first, labels.shown("/Volumes/Externo/Projetos/video.mp4"))
        self.assertIn(first, labels.text("erro em /Volumes/Externo/Projetos/video.mp4"))
        self.assertFalse((self.gbhome / ".log-salt").exists())

    def test_an_existing_key_is_the_one_messages_use(self):
        (self.gbhome / ".log-salt").write_text("ab" * 32 + "\n", encoding="utf-8")
        (self.gbhome / ".log-salt").chmod(0o600)
        # The message first: it must read the key from the file, not find it cached by a log line.
        shown = labels.shown("/Volumes/Externo/video.mp4")
        self.assertEqual("<pasta fora do projeto>/" + effects._hashed("video.mp4"), shown)


class ToolOutputTests(LabelCase):
    """O stderr de uma ferramenta (ffmpeg, yt-dlp, git) e o corpo de um erro HTTP chegam à pessoa: redigidos,
    com as pastas pela marca e só então cortados (`runtime.stderr_tail`)."""

    def test_a_tool_stderr_keeps_its_reason_after_a_file_outside_every_folder(self):
        tail = runtime.stderr_tail(
            "ffmpeg version x\n/Volumes/Externo/Cliente Reservado/in.mp4: Invalid data found when processing input\n"
            "yt-dlp: Unable to open /Volumes/pessoa/cookies.txt: Permission denied\n"
        )
        self.assertIn(": Invalid data found when processing input", tail)
        self.assertIn(": Permission denied", tail)
        for word in ("Externo", "Reservado", "pessoa", "cookies", "in.mp4"):
            self.assertNotIn(word, tail)

    def test_a_tool_stderr_names_the_project_file_by_label(self):
        stderr = f"[mov,mp4 @ 0x1] moov atom not found\n{self.project}/aroll/c01.mp4: Invalid data found\n"
        tail = runtime.stderr_tail(stderr)
        self.assertEqual("[mov,mp4 @ 0x1] moov atom not found | <projeto>/aroll/c01.mp4: Invalid data found", tail)
        self.assert_no_machine_path(tail)

    def test_the_tail_cut_never_leaves_the_start_of_a_folder(self):
        root = str(self.gbhome / "pasta com espaço")
        cap = runtime.STDERR_TAIL_MAX_CHARS
        for offset in range(cap - len(root) - 4, cap + 2):
            tail = runtime.stderr_tail("x" * offset + " " + root + "/a.mp4: erro")
            for size in range(4, len(root) + 1):
                if tail.endswith(root[:size]):
                    self.fail(f"{root[:size]!r} sobrou no fim com o corte em {offset}")
            self.assertNotIn(root[:12], tail)

    def test_a_failing_tool_names_the_file_by_label_in_the_error(self):
        broken = self.project / "aroll" / "c01.mp4"
        failure = subprocess.CalledProcessError(1, ["ffprobe"], stderr=f"{broken}: Invalid data found\n")
        with (
            patch("getbrolls.media.tool_path", return_value="ffprobe"),
            patch("getbrolls.effects.run", side_effect=failure),
            self.assertRaises(ValueError) as caught,
        ):
            media.run(["ffprobe", str(broken)])
        self.assertIn("stderr: <projeto>/aroll/c01.mp4: Invalid data found", str(caught.exception))
        self.assert_no_machine_path(str(caught.exception))

    def test_a_yt_dlp_warning_names_the_folder_by_label(self):
        stderr = f"WARNING: não consegui gravar {self.gbhome}/cache/x.json\n"
        self.assertEqual(["WARNING: não consegui gravar $GB_HOME/cache/x.json"], social._extract_warnings(stderr))

    def test_a_plugin_source_error_in_health_names_the_folder_by_label(self):
        error = http.ProviderError(f"Plugin demo: falhou em {self.gbhome}/plugins/demo")
        self.assertEqual(
            "[ProviderError] Plugin demo: falhou em $GB_HOME/plugins/demo", health._plugin_detail("demo", error)
        )

    def test_the_error_message_is_labelled_before_its_cut(self):
        root = str(self.gbhome / "pasta com espaço")
        head = root[:12]
        for offset in range(runtime.REDACT_LIMIT - len(root) - 20, runtime.REDACT_LIMIT + 2):
            for error in (ValueError, http.ProviderError):
                event = {}
                token = runtime.ACTIVE.set({"warnings": []})
                try:
                    runtime._classify_audited_error(event, error("x" * offset + " " + root + "/a"), None)
                finally:
                    runtime.ACTIVE.reset(token)
                texts = [event["message"], *(warning["message"] for warning in event.get("warnings", []))]
                for text in texts:
                    if head in text:
                        self.fail(f"{head!r} sobrou na mensagem ({error.__name__}) com o corte em {offset}")
            event = {}
            runtime._classify_audited_error(event, ValueError(f"falhou em {root}/a"), None)
            self.assertEqual("falhou em $GB_HOME/pasta com espaço/a", event["message"])


class LogAndDiagnosticsTests(LabelCase):
    def test_a_diagnostics_line_names_every_known_folder_by_label(self):
        event = {"repr": f"ValueError('{self.project}/x')", "count": 1, "warnings": [f"{self.gbhome}/y"], "ok": None}
        line = runtime.diagnostic_line(event)
        self.assertIn("ValueError('<projeto>/x')", line)
        self.assertIn('"$GB_HOME/y"', line)
        self.assertIn('"count": 1', line)
        self.assert_no_machine_path(line)

    def test_every_text_of_a_diagnostics_line_is_redacted(self):
        secrets = ("hunter2", "abc123", "Zq9xW2kL", "s3cr3tvalue")
        event = {
            "message": "login falhou: password=hunter2 em ?api_key=Zq9xW2kL&x=1",
            "warnings": [
                {"code": "PROVIDER_FAILED", "message": "erro: Authorization: Bearer s3cr3tvalue"},
                {"code": "YTDLP_WARNING", "message": "senha: abc123 e https://exemplo.test/a?token=Zq9xW2kL"},
            ],
            "repr": "ValueError('password=hunter2')",
            "nested": {"list": ["--password=hunter2"], "number": 3},
        }
        line = runtime.diagnostic_line(event)
        for secret in secrets:
            self.assertNotIn(secret, line)
        loaded = json.loads(line)
        self.assertEqual(3, loaded["nested"]["number"])
        self.assertEqual("PROVIDER_FAILED", loaded["warnings"][0]["code"])
        self.assertIn("password=[REDACTED]", loaded["message"])
        self.assertEqual(line, runtime.diagnostic_line(loaded))  # idempotent

    def test_a_path_outside_every_known_folder_keeps_only_a_hash_in_logs(self):
        outside = "/Volumes/Externo/Contrato-ClienteX/video.mp4"
        hashed = r"<pasta fora do projeto>/#[0-9a-f]{8}\.mp4"
        line = runtime.diagnostic_line({"repr": f"FileNotFoundError(2, 'No such file', '{outside}')"})
        self.assertRegex(line, "'" + hashed + "'")
        self.assertRegex(logs._format_value(outside), hashed)
        record = logs.logging.LogRecord("getbrolls", logs.logging.INFO, __file__, 1, "abri %s", (outside,), None)
        logs.RedactingFilter().filter(record)
        self.assertRegex(record.msg, "^abri " + hashed + "$")
        for text in (line, logs._format_value(outside), record.msg):
            self.assertNotIn("Volumes", text)
            self.assertNotIn("ClienteX", text)
            self.assertNotIn("video", text)
        self.assertRegex(
            labels.outside("em C:\\Dados\\Vídeo\\x.mp4."), r"^em <pasta fora do projeto>/#[0-9a-f]{8}\.mp4\.$"
        )
        for kept in ("<projeto>/x", "$GB_HOME/x", "~/x", "https://host/a/b", "brolls/x.json", "1/2", "a / b"):
            with self.subTest(kept=kept):
                self.assertEqual(f"em {kept}", labels.outside(f"em {kept}"))

    def test_the_cut_never_leaves_the_start_of_a_folder_or_a_token(self):
        # Redact (tokens, then folders), then cut: wherever the cut of a log value (500) or a log message
        # (4000) lands, nothing of the folder or of the token is left, not even its first characters.
        root = str(self.gbhome / "pasta com espaço")
        tokens = ("ghp_" + "A1b2C3d4" * 5, "sk-" + "Z9y8X7w6" * 6, "github_pat_" + "Q1w2E3r4" * 4)
        for cap, run in (
            (logs._MAX_VALUE_CHARS, logs._format_value),
            (logs.MAX_MESSAGE_CHARS, self.filtered),
        ):
            for secret in (root, *tokens):
                head = secret[: min(12, len(secret))]
                for offset in range(cap - len(secret) - 2, cap + 2):
                    text = "x" * offset + " " + secret + "/a fim"
                    out = run(text)
                    if head in out:
                        self.fail(f"{head!r} sobrou com o corte em {offset}: {out[-60:]!r}")

    def test_the_diagnostics_repr_is_labelled_before_its_cut(self):
        root = str(self.gbhome / "pasta com espaço")
        head = root[:12]
        for offset in range(runtime.REDACT_LIMIT - len(root) - 20, runtime.REDACT_LIMIT + 2):
            event = {}
            runtime._classify_audited_error(event, ValueError("x" * offset + " " + root + "/a"), None)
            if head in event["repr"]:
                self.fail(f"{head!r} sobrou no repr com o corte em {offset}: {event['repr'][-60:]!r}")

    def filtered(self, text):
        record = logs.logging.LogRecord("getbrolls", logs.logging.INFO, __file__, 1, text, (), None)
        logs.RedactingFilter().filter(record)
        return record.msg

    def test_a_log_value_and_a_log_message_name_known_folders_by_label(self):
        self.assertEqual("<projeto>/brolls", logs._format_value(str(self.project / "brolls")))
        record = logs.logging.LogRecord("getbrolls", logs.logging.INFO, __file__, 1, "em %s", (str(self.gbhome),), None)
        self.assertTrue(logs.RedactingFilter().filter(record))
        self.assertEqual("em $GB_HOME", record.msg)
        self.assertEqual("sem caminho", logs.labels.text("sem caminho"))


if __name__ == "__main__":
    unittest.main()
