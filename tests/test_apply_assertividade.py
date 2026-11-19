"""Falhas de inscrição de 17–19/11: queda de internet e perguntas fechadas que viravam pendência."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from vagas import netcheck
from vagas.config import LLMCfg
from vagas.qa import FieldQ, QAEngine


def engine():
    profile = {"personal": {"city": "Campinas - SP", "state": "SP"},
               "facts": {"work_models_accepted": "remoto", "salary_expectation_junior_clt": "6000"},
               "headline": "Desenvolvedor Full Stack", "summary": "Resumo do perfil.", "experiences": [], "skills": {}}
    return QAEngine(LLMCfg(), None, None, profile, None)


def ask(q, label, work_model="remote"):
    job = SimpleNamespace(company="Acme", title="Dev", classification={"work_model": work_model})
    return q._closed_answer(FieldQ(id="1", label=label, kind="radio", options=["Sim", "Não"]), job)


class ClosedAnswerTests(unittest.TestCase):
    def setUp(self):
        self.q = engine()

    def test_kinship(self):
        self.assertEqual(ask(self.q, "Você possui grau de parentesco com algum de nossos Elovers?"), "Não")

    def test_residence(self):
        self.assertEqual(ask(self.q, "Você reside em Ponta Grossa/PR?"), "Não")
        self.assertEqual(ask(self.q, "Você reside em Campinas?"), "Sim")

    def test_onsite_meetings_only_when_job_is_local(self):
        label = "Você possui disponibilidade para encontros presenciais periódicos em Santa Catarina ou Rio Grande do Sul?"
        self.assertEqual(ask(self.q, label), "Não")
        self.assertEqual(ask(self.q, label, work_model="hybrid"), "Sim")
        self.assertEqual(ask(self.q, "Você concorda em se locomover até a localidade da vaga?"), "Não")


class PandapeFieldsTests(unittest.TestCase):
    """Formulário da Smart Sky (Pandapé), visto na captura de 19/11."""
    def setUp(self):
        self.q = engine()
        self.job = SimpleNamespace(company="Smart Sky", title="Desenvolvedor(a) Backend Júnior - Banco de Talentos",
                                   description="", classification={"work_model": "remote", "seniority": "junior"})

    def a(self, label, kind="text", options=()):
        return self.q._closed_answer(FieldQ(id="1", label=label, kind=kind, options=list(options)), self.job)

    def test_salary_range_min_below_max(self):
        lo, hi = int(self.a("Entre R$ (Bruto Mensal)")), int(self.a("e R$ (Bruto Mensal)"))
        self.assertEqual(lo, 6000)
        self.assertGreater(hi, lo)

    def test_profile_fields(self):
        self.assertEqual(self.a("Título"), "Desenvolvedor Full Stack")
        self.assertEqual(self.a("Resumo", kind="textarea"), "Resumo do perfil.")
        self.assertEqual(self.a("Posto desejado"), "Desenvolvedor(a) Backend Júnior")
        self.assertEqual(self.a("Jornada desejada", kind="select", options=["Noturno", "Parcial manhãs", "Período Integral"]), "Período Integral")

    def test_does_not_hijack_other_questions(self):
        self.assertIsNone(self.a("A pretensão salarial é negociável?"))


class NetTests(unittest.TestCase):
    def test_network_error_only_counts_when_offline(self):
        err = "Page.goto: net::ERR_INTERNET_DISCONNECTED at https://x"
        self.assertTrue(netcheck.is_network_error(err))
        self.assertFalse(netcheck.is_network_error("não achei botão de avançar/enviar"))
        with patch.object(netcheck, "online", return_value=True):
            self.assertFalse(netcheck.offline_error(err))     # site com problema, internet ok: segue como falha
        with patch.object(netcheck, "online", return_value=False):
            self.assertTrue(netcheck.offline_error(err))


if __name__ == "__main__":
    unittest.main()
