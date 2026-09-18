"""Caracterização de `id_stem`: fixa o helper contra digestos literais
computados a partir da expressão inline atual (`hashlib.sha256(candidate_id
.encode()).hexdigest()[:16]`), para que a extração do helper nunca faça o
stem derivar. O stem vira nome de arquivo em disco de usuário; qualquer
mudança aqui invalida cache existente.
"""

import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls.models import id_stem

KNOWN_DIGESTS = {
    "youtube:abc123": "55d06e3305b71a49",
    "pexels:42": "b77684504182e336",
    "vimeo:zzz-999": "6b35c8d7728a19ec",
}


class TestIdStem(unittest.TestCase):
    def test_pins_known_digests(self):
        for candidate_id, expected in KNOWN_DIGESTS.items():
            self.assertEqual(id_stem(candidate_id), expected)

    def test_length_and_type(self):
        stem = id_stem("provider:source")
        self.assertIsInstance(stem, str)
        self.assertEqual(len(stem), 16)


if __name__ == "__main__":
    unittest.main()
