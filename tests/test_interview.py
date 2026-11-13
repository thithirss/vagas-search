import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from vagas import interview as iv
from vagas.db import DB, Job

MSG = """Olá, Fulano, bom dia!
Meu nome é Ana, trabalho aqui no RH do Grupo Horizonte.
Recebemos o seu currículo para a oportunidade de Desenvolvedor Python.
*Salário* R$ 3.500,00
*Endereço* : Rua das Flores, 100 - Centro, Belo Horizonte
*Horário* : Segunda a Sexta-feira de 08h as 18h
Entrevista hoje às 14h"""

PROFILE = {
    "skills": {"primary": ["PHP", "Laravel", "Vue.js"], "familiar": ["Python"]},
    "experiences": [{"company": "Acme", "title": "Dev Júnior", "start": "2025-07", "end": "",
                     "bullets": ["Refatorei módulos legados com PHPUnit.", "Orientei estagiários."]}],
    "facts": {"salary_expectation_junior_clt": "R$ 5.000", "preferred_seniority": "junior"},
    "education": [{"degree": "Sistemas de Informação", "status": "concluído"}],
}


class InterviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = DB(Path(self.tmp.name) / "v.db"); self.addCleanup(self.db.conn.close)

    def add_job(self, jid, company, title, applied=True):
        j = Job(id=jid, source="gupy", external_id=jid, title=title, company=company, url="https://x")
        j.status = "applied"; j.classification = {"stack": ["python"]}
        self.db.upsert_job(j); self.db.save_job(j)
        if applied:
            self.db.add_application(j, "gupy", "gupy", None, [{"q": "Conhece SQL?", "a": "Sim"}])

    def test_parse_recruiter_message(self):
        info = iv.parse_message(MSG)
        self.assertEqual(info["salary"], "R$ 3.500,00")
        self.assertIn("Centro", info["location"])
        self.assertEqual(info["mode"], "presencial")
        self.assertEqual(info["when"], f"{date.today().isoformat()} 14:00")   # não confunde com "08h as 18h"
        self.assertEqual(info["title"], "Desenvolvedor Python")

    def test_short_text_with_date(self):
        info = iv.parse_message("Stone, Backend, amanhã 10h")
        self.assertEqual(info["when"], f"{(date.today() + timedelta(days=1)).isoformat()} 10:00")

    def test_links_job_only_when_company_matches(self):
        self.add_job("gupy:1", "Grupo Horizonte Brasil", "DESENVOLVEDOR PYTHON")
        self.add_job("li:2", "Outra SA", "Desenvolvedor Backend")
        i = iv.create_from_text(self.db, MSG)
        got = iv.get(self.db, i, PROFILE)
        self.assertEqual(got["job_id"], "gupy:1")
        self.assertEqual(got["application"]["answers"], [{"q": "Conhece SQL?", "a": "Sim"}])
        j = iv.create_from_text(self.db, "Stone, Desenvolvedor Backend, amanhã 10h")
        other = iv.get(self.db, j, PROFILE)
        self.assertIsNone(other["job_id"])
        self.assertEqual((other["company"], other["title"]), ("Stone", "Desenvolvedor Backend"))

    def test_kit_is_honest_about_weak_stack(self):
        self.add_job("gupy:1", "Grupo Horizonte Brasil", "DESENVOLVEDOR PYTHON")
        k = iv.get(self.db, iv.create_from_text(self.db, MSG), PROFILE)["kit"]
        self.assertIn("Python", k["stack"])
        py = next(c for c in k["cards"] if c["q"] == "Qual sua experiência com Python?")
        self.assertIn("básico", py["a"])
        self.assertTrue(any(c["d"] == "Laravel ↔ Python" for c in k["cards"]))   # perfil com Laravel ganha a ponte
        self.assertIn("R$ 5.000", k["plan"]["salary"])
        self.assertIn("Acme", k["plan"]["pitch"])
        self.assertGreaterEqual(len(k["quiz"]), 10)

    def test_kit_without_profile_still_works(self):
        k = iv.get(self.db, iv.create_from_text(self.db, "Empresa X, Analista de Sistemas, 20/11 9h"), {})["kit"]
        self.assertTrue(k["cards"] and k["sim"] and k["quiz"])

    def test_invite_email_becomes_interview_once(self):
        from vagas import inbox
        inbox.save_response(self.db, msg_key="gm:1", received_at="2026-11-10T10:00:00", sender="rh@x.com", subject="Convite para entrevista",
                            snippet="", status="positivo", summary="", action="", link="", company="X", job_id=None, kind="entrevista",
                            deadline="2026-11-24")
        self.assertEqual(iv.sync_from_responses(self.db), 1)
        self.assertEqual(iv.sync_from_responses(self.db), 0)
        self.assertEqual(iv.list_interviews(self.db)[0]["scheduled_at"], "2026-11-24")


if __name__ == "__main__":
    unittest.main()
