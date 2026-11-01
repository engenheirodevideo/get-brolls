"""`labels`: a pasta da máquina vira a marca dela em toda mensagem, e nunca sobra o caminho inteiro."""

import errno
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _symlink import needs_symlink

from getbrolls import labels, runtime


class LabelsTests(unittest.TestCase):
    def assert_no_machine_path(self, text):
        real = os.path.realpath(self.tmp)
        for spelling in (str(self.tmp), real, real.removeprefix("/private")):
            self.assertNotIn(spelling, text)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-labels-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "video"
        (self.project / "brolls").mkdir(parents=True)
        self.gbhome = self.tmp / "gbhome"
        self.gbhome.mkdir()
        self.temp = self.tmp / "temp"
        self.temp.mkdir()
        self.person = self.tmp / "pessoa"
        self.person.mkdir()
        env = {
            "GB_HOME": str(self.gbhome),
            "GB_CACHE_DIR": str(self.gbhome / "cache"),
            "HOME": str(self.person),
            "USERPROFILE": str(self.person),
        }
        patched = patch.dict(os.environ, env)
        patched.start()
        self.addCleanup(patched.stop)
        temp = patch.object(tempfile, "tempdir", str(self.temp))
        temp.start()
        self.addCleanup(temp.stop)

    def test_each_known_folder_gets_its_tag(self):
        client = self.tmp / "clientes" / "acme"
        client.mkdir(parents=True)
        cases = {
            self.project / "brolls" / "review.html": "<projeto>/brolls/review.html",
            client / "templates": "<cliente>/templates",
            self.gbhome / "clients.json": "$GB_HOME/clients.json",
            self.gbhome / "cache" / "x": "$GB_CACHE_DIR/x",
            self.temp / "gb-digest-1": "$TMPDIR/gb-digest-1",
            self.person / "Videos": "~/Videos",
        }
        for path, expected in cases.items():
            with self.subTest(path=path.name):
                self.assertEqual(expected, labels.shown(path, project=self.project, extra=[(client, "<cliente>")]))

    def test_outside_every_folder_only_the_name_is_left(self):
        elsewhere = Path(self.tmp.anchor) / "gb-disco-externo" / "clip.mp4"
        # The name of a file outside every known folder can be a client's: a hash and the extension.
        shown = labels.shown(elsewhere, project=self.project)
        self.assertRegex(shown, r"^<pasta fora do projeto>/#[0-9a-f]{8}\.mp4$")
        self.assertEqual(shown, labels.shown(elsewhere, project=self.project))
        self.assertEqual("<pasta fora do projeto>", labels.shown(Path(self.tmp.anchor)))

    def test_what_is_not_a_path_comes_back_as_it_came(self):
        self.assertEqual("None", labels.shown(None))
        self.assertEqual("", labels.shown(""))
        self.assertIsNone(labels._paths.label("a\0b", [(self.project, "<projeto>")]))
        self.assertIsNone(labels._paths.label(None, [(self.project, "<projeto>")]))

    def test_a_folder_that_neither_stats_nor_resolves_is_compared_as_written(self):
        missing = self.tmp / "ainda-nao-existe"
        with patch.object(Path, "resolve", side_effect=RuntimeError("symlink loop")):
            self.assertEqual("<projeto>/x", labels._paths.label(missing / "x", [(missing, "<projeto>")]))

    def test_the_project_of_the_running_command_is_the_default(self):
        with patch("getbrolls.logs.project_root", return_value=self.project):
            self.assertEqual("<projeto>/brolls", labels.shown(self.project / "brolls"))

    def test_free_text_gets_every_folder_replaced_at_path_boundaries(self):
        real = Path(os.path.realpath(self.project))
        message = f"falhou em {real}/brolls/x.json e {self.gbhome}/plugins.json; {real}2 fica"
        out = labels.text(message, project=self.project)
        # `video2` is another folder, not `<projeto>2`: outside every anchor, so hidden to the end of the line.
        expected = r"^falhou em <projeto>/brolls/x\.json e \$GB_HOME/plugins\.json; "
        self.assertRegex(out, expected + r"<pasta fora do projeto>/#[0-9a-f]{8}$")

    def test_free_text_with_the_unresolved_spelling_of_a_resolved_folder(self):
        # macOS: the command holds the project resolved (`/private/var/…`), the person typed `/var/…`.
        real = Path(os.path.realpath(self.project))
        if not str(real).startswith(("/private/var/", "/private/tmp/")):
            self.skipTest("só no macOS a pasta temporária passa por /private")
        typed = str(real).removeprefix("/private")
        self.assertEqual("em <projeto>/x", labels.text(f"em {typed}/x", project=real))

    def test_free_text_names_a_folder_inside_another_by_the_deeper_one(self):
        # `$GB_HOME` inside the project: replacing the project first would leave `<projeto>/gbh/a`.
        nested = self.project / "gbh"
        nested.mkdir()
        with patch.dict(os.environ, {"GB_HOME": str(nested)}):
            self.assertEqual(
                "$GB_HOME/a e <projeto>/b", labels.text(f"{nested}/a e {self.project}/b", project=self.project)
            )

    def test_a_name_that_goes_on_past_the_folder_is_another_folder(self):
        gbh = self.gbhome
        with patch.dict(os.environ, {"GB_HOME": str(gbh)}):
            for other in (f"{gbh} 2026/outro", f"{gbh}(cópia)/x", f"{gbh},v2/x", f"{gbh}:x/y"):
                with self.subTest(other=other):
                    # Another folder, not `$GB_HOME 2026/outro`: outside every anchor, so by its hash.
                    out = labels.text(f"falhou em {other}")
                    self.assertNotIn("$GB_HOME", out)
                    self.assertTrue(out.startswith("falhou em <pasta fora do projeto>/#"), out)
                    self.assert_no_machine_path(out)
            for text, expected in (
                (f"{gbh}: Permission denied", "$GB_HOME: Permission denied"),
                (f"em {gbh} e depois", "em $GB_HOME e depois"),
                (f"({gbh}), x", "($GB_HOME), x"),
                (f"'{gbh}'", "'$GB_HOME'"),
            ):
                with self.subTest(text=text):
                    self.assertEqual(expected, labels.text(text))

    def test_an_existing_sibling_whose_name_starts_with_the_folder_is_left_alone(self):
        sibling = Path(f"{self.project} 2")
        sibling.mkdir()
        # Not `<projeto> 2`: the sibling is another folder, outside every anchor here, so only its hash shows.
        for text in (f"em {sibling}", f"em {sibling}, e"):
            with self.subTest(text=text):
                out = labels.text(text, project=self.project)
                self.assertNotIn("<projeto>", out)
                self.assertTrue(out.startswith("em <pasta fora do projeto>/#"), out)
                self.assert_no_machine_path(out)
        self.assertEqual("em <projeto>, e", labels.text(f"em {self.project}, e", project=self.project))

    def test_a_name_in_nfd_matches_the_same_name_in_nfc(self):
        import unicodedata  # pylint: disable=import-outside-toplevel

        nfc = self.tmp / unicodedata.normalize("NFC", "Vídeos-novos")  # not created: compared as text
        nfd = str(self.tmp / unicodedata.normalize("NFD", "Vídeos-novos"))
        self.assertEqual("<projeto>/x", labels._paths.label(nfd + "/x", [(nfc, "<projeto>")]))
        self.assertEqual("em <projeto>/x", labels.text(f"em {nfd}/x", project=nfc))

    def test_reason_of_an_os_error_is_the_system_text_only(self):
        error = PermissionError(errno.EACCES, "Permission denied", str(self.project / "x"))
        self.assertEqual("Permission denied", labels.reason(error))
        self.assertEqual("OSError", labels.reason(OSError()))
        self.assertEqual("<projeto>/x ruim", labels.reason(ValueError(f"{self.project}/x ruim"), project=self.project))

    def test_os_message_gives_the_reason_in_portuguese_and_the_advice_of_the_errno(self):
        cases = {
            errno.EACCES: ("sem permissão", "Confira as permissões"),
            errno.EPERM: ("sem permissão", "Confira as permissões"),
            errno.ENOSPC: ("o disco está cheio", "Libere espaço em disco"),
            errno.EROFS: ("o disco está somente leitura", "Use um disco que aceite gravação"),
            errno.ENAMETOOLONG: ("o nome é longo demais", "Use um nome mais curto"),
            errno.EEXIST: ("o arquivo já existe", "Escolha um nome que ainda não exista"),
            errno.EIO: ("Input/output error", "Confira o caminho e as permissões"),
        }
        for code, (reason, advice) in cases.items():
            with self.subTest(errno=code):
                strerror = os.strerror(code)
                named = labels.os_message(OSError(code, strerror, str(self.project / "x")), project=self.project)
                self.assertEqual(f"Não consegui acessar <projeto>/x ({reason or strerror}). {advice} e repita.", named)
                bare = labels.os_message(OSError(code, strerror))
                self.assertEqual(f"Erro de E/S ({reason or strerror}). {advice} e repita.", bare)

    def test_os_message_reasons_are_the_ones_the_roteiro_and_the_export_use(self):
        from getbrolls import export, roteiro_commands  # pylint: disable=import-outside-toplevel

        self.assertIs(labels.OS_REASONS, roteiro_commands.labels.OS_REASONS)
        self.assertEqual("sem permissão", export._OS_REASONS[errno.EACCES])  # pylint: disable=protected-access


class LinkedSpellingTests(unittest.TestCase):
    """macOS: `/var` and `/tmp` are links to `/private/…`, so one folder has two spellings. The folder a command
    holds (`GB_HOME`, `--project`) and the folder a message names can be spelled either way, in every pairing."""

    def setUp(self):
        real = Path(tempfile.mkdtemp(prefix="gb-linked-")).resolve()
        self.addCleanup(shutil.rmtree, real, ignore_errors=True)
        self.real = real
        if not str(real).startswith("/private/"):
            self.skipTest("só no macOS a pasta temporária passa por /private")
        self.linked = Path(str(real).removeprefix("/private"))
        self.assertTrue(self.linked.samefile(real))
        (real / "gbhome").mkdir()
        (real / "video").mkdir()
        patched = patch.dict(os.environ, {"GB_HOME": str(real / "gbhome")})
        patched.start()
        self.addCleanup(patched.stop)

    def pairings(self, folder):
        spellings = {"resolved": self.real / folder, "linked": self.linked / folder}
        for held_name, held in spellings.items():
            for named_name, named in spellings.items():
                yield f"held {held_name}, named {named_name}", held, named

    def test_gb_home_is_recognised_in_both_spellings(self):
        for label, held, named in self.pairings("gbhome"):
            with self.subTest(label), patch.dict(os.environ, {"GB_HOME": str(held)}):
                self.assertEqual("$GB_HOME/x.json", labels.shown(named / "x.json"))
                self.assertEqual("falhou em $GB_HOME/x.json: motivo", labels.text(f"falhou em {named}/x.json: motivo"))
                for shown in (labels.text(f"{named}/x"), labels.for_log(f"em {named}/x"), labels.outside(f"{named}/x")):
                    self.assertNotIn("gb-linked-", shown)

    def test_a_home_folder_is_scrubbed_in_both_spellings(self):
        for label, held, named in self.pairings("gbhome"):
            with self.subTest(label), patch.object(runtime.Path, "home", return_value=held):
                self.assertEqual("falhou em ~/x.json", runtime.scrub_home(f"falhou em {named}/x.json"))

    def test_the_project_is_recognised_in_both_spellings(self):
        for label, held, named in self.pairings("video"):
            with self.subTest(label):
                self.assertEqual("<projeto>/a.mp4", labels.shown(named / "a.mp4", project=held))
                self.assertEqual("em <projeto>/a.mp4", labels.text(f"em {named}/a.mp4", project=held))
                self.assertNotIn("gb-linked-", labels.for_log(f"em {named}/a.mp4", project=held))
                self.assertNotIn(
                    "gb-linked-", labels.message_of(FileNotFoundError(2, "x", str(named / "a.mp4")), project=held)
                )


class TypedSpellingTests(unittest.TestCase):
    """The folder the person typed for `--project` can be another spelling of the same folder (a link, or on
    Windows the 8.3 short name of a long folder): it is still `<projeto>`, and the rest of the sentence survives."""

    SENTENCE = "Projeto não encontrado em {}; nenhum arquivo foi criado."
    EXPECTED = "Projeto não encontrado em <projeto>; nenhum arquivo foi criado."

    def setUp(self):
        self.addCleanup(labels.forget_typed_project)
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-typed-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # The person's home is a folder inside `tmp`, never above it: on Windows the temp folder lives under the real
        # home, so `~` would otherwise hold every folder this class makes and show it as `~/…`.
        self.person = self.tmp / "pessoa"
        self.person.mkdir()
        env = {
            "GB_HOME": str(self.tmp / "gbhome"),
            "GB_CACHE_DIR": str(self.tmp / "gbhome" / "cache"),
            "HOME": str(self.person),
            "USERPROFILE": str(self.person),
        }
        patched = patch.dict(os.environ, env)
        patched.start()
        self.addCleanup(patched.stop)
        (self.tmp / "temp").mkdir()
        temp = patch.object(tempfile, "tempdir", str(self.tmp / "temp"))  # the folder under test is not `$TMPDIR`
        temp.start()
        self.addCleanup(temp.stop)

    @needs_symlink
    def test_a_link_to_the_folder_that_was_typed_is_the_project(self):
        real = self.tmp / "real"
        (real / "proj").mkdir(parents=True)
        alias = self.tmp / "alias"
        alias.symlink_to(real, target_is_directory=True)
        typed = alias / "proj"
        labels.note_typed_project(str(typed), typed)
        self.assertEqual(self.EXPECTED, labels.text(self.SENTENCE.format(typed), project=real / "proj"))

    def test_a_short_name_of_a_long_folder_is_the_project(self):
        # Windows hands out `RUNNER~1` for `runneradmin`; the resolver of the disk answers with the long name,
        # which is all this checks (the folder does not exist, like the project of a `status` that is refused).
        short, long_ = self.tmp / "RUNNER~1" / "proj", self.tmp / "runneradmin" / "proj"
        real_realpath = os.path.realpath

        def expand(path, *args, **kwargs):
            return real_realpath(str(path).replace("RUNNER~1", "runneradmin"), *args, **kwargs)

        with patch.object(os.path, "realpath", expand):
            labels.note_typed_project(str(short), short)
            self.assertEqual(self.EXPECTED, labels.text(self.SENTENCE.format(short), project=long_))
            self.assertEqual("<projeto>/a.mp4", labels.text(f"{short}/a.mp4", project=long_))
            self.assertEqual(self.EXPECTED, labels.for_log(self.SENTENCE.format(short), project=long_))

    def test_a_typed_folder_that_is_another_folder_is_not_the_project(self):
        other = self.tmp / "outra" / "proj"
        project = self.tmp / "video"
        project.mkdir()
        labels.note_typed_project(str(other), other)
        shown = labels.text(self.SENTENCE.format(other), project=project)
        self.assertNotIn("<projeto>", shown)
        self.assertNotIn("outra", shown)

    def test_a_typed_folder_under_the_home_that_is_not_the_project_is_shown_with_the_tilde(self):
        # The mirror of the test above: under the home the typed folder is a path the labels contract lets through
        # as `~/…` (the deepest known folder that holds it), the way Windows shows a temp folder under the home.
        other = self.person / "outra" / "proj"
        project = self.tmp / "video"
        project.mkdir()
        labels.note_typed_project(str(other), other)
        shown = labels.text(self.SENTENCE.format(other), project=project)
        self.assertEqual("Projeto não encontrado em ~/outra/proj; nenhum arquivo foi criado.", shown)

    def test_without_a_typed_project_nothing_changes(self):
        project = self.tmp / "video"
        project.mkdir()
        self.assertEqual("<projeto>/a.mp4", labels.text(f"{project}/a.mp4", project=project))


class OutsideStopsAtASemicolonTests(unittest.TestCase):
    """A path in free text ends where the sentence goes on after `; `, as it ends before `: `."""

    def test_the_text_after_a_semicolon_is_not_swallowed_into_the_hash(self):
        folder = "/Volumes/Externo/x/proj"
        hidden = labels.outside(folder)
        self.assertEqual(
            f"Projeto não encontrado em {hidden}; nenhum arquivo foi criado.",
            labels.outside(f"Projeto não encontrado em {folder}; nenhum arquivo foi criado."),
        )

    def test_a_folder_name_with_a_semicolon_and_a_blank_never_leaves_a_fragment(self):
        # `Cliente; Final` is one folder: the words after `; ` still hold a separator, so the path goes on.
        for text in (
            "Erro em /Volumes/Externo/Cliente; Final/a.mp4 nao achado",
            "Erro em /Volumes/Externo/Cliente: Final/a.mp4 nao achado",
            "Erro em /Volumes/Externo/Cliente; Final\\a.mp4 nao achado",
        ):
            with self.subTest(text=text):
                shown = labels.outside(text)
                self.assertNotIn("Final", shown)
                self.assertNotIn("a.mp4", shown)
                self.assertRegex(shown, "^Erro em " + re.escape(labels.OUTSIDE) + "/#[0-9a-f]{8}")

    def test_a_reason_without_a_separator_after_the_terminator_still_survives(self):
        folder = "/Volumes/Externo/x/proj"
        hidden = labels.outside(folder)
        for terminator in ("; ", ": "):
            with self.subTest(terminator=terminator):
                self.assertEqual(
                    f"falhou em {hidden}{terminator}arquivo nao criado; tente de novo.",
                    labels.outside(f"falhou em {folder}{terminator}arquivo nao criado; tente de novo."),
                )

    def test_a_separator_anywhere_later_on_the_line_keeps_the_path_going(self):
        # `Parte 1` alone is no path, but `Final/a.mp4` comes later on the same line: the folder name has several `; `.
        for text in (
            "Erro em /Volumes/Externo/Cliente; Parte 1; Final/a.mp4 nao achado",
            "Erro em /Volumes/Externo/Cliente: Parte 1; Final\\a.mp4 nao achado",
            "Erro em /Volumes/Externo/a; b; c; d/e.mp4 nao achado",
        ):
            with self.subTest(text=text):
                shown = labels.outside(text)
                for fragment in ("Parte", "Final", "a.mp4", "e.mp4", "b;", "d/"):
                    self.assertNotIn(fragment, shown)
                self.assertRegex(shown, "^Erro em " + re.escape(labels.OUTSIDE) + "/#[0-9a-f]{8}$")

    def test_the_reason_ends_the_path_when_no_separator_follows_on_that_line(self):
        folder = "/Volumes/Externo/x/proj"
        hidden = labels.outside(folder)
        self.assertEqual(
            f"falhou em {hidden}; tente de novo\n{hidden}: ok",
            labels.outside(f"falhou em {folder}; tente de novo\n{folder}: ok"),
        )

    def test_a_semicolon_inside_a_name_still_belongs_to_it(self):
        shown = labels.outside("em /Volumes/Externo/a;b/c e mais")
        self.assertRegex(shown, "^em " + re.escape(labels.OUTSIDE) + "/#[0-9a-f]{8}$")


class OutsideTests(unittest.TestCase):
    """`labels.outside` esconde só caminho de verdade: dois nomes ou mais, a partir da raiz de um disco."""

    def test_what_is_not_a_path_stays_as_it_is(self):
        for kept in (
            "progresso 3 /4 concluído",
            "rota=/review/save status=200",
            "GET /v1/search?q=gato",
            "use bv*+ba/b para o formato",
            "1080p/720p e 10/10/2026",
            "veja https://exemplo.com/a/b",
            "o caminho relativo aroll/c01.mp4 e ./assets/sfx",
            "e/ou isto e um / sozinho",
            "/tmp e /Users sozinhos",
        ):
            with self.subTest(kept=kept):
                self.assertEqual(kept, labels.outside(kept))

    def test_a_path_from_the_root_of_a_disk_is_hidden(self):
        for text, kept in (
            ("erro em /Volumes/Externo/video.mp4.", ("erro em ", ".mp4.")),
            ("C:/Dados/x/a e mais", ("", "")),  # the rest of the line goes with the path
            ("C:\\Dados\\x\\b.mov", ("", ".mov")),
            ("\\\\servidor\\pasta\\c.mp4", ("", ".mp4")),
            ("veja file:///x/y.mp4 agora", ("veja ", "")),
            ("veja file://localhost/x/y agora", ("veja ", "")),
        ):
            with self.subTest(text=text):
                shown = labels.outside(text)
                self.assertRegex(shown, "^" + re.escape(kept[0]) + re.escape(labels.OUTSIDE) + "/#[0-9a-f]{8}")
                self.assertTrue(shown.endswith(kept[1]), shown)
                self.assertNotIn("Externo", shown)
                self.assertNotIn("servidor", shown)

    def test_a_folder_with_a_parenthesis_after_a_label_is_one_name(self):
        self.assertEqual("falhou em $GB_HOME(copia)/x", labels.outside("falhou em $GB_HOME(copia)/x"))

    def test_the_output_never_grows_past_the_cap(self):
        dense = (" /Users/a" * (labels.INPUT_MAX // 9))[: labels.INPUT_MAX]
        self.assertLessEqual(len(labels.outside(dense)), labels.INPUT_MAX)
        self.assertLessEqual(len(labels.for_log(dense)), labels.INPUT_MAX)


class WindowsSpellingTests(unittest.TestCase):
    """Text from a Windows machine, fed to the pure functions: a `\\` tail is written with `/` like every
    label, and the drive alone (`C:\\`) is not a folder name."""

    PROJECT = r"C:\Dados\ana\proj"

    def swapped(self, text, **options):
        return labels._paths.replace_spellings(text, {self.PROJECT}, "<projeto>", slashes=True, **options)

    def test_the_names_after_a_windows_folder_follow_the_label_with_a_slash(self):
        self.assertEqual("em <projeto>/brolls", self.swapped(r"em C:\Dados\ana\proj\brolls"))
        self.assertEqual(
            "em <projeto>/brolls e <projeto>/a/b.mp4.",
            self.swapped(r"em C:\Dados\ana\proj\brolls e C:\Dados\ana\proj\a\b.mp4."),
        )
        self.assertEqual("(<projeto>/a/b)", self.swapped(r"(C:\Dados\ana\proj\a/b)"))
        self.assertEqual("<projeto>", self.swapped(self.PROJECT))
        self.assertEqual("<projeto>\\", self.swapped(self.PROJECT + "\\"))  # no name after it: nothing to follow

    def test_the_tail_is_found_in_the_json_and_slash_spellings_too(self):
        self.assertEqual('{"p": "<projeto>/brolls"}', self.swapped('{"p": "C:\\\\Dados\\\\ana\\\\proj\\\\brolls"}'))
        self.assertEqual("<projeto>/brolls", self.swapped(r"C:/Dados/ana/proj\brolls"))

    def test_a_tail_stops_at_a_blank_a_quote_or_a_separator_that_is_not_a_backslash(self):
        self.assertEqual("<projeto>/pasta com espaço", self.swapped(r"C:\Dados\ana\proj\pasta com espaço"))
        self.assertEqual("'<projeto>/a'", self.swapped(r"'C:\Dados\ana\proj\a'"))
        self.assertEqual("<projeto>/a: falhou", self.swapped(r"C:\Dados\ana\proj\a: falhou"))
        self.assertEqual("<projeto>/a/b", self.swapped(r"C:\Dados\ana\proj\a/b"))

    def test_another_folder_with_the_same_start_is_left_alone(self):
        for other in (r"C:\Dados\ana\proj2\brolls", r"C:\Dados\ana\proj-copia\a", r"xC:\Dados\ana\proj\a"):
            with self.subTest(other=other):
                self.assertEqual(other, self.swapped(other))

    def test_a_windows_folder_in_another_case_is_found_when_asked(self):
        self.assertEqual("<projeto>/Brolls", self.swapped(r"c:\dados\ANA\Proj\Brolls", ignore_case=True))

    def test_a_posix_folder_followed_by_a_backslash_is_not_a_windows_tail(self):
        out = labels._paths.replace_spellings(r"/x/proj\a", {"/x/proj"}, "<projeto>")
        self.assertEqual(r"<projeto>\a", out)

    def test_a_json_escape_after_the_folder_is_not_a_path_name(self):
        """In JSON text a `\\n`, `\\t`, `\\"` or `\\u00e9` after the path is an escape, not a folder."""
        for after in ("\nsecond line", "\tcol", ' "q"', "\u00e9x", "fim\n"):
            with self.subTest(after=after):
                line = json.dumps({"e": self.PROJECT + "\\brolls\\a.mp4" + after})
                expected = json.dumps({"e": "<projeto>/brolls/a.mp4" + after})
                self.assertEqual(expected, self.swapped(line))

    def test_a_plain_windows_folder_named_like_an_escape_is_still_a_folder(self):
        self.assertEqual("em <projeto>/new/tmp/a", self.swapped(r"em C:\Dados\ana\proj\new\tmp\a"))
        self.assertEqual("<projeto>/nome/uni", self.swapped(r"C:\Dados\ana\proj\nome\uni"))

    def test_a_folder_whose_name_starts_with_u_is_a_folder_not_a_unicode_escape(self):
        project = r"C:\Dados\usuarios\ana"
        out = labels._paths.replace_spellings(project + r"\x.mp4", {project}, "<projeto>", slashes=True)
        self.assertEqual("<projeto>/x.mp4", out)
        out = labels._paths.replace_spellings(
            r"C:\Dados\ana\proj\usuarios\x.mp4", {self.PROJECT}, "<projeto>", slashes=True
        )
        self.assertEqual("<projeto>/usuarios/x.mp4", out)
        escaped = json.dumps(project + r"\x.mp4")
        out = labels._paths.replace_spellings(escaped, {project}, "<projeto>", slashes=True)
        self.assertEqual('"<projeto>/x.mp4"', out)

    def test_the_escaped_forms_of_a_posix_folder_take_no_tail(self):
        spelling = "/srv/ana/proj"
        for text, kept in (
            (r"\u002fsrv\u002fana\u002fproj\u002fbrolls", r"\u002fbrolls"),
            (r"\/srv\/ana\/proj\/brolls", r"\/brolls"),
            (r"\\/srv\\/ana\\/proj\\/brolls", r"\\/brolls"),
        ):
            with self.subTest(text=text):
                out = labels._paths.replace_spellings(text, {spelling}, "<projeto>", slashes=True)
                self.assertEqual("<projeto>" + kept, out)

    def test_a_unc_folder_takes_its_tail_raw_and_escaped(self):
        unc = r"\\servidor\pasta\proj"
        out = labels._paths.replace_spellings(unc + r"\brolls\a", {unc}, "<projeto>", slashes=True)
        self.assertEqual("<projeto>/brolls/a", out)
        escaped = json.dumps(unc + r"\brolls") + json.dumps("\n")
        out = labels._paths.replace_spellings(escaped, {unc}, "<projeto>", slashes=True)
        self.assertEqual('"<projeto>/brolls"' + json.dumps("\n"), out)

    def test_without_slashes_the_tail_is_left_as_it_came(self):
        # `runtime.scrub_home` (a diagnostics line nobody acts on) keeps the system's own separators.
        out = labels._paths.replace_spellings(r"em C:\Dados\ana\proj\brolls", {self.PROJECT}, "~")
        self.assertEqual(r"em ~\brolls", out)

    def test_a_folder_label_takes_the_tail_of_a_windows_folder(self):
        with patch.object(labels._paths, "_lexical", return_value=self.PROJECT):
            out = labels._paths.replace_folder(r"em C:\Dados\ana\proj\brolls\a.mp4", self.PROJECT, "<projeto>")
        self.assertEqual("em <projeto>/brolls/a.mp4", out)

    def test_a_long_run_of_backslashes_is_linear(self):
        text = "C:\\Dados\\ana\\proj" + "\\a" * 20_000 + "\\\\" * 20_000
        self.assertEqual("<projeto>" + "/a" * 20_000 + "\\\\" * 20_000, self.swapped(text))

    def test_a_drive_alone_names_no_folder(self):
        for drive in ("C:\\", "c:/", "D:", "Z:\\\\"):
            with self.subTest(drive=drive):
                self.assertEqual("", labels._hidden_name(drive))
                with patch.object(labels._paths, "label", return_value=None):  # on a Windows machine it is no folder
                    self.assertEqual(labels.OUTSIDE, labels.shown(drive))

    def test_a_name_on_a_drive_is_still_hidden_by_hash(self):
        self.assertRegex(labels._hidden_name(r"Z:\gb-externo\clip.mp4"), r"^#[0-9a-f]{8}\.mp4$")


if __name__ == "__main__":
    unittest.main()
