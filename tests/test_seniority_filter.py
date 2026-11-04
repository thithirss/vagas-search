import tempfile
import unittest
from pathlib import Path

from vagas.config import Filters, Settings, load_settings
from vagas.db import DB, Job
from vagas.pipeline import Pipeline


class SeniorityFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = DB(Path(self.tmp.name) / 'vagas.db')
        self.addCleanup(self.db.conn.close)
        self.s = Settings()
        self.s.filters = Filters(allowed_seniorities=['junior'], seniority_filter_since='2026-10-21')
        self.s.courses.auto_suggest = False
        self.pipeline = Pipeline(self.s, self.db, None, None, {}, None)
        self.counter = 0

    def job(self, title, level, discovered='2026-10-21T10:00:00'):
        self.counter += 1
        job = Job(id=f'linkedin:test{self.counter}', source='linkedin', external_id=str(self.counter),
                   url='https://example.com/vaga', title=title, description='Descrição já obtida.',
                   discovered_at=discovered, classification={
                       'is_dev_role': True, 'seniority': level, 'work_model': 'remote',
                       'language': 'pt-BR', 'fit_score': 90}, status='queued')
        self.db.upsert_job(job)
        return job

    def test_existing_senior_queue_is_preserved(self):
        j = self.job('Desenvolvedor Pleno', 'mid', '2026-10-20T23:59:59')
        self.assertTrue(self.pipeline.prepare(None, j))
        self.assertEqual(self.db.get_job(j.id).status, 'queued')

    def test_new_junior_is_accepted(self):
        for title in ['Desenvolvedor Júnior', 'Desenvolvedor Jr.', 'Desenvolvedor']:
            with self.subTest(title=title):
                self.assertTrue(self.pipeline.prepare(None, self.job(title, 'junior')))

    def test_other_and_unknown_levels_are_rejected(self):
        for title, level in [('Dev Pleno', 'mid'), ('Dev Sênior', 'senior'),
                             ('Tech Lead', 'lead'), ('Desenvolvedor', 'unspecified')]:
            with self.subTest(title=title):
                j = self.job(title, level)
                self.assertFalse(self.pipeline.prepare(None, j))
                self.assertEqual(self.db.get_job(j.id).status, 'skipped')

    def test_explicit_title_overrides_wrong_junior_classification(self):
        self.assertFalse(self.pipeline.prepare(None, self.job('Dev Pleno', 'junior')))

    def test_already_classified_queue_cannot_bypass_filter(self):
        j = self.job('Dev Sênior', 'senior')
        result = self.pipeline.process(None, j, force=True)
        self.assertEqual(result.status, 'skipped')
        self.assertEqual(j.attempts, 0)

    def test_empty_filter_preserves_previous_behavior(self):
        self.s.filters.allowed_seniorities = []
        self.assertTrue(self.pipeline.prepare(None, self.job('Dev Sênior', 'senior')))

    def test_repository_configuration(self):
        s = load_settings()
        self.assertEqual(s.filters.allowed_seniorities, ['junior', 'mid', 'unspecified'])
        self.assertEqual(s.filters.seniority_filter_since.isoformat(), '2026-10-21')

    def test_mid_requires_fit_score_when_enabled(self):
        self.s.filters.allowed_seniorities = ['junior', 'mid']
        self.s.apply.min_fit_score = 70
        self.assertTrue(self.pipeline.prepare(None, self.job('Dev Pleno', 'mid')))
        job = self.job('Dev Pleno', 'mid')
        job.classification['fit_score'] = 69
        self.assertFalse(self.pipeline.prepare(None, job))
        self.assertEqual(job.reason, 'fit 69 < 70')


if __name__ == '__main__':
    unittest.main()
