"""B-05: licença de rota `fetch` já consumida e cache perdido → o `fetch` recusa sem
chamar a rota; só `fetch --reacquire` (nova licença, com o ok da pessoa) roda de novo."""

import shutil
import unittest

from _media import skip_unless_ffmpeg
from test_sdk_route_fetch import FetchRouteCase

from getbrolls.runtime import OperationError


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


if __name__ == "__main__":
    unittest.main()
