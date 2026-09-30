"""Storyboard de um projeto de layout 1: `/clips/<arquivo>` vem de `broll/`.

A página continua pedindo o caminho lógico `clips/…`; o servidor acha o arquivo nas
pastas de clipe do layout (`broll/`, e a antiga `brolls/clips/` quando existe), com as
mesmas travas de sempre: nada fora da pasta, nenhum link para fora, nenhum nome oculto.
"""

import http.client
import shutil
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import layout, rendering, serve
from getbrolls.ledger import Ledger

NOTICE = "getbrolls serve"


@contextmanager
def serving(root):
    server, port = serve.start(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def get(port, raw_path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        # http.client manda o caminho como está, sem a normalização do navegador.
        connection.request("GET", raw_path, headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class ServeProject(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-serve-layout-")).resolve()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        brolls = self.project / "brolls"
        brolls.mkdir()
        (brolls / "review.html").write_text("<html><body>storyboard</body></html>", encoding="utf-8")
        (brolls / "manifest.json").write_text("{}", encoding="utf-8")

    def outside(self):
        folder = Path(tempfile.mkdtemp(prefix="gb-serve-outside-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        (folder / "segredo.mp4").write_bytes(b"de fora")
        return folder

    def symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError:
            self.skipTest("este sistema não cria symlink")


class LayoutOneServeTests(ServeProject):
    def setUp(self):
        super().setUp()
        layout.write_project(self.project, layout.new_project_doc())
        (self.project / "broll").mkdir()
        (self.project / "broll" / "x.mp4").write_bytes(b"clipe em broll")

    def test_clips_url_serves_the_bytes_from_broll(self):
        with serving(self.project) as port:
            self.assertEqual((200, b"clipe em broll"), get(port, "/clips/x.mp4"))

    def test_a_clip_left_in_the_legacy_folder_is_served_too(self):
        legacy = self.project / "brolls" / "clips"
        legacy.mkdir()
        (legacy / "antigo.mp4").write_bytes(b"antigo")
        with serving(self.project) as port:
            self.assertEqual((200, b"antigo"), get(port, "/clips/antigo.mp4"))
            self.assertEqual((200, b"clipe em broll"), get(port, "/clips/x.mp4"))

    def test_the_same_name_in_both_folders_is_not_picked_silently(self):
        legacy = self.project / "brolls" / "clips"
        legacy.mkdir()
        (legacy / "x.mp4").write_bytes(b"outro")
        with serving(self.project) as port:
            self.assertEqual(404, get(port, "/clips/x.mp4")[0])

    def test_nothing_outside_the_clip_folders_is_reachable(self):
        (self.project / "broll" / ".oculto.mp4").write_bytes(b"oculto")
        with serving(self.project) as port:
            for raw in (
                "/clips/%2e%2e/project.json",
                "/clips/../project.json",
                "/clips/%2e%2e/%2e%2e/project.json",
                "/clips/%2e%2e/manifest.json",
                "/broll/x.mp4",
                "/clips/",
                "/clips",
                "/clips/.oculto.mp4",
                "/clips/%00x.mp4",
                "/project.json",
            ):
                with self.subTest(raw=raw):
                    self.assertEqual(404, get(port, raw)[0])
            self.assertEqual(200, get(port, "/clips/x.mp4")[0])

    def test_a_symlink_inside_broll_pointing_outside_is_refused(self):
        outside = self.outside()
        self.symlink(self.project / "broll" / "fora.mp4", outside / "segredo.mp4")
        with serving(self.project) as port:
            self.assertEqual(404, get(port, "/clips/fora.mp4")[0])

    def test_broll_itself_as_a_symlink_is_refused_and_the_page_still_opens(self):
        outside = self.outside()
        shutil.rmtree(self.project / "broll")
        self.symlink(self.project / "broll", outside, directory=True)
        with serving(self.project) as port:
            self.assertEqual(404, get(port, "/clips/segredo.mp4")[0])
            self.assertEqual(200, get(port, "/review.html")[0])


class LayoutZeroServeTests(ServeProject):
    def test_clips_still_come_from_brolls_clips_only(self):
        legacy = self.project / "brolls" / "clips"
        legacy.mkdir()
        (legacy / "x.mp4").write_bytes(b"brolls/clips")
        (self.project / "broll").mkdir()
        (self.project / "broll" / "y.mp4").write_bytes(b"da pessoa")
        with serving(self.project) as port:
            self.assertEqual((200, b"brolls/clips"), get(port, "/clips/x.mp4"))
            self.assertEqual(404, get(port, "/clips/y.mp4")[0])
            self.assertEqual(404, get(port, "/broll/y.mp4")[0])


class FileNoticeTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-serve-notice-")).resolve()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def page(self):
        return Path(rendering.render(Ledger(self.project))).read_text(encoding="utf-8")

    def test_layout_one_page_asks_to_open_it_with_serve_when_read_from_disk(self):
        layout.write_project(self.project, layout.new_project_doc())
        page = self.page()
        self.assertIn(NOTICE, page)
        self.assertIn('location.protocol === "file:"', page)

    def test_layout_zero_page_has_no_notice(self):
        self.assertNotIn(NOTICE, self.page())


if __name__ == "__main__":
    unittest.main()
