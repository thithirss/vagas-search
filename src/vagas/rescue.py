"""Plano B do formulário: quando as regras não sabem o que clicar, a IA barata escolhe.

Medição de 01–13/11/2026: fora o Indeed (Cloudflare), as falhas de envio eram uma cauda longa — cada site
falhava de um jeito ("não achei formulário" na Capgemini/EY/Pandapé, "sem botão de avançar" na TOTVS,
Avanade, Mindsight, Arcadis…). Regra fixa não acompanha isso. Aqui:

1. Lista os elementos clicáveis VISÍVEIS (inclusive em shadow DOM) e marca cada um com `data-vagas-rescue`.
2. Tira o que nunca pode ser clicado (`forms.BAD_RE`, menus, "minhas candidaturas", desabilitados).
3. Se este site já foi destravado antes, usa o texto aprendido (`kv rescue:<host>:<objetivo>`), sem IA.
4. Senão pergunta ao `cheap_model` (gpt-6-luna: ~US$ 0,0003 por pergunta) qual elemento avança.
5. Clica, confere se a página mudou e, se mudou, grava o texto do elemento para o próximo envio nesse site.

Limites: `llm.rescue_max_per_job` por candidatura, freio do orçamento (purpose "rescue") e, no dry-run, nunca
clica em algo que registraria a candidatura. Nada aqui inventa dado: só escolhe onde clicar.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from rich.console import Console

from . import forms
from .budget import BudgetExceeded

console = Console()

GOALS = {
    "apply": "abrir o formulário de candidatura desta vaga (botão Candidatar-se / Aplicar / Quero me candidatar)",
    "advance": "avançar para a próxima etapa do formulário ou enviar a candidatura (os campos visíveis já foram preenchidos)",
}
NEVER_RE = re.compile(r"minhas candidaturas|me candidatei|alerta|favorit|salvar vaga|save job|whatsapp|chat|idioma|language|"
                      r"pol[ií]tica|privacidade|privacy|termos|terms|rejeitar|reject|configura|settings|google|facebook|apple|"
                      r"outras vagas|ver vagas|vagas similares|similar jobs|imprimir|print|baixar|download|v[ií]deo|mapa",
                      re.I)
SENT_RE = re.compile(r"obrigad|sucesso|success|thank|recebemos|recebida|received|enviad", re.I)

CANDIDATES_JS = r"""
(maxN) => {
""" + forms.DEEP_JS_HELPERS + r"""
  const vis = (e) => {
    const st = getComputedStyle(e);
    if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  deepQuery(document, '[data-vagas-rescue]').forEach(e => e.removeAttribute('data-vagas-rescue'));
  const sel = 'a[href], button, [role=button], [role=link], [role=tab], input[type=submit], input[type=button], [onclick]';
  const out = [], seen = new Set();
  for (const e of deepQuery(document, sel)) {
    if (out.length >= maxN) break;
    if (!vis(e) || e.closest('[aria-hidden="true"]')) continue;
    const text = ((e.innerText || e.value || '') + '').replace(/\s+/g, ' ').trim().slice(0, 70);
    const aria = (e.getAttribute('aria-label') || e.getAttribute('title') || '').replace(/\s+/g, ' ').trim().slice(0, 70);
    if (!text && !aria) continue;
    const href = e.getAttribute('href') || '';
    const key = text + '|' + aria + '|' + href;
    if (seen.has(key)) continue;
    seen.add(key);
    const zone = e.closest('nav, header, footer, [role=navigation], aside') ? 'menu'
               : (e.closest('[role=dialog], [aria-modal="true"]') ? 'dialog' : 'main');
    const i = out.length;
    e.setAttribute('data-vagas-rescue', String(i));
    let h = '';
    try { h = href && !href.startsWith('#') && !href.startsWith('javascript') ? new URL(href, location.href).href.slice(0, 100) : href.slice(0, 20); } catch (err) {}
    out.push({i, tag: e.tagName.toLowerCase(), text, aria, href: h, zone,
              disabled: !!(e.disabled || e.getAttribute('aria-disabled') === 'true')});
  }
  return out;
}
"""


@dataclass
class Rescue:
    action: str = "none"          # click | none
    text: str = ""                # texto do elemento escolhido
    kind: str = ""                # candidatar | avancar | enviar | aceitar_cookies
    situation: str = ""           # normal | enviada | encerrada | exige_conta | sem_saida
    reason: str = ""
    source: str = ""              # aprendido | ia
    page: object = None           # página onde a candidatura continua (o clique pode abrir aba nova)


def candidates(page) -> list[dict]:
    try:
        raw = page.evaluate(CANDIDATES_JS, 60) or []
    except Exception:
        return []
    out = []
    for c in raw:
        label = f"{c.get('text', '')} {c.get('aria', '')}".strip()
        if c.get("disabled") or c.get("zone") == "menu":
            continue
        if forms.BAD_RE.search(label) or NEVER_RE.search(label) or NEVER_RE.search(c.get("href") or ""):
            continue
        out.append(c)
    return out[:40]


def _host(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower()


def _key(page, goal: str) -> str:
    return f"rescue:{_host(page.url)}:{goal}"


def _learned(db, page, goal: str) -> list[str]:
    try:
        return json.loads(db.kv_get(_key(page, goal)) or "[]")
    except Exception:
        return []


def _learn(db, page_url: str, goal: str, text: str) -> None:
    key = f"rescue:{_host(page_url)}:{goal}"
    try:
        known = json.loads(db.kv_get(key) or "[]")
    except Exception:
        known = []
    t = text.strip().lower()
    if t and t not in known:
        db.kv_set(key, json.dumps(([t] + known)[:5], ensure_ascii=False))


def _signature(page) -> tuple:
    try:
        return (page.url, hash((forms.page_text(page) or "")[:5000]), len(page.context.pages))
    except Exception:
        return (getattr(page, "url", ""), 0, 0)


def _page_info(page, fields, goal: str) -> dict:
    try:
        text = (forms.page_text(page) or "")[:1500]
    except Exception:
        text = ""
    try:
        errs = forms.errors(page)[:5]
    except Exception:
        errs = []
    try:
        title = page.title()[:100]
    except Exception:
        title = ""
    return {
        "objetivo": GOALS[goal], "site": _host(page.url), "titulo": title, "texto_visivel": text,
        "campos": [{"rotulo": f.q.label[:60], "tipo": f.q.kind, "preenchido": bool(f.is_filled)} for f in (fields or [])][:15],
        "erros_na_tela": errs,
    }


def pick(ctx, page, job, goal: str, fields=None) -> Rescue:
    """Decide (sem clicar) qual elemento leva a candidatura adiante."""
    cfg = ctx.settings.llm
    if not getattr(cfg, "rescue_enabled", True) or ctx.rescues >= getattr(cfg, "rescue_max_per_job", 3):
        return Rescue(reason="plano B desligado ou já usado o máximo nesta vaga")
    cands = candidates(page)
    if not cands:
        return Rescue(reason="nenhum elemento clicável utilizável na tela")
    by_text = {c["text"].strip().lower(): c for c in cands if c.get("text")}
    for t in _learned(ctx.db, page, goal):
        if t in by_text:
            c = by_text[t]
            return Rescue("click", c["text"], "avancar" if goal == "advance" else "candidatar", "normal",
                          "aprendido neste site", "aprendido")
    llm = getattr(ctx.qa, "llm", None)
    if llm is None or not hasattr(llm, "pick_action"):
        return Rescue(reason="sem IA disponível")
    elements = [{k: c[k] for k in ("i", "tag", "text", "aria", "href", "zone") if c.get(k)} for c in cands]
    try:
        out = llm.pick_action(goal=GOALS[goal], page=_page_info(page, fields, goal), elements=elements)
    except BudgetExceeded as e:
        return Rescue(reason=f"sem orçamento de IA: {e}")
    except Exception as e:
        return Rescue(reason=f"IA falhou: {str(e)[:80]}")
    situation = str(out.get("situacao") or "normal")
    reason = str(out.get("motivo") or "")[:120]
    idx = out.get("index")
    chosen = next((c for c in cands if isinstance(idx, int) and c["i"] == idx), None)
    if chosen is None:
        return Rescue(situation=situation, reason=reason or "IA não achou elemento que avance", source="ia")
    return Rescue("click", chosen["text"] or chosen.get("aria", ""), str(out.get("acao") or ""), situation, reason, "ia")


def click(ctx, page, job, goal: str, r: Rescue) -> Rescue:
    """Clica no elemento escolhido por `pick`. Se a página mudar, grava o texto como aprendido para o site."""
    ctx.rescues += 1
    url_before = page.url
    before_sig = _signature(page)
    before_pages = set(page.context.pages)
    loc = None
    try:
        cands = candidates(page)   # remarca: a página pode ter re-renderizado desde o pick
        c = next((c for c in cands if (c["text"] or c.get("aria", "")).strip().lower() == r.text.strip().lower()), None)
        if c is not None:
            loc = page.locator(f'[data-vagas-rescue="{c["i"]}"]').first
    except Exception:
        loc = None
    if loc is None:
        r.action, r.reason = "none", "elemento sumiu antes do clique"
        return r
    try:
        loc.scroll_into_view_if_needed(timeout=3000)
        loc.click(timeout=6000)
    except Exception as e:
        r.action, r.reason = "none", f"clique falhou: {str(e)[:80]}"
        return r
    time.sleep(2.5)
    new_pages = [p for p in page.context.pages if p not in before_pages]
    target = new_pages[-1] if new_pages else page
    if new_pages:
        try:
            target.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
    r.page = target
    changed = bool(new_pages) or _signature(page) != before_sig
    console.print(f"[magenta]  plano B ({r.source}): cliquei '{r.text[:40]}' — {r.reason[:70]}"
                  f"{'' if changed else ' (página não mudou)'}[/]")
    if changed:
        _learn(ctx.db, url_before, goal, r.text)
    else:
        r.action = "none"
    try:
        ctx.db.log("info", f"plano B {r.source}: '{r.text[:60]}' em {_host(url_before)} ({goal}); mudou={changed}", job.id,
                   {"reason": r.reason, "kind": r.kind})
    except Exception:
        pass
    return r


def would_commit(r: Rescue) -> bool:
    """O clique escolhido registraria a candidatura? (no dry-run, para antes)"""
    from .appliers.flow import DRY_RUN_COMMIT_RE, DRY_RUN_REVIEW_RE
    return r.kind == "enviar" or (bool(DRY_RUN_COMMIT_RE.search(r.text)) and not DRY_RUN_REVIEW_RE.search(r.text))


def looks_sent(page) -> bool:
    try:
        return bool(SENT_RE.search((forms.page_text(page) or "")[:3000]))
    except Exception:
        return False
