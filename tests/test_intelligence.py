import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vagas.budget import BudgetExceeded
from vagas.config import LLMCfg
from vagas.db import DB, Job
from vagas.intelligence import assess_job, failure_groups, failure_report, professional_evidence
from vagas.qa import AnswerBank, FieldQ, NeedsUserInput, QAEngine


PROFILE = {
    'personal': {'name': 'Pessoa Exemplo', 'email': 'private@example.com', 'cpf': 'SECRET', 'country': 'Brasil'},
    'headline': 'Desenvolvedor', 'skills': {'primary': ['Node.js'], 'familiar': ['React']},
    'projects': [{'name': 'ClickPets', 'description': 'Carteira de vacinação digital', 'stack': ['Node.js']}],
    'experiences': [{'company': 'Empresa', 'title': 'Desenvolvedor', 'stack': ['Node.js'],
                     'bullets': ['Integração de APIs REST em Node.js.']}],
    'facts': {}, 'years_experience_total': 3,
}


class FakeLLM:
    def __init__(self, result=None):
        self.cfg = LLMCfg()
        self.result = result
        self.calls = []

    def json(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result(kwargs) if callable(self.result) else self.result


class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = DB(Path(self.tmp.name) / 'test.db')
        self.addCleanup(self.db.conn.close)
        self.job = Job(id='job1', source='indeed', external_id='1', url='https://example.com/job',
                       title='Desenvolvedor Node.js', company='Empresa',
                       description='Obrigatório: Node.js. Diferencial: React.', classification={'fit_score': 70})

    def assessment(self):
        return {'requirements': [
            {'requirement_quote': 'Node.js', 'importance': 'required', 'status': 'matched', 'evidence_id': 'e0', 'evidence_quote': 'Node.js'},
            {'requirement_quote': 'React', 'importance': 'preferred', 'status': 'partial', 'evidence_id': 'e1', 'evidence_quote': 'React'},
        ]}

    def test_fit_has_verifiable_evidence_and_is_cached(self):
        llm = FakeLLM(self.assessment())
        result = assess_job(llm, self.db, PROFILE, self.job)
        self.assertEqual(result['score'], 83)
        self.assertEqual(result['requirements'][1]['source'], 'skills.familiar')
        self.assertEqual(assess_job(llm, self.db, PROFILE, self.job), result)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(self.job.classification['fit_score'], 70)
        profile2 = dict(PROFILE, skills={'primary': ['Python']})
        assess_job(llm, self.db, profile2, self.job)
        self.assertEqual(len(llm.calls), 2)

    def test_fit_discards_invented_requirements_and_evidence(self):
        llm = FakeLLM({'requirements': [
            {'requirement_quote': 'Node.js', 'status': 'matched', 'evidence_id': 'e0', 'evidence_quote': '10 anos como líder'},
            {'requirement_quote': 'Kubernetes', 'status': 'matched', 'evidence_id': 'e0', 'evidence_quote': 'Node.js'},
        ]})
        result = assess_job(llm, self.db, PROFILE, self.job)
        self.assertEqual(result['score'], 0)
        self.assertEqual(len(result['requirements']), 1)
        self.assertEqual(result['requirements'][0]['status'], 'unknown')
        payload = llm.calls[0]['user']
        self.assertNotIn('SECRET', payload)
        self.assertNotIn('private@example.com', payload)

    def test_budget_failure_keeps_existing_classification(self):
        self.assertIsNone(assess_job(FakeLLM(BudgetExceeded('teto')), self.db, PROFILE, self.job))
        self.assertEqual(self.job.classification, {'fit_score': 70})

    def seed_failures(self):
        for i in range(3):
            j = Job(id=f'failure{i}', source='indeed', external_id=str(i), url='https://example.com/job',
                    ats='indeed_apply', status='failed', attempts=2, reason='Telefone inválido')
            self.db.upsert_job(j)
        self.db.upsert_job(Job(id='sent', source='indeed', external_id='sent', url='u', status='applied', attempts=1, reason='Telefone inválido'))

    def test_failure_counts_vacancies_and_uses_only_aggregate_data(self):
        self.seed_failures()
        llm = FakeLLM({'recommendations': [
            {'platform': 'indeed_apply', 'category': 'phone', 'fix': 'Confirmar DDI antes de preencher o número nacional.'},
            {'platform': 'invented', 'category': 'phone', 'fix': 'Não existe'},
        ]})
        report = failure_report(self.db, llm)
        self.assertEqual(report['groups'], [{'platform': 'indeed_apply', 'category': 'phone', 'count': 3}])
        self.assertEqual(len(report['analysis']['recommendations']), 1)
        self.assertEqual(json.loads(llm.calls[0]['user']), report['groups'])
        failure_report(self.db, llm)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(failure_report(self.db)['analysis'], report['analysis'])

    def test_failure_budget_block_is_rate_limited(self):
        self.seed_failures()
        llm = FakeLLM(BudgetExceeded('teto'))
        self.assertEqual(failure_report(self.db, llm)['groups'][0]['count'], 3)
        failure_report(self.db, llm)
        self.assertEqual(len(llm.calls), 1)

    def test_open_answer_uses_project_evidence_and_is_not_saved_globally(self):
        class AnswersLLM:
            def answer_questions(inner, **kwargs):
                inner.input = kwargs
                return [{'id': 'q', 'answer': 'Desenvolvi o ClickPets com Node.js, conectando APIs à aplicação.',
                         'confidence': .98, 'evidence_ids': [next(e['id'] for e in kwargs['profile']['professional_evidence'] if e['source'] == 'projects')]}]
        llm = AnswersLLM()
        bank = AnswerBank(Path(self.tmp.name) / 'answers.yaml')
        qa = QAEngine(LLMCfg(), llm, self.db, PROFILE, bank)
        field = FieldQ('q', 'Descreva um projeto realizado e por que quer esta vaga', 'textarea', required=True)
        answers = qa.answer_batch([field], self.job)
        self.assertIn('ClickPets', answers['q'].value)
        self.assertEqual(bank.items, [])
        self.assertEqual(llm.input['job']['description'], self.job.description)
        self.assertTrue(llm.input['job']['personalized'])
        self.assertTrue(any(e['source'] == 'projects' for e in llm.input['profile']['professional_evidence']))

    def test_open_answer_with_invalid_citations_becomes_pending(self):
        llm = SimpleNamespace(answer_questions=lambda **kwargs: [
            {'id': 'q', 'answer': 'Tenho 10 anos de React', 'confidence': .99, 'evidence_ids': ['invented']}])
        bank = AnswerBank(Path(self.tmp.name) / 'answers.yaml')
        qa = QAEngine(LLMCfg(), llm, self.db, PROFILE, bank)
        with self.assertRaises(NeedsUserInput):
            qa.answer_batch([FieldQ('q', 'Descreva um projeto', 'textarea', required=True)], self.job)
        self.assertEqual(self.db.pending_questions()[0]['suggested'], '')

    def test_fit_is_persisted_by_pipeline_without_replacing_filter_score(self):
        from vagas.config import Settings
        from vagas.pipeline import Pipeline
        self.db.upsert_job(self.job)
        llm = FakeLLM(self.assessment())
        pipeline = Pipeline(Settings(), self.db, None, llm, PROFILE, None)
        pipeline._assess_fit(self.job)
        stored = self.db.get_job(self.job.id)
        self.assertEqual(stored.classification['fit_score'], 70)
        self.assertEqual(stored.classification['fit_assessment']['score'], 83)
        pipeline._assess_fit(self.job)
        self.assertEqual(len(llm.calls), 1)

    def test_failed_application_triggers_pattern_analysis(self):
        from vagas.config import Settings
        from vagas.forms import FlowResult
        from vagas.pipeline import Pipeline
        self.job.title = 'Desenvolvedor Júnior'
        self.job.classification = {'seniority': 'junior', 'fit_score': 95}
        self.job.status = 'queued'
        self.db.upsert_job(self.job)
        pipeline = Pipeline(Settings(), self.db, None, FakeLLM(), PROFILE, None)
        with patch.object(pipeline, 'apply', return_value=FlowResult('failed', 'Formato de telefone inválido')), \
                patch('vagas.intelligence.failure_report') as report:
            pipeline.process(None, self.job)
        report.assert_called_once_with(self.db, pipeline.llm)


if __name__ == '__main__':
    unittest.main()
