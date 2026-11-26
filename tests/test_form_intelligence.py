import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from vagas import forms
from vagas.appliers.base import ApplyContext
from vagas.appliers.flow import run_form_flow
from vagas.config import Settings
from vagas.db import DB, Job
from vagas.form_intelligence import FormIntelligence
from vagas.qa import AnswerBank, FieldQ, QAEngine
from tests.test_intelligence import FakeLLM, PROFILE


class FormIntelligenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = DB(Path(self.tmp.name) / 'test.db')
        self.addCleanup(self.db.conn.close)
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.settings = Settings()
        self.job = Job(id='j1', source='indeed', external_id='1', url='https://example.com/job', title='Dev')
        self.db.upsert_job(self.job)

    def ctx(self, llm):
        profile = dict(PROFILE, personal=dict(PROFILE['personal'], phone='+55 31 99999-9999'))
        qa = QAEngine(self.settings.llm, llm, self.db, profile, AnswerBank(Path(self.tmp.name) / 'answers.yaml'))
        return ApplyContext(self.settings, SimpleNamespace(detect_checkpoint=lambda p: None), self.db, qa, profile)

    def test_understands_unknown_country_from_context_without_sending_profile(self):
        self.page.set_content('<label for="x">Campo obrigatório</label><input id="x" name="contact_country" placeholder="Selecione uma opção" required>')
        llm = FakeLLM(lambda kw: {'fields': [{'id': json.loads(kw['user'])['fields'][0]['id'], 'meaning': 'country', 'confidence': .98}]})
        ctx = self.ctx(llm)
        engine = FormIntelligence(ctx, self.job, 'test')
        fields = forms.scan(self.page)
        engine.understand(self.page, fields)
        self.assertEqual(ctx.qa._rule(fields[0].q, self.job), 'Brasil')
        self.assertIn('contact_country', llm.calls[0]['user'])
        self.assertNotIn('SECRET', llm.calls[0]['user'])
        self.assertNotIn('private@example.com', llm.calls[0]['user'])
        fields = forms.scan(self.page)
        engine.understand(self.page, fields)
        self.assertEqual(fields[0].q.label, 'País / Country')
        self.assertEqual(len(llm.calls), 1)

    def phone_page(self):
        self.page.set_content('''<form>
          <label for="phone">Phone Number</label><input id="phone" value="+55 31 99999-9999" required>
          <button type="button" onclick="if (/^\\d{11}$/.test(document.querySelector('input').value)) document.body.innerHTML='<h2>Etapa seguinte</h2><input placeholder=Cidade>'">Continuar</button>
        </form>''')

    def test_recovers_with_known_value_and_learns_only_after_progress(self):
        self.phone_page()
        llm = FakeLLM(lambda kw: {'repairs': [{'id': json.loads(kw['user'])['fields'][0]['id'], 'strategy': 'phone_national', 'value': 'evil'}]})
        ctx = self.ctx(llm)
        engine = FormIntelligence(ctx, self.job, 'test')
        fields = forms.scan(self.page)
        engine.observe(self.page, fields)
        self.assertTrue(engine.recover(self.page, fields, ['Formato não aceito'], []))
        self.assertEqual(self.page.locator('#phone').input_value(), '31999999999')
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM kv WHERE key LIKE 'form_strategy:%'").fetchone()[0], 0)
        self.assertFalse(engine.recover(self.page, fields, ['Formato não aceito'], []))
        self.page.get_by_text('Continuar', exact=True).click()
        engine.observe(self.page, forms.scan(self.page))
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM kv WHERE key LIKE 'form_strategy:%'").fetchone()[0], 1)
        self.phone_page()
        no_llm = FakeLLM(AssertionError('Não deve chamar a IA'))
        learned = FormIntelligence(self.ctx(no_llm), self.job, 'test')
        self.assertTrue(learned.recover(self.page, forms.scan(self.page), ['Formato não aceito'], []))
        self.assertEqual(no_llm.calls, [])

    def test_untrusted_actions_and_other_person_fields_are_ignored(self):
        self.page.set_content('<label for=x>Nome da mãe</label><input id=x required>')
        llm = FakeLLM(lambda kw: {'fields': [{'id': json.loads(kw['user'])['fields'][0]['id'], 'meaning': 'full_name', 'confidence': 1}]})
        engine = FormIntelligence(self.ctx(llm), self.job, 'test')
        fields = forms.scan(self.page)
        engine.understand(self.page, fields)
        self.assertEqual(fields[0].q.label, 'Nome da mãe')
        llm.result = lambda kw: {'repairs': [{'id': fields[0].q.id, 'strategy': 'submit', 'value': 'Fake'}]}
        self.assertFalse(engine.recover(self.page, fields, [], []))
        self.assertEqual(self.page.locator('#x').input_value(), '')

    def test_call_limit_and_disabled_switches(self):
        self.phone_page()
        llm = FakeLLM({})
        engine = FormIntelligence(self.ctx(llm), self.job, 'test')
        for _ in range(7):
            engine.recover(self.page, forms.scan(self.page), ['Formato não aceito'], [])
        self.assertEqual(len(llm.calls), 3)
        self.settings.llm.diagnose_forms = False
        self.assertFalse(engine.recover(self.page, forms.scan(self.page), [], []))
        self.settings.llm.understand_fields = False
        engine.understand(self.page, forms.scan(self.page))
        self.assertEqual(len(llm.calls), 3)

    def test_flow_uses_diagnosis_after_persistent_validation_errors(self):
        self.page.set_content('''<form>
          <label for="phone">Phone Number</label><input id="phone" value="+55 31 99999-9999" required>
          <div role="alert" id="err"></div>
          <button type="button" onclick="if (/^\\d{11}$/.test(document.querySelector('input').value)) document.body.innerHTML='<h2>Sua candidatura foi enviada</h2>'; else document.querySelector('#err').textContent='Formato inválido'">Continuar</button>
        </form>''')
        llm = FakeLLM(lambda kw: {'repairs': [{'id': json.loads(kw['user'])['fields'][0]['id'], 'strategy': 'phone_national'}]})
        ctx = self.ctx(llm)
        with patch('vagas.humanize.pause'), patch('vagas.humanize.short'), patch('vagas.humanize.tiny'), patch('vagas.appliers.flow.dismiss_cookies'):
            result = run_form_flow(ctx, self.page, self.job, ats='test', max_steps=8)
        self.assertEqual(result.status, 'applied')
        self.assertTrue(any(a['source'] == 'diagnostic' for a in result.answers))
        self.assertEqual(len(llm.calls), 1)

    def test_repair_transforms_do_not_invent_missing_data(self):
        q = FieldQ('x', 'Data de nascimento', 'text')
        self.assertEqual(FormIntelligence._transform(q, '2002-04-06', 'date_slashes'), '06/04/2002')
        self.assertIsNone(FormIntelligence._transform(q, None, 'date_iso'))
        self.assertIsNone(FormIntelligence._transform(FieldQ('x', 'Country', 'combobox'), '+55 31 99999-9999', 'phone_national'))

    def test_phone_code_is_not_the_full_phone_or_country_name(self):
        qa = self.ctx(FakeLLM()).qa
        q = FieldQ('ddi', 'Campo', 'number', context={'meaning': 'phone_code'})
        self.assertEqual(qa._rule(q, self.job), '55')
        q.kind = 'text'
        self.assertEqual(qa._rule(q, self.job), '+55')

    def test_malformed_model_output_does_not_change_fields(self):
        self.phone_page()
        llm = FakeLLM({'repairs': [{'id': ['invalid'], 'strategy': {'bad': 'value'}}]})
        engine = FormIntelligence(self.ctx(llm), self.job, 'test')
        self.assertFalse(engine.recover(self.page, forms.scan(self.page), [], []))
        self.assertEqual(self.page.locator('#phone').input_value(), '+55 31 99999-9999')

    def test_empty_loading_screen_does_not_confirm_learning(self):
        self.phone_page()
        llm = FakeLLM(lambda kw: {'repairs': [{'id': json.loads(kw['user'])['fields'][0]['id'], 'strategy': 'phone_national'}]})
        engine = FormIntelligence(self.ctx(llm), self.job, 'test')
        fields = forms.scan(self.page)
        engine.observe(self.page, fields)
        self.assertTrue(engine.recover(self.page, fields, [], []))
        self.page.set_content('<p>Carregando...</p>')
        engine.observe(self.page, [])
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM kv WHERE key LIKE 'form_strategy:%'").fetchone()[0], 0)

    def test_dry_run_still_stops_before_submission(self):
        self.page.set_content('<form><label>Phone Number<input value="+55 31 99999-9999"></label><button type="submit">Enviar candidatura</button></form>')
        llm = FakeLLM(AssertionError('Não precisa de IA'))
        ctx = self.ctx(llm)
        ctx.dry_run = True
        with patch('vagas.humanize.pause'), patch('vagas.appliers.flow.dismiss_cookies'), patch('vagas.forms.dump_learn'):
            result = run_form_flow(ctx, self.page, self.job, ats='test', max_steps=2)
        self.assertEqual(result.status, 'dry_run')
        self.assertEqual(llm.calls, [])


if __name__ == '__main__':
    unittest.main()
