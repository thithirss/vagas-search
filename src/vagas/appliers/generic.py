"""Applier genérico (qualquer ATS) + variantes Gupy / InHire com seletores específicos."""
from __future__ import annotations

import re
import time

from rich.console import Console

from .. import forms, humanize as h
from ..browser import Page
from ..db import Job
from ..forms import FlowResult
from .base import Applier, ApplyContext
from .flow import dismiss_cookies, run_form_flow

console = Console()

APPLY_BTN_RE = re.compile(
    r"candidat|apply|inscrev|quero (essa|esta) vaga|quero me candidatar|aplicar|aplique|enviar curr|tenho interesse|"
    r"me inscrever|participar|i'm interested|estou interessado|postular|postule", re.I,
)
# textos que casam com APPLY_BTN_RE mas são navegação de conta/rodapé (Gupy logada: "Minhas candidaturas", "Empresas que me candidatei")
APPLY_NEG_RE = re.compile(r"minhas candidaturas|me candidatei|candidaturas?$|aviso|privacidade|termos|pol[ií]tica|cookies|suporte|sair|whatsapp|compartilh|share", re.I)
LOGIN_WALL_RE = re.compile(r"[/#](login|signin|sign-in|sign_in|entrar|auth|authenticate|cadastro|signup|sign-up|register|account)\b|^https?://(account|login|auth|accounts|id|sso)\.", re.I)
LOGIN_PROMPT_RE = re.compile(
    r"sua sess[aã]o expirou|voc[eê] foi desconectado|fa[cç]a login novamente|"
    r"quer se candidatar a esta vaga\?.{0,100}(criar meu curr[ií]culo|j[aá] tenho conta)|"
    r"entre no empregos\.com\.br|j[aá] existe um curr[ií]culo cadastrado com esse e-?mail", re.I | re.S)



def _registrable_domain(host: str) -> str:
    """"g4f.portaldetalentos.senior.com.br" -> "senior.com.br"; "accenture.wd103.myworkdayjobs.com" -> "myworkdayjobs.com"."""
    parts = (host or "").lower().split(".")
    if len(parts) >= 3 and parts[-2] in ("com", "net", "org", "gov", "edu", "co") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


class GenericApplier(Applier):
    name = "generic"
    apply_selectors: list[str] = []
    login_hosts: list[str] = []

    def _click_apply(self, ctx: ApplyContext, page: Page) -> Page:
        """Clica no botão de candidatura (se houver). Devolve a página onde o formulário está."""
        btn = None
        # SuccessFactors (careers.<empresa>.com, ex.: EY): o link real de candidatura fica em a.apply (oculto) ou no próprio
        # href do botão dropdown "Candidatar-se agora ▾"; navegar direto é mais robusto do que abrir o menu
        try:
            direct = page.locator("a.apply[href*='/apply/'], a.dialogApplyBtn[href*='/apply/'], button.dropdown-toggle[href*='/apply/'], a[href*='/talentcommunity/apply/']").first
            href = direct.get_attribute("href") if direct.count() else None
        except Exception:
            href = None
        if href and href.startswith(("/", "http")) and not APPLY_NEG_RE.search(href):
            from urllib.parse import urljoin, urlparse
            target_url = urljoin(page.url, href)
            page.goto(target_url, wait_until="domcontentloaded")
            h.medium()
            # SuccessFactors sem sessão devolve para a home do site de carreiras: exige conta de candidato
            if "/apply" not in (page.url or "") and urlparse(page.url or "").path.strip("/") in ("", "go", "home"):
                host = urlparse(page.url or "").netloc
                try:
                    ctx.db.kv_set(f"needs_account:{host}", target_url)
                    ctx.db.add_pending_question(None, f"O site {host} (SuccessFactors) exige conta de candidato para aplicar. Crie a conta uma vez no Chrome da ferramenta (vagas login) em {target_url} e recoloque as vagas dessa empresa na fila.", "link", [], "")
                except Exception:
                    pass
            return page

        def usable(el) -> bool:
            try:
                if not el.is_visible():
                    return False
                txt = (el.inner_text(timeout=500) or "") + " " + (el.get_attribute("aria-label") or "")
                if APPLY_NEG_RE.search(txt) or APPLY_NEG_RE.search(el.get_attribute("href") or ""):
                    return False
                # menus laterais/cabeçalho/rodapé não são o botão da vaga
                return not el.evaluate("e => !!e.closest('nav, aside, header, footer, [role=navigation], [class*=sidebar i], [class*=menu i]')")
            except Exception:
                return False

        for sel in self.apply_selectors:
            try:
                loc = page.locator(sel)
                for i in range(min(loc.count(), 6)):
                    b = loc.nth(i)
                    if usable(b):
                        btn = b
                        break
            except Exception:
                continue
            if btn:
                break
        if btn is None:
            cand = forms.find_button(page, APPLY_BTN_RE)
            if cand is not None and usable(cand):
                btn = cand
        if btn is None:
            # links/botões por papel + texto (SPAs que usam <a> sem role)
            for role in ("button", "link"):
                try:
                    loc = page.get_by_role(role, name=APPLY_BTN_RE)
                    for i in range(min(loc.count(), 6)):
                        el = loc.nth(i)
                        if usable(el):
                            btn = el
                            break
                except Exception:
                    continue
                if btn is not None:
                    break
        # SuccessFactors (careers.<empresa>.com, ex.: EY): "Candidatar-se agora ▾" é um dropdown-toggle e o link real
        # (a.apply[href*="/apply/"]) fica oculto no menu -> abre o menu e clica no item; sem botão, segue o href oculto
        if btn is not None:
            try:
                is_dropdown = bool(btn.evaluate("e => e.matches('.dropdown-toggle, [data-toggle=\"dropdown\"], [aria-haspopup=\"true\"]')"))
            except Exception:
                is_dropdown = False
            if is_dropdown:
                try:
                    h.click(btn)
                    time.sleep(0.8)
                    item = page.locator("a[href*='/apply'], [role='menuitem'] a, [role='menuitem'], .dropdown-menu a").filter(has_text=APPLY_BTN_RE).first
                    if item.count() and item.is_visible() and usable(item):
                        btn = item
                except Exception:
                    pass
        if btn is None:
            try:
                hidden = page.locator("a.apply[href*='/apply'], a[class*='apply' i][href*='/apply']").first
                href = hidden.get_attribute("href") if hidden.count() else None
            except Exception:
                href = None
            if href and not APPLY_NEG_RE.search(href):
                from urllib.parse import urljoin
                page.goto(urljoin(page.url, href), wait_until="domcontentloaded")
                h.medium()
            return page
        href = btn.get_attribute("href") or ""
        if href and not href.startswith(("#", "javascript:")) and not APPLY_NEG_RE.search(href):
            from urllib.parse import urljoin
            page.goto(urljoin(page.url, href), wait_until="domcontentloaded")
            h.medium()
            return page
        try:
            with page.context.expect_page(timeout=6000) as np:
                h.click(btn)
            newp = np.value
            newp.wait_for_load_state("domcontentloaded", timeout=30000)
            h.medium()
            return newp
        except Exception:
            h.medium()
            return page

    def _handle_login_wall(self, ctx: ApplyContext, page: Page) -> bool:
        url = page.url or ""
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        key = f"needs_account:{host}"
        known_wall = bool(ctx.db.kv_get(key) or ctx.db.kv_get("needs_account:*." + _registrable_domain(host)))
        fields_here = forms.best_scope(page)[1] if LOGIN_WALL_RE.search(url) or page.locator('input[type="password"]').count() else []
        login_form = bool(fields_here) and len(fields_here) <= 4 and any(f.q.kind == "password" for f in fields_here)
        login_prompt = bool(LOGIN_PROMPT_RE.search(forms.page_text(page)[:6000]))
        is_wall = login_prompt or login_form or any(host in url for host in self.login_hosts) or (LOGIN_WALL_RE.search(url) is not None and not fields_here)
        if not is_wall:
            return True
        if (login_form or login_prompt) and not ctx.interactive:
            # tela de login com senha (portal próprio da empresa): sem ninguém no navegador não adianta esperar
            try:
                ctx.db.kv_set(key, url)
                ctx.db.add_pending_question(None, f"O site {host} exige conta/login para candidatar. Crie a conta uma vez no Chrome da ferramenta (vagas login) em: {url}", "link", [], "")
            except Exception:
                pass
            console.print(f"[yellow]  {host} exige login/conta (tela com senha); pendência criada[/]")
            return False
        if not ctx.interactive and known_wall:
            # já sabemos que esse ATS exige conta e ninguém estava no navegador: não espera de novo
            console.print(f"[yellow]  {host} exige login/conta (crie a conta no Chrome da ferramenta: vagas login); pulando[/]")
            return False
        # leva sem ninguém olhando: espera pouco (3 min) e anota o host para as próximas
        # sem ninguém no teclado, login de site externo não se resolve sozinho: 5 s (redirecionamento) e pula
        ok = ctx.browser.wait_for_human(page, f"fazer login em {url} (a sessão fica salva no perfil)",
                                        timeout_min=None if ctx.interactive else 3, auto_seconds=5)
        if not ok and not ctx.interactive:
            try:
                ctx.db.kv_set(key, url)
                ctx.db.add_pending_question(None, f"O site {host} exige conta/login para candidatar. Crie a conta uma vez no Chrome da ferramenta (vagas login) em: {url}", "link", [], "")
            except Exception:
                pass
        return ok

    @staticmethod
    def _wait_render(page: Page, seconds: float = 12.0) -> None:
        """SPAs (InHire, Gupy, Recrutei) renderizam depois do load: espera rede acalmar e algum campo/botão aparecer."""
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                n = page.evaluate("() => document.querySelectorAll('input, textarea, select, button, [role=button]').length")
                if n and n > 2:
                    return
            except Exception:
                pass
            time.sleep(0.7)

    def _rescue_apply(self, ctx: ApplyContext, page: Page, job: Job):
        """Sem formulário nem botão conhecido: plano B (vagas/rescue.py) escolhe onde clicar para abrir a candidatura.
        Devolve a página com o formulário ou um FlowResult final."""
        from .. import rescue
        for _ in range(2):
            rs = rescue.pick(ctx, page, job, "apply")
            if rs.action != "click":
                if rs.situation == "encerrada":
                    return FlowResult("skipped", f"vaga encerrada (plano B: {rs.reason[:60]})")
                if rs.situation == "exige_conta":
                    ctx.db.add_pending_question(job.id, f"O site exige conta para candidatar. Crie a conta e recoloque a vaga na fila: {page.url}", "link", [], "")
                    return FlowResult("needs_user", "site exige conta para candidatar (plano B; pendência criada)")
                break
            if ctx.dry_run and rescue.would_commit(rs) and rs.kind == "enviar":
                return FlowResult("dry_run", f"dry-run: plano B pararia antes de '{rs.text[:40]}'")
            rs = rescue.click(ctx, page, job, "apply", rs)
            if rs.action != "click":
                break
            page = rs.page or page
            self._wait_render(page)
            dismiss_cookies(page)
            _, fields = forms.best_scope(page)
            if fields or forms.find_action_button(page):
                return page
        forms.dump_learn(page, job.id, self.name, [], "não achei formulário")
        return FlowResult("failed", "não achei formulário de candidatura")

    def apply(self, ctx: ApplyContext, page: Page, job: Job) -> FlowResult:
        if previous := ctx.db.previous_application(job, target_url=page.url):
            return FlowResult("already_applied", f"mesma vaga já aplicada: {previous.id}")
        # ATS que já sabemos exigir conta própria (Workday, SuccessFactors…): sem ninguém no navegador, pula na chegada.
        # Antes o robô começava a preencher o CADASTRO (e-mail + senha) do Workday até travar.
        from urllib.parse import urlparse
        host = urlparse(page.url or "").netloc
        wall = host and (ctx.db.kv_get(f"needs_account:{host}") or ctx.db.kv_get("needs_account:*." + _registrable_domain(host)))
        if wall and not ctx.interactive:
            console.print(f"[yellow]  {host} exige conta própria; pulando (crie a conta pelo painel: Abrir no navegador do bot)[/]")
            ctx.db.add_pending_question(job.id, f"O site {host} exige conta para candidatar. Crie a conta uma vez e recoloque a vaga na fila.", "link", [], page.url)
            return FlowResult("needs_user", f"{host} exige conta para candidatar (pendência criada)")
        self._wait_render(page)
        dismiss_cookies(page)
        # vaga removida/encerrada (Gupy: redirect para /candidates/not-found; "vaga encerrada"): não é falha do robô
        try:
            if forms.CLOSED_RE.search(page.url or "") or forms.CLOSED_RE.search(forms.page_text(page)[:3000]):
                return FlowResult("skipped", "vaga encerrada ou não encontrada no site da empresa")
        except Exception:
            pass
        if not self._handle_login_wall(ctx, page):
            ctx.db.add_pending_question(job.id, "Faça login no portal no navegador do bot e recoloque a vaga na fila.", "link", [], page.url)
            return FlowResult("needs_user", "login no portal pendente; candidatura aguardando sua ação")
        h.read_page(page)
        scope, fields = forms.best_scope(page)
        target = page
        if len(fields) < 2:
            target = self._click_apply(ctx, page)
            self._wait_render(target)
            dismiss_cookies(target)
            if not self._handle_login_wall(ctx, target):
                ctx.db.add_pending_question(job.id, "Faça login no portal no navegador do bot e recoloque a vaga na fila.", "link", [], target.url)
                return FlowResult("needs_user", "login no portal pendente; candidatura aguardando sua ação")
            # "Access restricted / crie uma conta para se candidatar": plataforma com cadastro próprio -> pendência, sem insistir
            try:
                wall_text = forms.page_text(target)[:6000]
            except Exception:
                wall_text = ""
            if forms.ACCOUNT_WALL_RE.search(wall_text):
                from urllib.parse import urlparse
                host = urlparse(target.url or "").netloc
                try:
                    ctx.db.kv_set(f"needs_account:{host}", target.url or "")
                    ctx.db.add_pending_question(job.id, f"O site {host} exige conta para candidatar. Crie a conta uma vez no Chrome da ferramenta (vagas login) e recoloque a vaga na fila: {target.url}", "link", [], "")
                except Exception:
                    pass
                return FlowResult("needs_user", f"{host} exige conta para candidatar (pendência criada)")
            _, fields = forms.best_scope(target)
            # tela sem campos mas com botão de avançar (Gupy pós-login: "Olá Fulano, vamos continuar sua candidatura?" ->
            # Continuar; InHire: "Iniciar") também é começo de fluxo: run_form_flow lida com etapas sem campos.
            # SPAs demoram a renderizar depois do redirect de login: espera até 12 s por campos ou botão.
            deadline = time.time() + 12
            while len(fields) < 1 and not forms.find_action_button(target) and time.time() < deadline:
                time.sleep(1.0)
                _, fields = forms.best_scope(target)
            if len(fields) < 1 and not forms.find_action_button(target):
                # talvez precise clicar de novo (página da vaga -> formulário)
                target2 = self._click_apply(ctx, target)
                self._wait_render(target2)
                _, fields = forms.best_scope(target2)
                if len(fields) < 1 and not forms.find_action_button(target2):
                    target2 = self._rescue_apply(ctx, target2, job)
                    if isinstance(target2, FlowResult):
                        return target2
                target = target2
        res = run_form_flow(ctx, target, job, ats=self.name)
        if res.status == "applied":
            forms.screenshot_evidence(target, job.id)
        return res


class GupyApplier(GenericApplier):
    name = "gupy"
    # (sem a[href*="/candidate"]: logado, os links do menu "Minhas candidaturas"/"Editar perfil" também têm /candidates/)
    apply_selectors = ['[data-testid="apply-button"]', "button:has-text('Candidatar-se')", "a:has-text('Candidatar-se')",
                       "button:has-text('Ir para candidatura')", "a:has-text('Ir para candidatura')", "a:has-text('Candidatar')"]
    login_hosts = ["login.gupy.io", "gupy.io/candidates/signin", "gupy.io/candidates/login"]

    def apply(self, ctx: ApplyContext, page: Page, job: Job) -> FlowResult:
        result = super().apply(ctx, page, job)
        if result.status != "failed" or not re.search(r"formulário|mesma etapa", result.reason):
            return result
        # A candidatura rápida pode não carregar. O portal oficial usa o mesmo jobId.
        import base64
        import json
        from urllib.parse import urlparse
        parsed = urlparse(job.external_url or page.url)
        if not (parsed.hostname or "").endswith(".gupy.io") or not parsed.path.startswith("/job/"):
            return result
        try:
            encoded = parsed.path.split("/job/", 1)[1].strip("/")
            data = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4)))
            job_id = str(data.get("jobId") or "")
            if not job_id.isdigit():
                return result
        except (ValueError, TypeError, UnicodeDecodeError):
            return result
        console.print("[dim]  candidatura rápida indisponível; abrindo portal do candidato da mesma vaga[/]")
        if not ctx.browser.goto(page, f"https://{parsed.hostname}/candidates/jobs/{job_id}/apply"):
            return FlowResult("needs_user", "login/verificação no portal Gupy pendente")
        return super().apply(ctx, page, job)

    def _auto_login(self, ctx: ApplyContext, page: Page) -> bool:
        """Login na Gupy com GUPY_PASSWORD (.env): e-mail + senha, 'Acessar conta', Turnstile, 'Acessar conta' de novo."""
        import os
        pwd = os.environ.get("GUPY_PASSWORD", "").strip()
        email = ((ctx.profile.get("personal") or {}).get("email") or "").strip()
        if not pwd or not email:
            return False
        try:
            self._wait_render(page, 6)
            dismiss_cookies(page)
            page.get_by_label(re.compile(r"e-?mail", re.I)).first.fill(email)
            page.get_by_label(re.compile(r"^senha", re.I)).first.fill(pwd)
            h.short()
            btn = page.get_by_role("button", name=re.compile(r"^acessar conta", re.I)).first
            btn.click(timeout=5000)
            # Turnstile: clica no checkbox pela posição do iframe
            deadline = time.time() + 25
            clicked = False
            while time.time() < deadline:
                time.sleep(1.0)
                if "signin" not in page.url and "login" not in page.url:
                    return True
                try:
                    if btn.is_enabled():
                        btn.click(timeout=3000)
                        time.sleep(2.5)
                        if "signin" not in page.url and "login" not in page.url:
                            return True
                except Exception:
                    pass
                if not clicked:
                    try:
                        fr = page.locator("iframe[src*='challenges.cloudflare']").first
                        box = fr.bounding_box() if fr.count() else None
                        if box:
                            page.mouse.move(box["x"] + 30, box["y"] + box["height"] / 2, steps=10)
                            time.sleep(0.3)
                            page.mouse.click(box["x"] + 30, box["y"] + box["height"] / 2)
                            clicked = True
                    except Exception:
                        pass
            return "signin" not in page.url and "login" not in page.url
        except Exception as e:
            console.print(f"[yellow]  login Gupy automático falhou: {str(e)[:100]}[/]")
            return False

    def _login_via_linkedin(self, ctx: ApplyContext, page: Page) -> bool:
        """Gupy -> 'Entrar com LinkedIn' -> LinkedIn OAuth (pede a senha mesmo logado) -> 'Permitir' -> volta logado. Sem Turnstile."""
        import os
        pwd = os.environ.get("LINKEDIN_PASSWORD", "").strip()
        email = ((ctx.profile.get("personal") or {}).get("email") or "").strip()
        if not pwd:
            return False
        try:
            self._wait_render(page, 6)
            dismiss_cookies(page)
            li = page.get_by_role("button", name=re.compile(r"linkedin", re.I)).first
            if not li.count():
                li = page.locator("button:has-text('LinkedIn'), a:has-text('LinkedIn')").first
            if not li.count():
                return False
            h.click(li)
            deadline = time.time() + 90
            filled = False
            while time.time() < deadline:
                time.sleep(1.5)
                url = page.url or ""
                if "gupy.io" in url and not re.search(r"signin|login", url):
                    return True
                if "linkedin.com" in url:
                    try:
                        em = page.locator("input#username, input[name='session_key']").first
                        if em.count() and em.is_visible() and not em.input_value():
                            em.fill(email)
                        pw = page.locator("input#password, input[name='session_password']").first
                        if pw.count() and pw.is_visible() and not filled:
                            pw.fill(pwd); filled = True
                            time.sleep(0.5)
                            try:
                                page.get_by_role("button", name=re.compile(r"^entrar$|^sign in$", re.I)).first.click(timeout=4000)
                            except Exception:
                                pw.press("Enter")   # botão às vezes não é encontrado pelo papel; Enter envia o form
                            time.sleep(3)
                            continue
                        allow = page.get_by_role("button", name=re.compile(r"permitir|allow|autorizar", re.I)).first
                        if allow.count() and allow.is_visible():
                            h.click(allow); time.sleep(3); continue
                    except Exception:
                        pass
                    cp = ctx.browser.detect_checkpoint(page)
                    if cp == "captcha" or "checkpoint/challenge" in url:
                        return ctx.browser.wait_for_human(page, "verificação do LinkedIn no login da Gupy")
            return "gupy.io" in (page.url or "") and not re.search(r"signin|login", page.url or "")
        except Exception as e:
            console.print(f"[yellow]  login via LinkedIn falhou: {str(e)[:100]}[/]")
            return False

    def _handle_login_wall(self, ctx: ApplyContext, page: Page) -> bool:
        url = page.url or ""
        if any(host in url for host in self.login_hosts):
            if self._login_via_linkedin(ctx, page):
                console.print("[green]  login na Gupy via LinkedIn feito[/]")
                h.medium()
                return True
            if self._auto_login(ctx, page):
                console.print("[green]  login na Gupy feito com senha[/]")
                h.medium()
                return True
            return ctx.browser.wait_for_human(page, f"fazer login na Gupy em {url} (defina LINKEDIN_PASSWORD ou GUPY_PASSWORD no .env para automatizar)")
        return super()._handle_login_wall(ctx, page)


class InHireApplier(GenericApplier):
    name = "inhire"
    apply_selectors = ["button:has-text('Candidatar-se')", "a:has-text('Candidatar-se')", "button:has-text('Quero me candidatar')", "button:has-text('Candidatar')"]


class GreenhouseApplier(GenericApplier):
    name = "greenhouse"
    apply_selectors = ["a:has-text('Apply')", "button:has-text('Apply')", "#apply_button", "a.button--primary"]


class LeverApplier(GenericApplier):
    name = "lever"
    apply_selectors = ["a.postings-btn:has-text('Apply')", "a:has-text('Apply for this job')", "a[href$='/apply']"]


class RecruteiApplier(GenericApplier):
    name = "recrutei"
    apply_selectors = ["a:has-text('Candidatar-se agora')", "button:has-text('Candidatar-se agora')", "a:has-text('Candidatar-se para essa vaga')",
                       "a:has-text('Candidatar-se')", "button:has-text('Candidatar')"]
    login_hosts = ["recrutei.com.br/login", "recrutei.com.br/candidato/login"]


class WorkableApplier(GenericApplier):
    name = "workable"
    apply_selectors = ["a[data-ui='overview-apply-now']", "button:has-text('Apply')", "a:has-text('Apply')"]
