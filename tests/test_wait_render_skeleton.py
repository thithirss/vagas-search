"""InHire (25/11): o esqueleto de carregamento tem botões do widget de acessibilidade; a espera não pode terminar nele."""
import unittest
from unittest.mock import patch

from vagas.appliers.generic import GenericApplier


class FakePage:
    def __init__(self, states):
        self.states = list(states)
        self.calls = 0

    def wait_for_load_state(self, *a, **k):
        pass

    def evaluate(self, js):
        self.calls += 1
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


class WaitRenderTests(unittest.TestCase):
    def test_skeleton_keeps_waiting_until_content(self):
        page = FakePage([[3, 0, 0], [3, 10, 0], [8, 900, 0]])
        with patch("vagas.appliers.generic.time.sleep"):
            GenericApplier._wait_render(page, seconds=5)
        self.assertEqual(page.calls, 3)

    def test_form_with_inputs_returns_at_once(self):
        page = FakePage([[5, 40, 3]])
        with patch("vagas.appliers.generic.time.sleep"):
            GenericApplier._wait_render(page, seconds=5)
        self.assertEqual(page.calls, 1)


if __name__ == "__main__":
    unittest.main()
