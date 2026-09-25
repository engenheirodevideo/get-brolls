"""Beat sem `target` usa o título do candidato como nome de pasta — sem quebrar a entrega.

Um título real do YouTube como "AC/DC ao vivo" tem barra: passado cru para a guarda
de caminho, derrubava o `deliver` do projeto inteiro. O nome da pasta vem do slug do
título; o título de verdade continua no ORIGEM.md.
"""

import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from test_delivery import fetched, project

from getbrolls import delivery


class TitleWithSlashBecomesASafeFolder(unittest.TestCase):
    def test_a_title_with_a_slash_is_slugged_and_kept_in_origem(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("acdc", "AC/DC ao vivo", shot="show", clip="clips/acdc.mp4", sheet="previews/a.jpg")])
            report = delivery.build_delivery(tmp)
            root = Path(tmp) / "entrega"
            folders = [p.name for p in root.iterdir() if p.is_dir()]
            self.assertEqual(["01-show-ac-dc-ao-vivo"], folders)
            origin = (root / folders[0] / "ORIGEM.md").read_text(encoding="utf-8")
            self.assertIn("AC/DC ao vivo", origin)
            self.assertEqual(1, len(report["items"]))

    def test_a_title_with_parent_refs_is_slugged_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(
                tmp, [fetched("dots", "../ saída .. final", shot="fim", clip="clips/d.mp4", sheet="previews/d.jpg")]
            )
            delivery.build_delivery(tmp)
            folders = [p.name for p in (Path(tmp) / "entrega").iterdir() if p.is_dir()]
        self.assertEqual(["01-fim-saida-final"], folders)

    def test_a_plain_title_keeps_the_same_folder_name_as_before(self):
        self.assertEqual(
            delivery.beat_dir_name(1, "show", "Palco principal"),
            delivery.beat_dir_name(1, "show", delivery.slug("Palco principal")),
        )


if __name__ == "__main__":
    unittest.main()
