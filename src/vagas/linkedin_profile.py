"""Estruturar o LinkedIn: análise com dados do mercado, capa e foto (tela "LinkedIn" do painel).

Fluxo (opção B, escolhida pelo usuário em 13/11/2026):
1. `read_current(page)` lê o perfil atual no navegador do bot (título, Sobre, texto da página) — só leitura.
2. `market(db, profile)` cruza as tecnologias das vagas de dev dos últimos N dias com as do perfil.
3. `analyze(...)` pede ao motor ESCOLHIDO (vagas/engines.py: ChatGPT API, Claude Code, API Anthropic, Codex)
   diagnóstico + textos novos; `validate` tira competência fora do perfil e avisa tecnologia citada que você não tem.
4. Capa (`banner`) e foto (`photo_improve`) são HTML renderizado pelo próprio Chromium do bot → PNG, sem IA.
   IA de imagem é opcional e só pela API da OpenAI (`image_ai`).
5. `apply_item(page, item)` abre o formulário certo do LinkedIn, preenche e PARA antes de "Salvar": quem salva é você.

Nada aqui inventa fato: só o profile.yaml, os projetos dele e o que já está no LinkedIn.
"""
from __future__ import annotations

import base64
import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from .config import DATA_DIR

DIR = DATA_DIR / "linkedin"
IMG = DIR / "img"
STATE = DIR / "analysis.json"
CURRENT = DIR / "current.json"
STATUS = DIR / "status.json"

BANNER_W, BANNER_H = 1584, 396
_FONTS = (Path(__file__).parent / "resume" / "templates" / "fonts").resolve().as_uri()
PHOTO = 800
# custo por imagem do gpt-image-2 (tabela oficial, 1024x1024 / 1536x1024), em US$; as fotos de referência somam entrada
IMAGE_COST_USD = {"low": 0.006, "medium": 0.053, "high": 0.211}
IMAGE_INPUT_EST_USD = 0.02


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def set_status(state: str, msg: str = "", **extra) -> None:
    _write(STATUS, {"state": state, "msg": msg, "at": _now(), **extra})


def status() -> dict:
    return _read(STATUS, {}) or {}


# ---------------------------------------------------------------- mercado
def profile_techs(profile: dict) -> set[str]:
    """Tecnologias do perfil no vocabulário do classificador. Além do nome canônico de cada item, detecta as que
    aparecem DENTRO dos nomes e textos ("AWS S3" → s3, "AWS" → cloud), senão o aviso de fatos dá alarme falso."""
    from .classify_rules import _fold, _tech_counts, canonical_tech
    from .resume.profile import all_skills
    skills = all_skills(profile)
    have = {canonical_tech(s) for s in skills}
    text = " ".join(skills) + " " + str(profile.get("summary") or "") + " " + " ".join(
        f"{p.get('name', '')} {p.get('description', '')}" for p in profile.get("projects") or [])
    have |= set(_tech_counts(_fold(text)))
    return have


def market(db, profile: dict, settings, days: int | None = None) -> dict:
    """Tecnologias mais pedidas nas vagas de DEV dos níveis que você busca, e quais você tem."""
    from .classify_rules import canonical_tech
    days = days or settings.linkedin.market_days
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    pref = set(settings.filters.preferred_seniorities or [])
    wanted = (pref | {"unspecified"}) if pref else None   # sem preferência no settings: todos os níveis
    cnt: Counter = Counter()
    total = 0
    for (raw,) in db.conn.execute("SELECT classification FROM jobs WHERE classification IS NOT NULL AND substr(discovered_at,1,10) >= ?", (since,)):
        try:
            c = json.loads(raw)
        except Exception:
            continue
        if not c.get("is_dev_role") or (wanted is not None and (c.get("seniority") or "unspecified") not in wanted):
            continue
        total += 1
        for t in {canonical_tech(str(x)) for x in (c.get("stack") or []) if x}:
            cnt[t] += 1
    have = profile_techs(profile)
    top = [{"tech": t, "jobs": n, "pct": round(100 * n / total, 1) if total else 0, "have": t in have} for t, n in cnt.most_common(40)]
    return {"jobs": total, "days": days, "top": top,
            "missing": [x for x in top if not x["have"]][:15], "have": [x for x in top if x["have"]][:25]}


# ---------------------------------------------------------------- perfil atual (navegador do bot, só leitura)
FIELD_JS = r"""(want) => {
  const deep = (root, sel, out=[]) => { root.querySelectorAll(sel).forEach(e => out.push(e)); root.querySelectorAll('*').forEach(e => e.shadowRoot && deep(e.shadowRoot, sel, out)); return out; };
  const lab = e => { const id = e.id; const l = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null; return ((l ? l.innerText : '') || e.getAttribute('aria-label') || e.placeholder || '').trim(); };
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const re = new RegExp(want, 'i');
  const el = deep(document, 'input:not([type=hidden]), textarea, [contenteditable=true], [contenteditable=""], [role=textbox]').filter(vis).find(e => re.test(lab(e)));
  if (!el) return null;
  document.querySelectorAll('[data-vagas-li]').forEach(x => x.removeAttribute('data-vagas-li'));
  el.setAttribute('data-vagas-li', '1');
  return {label: lab(el), value: (el.value !== undefined && el.tagName !== 'DIV' ? el.value : el.innerText) || ''};
}"""
EDIT = {"headline": ("/edit/intro/", r"^(t[ií]tulo|headline)"), "about": ("/edit/forms/summary/new/", r"^(sobre|about|resumo|summary)")}
# lápis de cada seção no perfil (13/11: abrir /edit/forms/summary/new/ direto devolvia para o perfil; clicar no lápis abre)
EDIT_LINK = {"headline": 'a[href*="/edit/intro"], a[aria-label="Editar introdução"], a[aria-label="Edit intro"]',
             "about": 'a[href*="/edit/forms/summary"], a[aria-label="Editar sobre"], a[aria-label="Edit about"]'}


def _profile_base(page) -> str:
    page.goto("https://www.linkedin.com/in/me/", wait_until="domcontentloaded")
    time.sleep(4)
    return re.sub(r"/+$", "", page.url.split("?")[0])


def _open_edit(page, base: str, item: str) -> None:
    """Abre o formulário de edição pelo lápis da seção (navegação interna do LinkedIn); endereço direto só como reserva."""
    path, want = EDIT[item]
    if not re.search(r"/in/[^/]+/?$", page.url.split("?")[0]):
        page.goto(base + "/", wait_until="domcontentloaded")
        time.sleep(3)
    for _ in range(3):
        loc = page.locator(EDIT_LINK[item])
        try:
            if loc.count():
                loc.first.scroll_into_view_if_needed(timeout=4000)
                loc.first.click(timeout=6000)
                time.sleep(2.5)
                if page.evaluate(FIELD_JS, want) is not None or "/edit/" in page.url:
                    return
        except Exception:
            pass
        page.mouse.wheel(0, 900)
        time.sleep(1.5)
    page.goto(base + path, wait_until="domcontentloaded")


def _field_value(page, want: str, wait_s: float = 12) -> str:
    """O formulário de edição carrega o texto depois do campo aparecer: espera até ter valor (ou o tempo acabar)."""
    end, last = time.time() + wait_s, ""
    while time.time() < end:
        time.sleep(1.0)
        try:
            f = page.evaluate(FIELD_JS, want)
        except Exception:
            f = None
        if f is not None:
            last = f.get("value", "")
            if last.strip():
                return last
    return last


SECTION_RE = re.compile(r"^(Atividade|Experiência|Formação acadêmica|Competências|Principais competências|Licenças e certificados|"
                        r"Projetos|Recomendações|Idiomas|Interesses|Activity|Experience|Education|Skills|Top skills|Projects)$", re.M)


def about_from_page(text: str) -> str:
    """Reserva: o "Sobre" na página do perfil (pode vir cortado em "… mais")."""
    m = re.search(r"^(Sobre|About)\s*$", text or "", re.M)
    if not m:
        return ""
    rest = text[m.end():]
    n = SECTION_RE.search(rest)
    return re.sub(r"\s*…\s*(mais|see more)\s*$", "", (rest[:n.start()] if n else rest[:2600]).strip())


def read_current(page) -> dict:
    base = _profile_base(page)
    out = {"url": base + "/", "at": _now()}
    try:
        out["page_text"] = page.locator("main").inner_text(timeout=10000)[:12000]
    except Exception:
        out["page_text"] = ""
    for key, (path, want) in EDIT.items():
        try:
            _open_edit(page, base, key)
            out[key] = _field_value(page, want)
            page.keyboard.press("Escape")   # fecha o formulário sem salvar (é só leitura)
            time.sleep(1)
            page.goto(base + "/", wait_until="domcontentloaded")
            time.sleep(2)
        except Exception as e:
            out[key + "_error"] = str(e)[:200]
    if not out.get("about"):
        out["about"] = about_from_page(out.get("page_text", ""))
    page.goto(base + "/", wait_until="domcontentloaded")
    _write(CURRENT, out)
    return out


def current() -> dict:
    return _read(CURRENT, {}) or {}


def save_pasted(headline: str, about: str, page_text: str = "") -> dict:
    cur = current()
    cur.update({"headline": headline, "about": about, "at": _now(), "pasted": True})
    if page_text:
        cur["page_text"] = page_text[:12000]
    _write(CURRENT, cur)
    return cur


# ---------------------------------------------------------------- análise
SYSTEM = """Você é especialista em perfis de LinkedIn para pessoas desenvolvedoras no Brasil (recrutadores tech, buscas por
palavra-chave, ATS). Recebe: o perfil REAL da pessoa (único lugar de onde podem sair fatos), o LinkedIn atual dela e as
tecnologias mais pedidas nas vagas que ela busca. Gere um diagnóstico honesto e textos prontos para colar.

REGRAS DURAS
1. Só fatos do perfil: empresas, cargos, datas, tecnologias, projetos e números que estão nele. Nada de métrica inventada.
2. Tecnologia que a pessoa NÃO tem nunca entra nos textos nem nas competências: vai para "estudar".
3. Título (headline) até 220 caracteres, com cargo + 3 a 5 tecnologias mais pedidas QUE ELA TEM + um diferencial real.
4. "Sobre" até 2.000 caracteres, 1ª pessoa, parágrafos curtos, começa pelo que ela faz e para quem; cite projetos reais
   (o primeiro projeto do perfil é o destaque); termine com o que ela busca. Versão em português e em inglês.
5. Experiências: 3 a 5 bullets por experiência, verbo de ação no início, só fatos daquela experiência.
6. Competências: até 50, as mais pedidas primeiro, e 3 de destaque.
7. Português do Brasil, direto, sem clichês ("apaixonado por tecnologia", "proativo").

JSON:
{"nota_geral": 0-100,
 "diagnostico": [{"secao": "Título|Sobre|Experiências|Competências|Projetos|Foto|Capa|URL", "nota": 0-10, "motivo": "curto", "acao": "o que fazer"}],
 "headline": ["opção 1", "opção 2", "opção 3"],
 "about_pt": "texto", "about_en": "texto",
 "experiences": [{"company": "...", "title": "...", "bullets": ["..."]}],
 "skills_top3": ["..."], "skills": ["..."],
 "projects": [{"name": "...", "description": "até 300 caracteres", "link": "se houver no perfil"}],
 "estudar": ["tecnologia pedida que falta, com o porquê"],
 "dicas": ["até 5 dicas extras (URL personalizada, Disponível para, recomendações, publicações)"]}"""


def _profile_payload(profile: dict) -> dict:
    p = profile.get("personal") or {}
    keep = {k: profile.get(k) for k in ("headline", "summary", "skills", "languages", "education", "projects")}
    keep["personal"] = {k: p.get(k) for k in ("first_name", "city", "state", "linkedin", "github", "portfolio")}
    keep["experiences"] = [{k: e.get(k) for k in ("company", "title", "start", "end", "location", "employment_type", "stack", "bullets")}
                           for e in profile.get("experiences") or []]
    keep["certifications"] = [str(c.get("name") if isinstance(c, dict) else c)[:80] for c in (profile.get("certifications") or [])][:10]
    return keep


def build_prompt(profile: dict, cur: dict, mkt: dict) -> str:
    return json.dumps({
        "perfil_real": _profile_payload(profile),
        "linkedin_atual": {"titulo": cur.get("headline", ""), "sobre": cur.get("about", ""),
                           "pagina": (cur.get("page_text") or "")[:6000]},
        "mercado": {"vagas_analisadas": mkt["jobs"], "dias": mkt["days"],
                    "mais_pedidas_que_tem": [f"{x['tech']} ({x['pct']}%)" for x in mkt["have"][:20]],
                    "mais_pedidas_que_falta": [f"{x['tech']} ({x['pct']}%)" for x in mkt["missing"][:12]]},
    }, ensure_ascii=False)


def validate(result: dict, profile: dict) -> dict:
    """Barreira de fatos: competência fora do perfil sai; tecnologia citada que você não tem vira aviso."""
    from .classify_rules import _fold, _tech_counts, canonical_tech
    have = profile_techs(profile)
    warnings: list[str] = []
    skills, removed = [], []
    for s in result.get("skills") or []:
        s = str(s).strip()
        c = canonical_tech(s)
        known_tech = bool(_tech_counts(_fold(s)))
        if known_tech and c not in have and not any(canonical_tech(x) in have for x in _tech_counts(_fold(s))):
            removed.append(s)
        elif s and s not in skills:
            skills.append(s)
    result["skills"] = skills[:50]
    result["skills_top3"] = [s for s in (result.get("skills_top3") or []) if s in skills][:3] or skills[:3]
    if removed:
        warnings.append("Tirei das competências o que não está no seu perfil: " + ", ".join(removed[:10]))
    companies = {(e.get("company") or "").strip().lower() for e in profile.get("experiences") or []}
    result["experiences"] = [e for e in result.get("experiences") or [] if (e.get("company") or "").strip().lower() in companies]
    texts = {"título": " ".join(result.get("headline") or []), "Sobre": (result.get("about_pt") or "") + " " + (result.get("about_en") or ""),
             "experiências": " ".join(" ".join(e.get("bullets") or []) for e in result["experiences"])}
    for where, t in texts.items():
        extra = sorted({x for x in _tech_counts(_fold(t)) if x not in have})
        if extra:
            warnings.append(f"Confira no {where}: cita {', '.join(extra)}, que não está no seu perfil")
    result["headline"] = [h[:220] for h in (result.get("headline") or []) if h][:3]
    result["warnings"] = warnings
    return result


def analyze(engine: str, *, settings, db, profile: dict, llm=None, model: str | None = None) -> dict:
    from . import engines
    cur = current()
    mkt = market(db, profile, settings)
    last_usage = db.conn.execute("SELECT COALESCE(MAX(id),0) FROM llm_usage").fetchone()[0]
    t0 = time.time()
    out = engines.run(engine, system=SYSTEM, user=build_prompt(profile, cur, mkt), settings=settings, llm=llm,
                      purpose="linkedin_profile", max_tokens=7000, model=model)
    res = validate(out["data"], profile)
    cost = 0.0
    try:
        from .budget import call_cost_usd
        # só as chamadas DESTA análise: o bot pode estar respondendo formulários ao mesmo tempo
        for m, pin, pout, cached in db.conn.execute("SELECT model, prompt_tokens, completion_tokens, COALESCE(cached_tokens,0) FROM llm_usage WHERE id > ? AND purpose = 'linkedin_profile'", (last_usage,)):
            cost += call_cost_usd(m, pin, pout, cached)
    except Exception:
        pass
    state = {"at": _now(), "engine": out["engine"], "model": out["model"], "seconds": round(time.time() - t0),
             "cost_brl": round(cost * settings.llm.usd_brl, 4), "market": mkt, "current_at": cur.get("at"),
             "result": res, "applied": (_read(STATE, {}) or {}).get("applied", {})}
    _write(STATE, state)
    return state


def analysis() -> dict:
    return _read(STATE, {}) or {}


def mark_applied(item: str, value: bool = True) -> dict:
    st = analysis()
    st.setdefault("applied", {})[item] = _now() if value else None
    _write(STATE, st)
    return st


# ---------------------------------------------------------------- imagens (Chromium do bot, sem IA)
BANNER_STYLES = {
    "gradiente": "background: radial-gradient(1200px 500px at 85% -20%, #8b5cf6 0%, transparent 60%), radial-gradient(900px 500px at 10% 120%, #06b6d4 0%, transparent 55%), #0f172a; color: #fff;",
    "minimal": "background: #f8fafc; color: #0f172a; border-bottom: 10px solid #4f46e5;",
    "tech": "background-color: #0b1020; background-image: linear-gradient(rgba(99,102,241,.18) 1px, transparent 1px), linear-gradient(90deg, rgba(99,102,241,.18) 1px, transparent 1px); background-size: 44px 44px; color: #e2e8f0;",
    "claro": "background: linear-gradient(120deg, #eef2ff 0%, #fdf4ff 55%, #ecfeff 100%); color: #1e1b4b;",
}


def _first_project(profile: dict) -> str:
    pr = (profile.get("projects") or [{}])[0].get("name") or ""
    return re.split(r"\s+[—-]\s+", pr)[0].strip()


def banner_html(profile: dict, style: str = "gradiente", title: str = "", techs: list[str] | None = None,
                tagline: str = "", bg_image: str = "") -> str:
    from html import escape as e
    p = profile.get("personal") or {}
    name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    title = title or (profile.get("headline") or "").split("|")[0].strip()
    techs = techs if techs is not None else ((profile.get("skills") or {}).get("primary") or [])[:6]
    proj = _first_project(profile)
    tagline = tagline or (f"Criador do {proj}" if proj else "")
    links = " · ".join(x.replace("https://", "").replace("www.", "").rstrip("/") for x in (p.get("github"), p.get("portfolio")) if x)
    css = BANNER_STYLES.get(style, BANNER_STYLES["gradiente"])
    if bg_image:
        css = f"background: linear-gradient(90deg, rgba(2,6,23,.15), rgba(2,6,23,.72)), url('{bg_image}') center/cover; color: #fff;"
    dark = "color: #fff" in css or "#e2e8f0" in css
    chip = "rgba(255,255,255,.12)" if dark else "rgba(79,70,229,.10)"
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face {{ font-family: Inter; font-weight: 400; src: url('{_FONTS}/Inter-400.ttf'); }}
@font-face {{ font-family: Inter; font-weight: 600; src: url('{_FONTS}/Inter-600.ttf'); }}
@font-face {{ font-family: Inter; font-weight: 800; src: url('{_FONTS}/Inter-700.ttf'); }}
* {{ margin: 0; box-sizing: border-box; }}
body {{ width: {BANNER_W}px; height: {BANNER_H}px; font-family: Inter, 'Segoe UI', sans-serif; {css} overflow: hidden; }}
.wrap {{ position: absolute; right: 90px; top: 0; bottom: 0; width: 900px; display: flex; flex-direction: column; justify-content: center; gap: 14px; text-align: right; align-items: flex-end; }}
h1 {{ font-size: 50px; font-weight: 800; letter-spacing: -1px; }}
h2 {{ font-size: 26px; font-weight: 600; opacity: .9; }}
.tag {{ font-size: 21px; font-weight: 600; opacity: .95; }}
.chips {{ display: flex; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }}
.chips span {{ background: {chip}; border-radius: 999px; padding: 7px 16px; font-size: 18px; font-weight: 600; }}
.links {{ font-size: 17px; opacity: .75; }}
</style></head><body><div class="wrap">
<h1>{e(name)}</h1><h2>{e(title)}</h2>{f'<div class="tag">{e(tagline)}</div>' if tagline else ''}
<div class="chips">{''.join(f'<span>{e(t)}</span>' for t in techs[:7])}</div>
{f'<div class="links">{e(links)}</div>' if links else ''}
</div></body></html>"""


def _render_png(html: str, out: Path, w: int, h: int) -> Path:
    from playwright.sync_api import sync_playwright
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".html")
    tmp.write_text(html, encoding="utf-8")
    with sync_playwright() as pw:
        try:
            br = pw.chromium.launch(headless=True)
        except Exception:
            br = pw.chromium.launch(headless=True, channel="chrome")
        try:
            pg = br.new_page(viewport={"width": w, "height": h})
            pg.goto(tmp.resolve().as_uri(), wait_until="load")
            pg.wait_for_timeout(400)
            pg.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": w, "height": h})
        finally:
            br.close()
    tmp.unlink(missing_ok=True)
    return out


def banner(profile: dict, style: str = "gradiente", **kw) -> Path:
    bg = kw.pop("bg_file", "")
    out = IMG / f"capa-{style}-{datetime.now():%Y%m%d-%H%M%S}.png"
    html = banner_html(profile, style, bg_image=(Path(bg).resolve().as_uri() if bg else ""), **kw)
    return _render_png(html, out, BANNER_W, BANNER_H)


def save_upload(data_url: str, prefix: str = "original") -> Path:
    m = re.match(r"data:image/(png|jpe?g|webp);base64,(.+)", data_url or "", re.S)
    if not m:
        raise ValueError("envie uma imagem PNG, JPG ou WEBP")
    raw = base64.b64decode(m.group(2))
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError("imagem maior que 12 MB")
    ext = "jpg" if m.group(1).startswith("jp") else m.group(1)
    out = IMG / f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}.{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    return out


def photo_improve(src: Path, *, x: float = 50, y: float = 40, zoom: float = 1.0, brightness: float = 1.05,
                  contrast: float = 1.06, saturate: float = 1.05, bg: str = "") -> Path:
    """Recorta quadrado (foco x/y em %, zoom), corrige luz/cor. Sem IA: é a sua foto, só melhor enquadrada."""
    src = Path(src)
    zoom = max(1.0, min(3.0, float(zoom)))
    css_bg = bg or "#e5e7eb"
    html = f"""<!doctype html><html><head><style>* {{ margin: 0 }} body {{ width: {PHOTO}px; height: {PHOTO}px; overflow: hidden; background: {css_bg}; }}
img {{ width: 100%; height: 100%; object-fit: cover; object-position: {float(x)}% {float(y)}%; transform: scale({zoom}); transform-origin: {float(x)}% {float(y)}%;
filter: brightness({float(brightness)}) contrast({float(contrast)}) saturate({float(saturate)}); }}</style></head>
<body><img src="{src.resolve().as_uri()}"></body></html>"""
    out = IMG / f"foto-{datetime.now():%Y%m%d-%H%M%S}.png"
    return _render_png(html, out, PHOTO, PHOTO)


# ---------------------------------------------------------------- IA de imagem (opcional, API da OpenAI)
PROMPTS = {
    "fundo": ("Edite esta foto de perfil profissional. Mantenha EXATAMENTE o rosto, os traços, a expressão, o cabelo, a pele e a "
              "roupa da pessoa, sem embelezar nem alterar a identidade. Troque apenas o fundo por um fundo neutro de estúdio "
              "(cinza claro levemente degradê), com luz suave e equilibrada. Enquadramento de busto, centralizado, para LinkedIn."),
    "retrato": ("Crie um retrato profissional de LinkedIn DESTA MESMA pessoa das fotos de referência: mesma identidade, rosto, "
                "formato do rosto, tom de pele, cabelo e traços, sem rejuvenescer nem embelezar. Roupa social casual "
                "(camisa ou blazer sem gravata), fundo de escritório desfocado e claro, luz de estúdio suave, sorriso leve, "
                "enquadramento de busto, olhando para a câmera. Fotografia realista, não ilustração."),
    "capa": ("Fundo abstrato para capa de LinkedIn de uma pessoa desenvolvedora de software: formas geométricas suaves, "
             "linhas de circuito discretas, tons de azul, roxo e ciano, bastante espaço vazio, SEM nenhum texto, letra, "
             "número ou logotipo. Estilo moderno e limpo."),
}


def image_cost_brl(settings, quality: str, n_refs: int = 1) -> float:
    usd = IMAGE_COST_USD.get(quality, IMAGE_COST_USD["medium"]) + IMAGE_INPUT_EST_USD * max(0, n_refs)
    return round(usd * settings.llm.usd_brl, 3)


def image_ai(mode: str, *, settings, llm, refs: list[Path] | None = None, quality: str | None = None) -> Path:
    """mode: fundo (troca só o fundo da sua foto) | retrato (retrato de estúdio a partir das suas fotos) | capa (fundo da capa)."""
    from openai import OpenAI
    quality = quality or settings.linkedin.image_quality
    if llm is not None and llm.budget is not None and not llm.budget.allows("linkedin_image"):
        from .budget import BudgetExceeded
        raise BudgetExceeded(llm.budget.why_blocked("linkedin_image"))
    client = OpenAI()
    model = settings.linkedin.image_model
    if mode == "capa":
        r = client.images.generate(model=model, prompt=PROMPTS["capa"], size="1536x1024", quality=quality)
    else:
        if not refs:
            raise ValueError("envie pelo menos uma foto sua")
        files = [open(p, "rb") for p in refs[:3]]
        try:
            r = client.images.edit(model=model, image=files if len(files) > 1 else files[0], prompt=PROMPTS[mode],
                                   size="1024x1024", quality=quality)
        finally:
            for f in files:
                f.close()
    u = getattr(r, "usage", None)
    if llm is not None and llm.db is not None:
        pin = int(getattr(u, "input_tokens", 0) or 0)
        pout = int(getattr(u, "output_tokens", 0) or 0)
        if not (pin or pout):   # sem usage na resposta: registra pelo preço da tabela (o freio do teto precisa ver)
            pout = int(IMAGE_COST_USD.get(quality, 0.053) / 30 * 1_000_000)
        llm.db.add_llm_usage(model, "linkedin_image", pin, pout, 0)
    out = IMG / f"ia-{mode}-{datetime.now():%Y%m%d-%H%M%S}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(r.data[0].b64_json))
    return out


def images() -> list[dict]:
    if not IMG.exists():
        return []
    out = []
    for p in sorted((x for x in IMG.glob("*.png") if not x.name.startswith("original-")), key=lambda x: x.stat().st_mtime, reverse=True)[:40]:
        kind = "capa" if p.name.startswith(("capa", "ia-capa")) else "foto"
        out.append({"name": p.name, "kind": kind, "at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")})
    for p in sorted(list(IMG.glob("original-*")), key=lambda x: x.stat().st_mtime, reverse=True)[:6]:
        out.append({"name": p.name, "kind": "original", "at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")})
    return out


# ---------------------------------------------------------------- aplicar no LinkedIn (opção B: para antes de Salvar)
def _wait_user_save(page, minutes: float = 10) -> bool:
    """Espera você clicar em Salvar (o formulário/diálogo some ou a URL sai de /edit/)."""
    end = time.time() + minutes * 60
    while time.time() < end:
        time.sleep(2)
        try:
            if "/edit/" not in (page.url or "") and not page.locator("[role=dialog]").count():
                return True
        except Exception:
            return True
    return False


def _editing(page) -> bool:
    try:
        return "/edit/" in (page.url or "") or bool(page.locator("[role=dialog]").count())
    except Exception:
        return False


def _wait_user_edit(page, minutes: float = 10) -> bool:
    """Reserva quando o bot não achou o campo: espera você abrir a edição (lápis) e depois fechar/salvar."""
    end = time.time() + minutes * 60
    while time.time() < end and not _editing(page):
        time.sleep(2)
    return _wait_user_save(page, max(1.0, (end - time.time()) / 60)) if _editing(page) else False


def _saved_on_profile(page, base: str, value: str) -> bool:
    """Confere na página do perfil se o texto novo está lá (fechar sem salvar também some com o formulário)."""
    try:
        page.goto(base + "/", wait_until="domcontentloaded")
        time.sleep(3)
        text = re.sub(r"\s+", " ", page.locator("main").inner_text(timeout=8000))
        probe = re.sub(r"\s+", " ", value).strip()[:60]
        return bool(probe) and probe in text
    except Exception:
        return False


HISTORY = DIR / "history.json"
SAVE_RE = re.compile(r"^\s*(salvar|salvar alterações|salvar foto|aplicar|save|save changes|apply)\s*$", re.I)


def _btn(page, name: str | re.Pattern, exact: bool = True):
    """Último botão VISÍVEL com esse nome (os modais do LinkedIn empilham: o de cima é o último)."""
    loc = page.get_by_role("button", name=name, exact=exact) if isinstance(name, str) else page.get_by_role("button", name=name)
    for i in range(loc.count() - 1, -1, -1):
        try:
            if loc.nth(i).is_visible():
                return loc.nth(i)
        except Exception:
            continue
    return None


def _ai_click(page, llm, goal: str, log) -> bool:
    """Plano B quando o LinkedIn muda o botão de lugar: a IA barata escolhe entre os elementos visíveis (vagas/rescue.py)."""
    if llm is None or not hasattr(llm, "pick_action"):
        return False
    from . import rescue
    cands = [c for c in rescue.candidates(page) if not re.search(r"excluir|delete|remover|sair|logout|descartar", c.get("text", "") + " " + c.get("aria", ""), re.I)]
    if not cands:
        return False
    try:
        out = llm.pick_action(goal=goal, page={"site": "linkedin.com", "titulo": page.title()[:80],
                                               "texto_visivel": (page.locator("body").inner_text(timeout=4000) or "")[:1200]},
                              elements=[{k: c[k] for k in ("i", "tag", "text", "aria", "zone") if c.get(k)} for c in cands])
    except Exception as e:
        log(f"IA não ajudou: {str(e)[:80]}")
        return False
    idx = out.get("index")
    if not isinstance(idx, int):
        return False
    try:
        page.locator(f'[data-vagas-rescue="{idx}"]').first.click(timeout=5000)
        log(f"IA escolheu clicar em '{next((c['text'] or c.get('aria', '') for c in cands if c['i'] == idx), '?')[:40]}'")
        return True
    except Exception:
        return False


def _step(page, llm, log, goal: str, *attempts) -> bool:
    """Tenta cada clique conhecido; se nenhum servir, pede à IA. True se clicou."""
    for fn in attempts:
        try:
            if fn():
                return True
        except Exception:
            continue
    log(f"não achei onde clicar para: {goal}; tentando com IA")
    return _ai_click(page, llm, goal, log)


def _click_text(page, pattern: str):
    def go():
        loc = page.get_by_text(re.compile(pattern, re.I))
        for i in range(loc.count() - 1, -1, -1):
            if loc.nth(i).is_visible():
                loc.nth(i).click(timeout=5000)
                return True
        return False
    return go


def _click_button(page, name):
    def go():
        b = _btn(page, name, exact=isinstance(name, str))
        if b is None:
            return False
        b.click(timeout=5000)
        return True
    return go


def _chooser(page, file: str, opener) -> bool:
    """Clica no botão que abre o seletor de arquivos e entrega a imagem."""
    try:
        with page.expect_file_chooser(timeout=8000) as fc:
            if not opener():
                raise RuntimeError("botão de carregar não encontrado")
        fc.value.set_files(file)
        return True
    except Exception:
        inp = page.locator("input[type=file]")
        if inp.count():
            inp.last.set_input_files(file)
            return True
        return False


def _modal_open(page) -> bool:
    try:
        return "/edit/" in (page.url or "") or _btn(page, SAVE_RE) is not None
    except Exception:
        return False


def _wait_modal_closed(page, minutes: float) -> bool:
    end = time.time() + minutes * 60
    while time.time() < end:
        time.sleep(2)
        if not _modal_open(page):
            return True
    return False


def _top_images(page) -> dict:
    """src da foto e da capa no cartão do topo (para conferir a troca e guardar a antiga)."""
    return page.evaluate(r"""() => { const imgs = [...document.querySelectorAll('main img')].map(i => ({src: i.currentSrc || i.src, r: i.getBoundingClientRect()}))
        .filter(x => x.r.top < 420 && x.r.width > 60 && x.src && !x.src.startsWith('data:'));
      const banner = imgs.filter(x => x.r.width > 500).map(x => x.src)[0] || '';
      const photo = imgs.filter(x => x.r.width <= 260 && Math.abs(x.r.width - x.r.height) < 8).map(x => x.src)[0] || '';
      return {photo, banner}; }""")


def _backup_image(page, url: str, kind: str) -> str:
    if not url:
        return ""
    try:
        r = page.context.request.get(url, timeout=20000)
        if r.ok:
            out = IMG / f"backup-{kind}-{datetime.now():%Y%m%d-%H%M%S}.jpg"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(r.body())
            return out.name
    except Exception:
        pass
    return ""


def _history(entry: dict) -> None:
    h = _read(HISTORY, []) or []
    h.insert(0, dict(entry, at=_now()))
    _write(HISTORY, h[:50])


def history() -> list:
    return _read(HISTORY, []) or []


def _open_photo(page) -> bool:
    page.evaluate(r"""() => { const a = document.querySelector('[componentkey="topcard-logo-image-referencekey"]') || [...document.querySelectorAll('main a')]
        .find(a => { const r = a.getBoundingClientRect(); return r.width > 100 && r.width < 260 && r.top < 420 && a.querySelector('img'); });
      if (a) a.setAttribute('data-vagas-photo', '1'); }""")
    loc = page.locator("[data-vagas-photo='1']")
    if not loc.count():
        return False
    loc.first.click(timeout=5000)
    time.sleep(2.5)
    return True


def apply_item(page, item: str, value: str = "", file: str = "", auto: bool = False, llm=None, log=print) -> dict:
    """Título/Sobre: abre pelo lápis e preenche. Foto/capa: abre o editor e carrega a imagem.
    auto=True: clica nos botões de salvar e confere no perfil. auto=False: para antes de salvar e espera você.
    Antes de trocar, guarda o que estava lá (history.json + backup da imagem) para dar para desfazer."""
    base = _profile_base(page)
    if item in EDIT:
        path, want = EDIT[item]
        _open_edit(page, base, item)
        old = _field_value(page, want, wait_s=8)
        f = page.evaluate(FIELD_JS, want)
        if not f:
            if auto:
                return {"ok": False, "saved": False, "msg": "não achei o campo no formulário do LinkedIn"}
            _wait_user_edit(page)
            saved = _saved_on_profile(page, base, value)
            return {"ok": saved, "saved": saved, "msg": "você colou e salvou" if saved else "não achei o campo; deixei aberto para colar, mas não vi o texto novo"}
        loc = page.locator("[data-vagas-li='1']").first
        loc.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        time.sleep(0.3)
        try:
            loc.fill(value)
        except Exception:
            page.keyboard.insert_text(value)
        time.sleep(1)
        if auto:
            _history({"item": item, "old": old, "new": value})
            if not _step(page, llm, log, "clicar em Salvar no formulário de edição do perfil", _click_button(page, "Salvar"), _click_button(page, SAVE_RE)):
                return {"ok": False, "saved": False, "msg": "preenchi, mas não achei o botão Salvar"}
            time.sleep(4)
        else:
            if not _wait_modal_closed(page, 10):
                return {"ok": True, "saved": False, "msg": "preenchido; não vi o Salvar em 10 min"}
        saved = _saved_on_profile(page, base, value)
        if saved and not auto:
            _history({"item": item, "old": old, "new": value})
        return {"ok": saved, "saved": saved, "msg": ("salvo e conferido no perfil" if saved else
                                                     "o formulário fechou, mas o texto novo não apareceu no perfil")}
    if item in ("photo", "banner"):
        if not file:
            return {"ok": False, "saved": False, "msg": "escolha uma imagem"}
        page.goto(base + "/", wait_until="domcontentloaded")
        time.sleep(3)
        before = _top_images(page)
        backup = _backup_image(page, before.get(item if item == "banner" else "photo"), "capa" if item == "banner" else "foto")
        if item == "photo":
            ok = _step(page, llm, log, "abrir a foto de perfil (círculo com a foto no topo do perfil)", lambda: _open_photo(page))
            ok = ok and _step(page, llm, log, "clicar em Atualizar foto", _click_text(page, r"^\s*Atualizar\s*$"), _click_button(page, re.compile(r"atualizar|alterar foto|change photo", re.I)))
            time.sleep(1.5)
            up = ok and _chooser(page, file, lambda: _click_button(page, re.compile(r"carregar foto|upload photo", re.I))() or _click_text(page, r"^\s*Carregar foto\s*$")())
        else:
            ok = _step(page, llm, log, "abrir o menu da imagem de capa (ícone de câmera no topo)", _click_button(page, re.compile(r"imagem de fundo|imagem de capa|background", re.I)))
            time.sleep(1.5)
            ok = ok and _step(page, llm, log, "escolher Adicionar imagem de capa", _click_text(page, r"adicionar imagem de capa|alterar imagem de capa|editar imagem de capa|add cover|change cover"))
            time.sleep(2)
            up = ok and _chooser(page, file, lambda: _click_button(page, re.compile(r"carregar foto|upload", re.I))() or _click_text(page, r"carregar foto")())
        if not up:
            return {"ok": False, "saved": False, "msg": "não consegui chegar no envio da imagem"}
        time.sleep(5)   # o editor (recortar/zoom) carrega
        if not auto:
            closed = _wait_modal_closed(page, 10)
        else:
            _step(page, llm, log, "clicar em Salvar alterações no editor de imagem", _click_button(page, "Salvar alterações"), _click_button(page, SAVE_RE))
            time.sleep(4)
            for _ in range(3):   # a capa ainda tem o "Salvar" do diálogo de baixo; a foto às vezes também
                b = _btn(page, SAVE_RE)
                if b is None:
                    break
                try:
                    b.click(timeout=5000)
                except Exception:
                    break
                time.sleep(4)
            closed = not _modal_open(page)
        page.goto(base + "/", wait_until="domcontentloaded")
        time.sleep(4)
        after = _top_images(page)
        key = "banner" if item == "banner" else "photo"
        saved = bool(after.get(key)) and after.get(key) != before.get(key)
        if saved:
            _history({"item": item, "old": backup, "new": Path(file).name})
        return {"ok": saved, "saved": saved, "msg": ("imagem trocada e conferida no perfil" if saved else
                                                     "o editor fechou, mas a imagem do perfil não mudou" if closed else "não vi o salvar em 10 min")}
    raise ValueError(f"item desconhecido: {item}")


def apply_batch(page, items: list[dict], auto: bool, llm=None, log=print) -> list[dict]:
    out = []
    for it in items:
        try:
            r = apply_item(page, it["item"], value=it.get("value", ""), file=it.get("file", ""), auto=auto, llm=llm, log=log)
        except Exception as e:
            r = {"ok": False, "saved": False, "msg": f"{type(e).__name__}: {str(e)[:150]}"}
        r["item"] = it["item"]
        if r.get("saved"):
            mark_applied(it["item"])
        log(f"{it['item']}: {r['msg']}")
        set_status("aplicando", f"{it['item']}: {r['msg']}")
        out.append(r)
        time.sleep(3)
    return out
