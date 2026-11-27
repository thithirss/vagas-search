import json
import unittest
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright


class IntelligenceDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.calls = []
        self.bodies = []
        self.errors = []
        self.page.on('pageerror', lambda exc: self.errors.append(str(exc)))
        self.page.route('http://dashboard.test/**', self.route)
        self.page.goto('http://dashboard.test/#/falhas')

    def route(self, route):
        from urllib.parse import urlparse
        path = urlparse(route.request.url).path
        self.calls.append((route.request.method, path))
        if route.request.method == 'POST':
            self.bodies.append((path, json.loads(route.request.post_data or '{}')))
        if path == '/':
            html = (Path(__file__).parents[1] / 'src/vagas/dashboard_static/index.html').read_text(encoding='utf-8')
            route.fulfill(body=html, content_type='text/html')
            return
        result = {}
        if path == '/api/failures' or path == '/api/failures/analyze':
            result = {'groups': [{'platform': 'indeed_apply', 'category': 'phone', 'count': 3}],
                      'analysis': {'recommendations': [{'platform': 'indeed_apply', 'count': 3, 'fix': 'Confirmar o DDI.'}]}, 'stale': False}
        elif path == '/api/job/job1':
            result = {'id': 'job1', 'title': 'Dev', 'company': 'Empresa', 'source': 'indeed', 'status': 'queued',
                      'url': 'https://example.com', 'attempts': 1, 'learn': [], 'pending': [], 'events': [],
                      'classification': {'fit_assessment': {'score': 50, 'requirements': [
                          {'requirement': '<img src=x onerror=alert(1)> React', 'importance': 'preferred',
                           'status': 'gap', 'evidence': ''}]}}}
        elif path == '/api/job/job1/fit':
            result = {'score': 50}
        elif path == '/api/prefs':
            result = {
                'values': {'filters.allowed_seniorities': ['junior']},
                'schema': [{'id': 'filters', 'title': 'Filtros', 'fields': [{
                    'key': 'filters.allowed_seniorities', 'label': 'Senioridade', 'type': 'multi',
                    'options': [['junior', 'Júnior'], ['pleno', 'Pleno']],
                }]}],
                'changed': [], 'presets': {}, 'searches_total': 1, 'run_running': False,
            }
        elif path == '/api/tasks':
            result = {'running': [], 'recent': []}
        elif path == '/api/pending':
            result = [{
                'id': 10, 'job_id': 'job-pending', 'title': 'Backend', 'company': 'Acme',
                'question': 'Experiência com Python?', 'field_type': 'text', 'options': [],
                'suggested': '', 'created_at': '2026-11-27T10:00:00', 'is_note': False,
                'job_status': 'pending_question',
            }, {
                'id': 11, 'job_id': 'job-pending', 'title': 'Backend', 'company': 'Acme',
                'question': 'Pretensão salarial?', 'field_type': 'text', 'options': [],
                'suggested': '', 'created_at': '2026-11-27T10:00:00', 'is_note': False,
                'job_status': 'pending_question',
            }]
        elif path == '/api/answer':
            result = {'ok': True}
        elif path == '/api/applications':
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            result = [{
                'job_id': f'job-{i}', 'title': 'Dev', 'company': 'Empresa', 'source': 'linkedin',
                'ats': 'linkedin', 'applied_at': f'{yesterday}T10:0{i}:00', 'answers': [],
                'resume_rel': '', 'classification': {'seniority': 'junior', 'fit_score': 80},
            } for i in range(2)] + [{
                'job_id': 'job-today', 'title': 'Backend', 'company': 'Acme', 'source': 'gupy',
                'ats': 'gupy', 'applied_at': f'{date.today().isoformat()}T09:00:00', 'answers': [],
                'resume_rel': '', 'classification': {'seniority': 'junior', 'fit_score': 90},
            }]
        elif path.startswith('/api/jobs'):
            result = []
        route.fulfill(body=json.dumps(result), content_type='application/json')

    def test_failure_patterns_and_analysis_action(self):
        self.page.locator('#analyze-failures').wait_for()
        self.assertIn('Confirmar o DDI.', self.page.locator('#content').inner_text())
        self.page.locator('#analyze-failures').click()
        self.page.wait_for_function("() => document.querySelector('#analyze-failures') && !document.querySelector('#analyze-failures').disabled")
        self.assertIn(('POST', '/api/failures/analyze'), self.calls)
        self.assertEqual(self.errors, [])

    def test_job_evidence_is_displayed_as_text_and_can_be_refreshed(self):
        self.page.locator('#analyze-failures').wait_for()
        self.page.evaluate("() => openJob('job1')")
        self.assertIn('Compatibilidade com seu perfil', self.page.locator('#d-body').inner_text())
        self.assertIn('lacuna', self.page.locator('#d-body').inner_text())
        self.assertEqual(self.page.locator('#d-body img').count(), 0)
        self.page.locator('[data-fitjob]').click()
        self.page.wait_for_function("() => document.querySelector('[data-fitjob]') && !document.querySelector('[data-fitjob]').disabled")
        self.assertIn(('POST', '/api/job/job1/fit'), self.calls)
        self.assertEqual(self.errors, [])

    def test_preference_chip_updates_on_first_click(self):
        self.page.evaluate("location.hash = '#/prefs'")
        button = self.page.locator('button[data-multi="pleno"]')
        button.wait_for()
        button.click()

        self.assertIn('on', (button.get_attribute('class') or '').split())
        self.assertEqual(self.errors, [])

    def test_applications_show_yesterday_count(self):
        self.page.evaluate("location.hash = '#/candidaturas'")
        history = self.page.locator('#send-history')
        history.wait_for()

        self.assertIn('Ontem', history.inner_text())
        self.assertIn('2', history.inner_text())
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.page.locator('#sent-day').select_option(yesterday)
        self.assertEqual(self.page.locator('#sent-list tr.row').count(), 2)
        self.assertEqual(self.page.locator('#sent-selected').inner_text(), '2')
        self.assertEqual(self.page.locator(f'[data-sent-day="{yesterday}"]').get_attribute('aria-pressed'), 'true')
        self.assertEqual(self.errors, [])

    def test_application_history_fits_phone_in_dark_mode(self):
        self.page.set_viewport_size({'width': 375, 'height': 812})
        self.page.emulate_media(color_scheme='dark')
        self.page.evaluate("location.hash = '#/candidaturas'")
        card = self.page.locator('.sent-day').first
        card.wait_for()

        self.assertGreaterEqual(card.bounding_box()['height'], 44)
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
        self.assertEqual(self.errors, [])

    def test_all_pending_answers_are_sent_with_one_click(self):
        self.page.evaluate("location.hash = '#/pendencias'")
        self.page.locator('.pending-job summary').click()
        fields = self.page.locator('.pending-question .ans')
        fields.nth(0).fill('5 anos')
        fields.nth(1).fill('R$ 8.000')
        self.page.locator('[data-answer-all]').click()
        self.page.wait_for_function("() => document.querySelector('[data-answer-all]:not(:disabled)')")

        answers = [body for path, body in self.bodies if path == '/api/answer']
        self.assertEqual(answers, [{'id': 10, 'answer': '5 anos'}, {'id': 11, 'answer': 'R$ 8.000'}])
        self.assertEqual(self.calls.count(('GET', '/api/pending')), 2)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
