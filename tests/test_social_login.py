"""Login social com LinkedIn em sites de candidatura (2026-11-17): achar o botão certo e conduzir o OAuth."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from vagas.appliers.generic import GenericApplier, find_linkedin_login, linkedin_oauth
from vagas.config import Settings


class SocialLoginTests(unittest.TestCase):
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

    def found(self, html):
        self.page.set_content(html)
        return find_linkedin_login(self.page)

    def test_finds_social_buttons(self):
        self.assertIn("LinkedIn", self.found("<form><input type=email><button>Entrar com LinkedIn</button></form>"))
        self.assertIn("LinkedIn", self.found("<a href='/x'>Candidate-se com LinkedIn</a>"))
        self.assertTrue(self.found("<a href='https://www.linkedin.com/oauth/v2/authorization?client_id=1'>Continuar</a>"))
        self.assertTrue(self.found("<a href='/auth/linkedin'>Usar conta</a>"))
        # botão só com ícone, dentro de um bloco de login
        self.assertTrue(self.found("<div class='login'><p>Entre na sua conta</p><button class='btn-linkedin' aria-label='LinkedIn'>"
                                   "<svg width=20 height=20></svg></button></div>"))

    def test_ignores_follow_share_and_profile_links(self):
        self.assertIsNone(self.found("<a href='https://www.linkedin.com/company/acme'>Siga-nos no LinkedIn</a>"))
        self.assertIsNone(self.found("<button>Compartilhar no LinkedIn</button>"))
        self.assertIsNone(self.found("<a href='https://www.linkedin.com/in/fulano'>LinkedIn</a>"))
        self.assertIsNone(self.found("<footer><a href='/auth/linkedin'>Entrar com LinkedIn</a></footer>"))
        self.assertIsNone(self.found("<form><label>LinkedIn<input name=linkedin></label><button>Enviar</button></form>"))

    def test_oauth_clicks_allow_and_returns_when_back(self):
        """Lado do LinkedIn já logado: só "Permitir"; depois volta ao site."""
        allow = SimpleNamespace(count=lambda: 1, is_visible=lambda: True)
        none = SimpleNamespace(count=lambda: 0, is_visible=lambda: False)

        class FakePage:
            url = "https://www.linkedin.com/oauth/v2/authorization"
            def is_closed(self): return False
            def locator(self, sel): return SimpleNamespace(first=none)
            def get_by_role(self, role, name=None): return SimpleNamespace(first=allow)
        page = FakePage()
        state = page.__dict__
        ctx = SimpleNamespace(profile={}, browser=SimpleNamespace(detect_checkpoint=lambda p: None))

        def click(el):
            state["url"] = "https://site.example/minha-candidatura"
        with patch("vagas.appliers.generic.h.click", side_effect=click), patch("vagas.appliers.generic.time.sleep"):
            self.assertTrue(linkedin_oauth(ctx, page, lambda p: "site.example" in p.url, timeout=5))

    def test_disabled_by_preference(self):
        self.page.set_content("<button>Entrar com LinkedIn</button>")
        s = Settings.model_validate({"apply": {"social_login_linkedin": False}})
        ctx = SimpleNamespace(settings=s, db=None)
        self.assertFalse(GenericApplier()._social_login(ctx, self.page))

    def test_pandape_dropdown_prefers_form_without_account(self):
        self.page.set_content("""<div class="dropdown"><a role="button" class="btn dropdown-toggle" data-toggle="dropdown"
            onclick="document.querySelector('.dropdown-menu').style.display='block'">CANDIDATE-SE COM</a>
            <div class="dropdown-menu" style="display:none"><a href="#ij">Infojobs</a><a href="#ct">Seu currículo</a><a href="#chat">Chat</a></div></div>""")
        ctx = SimpleNamespace(settings=Settings(), db=None)
        with patch("vagas.appliers.generic.h.medium"), patch("vagas.appliers.generic.h.click", side_effect=lambda el: el.click()):
            GenericApplier()._click_apply(ctx, self.page)
        self.assertTrue(self.page.url.endswith("#ct"), self.page.url)


if __name__ == "__main__":
    unittest.main()
