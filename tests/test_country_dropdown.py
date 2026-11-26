import re
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from vagas.forms import FormField, fill_field, scan
from vagas.qa import FieldQ, QAEngine


class CountryDropdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)

    def dropdown(self, country):
        self.page.set_content('''
            <label for="country">Country *</label>
            <button id="country" role="combobox" aria-haspopup="listbox"
                    onclick="document.querySelector('#popup').hidden=false">Selecione uma opção</button>
            <div id="popup" hidden>
              <input placeholder="Pesquise para selecionar uma opção">
              <ul role="listbox"></ul>
            </div>
            <script>
              const search = document.querySelector('#popup input');
              const list = document.querySelector('ul');
              let countries = [];
              window.setCountries = values => { countries = values; render(); };
              function render() {
                list.replaceChildren();
                for (const name of countries.filter(n => n.toLowerCase().includes(search.value.toLowerCase()))) {
                  const li = document.createElement('li');
                  li.setAttribute('role', 'option'); li.textContent = name;
                  li.onclick = () => {
                    document.querySelector('#country').textContent = name;
                    document.querySelector('#popup').hidden = true;
                  };
                  list.append(li);
                }
              }
              search.oninput = render;
            </script>
        ''')
        self.page.evaluate("values => setCountries(values)", [
            "Afghanistan (+93)", "Aland Islands (+358)", "Albania (+355)", "Algeria (+213)", country,
        ])
        return FormField(FieldQ("country", "Country", "combobox", required=True), "#country", "button", "button")

    def test_searches_english_country_name(self):
        field = self.dropdown("Brazil (+55)")
        self.assertTrue(fill_field(self.page, field, "Brasil", None))
        self.assertEqual(self.page.locator('#country').inner_text(), "Brazil (+55)")

    def test_searches_country_name_without_dial_code(self):
        field = self.dropdown("Brasil (+55)")
        self.assertTrue(fill_field(self.page, field, "Brazil (+55)", None))
        self.assertEqual(self.page.locator('#country').inner_text(), "Brasil (+55)")

    def test_missing_country_does_not_select_another(self):
        field = self.dropdown("Brunei (+673)")
        self.assertFalse(fill_field(self.page, field, "Brasil", None))
        self.assertEqual(self.page.locator('#country').inner_text(), "Selecione uma opção")

    def test_country_label_with_phone_instructions(self):
        self.dropdown("Brazil (+55)")
        self.page.evaluate('''() => {
            const label = document.querySelector('label');
            label.removeAttribute('for'); label.id = 'country-label';
            label.innerHTML = 'Mobile Number<br><br>Provide valid phone numbers to allow Recruiters to contact you.<br><br>Country *<span>obrigatório</span>';
            const button = document.querySelector('#country');
            const combo = document.createElement('div');
            for (const attr of button.attributes) combo.setAttribute(attr.name, attr.value);
            combo.tabIndex = 0; combo.textContent = button.textContent;
            combo.setAttribute('aria-labelledby', label.id);
            button.replaceWith(combo);
            const phoneLabel = document.createElement('label');
            phoneLabel.htmlFor = 'phone'; phoneLabel.textContent = 'Phone Number';
            const phone = document.createElement('input'); phone.id = 'phone'; phone.type = 'tel';
            document.body.append(phoneLabel, phone);
        }''')
        fields = scan(self.page)
        country = next(f for f in fields if f.q.kind == 'combobox')
        phone = next(f for f in fields if f.q.kind == 'tel')
        self.assertEqual(country.q.label, 'Country')
        qa = object.__new__(QAEngine)
        qa.compact = {'personal': {'country': 'Brasil', 'phone': '+55 31 99999-9999'}, 'facts': {}}
        qa.profile = qa.compact
        self.assertEqual(qa._rule(country.q), 'Brasil')
        self.assertEqual(qa._rule(phone.q), '+55 31 99999-9999')
        self.assertTrue(fill_field(self.page, country, qa._rule(country.q), None))
        self.assertEqual(self.page.locator('#country').inner_text(), 'Brazil (+55)')
        rescanned = next(f for f in scan(self.page) if f.q.kind == 'combobox')
        self.assertEqual(rescanned.q.current, 'Brazil (+55)')

    def test_saved_indeed_contact_fields(self):
        snapshot = Path('data/learn/indeed/indeed_d6e7cbb99d06bd3b/step10.html')
        if not snapshot.exists():
            self.skipTest('Captura local do Indeed não disponível')
        self.page.route('**/*', lambda route: route.abort())
        html = re.sub(r'<script\b[^>]*>.*?</script>', '', snapshot.read_text(encoding='utf-8'), flags=re.S | re.I)
        self.page.set_content(html, wait_until='domcontentloaded')
        fields = scan(self.page)
        country = next(f for f in fields if f.q.kind == 'combobox')
        self.assertEqual(country.q.label, 'Country')
        self.assertEqual(country.q.current, 'Brazil (+55)')
        self.assertTrue(country.q.required)
        self.assertTrue(any(f.q.label == 'Phone Number' for f in fields))


if __name__ == "__main__":
    unittest.main()
