"""LinkedIn: Easy Apply (modal) e redirecionamento para site externo."""
from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, unquote, urlparse

from rich.console import Console

from .. import forms, humanize as h
from ..browser import Page
from ..db import Job
from ..forms import FlowResult
from ..sources.linkedin import LinkedInSource
from .base import Applier, ApplyContext
from .flow import run_form_flow
from .registry import detect_ats, get_applier

console = Console()

MODAL_SELS = [".jobs-easy-apply-modal", "[data-test-modal][role='dialog']", "[role='dialog']", "dialog[open]", "[aria-modal='true']"]
EASY_APPLY_ROOT = '[data-vagas-easy-apply-root="true"]'
MODAL_JS = r"""() => {
""" + forms.DEEP_JS_HELPERS + r"""
  const visible = el => {
    const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const candidates = deepQuery(document, '.jobs-easy-apply-modal, [data-test-modal], [role="dialog"], dialog[open], [aria-modal="true"]')
    .filter(visible);
  // O LinkedIn também usa elementos diferentes de div dentro de shadow roots.
  // A confirmação de salvar não deve substituir a raiz do formulário.
  const controls = el => deepQuery(el, 'input:not([type="hidden"]), textarea, select, [role="combobox"]')
    .filter(control => control.type === 'file' || visible(control)).length;
  let root = candidates.find(el => controls(el) > 0);
  if (!root) root = candidates.find(el =>
    /candidat|apply|application/i.test(el.innerText || '') &&
    !/salvar esta candidatura|save this application|discard application/i.test(el.innerText || ''));
  if (!root) {
    const host = document.querySelector('#interop-outlet');
    if (host && visible(host) && controls(host) > 0 && /candidat|apply|application/i.test(host.innerText || host.shadowRoot?.textContent || '')) root = host;
  }
  if (!root) return false;
  deepQuery(document, '[data-vagas-easy-apply-root]').forEach(el => el.removeAttribute('data-vagas-easy-apply-root'));
  root.setAttribute('data-vagas-easy-apply-root', 'true');
  return true;
}"""
CONTINUE_RE = re.compile(r"continuar candidatura|continue applying|continuar", re.I)


def _member_page(ctx: ApplyContext, page: Page, job: Job) -> bool:
    """Uma página pública pede cadastro; não é o formulário externo da empresa."""
    def guest() -> bool:
        url = page.url or ""
        if re.search(r"linkedin\.com/(signup|login|authwall|uas/)", url):
            return True
        return bool(page.locator('.top-card-layout, .contextual-sign-in-modal, [data-tracking-control-name="public_jobs_apply-link-offsite"]').count())
    try:
        if not guest():
            return True
        ctx.browser.goto(page, "https://www.linkedin.com/feed/")
        ctx.browser.goto(page, f"https://www.linkedin.com/jobs/view/{job.external_id}/")
        h.medium()
        if not guest():
            return True
    except Exception:
        pass
    ctx.db.add_pending_question(job.id, "Faça login no LinkedIn no navegador do bot e recoloque esta vaga na fila.", "link", [], job.url)
    return False


def _button_kind(btn) -> str:
    """'easy_apply' ou 'external' conforme o texto/aria do botão encontrado na página."""
    try:
        label = (btn.inner_text(timeout=2000) or "") + " " + (btn.get_attribute("aria-label") or "")
    except Exception:
        label = ""
    return "easy_apply" if re.search(r"simplificada|easy apply", label, re.I) else "external"



def _no_apply_button(ctx: ApplyContext, page: Page, job: Job) -> FlowResult:
    """Sem botão de candidatura na página da vaga: já aplicada? encerrada? senão recarrega uma vez e registra dump."""
    text = forms.page_text(page)
    if forms.ALREADY_RE.search(text):
        return FlowResult("already_applied", "já aplicada anteriormente (página da vaga)")
    if forms.CLOSED_RE.search(text):
        return FlowResult("skipped", "vaga encerrada (não aceita mais candidaturas)")
    try:
        page.reload(wait_until="domcontentloaded")
        h.medium()
        btn = LinkedInSource.apply_button(page)
        if btn is not None:
            return FlowResult("retry", "botão apareceu após recarregar")
    except Exception:
        pass
    text = forms.page_text(page)
    if forms.CLOSED_RE.search(text):
        return FlowResult("skipped", "vaga encerrada (não aceita mais candidaturas)")
    forms.dump_learn(page, job.id, "linkedin", [], "botão de candidatura não encontrado", 0)
    return FlowResult("failed", "botão de candidatura não encontrado")


class LinkedInEasyApplyApplier(Applier):
    name = "linkedin_easy_apply"

    @staticmethod
    def _modal_selector(page: Page) -> str | None:
        try:
            return EASY_APPLY_ROOT if page.evaluate(MODAL_JS) else None
        except Exception:
            pass
        for sel in MODAL_SELS:
            try:
                loc = page.locator(sel)
                for i in range(min(loc.count(), 3)):
                    if loc.nth(i).is_visible():
                        return sel
            except Exception:
                continue
        return None

    @staticmethod
    def _resume_open_application(page: Page) -> None:
        """Fecha apenas a confirmação de salvar, preservando os dados do formulário."""
        try:
            dialogs = page.get_by_role("dialog").filter(has_text=re.compile(
                r"salvar esta candidatura\?|save this application\?", re.I))
            for i in range(dialogs.count() - 1, -1, -1):
                dialog = dialogs.nth(i)
                if not dialog.is_visible():
                    continue
                close = dialog.get_by_role("button", name=re.compile(
                    r"^(fechar|close|dismiss)( caixa de di[aá]logo)?$", re.I)).first
                if close.count() and close.is_visible():
                    close.click(timeout=2000)
                    return
        except Exception:
            pass

    @staticmethod
    def _continue_applying(page: Page) -> None:
        """Dialog 'Lembrete de segurança na busca de vagas' -> 'Continuar candidatura'."""
        try:
            for sel in ["a:has-text('Continuar candidatura')", "button:has-text('Continuar candidatura')", "a:has-text('Continue applying')", "button:has-text('Continue applying')"]:
                b = page.locator(sel).first
                if b.count() and b.is_visible():
                    h.click(b)
                    h.short()
                    return
        except Exception:
            pass

    @staticmethod
    def _unfollow(page: Page) -> None:
        try:
            cb = page.locator("#follow-company-checkbox")
            if cb.count() and cb.is_checked():
                page.locator("label[for='follow-company-checkbox']").first.click(timeout=2000)
                h.tiny()
        except Exception:
            pass

    @staticmethod
    def _close_success(page: Page) -> None:
        for sel in ["button[aria-label='Fechar']", "button[aria-label='Dismiss']", "button:has-text('Concluído')", "button:has-text('Done')", "button[aria-label='Fechar caixa de diálogo']"]:
            try:
                b = page.locator(sel).first
                if b.count() and b.is_visible():
                    h.click(b)
                    h.short()
                    return
            except Exception:
                continue

    @staticmethod
    def _discard(page: Page) -> None:
        try:
            for sel in ["button[aria-label='Fechar']", "button[aria-label='Dismiss']", ".artdeco-modal__dismiss"]:
                b = page.locator(sel).first
                if b.count() and b.is_visible():
                    h.click(b)
                    h.short()
                    break
            for sel in ["button[data-control-name='discard_application_confirm_btn']", "button:has-text('Descartar')", "button:has-text('Discard')"]:
                b = page.locator(sel).first
                if b.count() and b.is_visible():
                    h.click(b)
                    h.short()
                    break
        except Exception:
            pass

    def apply(self, ctx: ApplyContext, page: Page, job: Job) -> FlowResult:
        if job.url.rstrip("/") not in page.url:
            ctx.browser.goto(page, job.url)
            h.medium()
        self._resume_open_application(page)
        if not _member_page(ctx, page, job):
            return FlowResult("needs_user", "LinkedIn abriu página pública; sessão de login precisa ser restaurada")
        root = self._modal_selector(page)
        btn = None if root else LinkedInSource.apply_button(page)
        if btn is None and not root:
            res = _no_apply_button(ctx, page, job)
            if res.status != "retry":
                return res
            btn = LinkedInSource.apply_button(page)
            if btn is None:
                return FlowResult("failed", "botão de candidatura não encontrado")
        if btn is not None and _button_kind(btn) == "external":
            console.print("[dim]  a vaga virou candidatura externa; redirecionando para o fluxo externo[/]")
            job.apply_type = "external"
            ctx.db.save_job(job)
            return LinkedInExternalApplier().apply(ctx, page, job)
        if not root:
            h.click(btn)
            h.medium()
        self._continue_applying(page)
        root = self._modal_selector(page)
        if not root:
            h.medium()
            root = self._modal_selector(page)
        if not root:
            # o clique pode ter sido engolido (página ainda carregando, botão trocado no re-render): acha de novo e reclica
            btn2 = LinkedInSource.apply_button(page)
            if btn2 is not None and _button_kind(btn2) != "external":
                try:
                    h.click(btn2)
                except Exception:
                    pass
                deadline = time.time() + 10
                while time.time() < deadline and not root:
                    time.sleep(1.0)
                    self._continue_applying(page)
                    root = self._modal_selector(page)
        if not root:
            body = forms.page_text(page)
            if forms.LIMIT_RE.search(body):
                return FlowResult("limit", "limite diário do Easy Apply")
            if forms.ALREADY_RE.search(body):
                return FlowResult("already_applied", "já aplicada anteriormente", 0, [])
            forms.dump_learn(page, job.id, "linkedin", [], "modal do Easy Apply não abriu", 0)
            return FlowResult("failed", "modal do Easy Apply não abriu")

        def pre_step(p: Page, step: int) -> None:
            self._resume_open_application(p)
            self._continue_applying(p)
            self._unfollow(p)

        res = run_form_flow(ctx, page, job, ats="linkedin", root_selector=root, pre_step=pre_step)
        if res.status == "applied":
            forms.screenshot_evidence(page, job.id)
            self._close_success(page)
        elif res.status == "dry_run":
            self._discard(page)
        else:
            self._discard(page)
        return res


class LinkedInExternalApplier(Applier):
    name = "linkedin_external"

    @staticmethod
    def _find_external_link(page: Page) -> str | None:
        """Procura na página o link de candidatura externa (redirecionador do LinkedIn)."""
        for sel in ["a[href*='linkedin.com/safety/go']", "a[href*='/redir/']", "a[target='_blank'][href*='url=']"]:
            try:
                loc = page.locator(sel)
                for i in range(min(loc.count(), 5)):
                    a = loc.nth(i)
                    href = a.get_attribute("href") or ""
                    txt = (a.inner_text(timeout=500) or "") + " " + (a.get_attribute("aria-label") or "")
                    if re.search(r"candidat|apply", txt, re.I) or "safety/go" in href:
                        url = LinkedInExternalApplier._external_url(a)
                        if url:
                            return url
            except Exception:
                continue
        return None

    @staticmethod
    def _external_url(btn) -> str | None:
        try:
            href = btn.get_attribute("href") or ""
        except Exception:
            return None
        if not href:
            return None
        if "linkedin.com/safety/go" in href or "/redir/" in href:
            q = parse_qs(urlparse(href).query)
            target = (q.get("url") or [""])[0]
            return unquote(target) if target else None
        if href.startswith("http") and "linkedin.com" not in href:
            return href
        return None

    def apply(self, ctx: ApplyContext, page: Page, job: Job) -> FlowResult:
        if job.url.rstrip("/") not in page.url:
            ctx.browser.goto(page, job.url)
            h.medium()
        if not _member_page(ctx, page, job):
            return FlowResult("needs_user", "LinkedIn abriu página pública; sessão de login precisa ser restaurada")
        btn = LinkedInSource.apply_button(page)
        if btn is None:
            res = _no_apply_button(ctx, page, job)
            if res.status != "retry":
                return res
            btn = LinkedInSource.apply_button(page)
            if btn is None:
                return FlowResult("failed", "botão de candidatura não encontrado")
        if _button_kind(btn) == "easy_apply":
            console.print("[dim]  a vaga está em Easy Apply; redirecionando para o modal[/]")
            job.apply_type = "easy_apply"
            ctx.db.save_job(job)
            return LinkedInEasyApplyApplier().apply(ctx, page, job)
        newp: Page | None = None
        ext = self._external_url(btn) or self._find_external_link(page)
        if not ext and job.external_url and "linkedin.com" not in (urlparse(job.external_url).hostname or ""):
            ext = job.external_url
        if not ext:
            try:
                console.print(f"[dim]  botão externo sem href: tag={btn.evaluate('e => e.tagName')} aria={btn.get_attribute('aria-label')!r}[/]")
            except Exception:
                pass
        if not ext:
            # Botão sem href (LinkedIn 2026): o clique abre o diálogo "Gostaria de compartilhar seu perfil?", cujo
            # "Continuar" é o <a safety/go?url=...>. Clica, e se o link aparecer abre a URL decodificada como sempre.
            before = set(page.context.pages)
            try:
                h.click(btn)
                h.short()
                LinkedInEasyApplyApplier._continue_applying(page)
            except Exception:
                pass
            ext = self._find_external_link(page)
            opened = [p for p in page.context.pages if p not in before]
            if ext:
                console.print(f"[dim]  link externo achado no diálogo depois do clique[/]")
                for p in opened:
                    p.close()
            elif opened:
                newp = opened[-1]
                try:
                    newp.wait_for_load_state("domcontentloaded", timeout=45000)
                except Exception:
                    pass
        if ext:
            # abre direto o site da empresa (o link do LinkedIn é um redirecionador "safety/go?url=...")
            newp = page.context.new_page()
            try:
                newp.goto(ext, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                console.print(f"[yellow]  abrir {ext[:80]}: {str(e)[:80]}[/]")
        elif newp is None:
            try:
                with page.context.expect_page(timeout=15000) as np:
                    h.click(btn)
                    h.short()
                    LinkedInEasyApplyApplier._continue_applying(page)
                newp = np.value
                newp.wait_for_load_state("domcontentloaded", timeout=45000)
            except Exception:
                h.medium()
                if "linkedin.com/jobs" not in page.url:
                    newp = page
        # aba nova que ainda está em about:blank (o redirecionamento do LinkedIn demora): espera o site carregar;
        # se não sair do branco, usa o link guardado da vaga (ABGi falhou com "não achei formulário" em about:blank)
        if newp is not None and newp is not page and (newp.url or "about:blank").startswith("about:"):
            for _ in range(20):
                if not (newp.url or "about:blank").startswith("about:"):
                    break
                newp.wait_for_timeout(1000)
            if (newp.url or "about:blank").startswith("about:"):
                fallback = job.external_url if job.external_url and "linkedin.com" not in (urlparse(job.external_url).hostname or "") else None
                if fallback:
                    console.print(f"[dim]  aba ficou em branco; abrindo o link guardado {fallback[:80]}[/]")
                    try:
                        newp.goto(fallback, wait_until="domcontentloaded", timeout=45000)
                    except Exception:
                        pass
                else:
                    newp.close()
                    newp = None
        if newp is None:
            forms.dump_learn(page, job.id, "linkedin", [], "site externo não abriu", 0)
            return FlowResult("failed", "site externo não abriu")
        # link encurtado lnkd.in abre o aviso "This link will take you to a page that's not on LinkedIn" com o destino
        # num <a>: segue o destino (a Capgemini falhou com "não achei formulário" tentando preencher o aviso)
        if (urlparse(newp.url).hostname or "").endswith("lnkd.in"):
            try:
                dest = newp.evaluate("""() => [...document.querySelectorAll('a[href^="http"]')].map(a => a.href)
                    .find(h => !/(^|\\.)(linkedin\\.com|lnkd\\.in)(\\/|$)/.test(new URL(h).hostname + '/'))""")
                if dest:
                    console.print(f"[dim]  lnkd.in: seguindo para {dest[:90]}[/]")
                    newp.goto(dest, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                console.print(f"[yellow]  lnkd.in: {str(e)[:80]}[/]")
        if "linkedin.com" in (urlparse(newp.url).hostname or ""):
            if newp is not page:
                newp.close()
            ctx.db.add_pending_question(job.id, "Restaure o login no LinkedIn no navegador do bot e recoloque a vaga na fila.", "link", [], job.url)
            return FlowResult("needs_user", "redirecionamento do LinkedIn pediu login; não é o ATS da empresa")
        h.medium()
        job.external_url = newp.url
        host = urlparse(newp.url).hostname or ""
        job.ats = detect_ats(newp.url, ctx.settings.ats.known_hosts)
        console.print(f"[dim]  externo: {job.ats} · {newp.url[:100]}[/]")
        if any(host == s or host.endswith("." + s) for s in ctx.settings.ats.skip_hosts):
            res = FlowResult("failed", f"host ignorado por configuração: {host}")
        else:
            res = get_applier(job.ats).apply(ctx, newp, job)
        if newp is not page:
            try:
                newp.close()
            except Exception:
                pass
        return res


class LinkedInAutoApplier(Applier):
    """Tipo de candidatura desconhecido (descoberta sem conta): abre a vaga logado, descobre e despacha."""
    name = "linkedin_auto"

    def apply(self, ctx: ApplyContext, page: Page, job: Job) -> FlowResult:
        src = LinkedInSource(mode="session")
        src.fetch_details_session(ctx.browser, page, job)
        ctx.db.save_job(job)
        if job.apply_type == "closed":
            return FlowResult("failed", "vaga encerrada")
        if job.apply_type == "already_applied":
            return FlowResult("already_applied", "já aplicada anteriormente")
        if job.apply_type == "easy_apply":
            return LinkedInEasyApplyApplier().apply(ctx, page, job)
        if job.apply_type == "external":
            return LinkedInExternalApplier().apply(ctx, page, job)
        return FlowResult("failed", "não identifiquei o botão de candidatura")
