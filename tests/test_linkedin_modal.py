import unittest
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from vagas.appliers.linkedin import LinkedInEasyApplyApplier, EASY_APPLY_ROOT
from vagas.db import Job
from vagas.forms import FlowResult, scan


class LinkedInModalTests(unittest.TestCase):
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
        self.applier = LinkedInEasyApplyApplier()

    def fixture(self, shadow=False, confirmation=False):
        self.page.set_content('<div id="interop-outlet" style="display:block;width:800px;min-height:500px"></div>')
        self.page.evaluate('''({shadow, confirmation}) => {
          const host = document.querySelector('#interop-outlet');
          const root = shadow ? host.attachShadow({mode: 'open'}) : host;
          root.innerHTML = `<section role="dialog" aria-label="Candidate-se à empresa" style="width:700px;height:400px">
            <h2>Candidate-se à empresa</h2><label>Nome<input name="nome" value="Fulano"></label>
            <button>Avançar</button></section>`;
          if (confirmation) {
            const prompt = document.createElement('section');
            prompt.setAttribute('role', 'dialog');
            prompt.innerHTML = '<h2>Salvar esta candidatura?</h2><button aria-label="Fechar">X</button><button>Descartar</button><button>Salvar</button>';
            prompt.querySelector('button').onclick = () => prompt.remove();
            root.append(prompt);
          }
        }''', {'shadow': shadow, 'confirmation': confirmation})

    def test_non_div_dialog_is_detected(self):
        self.fixture()
        self.assertEqual(self.applier._modal_selector(self.page), EASY_APPLY_ROOT)
        self.assertEqual(len(scan(self.page, EASY_APPLY_ROOT)), 1)

    def test_shadow_root_dialog_and_save_prompt(self):
        self.fixture(shadow=True, confirmation=True)
        self.applier._resume_open_application(self.page)
        self.assertEqual(self.applier._modal_selector(self.page), EASY_APPLY_ROOT)
        self.assertEqual(len(scan(self.page, EASY_APPLY_ROOT)), 1)
        self.assertEqual(self.page.get_by_role('button', name='Descartar').count(), 0)

    def test_unrelated_dialog_is_not_application(self):
        self.page.set_content('<section role="dialog">Notificações<button>Fechar</button></section>')
        self.assertIsNone(self.applier._modal_selector(self.page))

    def test_open_application_is_not_clicked_again(self):
        self.fixture(shadow=True)
        ctx = SimpleNamespace()
        job = Job(id='test', source='linkedin', external_id='test', url='about:blank')
        with patch('vagas.appliers.linkedin.LinkedInSource.apply_button') as button, \
             patch('vagas.appliers.linkedin.h.click') as click, \
             patch('vagas.appliers.linkedin.run_form_flow', return_value=FlowResult('failed', 'teste local')) as flow, \
             patch.object(self.applier, '_discard'):
            result = self.applier.apply(ctx, self.page, job)
        button.assert_not_called()
        click.assert_not_called()
        self.assertTrue(flow.called)
        self.assertEqual(result.reason if hasattr(result, 'reason') else result.message, 'teste local')
