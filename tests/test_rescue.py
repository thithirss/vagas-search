import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from playwright.sync_api import sync_playwright

from vagas import rescue
from vagas.budget import BudgetExceeded
from vagas.config import Settings
from vagas.db import DB, Job

PAGE = """<html><body>
<nav><a href="/vagas">Vagas</a><a href="/login">Entrar</a></nav>
<h1>Desenvolvedor Python</h1><p>Descrição da vaga…</p>
<button>Compartilhar</button><button>Criar alerta de vagas</button>
<button disabled>Enviar</button>
<div class="x" role="button" onclick="document.body.innerHTML='<form><input name=nome placeholder=Nome></form>'">Quero participar</div>
</body></html>"""


class FakeLLM:
    def __init__(self, answer=None, exc=None):
        self.answer, self.exc, self.calls = answer, exc, []

    def pick_action(self, *, goal, page, elements):
        self.calls.append(elements)
        if self.exc:
            raise self.exc
        return self.answer(elements) if callable(self.answer) else self.answer


class RescueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = DB(Path(self.tmp.name) / "v.db"); self.addCleanup(self.db.conn.close)
        self.page = self.browser.new_page(); self.addCleanup(self.page.close)
        self.page.route("https://carreiras.exemplo.com/**", lambda r: r.fulfill(body=PAGE, content_type="text/html"))
        self.page.goto("https://carreiras.exemplo.com/vaga/1")
        self.job = Job(id="li:1", source="linkedin", external_id="1", title="Dev", company="Exemplo", url="https://x")

    def ctx(self, llm):
        return SimpleNamespace(settings=Settings(), db=self.db, qa=SimpleNamespace(llm=llm), rescues=0, dry_run=False)

    def test_candidates_drop_menu_disabled_and_forbidden(self):
        texts = [c["text"] for c in rescue.candidates(self.page)]
        self.assertEqual(texts, ["Quero participar"])

    def test_ai_pick_clicks_and_learns_for_the_site(self):
        llm = FakeLLM(lambda els: {"index": els[0]["i"], "acao": "candidatar", "situacao": "normal", "motivo": "abre o formulário"})
        ctx = self.ctx(llm)
        r = rescue.pick(ctx, self.page, self.job, "apply")
        self.assertEqual((r.action, r.text, r.source), ("click", "Quero participar", "ia"))
        r = rescue.click(ctx, self.page, self.job, "apply", r)
        self.assertEqual(r.action, "click")
        self.assertEqual(self.page.locator("input[name=nome]").count(), 1)
        # próxima vaga no mesmo site: sem IA
        self.page.goto("https://carreiras.exemplo.com/vaga/2")
        ctx2 = self.ctx(FakeLLM(exc=AssertionError("não devia chamar a IA")))
        r2 = rescue.pick(ctx2, self.page, self.job, "apply")
        self.assertEqual((r2.action, r2.source), ("click", "aprendido"))

    def test_no_budget_or_limit_means_no_action(self):
        r = rescue.pick(self.ctx(FakeLLM(exc=BudgetExceeded("teto"))), self.page, self.job, "apply")
        self.assertEqual(r.action, "none")
        ctx = self.ctx(FakeLLM({"index": 0})); ctx.rescues = 99
        self.assertEqual(rescue.pick(ctx, self.page, self.job, "apply").action, "none")

    def test_ai_index_outside_list_is_ignored(self):
        r = rescue.pick(self.ctx(FakeLLM({"index": 42, "situacao": "encerrada", "motivo": "vaga encerrada"})), self.page, self.job, "apply")
        self.assertEqual((r.action, r.situation), ("none", "encerrada"))

    def test_dry_run_guard(self):
        self.assertTrue(rescue.would_commit(rescue.Rescue("click", "Enviar candidatura", "enviar")))
        self.assertTrue(rescue.would_commit(rescue.Rescue("click", "Salvar e continuar", "avancar")))
        self.assertFalse(rescue.would_commit(rescue.Rescue("click", "Próximo", "avancar")))


class SocialLinkTests(unittest.TestCase):
    def test_follow_is_never_next_and_social_links_are_skipped(self):
        from vagas import forms
        from vagas.appliers.flow import SOCIAL_HOST_RE
        for t in ("Seguir", "Follow", "Deixar de seguir"):
            self.assertTrue(forms.BAD_RE.search(t), t)
            self.assertFalse(forms.NEXT_RE.match(t), t)
        self.assertTrue(forms.NEXT_RE.match("Prosseguir"))
        self.assertTrue(SOCIAL_HOST_RE.search("https://x.com/krakenfx"))
        self.assertFalse(SOCIAL_HOST_RE.search("https://max.com/carreiras"))


class IndeedPauseTests(unittest.TestCase):
    def test_blocked_refunds_attempt_and_pauses_source(self):
        from unittest.mock import patch
        from vagas.pipeline import Pipeline
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        db = DB(Path(tmp.name) / "v.db"); self.addCleanup(db.conn.close)
        p = Pipeline.__new__(Pipeline)
        p.s, p.db = Settings(), db
        with patch("vagas.pipeline.notify") as n:
            self.assertEqual(p.source_blocked("indeed"), "")
            p.block_source("indeed", "Indeed pediu verificação (captcha)")
            p.block_source("indeed", "de novo")
        self.assertTrue(p.source_blocked("indeed"))
        self.assertEqual(n.call_count, 1)   # avisa uma vez por pausa
        self.assertEqual(p.source_blocked("linkedin"), "")


if __name__ == "__main__":
    unittest.main()
