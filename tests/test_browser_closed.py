import unittest
from types import SimpleNamespace
from unittest import mock

from vagas.pipeline import BROWSER_CLOSED_RE, Pipeline


class BrowserClosedTest(unittest.TestCase):
    def test_regex(self):
        self.assertTrue(BROWSER_CLOSED_RE.search("Page.goto: Target page, context or browser has been closed"))
        self.assertFalse(BROWSER_CLOSED_RE.search("Timeout 15000ms exceeded"))

    def test_apply_devolve_browser_closed(self):
        p = object.__new__(Pipeline)
        p.s = SimpleNamespace(apply=SimpleNamespace(pause_before_submit=False),
                              llm=SimpleNamespace(assess_fit=False))   # aderência por IA (ADJUSTS-IA) fora do teste
        p.llm = None
        p.db = mock.Mock()
        p.qa = p.browser = p.profile = None
        p.dry_run = p.interactive = False
        p.confirm = None

        class Boom:
            name = "x"

            def apply(self, ctx, page, job):
                raise RuntimeError("Page.goto: Target page, context or browser has been closed")

        p._applier = lambda job: Boom()
        res = p.apply(None, SimpleNamespace(id="j"))
        self.assertEqual(res.status, "browser_closed")


if __name__ == "__main__":
    unittest.main()
