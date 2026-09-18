"""Smoke de triggering: a description do SKILL.md cobre as palavras que a
pessoa realmente usa.

Não simula o roteador de skills — só garante que, para cada frase típica do
corpus, a palavra-gatilho dela está escrita na description. Sem isso, o melhor
fluxo do mundo nunca chega a rodar.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = (ROOT / "SKILL.md", ROOT / "skills" / "get-brolls" / "SKILL.md")

# (frase que a pessoa escreve, palavra-gatilho que precisa estar na description)
PHRASES = [
    ("preciso de uns vídeos de apoio pro meu Reel", "vídeos de apoio"),
    ("me acha um corte do Elon falando sobre Marte", "corte do X falando Y"),
    ("quero um print da tela do site deles", "print da tela"),
    ("junta umas imagens de apoio pra essa aula", "imagens de apoio"),
    ("coleta b-roll pro vídeo novo", "b-roll"),
    ("preciso de footage de enchente em São Paulo", "footage"),
    ("arruma cutaways pra essa entrevista", "cutaways"),
    ("preciso de inserts pra cobrir o corte", "inserts"),
    ("baixa um stock video de café", "stock video"),
    ("pega umas screen grabs da matéria", "screen grabs"),
]

# O limite também é gatilho: sem ele a skill é chamada para editar o vídeo.
BOUNDARY = "Not for editing or rendering"
BOUNDARY_PT = "Não serve para editar, montar ou renderizar o vídeo final."


def description(path):
    match = re.search(r"\A---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.DOTALL)
    assert match, f"{path} sem frontmatter"
    for line in match.group(1).splitlines():
        if line.strip().startswith("description:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"{path} sem description")


class DescriptionTriggerTests(unittest.TestCase):
    def test_every_phrase_has_its_trigger_word_in_the_description(self):
        for path in SKILLS:
            text = description(path).lower()
            for phrase, trigger in PHRASES:
                self.assertIn(
                    trigger.lower(),
                    text,
                    f'{path.name}: gatilho ausente para "{phrase}" → {trigger}',
                )

    def test_description_states_what_the_skill_is_not_for(self):
        for path in SKILLS:
            self.assertIn(BOUNDARY, description(path))
            self.assertIn(BOUNDARY_PT, description(path))

    def test_descriptions_match_between_root_and_mirror(self):
        self.assertEqual(description(SKILLS[0]), description(SKILLS[1]))


if __name__ == "__main__":
    unittest.main()
