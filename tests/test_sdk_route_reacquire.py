"""Licença de rota `fetch` já consumida e cache perdido → o `fetch` recusa sem
chamar a rota; só `fetch --reacquire` (nova licença, com o ok da pessoa) roda de novo."""

import json
import shutil
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _media import skip_unless_ffmpeg
from test_sdk_route_fetch import FETCH_PLUGIN, FetchRouteCase

from getbrolls.runtime import OperationError

FIRST_CALL = "        target = workdir / TARGET\n"
RETURN = '        return RouteResult(target, "Standard License #42")\n'


def on_first_call(line, anchor=FIRST_CALL):
    """A rota de `fetch` que, na 1ª chamada, já registrou a chamada e roda `line` antes de `anchor`."""
    code = FETCH_PLUGIN.replace(
        anchor,
        '        if len(Path(os.environ["DEMO_CALLS"]).read_text(encoding="utf-8").splitlines()) == 1:\n'
        f"            {line}\n" + anchor,
    )
    assert code != FETCH_PLUGIN
    return code


def failing_first_call(raise_line):
    """A rota de `fetch` que, na 1ª chamada, já registrou a chamada e então levanta `raise_line`."""
    return on_first_call(raise_line)


@skip_unless_ffmpeg
class ReacquireTests(FetchRouteCase):
    def test_lost_cache_refuses_and_reacquire_records_the_new_license(self):
        self.enable()
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.approve_range(ident, 0, 1)
        self.gb("fetch", "--candidate", ident)
        self.assertEqual(["demo:1"], self.calls_made())
        first = self.manifest_item(ident)["acquisition"]["route_consumed_at"]

        shutil.rmtree(self.project / ".getbrolls-sources")
        self.approve_range(ident, 1, 2)
        with self.assertRaises(OperationError) as caught:
            self.gb("fetch", "--candidate", ident)
        message = str(caught.exception)
        self.assertIn("já foi consumida em " + first, message)
        self.assertIn("--reacquire", message)
        self.assertEqual(["demo:1"], self.calls_made())  # a rota não rodou

        done = self.gb("fetch", "--candidate", ident, "--reacquire")
        self.assertTrue(done["output"]["verified"])
        self.assertEqual(["demo:1", "demo:1"], self.calls_made())
        stored = self.manifest_item(ident)["acquisition"]
        self.assertEqual(first, stored["route_consumed_at"])
        self.assertEqual(1, len(stored["route_reacquired_at"]))

    def test_cache_present_never_reruns_even_with_reacquire(self):
        self.enable()
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.approve_range(ident, 0, 1)
        self.gb("fetch", "--candidate", ident)
        self.approve_range(ident, 1, 2)
        self.gb("fetch", "--candidate", ident, "--reacquire")
        self.assertEqual(["demo:1"], self.calls_made())
        self.assertNotIn("route_reacquired_at", self.manifest_item(ident)["acquisition"])

    def test_interrupted_route_is_not_run_again_without_reacquire(self):
        """Ctrl+C depois de a rota rodar (licença consumida) e antes de o arquivo entrar no cache."""
        self.enable(failing_first_call("raise KeyboardInterrupt"))
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.approve_range(ident, 0, 1)
        with self.assertRaises(OperationError) as interrupted:
            self.gb("fetch", "--candidate", ident)
        self.assertIn("interrompida", str(interrupted.exception))
        self.assertEqual(["demo:1"], self.calls_made())

        with self.assertRaises(OperationError) as caught:
            self.gb("fetch", "--candidate", ident)
        message = str(caught.exception)
        started = self.manifest_item(ident)["acquisition"]["route_started_at"]
        # O arquivo nunca entrou no cache: a frase não manda restaurar a pasta de cache.
        self.assertIn(f"começou em {started} e o arquivo não chegou ao cache", message)
        self.assertNotIn("Restaure a pasta", message)
        self.assertIn("--reacquire", message)
        self.assertEqual(["demo:1"], self.calls_made())  # a rota não rodou de novo

        done = self.gb("fetch", "--candidate", ident, "--reacquire")
        self.assertTrue(done["output"]["verified"])
        self.assertEqual(["demo:1", "demo:1"], self.calls_made())
        self.assertEqual(1, len(self.manifest_item(ident)["acquisition"]["route_reacquired_at"]))

    def assert_returned_route_is_not_run_again(self, code, refusal):
        """A rota voltou (a licença saiu) e o core recusou o que ela devolveu: a marca fica."""
        self.enable(code)
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.approve_range(ident, 0, 1)
        with self.assertRaises(OperationError) as refused:
            self.gb("fetch", "--candidate", ident)
        self.assertIn(refusal, str(refused.exception))
        self.assertIn("route_started_at", self.manifest_item(ident)["acquisition"])

        with self.assertRaises(OperationError) as caught:
            self.gb("fetch", "--candidate", ident)
        self.assertIn("--reacquire", str(caught.exception))
        self.assertEqual(["demo:1"], self.calls_made())  # o retry não paga a rota de novo

        done = self.gb("fetch", "--candidate", ident, "--reacquire")
        self.assertTrue(done["output"]["verified"])
        self.assertEqual(["demo:1", "demo:1"], self.calls_made())

    def test_a_route_that_returned_a_file_that_is_not_media_is_not_run_again(self):
        html = 'html = workdir / "erro.html"; html.write_text("<p>erro</p>"); return RouteResult(html, None)'
        self.assert_returned_route_is_not_run_again(on_first_call(html), "não é vídeo nem imagem")

    def test_a_route_that_returned_an_invalid_license_is_not_run_again(self):
        code = on_first_call('return RouteResult(target, "  ")', anchor=RETURN)
        self.assert_returned_route_is_not_run_again(code, "RouteResult.license")

    def test_a_route_that_reports_a_failure_runs_again_on_retry(self):
        """A rota falhou e disse por quê (nenhum arquivo entregue): o próximo fetch pode chamá-la."""
        self.enable(failing_first_call('raise RuntimeError("fora do ar")'))
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.approve_range(ident, 0, 1)
        with self.assertRaises(OperationError):
            self.gb("fetch", "--candidate", ident)
        self.assertNotIn("route_started_at", self.manifest_item(ident)["acquisition"])
        done = self.gb("fetch", "--candidate", ident)
        self.assertTrue(done["output"]["verified"])
        self.assertEqual(["demo:1", "demo:1"], self.calls_made())
        self.assertNotIn("route_reacquired_at", self.manifest_item(ident)["acquisition"])

    def test_a_failed_route_leaves_the_state_uncommitted(self):
        """A marca e o desfazer não mudam a página: o erro não manda rodar `review` à toa."""
        self.enable(failing_first_call('raise RuntimeError("fora do ar")'))
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.approve_range(ident, 0, 1)
        events = self.project / "brolls" / "events.jsonl"
        before = len(events.read_text(encoding="utf-8").splitlines())
        with self.assertRaises(OperationError) as caught:
            self.gb("fetch", "--candidate", ident)
        payload = caught.exception.payload
        self.assertFalse(payload["state_committed"])
        self.assertIsNone(payload.get("hint"))
        # O journal registra a marca e o desfazer com o nome deles, não como uma busca da rota.
        added = events.read_text(encoding="utf-8").splitlines()[before:]
        self.assertEqual(["fetch-route-start", "fetch-route-undo"], [json.loads(line)["operation"] for line in added])


if __name__ == "__main__":
    unittest.main()
