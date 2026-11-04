import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from vagas.config import Settings
from vagas.db import DB, Job, posting_url_key
from vagas.pipeline import Pipeline
from vagas.appliers.generic import GenericApplier


class ApplicationDeduplicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = DB(Path(self.tmp.name) / 'jobs.db')
        self.addCleanup(self.db.conn.close)

    def job(self, identifier, **values):
        return Job(id=f'linkedin:{identifier}', external_id=identifier, source='linkedin',
                   url=f'https://www.linkedin.com/jobs/view/{identifier}/', **values)

    def record(self, job):
        self.db.upsert_job(job)
        self.db.add_application(job, 'external', job.ats, None, [])

    def test_requeue_and_old_date_cannot_erase_send_history(self):
        job = self.job('100')
        self.record(job)
        self.db.conn.execute("UPDATE applications SET applied_at='2020-01-01'")
        self.db.conn.execute("UPDATE jobs SET applied_at='2020-01-01', status='queued'")
        self.db.conn.commit()
        self.assertEqual(self.db.previous_application(job).id, job.id)
        pipeline = Pipeline(Settings(), self.db, None, None, {}, None)
        pipeline.apply = Mock()
        self.assertEqual(pipeline.process(None, job, force=True).status, 'already_applied')
        pipeline.apply.assert_not_called()
        self.assertEqual(self.db.get_job(job.id).status, 'applied')

    def test_gupy_portal_and_encoded_link_are_same_posting(self):
        encoded = base64.b64encode(json.dumps({'jobId': 12756252, 'source': 'linkedin'}).encode()).decode()
        old = self.job('100', external_url=f'https://verity.gupy.io/job/{encoded}?jobBoardSource=linkedin')
        self.record(old)
        new = self.job('200', company='Outro nome', title='Outro título',
                       external_url='https://verity.gupy.io/candidates/jobs/12756252/apply')
        self.assertEqual(self.db.previous_application(new).id, old.id)
        self.assertIsNone(self.db.previous_application(self.job('300', external_url='https://verity.gupy.io/jobs/12755385')))

    def test_tracking_does_not_hide_same_external_job(self):
        old = self.job('100', external_url='https://boards.example.com/jobs/42?utm_source=linkedin&lang=pt')
        self.record(old)
        new = self.job('200', external_url='https://boards.example.com/jobs/42?lang=pt&utm_source=indeed')
        self.assertEqual(self.db.previous_application(new).id, old.id)
        new.external_url = 'https://boards.example.com/jobs/43?lang=pt'
        self.assertIsNone(self.db.previous_application(new))

    def test_job_query_parameter_is_preserved(self):
        self.assertNotEqual(posting_url_key('https://ats.example.com/apply?job=42'),
                            posting_url_key('https://ats.example.com/apply?job=43'))
        self.assertIsNone(posting_url_key('https://ats.example.com/login'))
        self.assertIsNone(posting_url_key('https://ats.example.com/jobs'))
        self.assertIsNone(posting_url_key('https://tenant.gupy.io/job/not-base64'))

    def test_normalized_company_title_and_different_roles(self):
        old = self.job('100', company=' Empresa  Ágil ', title='Dev PHP  Pleno')
        self.record(old)
        new = self.job('200', company='empresa ágil', title='dev php pleno')
        self.assertEqual(self.db.previous_application(new).id, old.id)
        new.title = 'Dev PHP Júnior'
        self.assertIsNone(self.db.previous_application(new))

    def test_site_detected_previous_application_without_local_send(self):
        old = self.job('100', company='Empresa', title='Dev PHP', status='applied')
        self.db.upsert_job(old)
        self.assertEqual(self.db.previous_application(old).id, old.id)

    def test_add_application_twice_keeps_one_record(self):
        job = self.job('100')
        self.record(job)
        self.db.add_application(job, 'external', None, None, [])
        self.assertEqual(self.db.applications_count(), 1)

    def test_redirected_external_url_blocks_form_before_any_click(self):
        old = self.job('100', external_url='https://ats.example.com/jobs/42')
        self.record(old)
        ctx = Mock(db=self.db)
        page = Mock(url='https://ats.example.com/jobs/42?utm_source=indeed')
        new = self.job('200')
        result = GenericApplier().apply(ctx, page, new)
        self.assertEqual(result.status, 'already_applied')
        ctx.browser.assert_not_called()
        page.evaluate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
