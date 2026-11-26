import json
import unittest
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
        self.errors = []
        self.page.on('pageerror', lambda exc: self.errors.append(str(exc)))
        self.page.route('http://dashboard.test/**', self.route)
        self.page.goto('http://dashboard.test/#/falhas')

    def route(self, route):
        from urllib.parse import urlparse
        path = urlparse(route.request.url).path
        self.calls.append((route.request.method, path))
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
        elif path == '/api/tasks':
            result = {'running': [], 'recent': []}
        elif path.startswith('/api/jobs') or path == '/api/applications':
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


if __name__ == '__main__':
    unittest.main()
