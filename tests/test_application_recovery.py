import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vagas.config import Settings
from vagas.db import DB, Job
from vagas.pipeline import Pipeline, desc_hash
from vagas.classify_rules import classify_job_rules
from playwright.sync_api import sync_playwright
from vagas import forms
from vagas.forms import FlowResult
from vagas.sources.indeed import IndeedSource, already_applied
from vagas.appliers.linkedin import _member_page
from vagas.appliers.generic import GenericApplier
from vagas.appliers.flow import run_form_flow
from vagas.db import Job


class RecoveryBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch(channel='chrome', headless=True)
        except Exception:
            cls.pw.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)

    def test_indeed_submission_confirmation_stops_before_any_other_action(self):
        self.page.set_content('<h1>Sua inscrição foi enviada</h1><button>Continuar</button>')
        browser = SimpleNamespace(detect_checkpoint=lambda p: None)
        ctx = SimpleNamespace(settings=Settings(), browser=browser)
        job = Job(id='indeed:test', source='indeed', external_id='test', url='https://example.com')
        with patch('vagas.appliers.flow.h.click') as click:
            result = run_form_flow(ctx, self.page, job, ats='indeed')
        self.assertEqual(result.status, 'applied')
        click.assert_not_called()
        self.assertFalse(forms.SUCCESS_RE.search('Sua inscrição não foi enviada'))

    def test_linkedin_avaliar_is_next_even_for_submit_type(self):
        self.page.set_content('<button type="submit">Avaliar</button>')
        result = forms.find_action_button(self.page)
        self.assertIsNotNone(result)
        self.assertEqual(result[1], 'next')

    def test_navigation_tooltip_is_not_form_error(self):
        self.page.set_content('<div role="tooltip">Back to jobs</div><div role="alert">Campo obrigatório</div>')
        self.assertEqual(forms.errors(self.page), ['Campo obrigatório'])

    def test_login_modal_on_job_url_is_detected(self):
        self.page.set_content('<label>Email<input type="email"></label><label>Senha<input type="password"></label>')
        db = SimpleNamespace(kv_get=lambda key: None, kv_set=unittest.mock.Mock(), add_pending_question=unittest.mock.Mock())
        self.assertFalse(GenericApplier()._handle_login_wall(SimpleNamespace(db=db, interactive=False), self.page))
        db.add_pending_question.assert_called_once()

    def test_generic_apply_follows_real_link_in_same_page(self):
        self.page.route('**/*', lambda route: route.fulfill(body='<h1>Entrar</h1>', content_type='text/html'))
        self.page.set_content('<a href="https://app.example.com/signin?job=42" target="_blank">Apply now</a>')
        with patch('vagas.appliers.generic.h.medium'):
            target = GenericApplier()._click_apply(SimpleNamespace(), self.page)
        self.assertIs(target, self.page)
        self.assertEqual(self.page.url, 'https://app.example.com/signin?job=42')

    def test_expired_session_without_password_is_pending(self):
        self.page.set_content('<div>Você foi desconectado. Sua sessão expirou. Faça login novamente.</div>')
        db = SimpleNamespace(kv_get=lambda key: None, kv_set=unittest.mock.Mock(), add_pending_question=unittest.mock.Mock())
        self.assertFalse(GenericApplier()._handle_login_wall(SimpleNamespace(db=db, interactive=False), self.page))

    def test_indeed_applied_badge_requires_disabled_state(self):
        self.page.set_content('<button data-testid="viewjob-indeed-apply" disabled>Candidaturas</button>')
        self.assertTrue(already_applied(self.page))
        self.page.set_content('<button data-testid="viewjob-indeed-apply">Candidaturas</button>')
        self.assertFalse(already_applied(self.page))

    def test_character_counters_are_not_errors_but_overflow_is(self):
        self.page.set_content('''<div class="helper-text">235 / 1500 caracteres</div>
          <div class="helper-text">1501 / 1500 caracteres</div>
          <div role="alert">Campo obrigatório</div>''')
        errors = forms.errors(self.page)
        self.assertNotIn('235 / 1500 caracteres', errors)
        self.assertIn('1501 / 1500 caracteres', errors)
        self.assertIn('Campo obrigatório', errors)
        # LinkedIn junta os dois formatos no mesmo texto (30/10)
        self.page.set_content('''<div role="alert">0/20 0 de 20 caracteres</div>
          <div role="alert">21/20 21 de 20 caracteres</div>''')
        errors = forms.errors(self.page)
        self.assertNotIn('0/20 0 de 20 caracteres', errors)
        self.assertIn('21/20 21 de 20 caracteres', errors)

    def test_current_indeed_description_and_smartapply_button(self):
        self.page.route('**/*', lambda route: route.fulfill(body='''<h1>Desenvolvedor PHP Pleno</h1>
          <div class="simple-job-description-html">Descrição completa da vaga com Laravel, PHP e MySQL. Trabalho remoto.</div>
          <a data-testid="viewjob-indeed-apply" href="https://smartapply.indeed.com/beta/indeedapply/test">Candidatar-se</a>''', content_type='text/html'))
        self.page.goto('https://fixture.invalid/viewjob?jk=test')
        job = Job(id='indeed:test', external_id='test', source='indeed', url=self.page.url)
        with patch('vagas.sources.indeed.h.read_page'):
            IndeedSource().fetch_details(None, self.page, job)
        self.assertIn('Laravel', job.description)
        self.assertEqual(job.apply_type, 'indeed_apply')

    def test_guest_linkedin_page_requires_session_instead_of_external_ats(self):
        self.page.set_content('<div class="top-card-layout">Cadastre-se</div>')
        db = SimpleNamespace(add_pending_question=unittest.mock.Mock())
        browser = SimpleNamespace(goto=unittest.mock.Mock())
        job = Job(id='linkedin:test', external_id='test', source='linkedin', url='https://www.linkedin.com/jobs/view/test/')
        with patch('vagas.appliers.linkedin.h.medium'):
            self.assertFalse(_member_page(SimpleNamespace(browser=browser, db=db), self.page, job))
        self.assertEqual(browser.goto.call_count, 2)
        db.add_pending_question.assert_called_once()

    def test_cached_login_requirement_does_not_block_logged_in_form(self):
        self.page.set_content('<label>Nome<input></label><label>Email<input></label><button>Enviar</button>')
        ctx = SimpleNamespace(db=SimpleNamespace(kv_get=lambda key: 'login requerido'), interactive=False)
        self.assertTrue(GenericApplier()._handle_login_wall(ctx, self.page))


class ClassificationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = DB(Path(self.tmp.name) / 'jobs.db')
        self.addCleanup(self.db.conn.close)
        self.settings = Settings()
        self.settings.courses.auto_suggest = False
        self.profile = {'skills': {'primary': ['PHP', 'Laravel']}}
        self.pipeline = Pipeline(self.settings, self.db, None, None, self.profile, None)

    def job(self, id, description='PHP Laravel desenvolvimento remoto. ' * 12):
        return Job(id=id, source='linkedin', external_id=id, title='Desenvolvedor PHP Pleno', company='Empresa',
                   url='https://example.com', location='Brasil', description=description)

    def test_already_applied_is_neither_new_send_nor_failure(self):
        job = self.job('already')
        job.status = 'queued'
        job.classification = {'seniority': 'mid', 'fit_score': 95}
        self.db.upsert_job(job)
        self.pipeline.browser = SimpleNamespace(page=lambda: None, close_extra_pages=lambda page: None)
        with patch.object(self.pipeline, 'process', return_value=FlowResult('already_applied', 'já enviada')), \
             patch.object(self.pipeline.pacer, 'in_active_hours', return_value=True), \
             patch.object(self.pipeline.pacer, 'can_apply', return_value=(True, '')), \
             patch.object(self.pipeline.pacer, 'after_application'):
            summary = self.pipeline.process_queue()
        self.assertEqual(summary.already_applied, 1)
        self.assertEqual(summary.applied, 0)
        self.assertEqual(summary.failed, 0)

    def test_empty_descriptions_cannot_share_classification(self):
        cached = self.job('old', '')
        cached.classification = {'fit_score': 55, '_desc_hash': desc_hash('')}
        self.db.upsert_job(cached)
        self.assertIsNone(self.pipeline._reuse_classification(self.job('new', '')))

    def test_classification_cache_accounts_for_title_and_profile(self):
        cached = self.job('old')
        cached.classification = {'fit_score': 95, '_input_hash': self.pipeline._classification_key(cached)}
        self.db.upsert_job(cached)
        new = self.job('new')
        self.assertEqual(self.pipeline._reuse_classification(new)['fit_score'], 95)
        new.title = 'Desenvolvedor PHP Sênior'
        self.assertIsNone(self.pipeline._reuse_classification(new))
        new.title = cached.title
        self.pipeline.profile = {'skills': {'primary': ['Python']}}
        self.assertIsNone(self.pipeline._reuse_classification(new))

    def test_city_alone_does_not_prove_onsite(self):
        description = 'Desenvolver APIs com PHP e Laravel, usando testes e Git. ' * 12
        result, _ = classify_job_rules('Desenvolvedor PHP Pleno', 'Empresa', 'São Paulo, SP', description, self.profile, self.settings)
        self.assertEqual(result['work_model'], 'unspecified')
        result, _ = classify_job_rules('Desenvolvedor PHP Pleno', 'Empresa', 'São Paulo, SP', description+' Trabalho presencial no escritório.', self.profile, self.settings)
        self.assertEqual(result['work_model'], 'onsite')

    def test_unspecified_level_requires_higher_fit(self):
        self.settings.filters.allowed_seniorities = ['junior', 'mid', 'unspecified']
        job = self.job('new')
        job.title = 'Desenvolvedor PHP'
        job.classification = {'seniority': 'unspecified', 'fit_score': 79}
        self.assertIsNotNone(self.pipeline._seniority_rejection(job))
        job.classification['fit_score'] = 80
        self.assertIsNone(self.pipeline._seniority_rejection(job))

    def test_missing_description_is_retried_without_false_classification(self):
        job = self.job('new', '')
        self.db.upsert_job(job)
        source = SimpleNamespace(fetch_details=lambda *args: None)
        with patch.object(self.pipeline, '_source', return_value=source):
            self.assertFalse(self.pipeline.prepare(None, job, allow_browser=False))
        self.assertEqual(job.status, 'queued')
        self.assertIsNone(job.classification)
