import json
import tempfile
import unittest
from pathlib import Path

from vagas import engines, linkedin_profile as lp
from vagas.config import Settings
from vagas.db import DB, Job

PROFILE = {
    "personal": {"first_name": "Ana", "last_name": "Silva", "github": "https://github.com/ana", "portfolio": ""},
    "headline": "Desenvolvedora Full Stack | Back-end",
    "summary": "Trabalha com PHP e Laravel.",
    "skills": {"primary": ["PHP", "Laravel", "React"], "also_experienced": ["AWS S3", "Docker"]},
    "experiences": [{"company": "Acme", "title": "Dev", "stack": ["PHP"], "bullets": ["Fiz APIs."]}],
    "projects": [{"name": "Loja X — SaaS de vendas", "description": "Next.js", "stack": ["Next.js"]}],
}


class LinkedInProfileTests(unittest.TestCase):
    def test_parse_json_from_cli_output(self):
        self.assertEqual(engines.parse_json('Claro! ```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(engines.parse_json('texto antes {"a": {"b": 2}} depois'), {"a": {"b": 2}})
        with self.assertRaises(ValueError):
            engines.parse_json("sem json")

    def test_engines_report_why_unavailable(self):
        ids = {e["id"]: e for e in engines.available(Settings())}
        self.assertEqual(set(ids), {"openai", "claude_code", "anthropic", "codex"})
        for e in ids.values():
            self.assertTrue(e["ok"] or e["why"])
        self.assertFalse(ids["claude_code"]["images"])   # só a API da OpenAI gera imagem

    def test_validate_removes_skills_outside_profile_and_warns(self):
        res = lp.validate({"skills": ["PHP", "Angular", "Docker", "Comunicação"], "skills_top3": ["Angular", "PHP"],
                           "headline": ["Dev PHP | Laravel | Kubernetes"], "about_pt": "Uso AWS S3 e Laravel.",
                           "experiences": [{"company": "Acme", "bullets": ["APIs em PHP"]}, {"company": "Inventada", "bullets": ["x"]}]},
                          PROFILE)
        self.assertEqual(res["skills"], ["PHP", "Docker", "Comunicação"])
        self.assertEqual(res["skills_top3"], ["PHP"])
        self.assertEqual([e["company"] for e in res["experiences"]], ["Acme"])
        self.assertTrue(any("kubernetes" in w for w in res["warnings"]))
        self.assertFalse(any("s3" in w for w in res["warnings"]))   # "AWS S3" do perfil conta como s3

    def test_about_from_page_text(self):
        txt = "Análise\n\nSobre\n\nSou dev há 2 anos e gosto de APIs… mais\nAtividade\n\n10 seguidores"
        self.assertEqual(lp.about_from_page(txt), "Sou dev há 2 anos e gosto de APIs")
        self.assertEqual(lp.about_from_page("sem seção"), "")

    def test_market_counts_dev_jobs_and_marks_what_you_have(self):
        with tempfile.TemporaryDirectory() as d:
            db = DB(Path(d) / "v.db")
            try:
                for i, (stack, dev) in enumerate([(["php", "angular"], True), (["php"], True), (["excel"], False)]):
                    j = Job(id=f"x:{i}", source="linkedin", external_id=str(i), title="Dev", company="C", url="u")
                    j.classification = {"is_dev_role": dev, "seniority": "junior", "stack": stack}
                    db.upsert_job(j); db.save_job(j)
                m = lp.market(db, PROFILE, Settings())
            finally:
                db.conn.close()
        top = {x["tech"]: x for x in m["top"]}
        self.assertEqual(m["jobs"], 2)
        self.assertEqual(top["php"]["pct"], 100.0)
        self.assertTrue(top["php"]["have"])
        self.assertFalse(top["angular"]["have"])
        self.assertNotIn("excel", top)

    def test_banner_html_keeps_text_on_the_right_and_uses_profile(self):
        h = lp.banner_html(PROFILE, "minimal")
        self.assertIn("Ana Silva", h)
        self.assertIn("Criador do Loja X", h)
        self.assertIn("github.com/ana", h)
        self.assertIn("right: 90px", h)


if __name__ == "__main__":
    unittest.main()
