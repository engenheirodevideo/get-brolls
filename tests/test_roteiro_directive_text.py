"""`roteiro.directive_text`: a diretiva lida volta a ser texto que o parser lê igual."""

import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import roteiro

HEAD = "---\ntype: roteiro\ngenero: reels\ntema: teste\n---\n"
LINES = (
    "[A-ROLL]",
    "[A-ROLL: t2]",
    "[BROLL: cidade à noite, 1998]",
    "[SPLIT: tela do app | A-ROLL]",
    '[SPLIT: A-ROLL: t3 | "cartela"]',
    "[FULL: logo]",
    '[FULL: "Texto da cartela"]',
    "[UGC: pessoa abrindo a caixa]",
    "[BROLL: d'água limpa]",
)
LAYERS = (
    '[LETTERING: "Olá, mundo!"]',
    '[LETTERING: "diz \\"oi\\" | ali" | titulo-grande]',
    "[LETTERING: 'aspas simples' | estilo]",
    "[SFX: whoosh]",
    "[MUSICA: tema calmo]",
    "[COMP: abertura]",
)


def _scene(layout, layers=()):
    return HEAD + "## Cena\n" + layout + "\n" + "".join(f"{layer}\n" for layer in layers) + "Uma fala.\n"


def _shape(directive):
    return directive.kind, directive.args, directive.quoted, directive.plugin, directive.name


class DirectiveTextTests(unittest.TestCase):
    def test_layouts_round_trip(self):
        for line in LINES:
            with self.subTest(line=line):
                scene = roteiro.parse(_scene(line), plugins=frozenset()).scenes[0]
                text = roteiro.directive_text(scene.layout)
                again = roteiro.parse(_scene(text), plugins=frozenset()).scenes[0]
                self.assertEqual(_shape(scene.layout), _shape(again.layout))

    def test_layers_round_trip(self):
        scene = roteiro.parse(_scene("[A-ROLL]", LAYERS), plugins=frozenset()).scenes[0]
        texts = [roteiro.directive_text(layer) for layer in scene.layers]
        again = roteiro.parse(_scene("[A-ROLL]", texts), plugins=frozenset()).scenes[0]
        self.assertEqual([_shape(d) for d in scene.layers], [_shape(d) for d in again.layers])

    def test_canonical_spelling(self):
        scene = roteiro.parse(_scene("[apresentador]", ["[trilha: tema]"]), plugins=frozenset()).scenes[0]
        self.assertEqual("[A-ROLL]", roteiro.directive_text(scene.layout))
        self.assertEqual("[MUSICA: tema]", roteiro.directive_text(scene.layers[0]))

    def test_plugin_directive_round_trips(self):
        text = _scene("[A-ROLL]", ['[demo:zoom: "forte" | 2]'])
        scene = roteiro.parse(text, plugins=frozenset({"demo"})).scenes[0]
        rendered = roteiro.directive_text(scene.extensions[0])
        again = roteiro.parse(_scene("[A-ROLL]", [rendered]), plugins=frozenset({"demo"})).scenes[0]
        self.assertEqual(_shape(scene.extensions[0]), _shape(again.extensions[0]))

    def test_note_is_not_a_directive(self):
        with self.assertRaises(ValueError):
            roteiro.directive_text(roteiro.Directive("NOTE", (), 1))


if __name__ == "__main__":
    unittest.main()
