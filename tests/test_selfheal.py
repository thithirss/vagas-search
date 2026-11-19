"""Agente de correção (vagas/selfheal.py): agrupamento das falhas e trava do que só uma pessoa resolve."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vagas import selfheal as sh
from vagas.config import Settings
from vagas.db import DB, Job


class SelfHealTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db = DB(Path(self.tmp.name) / "jobs.db")
        self.addCleanup(self.db.conn.close)
        self.s = Settings.model_validate({"selfheal": {"enabled": True, "min_occurrences": 2}})
        p = patch.object(sh, "_artifacts", lambda job_id: ["data/learn/generic/x/step0.png"])   # há evidência
        p.start()
        self.addCleanup(p.stop)

    def add(self, i, reason, url="https://a.vagas.solides.com.br/vaga/1", status="failed"):
        j = Job(id=f"linkedin:{i}", source="linkedin", external_id=str(i), url="u", title="Dev", company="C",
                external_url=url, status=status, reason=reason, attempts=1)
        self.db.upsert_job(j)
        self.db.conn.execute("UPDATE jobs SET status=?, reason=?, attempts=1 WHERE id=?", (status, reason, j.id))
        self.db.conn.commit()

    def test_groups_by_reason_and_site(self):
        self.add(1, "erros no formulário: A data de nascimento é obrigatória")
        self.add(2, "erros no formulário: A data de nascimento é obrigatória", "https://b.vagas.solides.com.br/vaga/2")
        self.add(3, "não achei botão de avançar/enviar", "https://x.com/1")
        g = sh.candidates(self.db, self.s)
        self.assertEqual(len(g), 1)                       # a de 1 ocorrência só não dispara
        self.assertIn("solides.com.br", g[0]["signature"])
        self.assertEqual(len(g[0]["jobs"]), 2)

    def test_skips_what_only_a_person_solves(self):
        for i in range(3):
            self.add(10 + i, "site.com exige conta para candidatar (pendência criada)")
            self.add(20 + i, "detalhes falharam: Page.goto: net::ERR_INTERNET_DISCONNECTED")
        self.assertEqual(sh.candidates(self.db, self.s), [])

    def test_signature_already_handled_is_not_repeated(self):
        self.add(1, "não achei formulário de candidatura")
        self.add(2, "não achei formulário de candidatura")
        sig = sh.candidates(self.db, self.s)[0]["signature"]
        self.db.kv_set(f"selfheal:{sig}", json.dumps({"status": "no_fix"}))
        self.assertEqual(sh.candidates(self.db, self.s), [])

    def test_needs_evidence(self):
        self.add(1, "não achei formulário de candidatura")
        self.add(2, "não achei formulário de candidatura")
        with patch.object(sh, "_artifacts", lambda job_id: []):
            self.assertEqual(sh.candidates(self.db, self.s), [])

    def test_disabled_by_default(self):
        self.add(1, "x"); self.add(2, "x")
        self.assertFalse(sh.due(self.db, Settings()))


if __name__ == "__main__":
    unittest.main()
