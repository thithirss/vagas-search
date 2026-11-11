"""Fit Cultural da Gupy respondido com o gabarito que o usuário aprovou (`data/fitcultural/gupy.json`).

O teste (assessments.gupy.io/fitcultural?candidate-id=...) é UM por candidato: 10 questões, cada uma com 4 frases para
ordenar de 1 (mais importante) a 4. Cada frase mede uma dimensão de cultura (pessoas, inovação, resultado, estrutura).
Gravado em 2026-11-11 com o usuário fazendo o teste; a ordem usada aqui é a versão AJUSTADA que ele aprovou ('use').

Regras de segurança:
- só ordena quando reconhece as 4 frases (pelo gabarito ou, se a Gupy trocar o texto, pela dimensão de cada frase);
- confere a ordem na tela antes de clicar em "Próximo passo"; se não bater, para ali e avisa (nada é enviado errado);
- teste que a Gupy já mostra como respondido só é marcado como feito.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from difflib import SequenceMatcher

from rich.console import Console

from .config import DATA_DIR

console = Console()
KEY_PATH = DATA_DIR / "fitcultural" / "gupy.json"
DONE_RE = re.compile(r"teste finalizado|respondeu 100%|j[aá] (respondeu|realizou|concluiu)|teste conclu[ií]do|obrigad[oa] por responder", re.I)
START_RE = re.compile(r"^(iniciar|come[cç]ar|continuar|retomar)( (o )?teste| a responder| agora)?$|^vamos (l[aá]|come[cç]ar)$", re.I)
NEXT_RE = re.compile(r"^(pr[oó]ximo passo|pr[oó]xim[oa]|avan[cç]ar|finalizar|concluir)$", re.I)

# frase nova (a Gupy mudou o texto): dimensão pelas palavras
DIM_WORDS = {
    "cla": r"equipe|grupo|coletiv|uni[aã]o|harmonia|colaborad|colabora[cç]|pessoas|proximidade|reconhe[cç]a|concord",
    "adhocracia": r"mudan[cç]|inova|flex[ií]ve|din[aâ]mic|novidade|solu[cç][oõ]es novas|independente|ajustar seu dia|[aà] medida",
    "mercado": r"lucro|resultado|meta|venda|concorr|competi|produtividade|desempenho|esfor[cç]o individual|clientes",
    "hierarquia": r"estrutura|estabil|est[aá]vel|padroniza|planejad|planejamento|procedimento|formais|riscos|claramente|deixe claro|acompanhe de perto|fixa",
}
# expressões decisivas (peso 3): vencem palavras soltas de outra dimensão na mesma frase
# ("remuneração FIXA e não baseada na produtividade" é estrutura; "COLABORADORES possam ajustar seu dia" é flexibilidade)
DIM_STRONG = {
    "hierarquia": r"remunera[cç][aã]o seja fixa|n[aã]o baseada na produtividade|ambiente de trabalho est[aá]vel|evite correr riscos",
    "adhocracia": r"ajustar seu dia|de forma independente|tomarem decis[oõ]es|forma mais flex[ií]vel|planos flex[ií]veis",
    "mercado": r"de acordo com a minha produtividade|orientada a lucros|superar o desempenho|superem os da concorr",
    "cla": r"esfor[cç]o mais coletivo|equipe seja o aspecto|independentemente dos resultados",
}

# Mapeamento comportamental (assessments.gupy.io/behavioral/<id>): 21 questões, 3 frases cada, traços do Big Five.
# Ordem para vaga de desenvolvimento: organização/entrega > curiosidade/tecnologia > calma > cooperação > sociabilidade,
# e impulsividade/desconforto por último (gravado com o usuário em 11/11; data/comportamental/gupy.json tem as escolhas dele).
BEH_WORDS = {
    "disciplina": r"resultado|hor[aá]rio|[uú]ltima hora|perfeito|determinad|desisto|perco o interesse|al[eé]m das expectativas|"
                  r"pesquis|pr[oó]s e os contras|racional|todas as informa[cç][oõ]es|planej|organiz|prazo|cumpr",
    "abertura": r"novas? ideias|atividades novas|discuss[oõ]es|novas culturas|tecnolog|refletir|art[ií]stic|aprend|curios|criativ",
    "estabilidade": r"tranquil|calm|irritad|para baixo|emo[cç][oõ]es|dar certo|superar dificuldades|satisfeito comigo|"
                    r"confian[cç]a no meu pr[oó]prio|press[aã]o|equil[ií]bri",
    "amabilidade": r"ajudar|no lugar das pessoas|gratid[aã]o|conforto|elogi|suporte|como est[aã]o as pessoas|me dou bem|"
                   r"converso com colegas|incentivo|motivo os outros|qualidade de vida|coopera|confio nas pessoas",
    "extroversao": r"outras pessoas|aproximar|minha presen[cç]a|inicio conversas|fazer amigos|em contato com|falar em p[uú]blico|"
                   r"comunicar minhas ideias|expresso|cr[ií]ticas e sugest[oõ]es|minhas opini[oõ]es",
    "impulso": r"instintos|decis[oõ]es r[aá]pidas|espont[aâ]neas|calor do momento|desconfort[aá]vel|n[aã]o consigo ficar calado|ansios",
}
BEH_STRONG = {
    "disciplina": r"tomo decis[oõ]es de maneira racional|pr[oó]s e os contras",
    "abertura": r"novas tecnologias|produtos tecnol[oó]gicos|usar tecnologias|novas ideias",
    "estabilidade": r"raramente me sinto|controlar minhas emo[cç][oõ]es|lido de forma tranquila",
    "amabilidade": r"gosto de ajudar|gosto de dar suporte|elogio os colegas|ajudar os outros",
    "extroversao": r"falar em p[uú]blico|fazer amigos|inicio conversas",
    "impulso": r"calor do momento|confio nos meus instintos|decis[oõ]es s[aã]o espont|tomo decis[oõ]es r[aá]pidas|alguém observando",
}

TESTS = {
    "fit_cultural": {"label": "fit cultural", "key": DATA_DIR / "fitcultural" / "gupy.json", "url": "/fitcultural",
                     "step": r"fit cultural|mapeamento cultural|cultura organizacional", "words": DIM_WORDS, "strong": DIM_STRONG,
                     "prio": ["cla", "adhocracia", "mercado", "hierarquia"], "distinct": True},
    "comportamental": {"label": "comportamental", "key": DATA_DIR / "comportamental" / "gupy.json", "url": "/behavioral",
                       "step": r"mapeamento comportamental|perfil comportamental|teste comportamental", "words": BEH_WORDS,
                       "strong": BEH_STRONG, "prio": ["disciplina", "abertura", "estabilidade", "amabilidade", "extroversao", "impulso"],
                       "distinct": False},
}

ROWS_JS = r"""
() => {
  const icons = [...document.querySelectorAll('body *')].filter(e => e.childElementCount === 0 && (e.textContent || '').trim() === 'drag_indicator');
  const rows = [];
  for (const ic of icons) {
    let r = ic.parentElement;
    while (r && r !== document.body && !r.querySelector('[role=combobox], select, button[aria-haspopup]')) r = r.parentElement;
    if (!r || r === document.body || rows.includes(r)) continue;
    rows.push(r);
  }
  rows.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  return rows.map((r, i) => {
    r.setAttribute('data-vagas-row', String(i));
    // na Gupy a LINHA inteira é a alça (react-beautiful-dnd: data-rbd-drag-handle-draggable-id, tabindex=0);
    // procurar só dentro dela pegava a lista de posições (ul tabindex=0) e o teclado não movia nada (11/11)
    const HANDLE = '[data-rbd-drag-handle-draggable-id],[data-rfd-drag-handle-draggable-id],[aria-roledescription*=drag i]';
    const handle = (r.matches(HANDLE) ? r : null) || r.closest(HANDLE) || r.querySelector(HANDLE) ||
                   [...r.querySelectorAll('button,[role=button]')].find(b => !b.matches('[role=combobox],[aria-haspopup]')) || r;
    handle.setAttribute('data-vagas-handle', String(i));
    const combo = r.querySelector('[role=combobox], select, button[aria-haspopup]');
    if (combo) combo.setAttribute('data-vagas-combo', String(i));
    const text = (r.innerText || '').split('\n').map(s => s.trim()).filter(s => s && s !== 'drag_indicator' && !/^[1-9]$/.test(s) && !/^(expand_more|arrow_drop_down|keyboard_arrow_down)$/.test(s)).join(' ');
    return text;
  });
}
"""


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", re.sub(r"\s+", " ", s)).strip()


def load_key(kind: str = "fit_cultural") -> dict:
    """Gabarito pessoal gravado (data/<teste>/gupy.json) ou, em clone novo, a prioridade de `settings.fitcultural`."""
    t = TESTS[kind]
    path = KEY_PATH if kind == "fit_cultural" else t["key"]   # KEY_PATH continua trocável nos testes
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        from .config import load_settings
        fc = load_settings().fitcultural
        prio = list(fc.priority if kind == "fit_cultural" else fc.behavioral_priority)
    except Exception:
        prio = []
    if sorted(prio) != sorted(t["prio"]):
        prio = list(t["prio"])
    return {"dimension_priority": prio, "questions": [], "_from": "settings"}


def _dimension(text: str, kind: str = "fit_cultural") -> str:
    t, words, strong = text.lower(), TESTS[kind]["words"], TESTS[kind]["strong"]
    hits = {d: len(re.findall(p, t)) + 3 * len(re.findall(strong[d], t)) for d, p in words.items()}
    best = max(hits, key=hits.get)
    return best if hits[best] else ""


def desired_order(statements: list[str], key: dict | None = None, qnum: int = 0, kind: str = "fit_cultural") -> list[str] | None:
    """Ordem (1 → N) para as frases da tela. Gabarito primeiro; senão pela dimensão de cada frase. None = não reconheci."""
    key = key or load_key(kind)
    n = len(statements)
    if n < 2 or n != len(set(statements)):
        return None
    for q in key.get("questions") or []:
        use = q.get("use") or q.get("answered") or []
        ref = [t for t, _ in use]
        if len(ref) != n:
            continue
        mapping = {}
        for s in statements:
            best = max(ref, key=lambda r: SequenceMatcher(None, _norm(r), _norm(s)).ratio())
            if SequenceMatcher(None, _norm(best), _norm(s)).ratio() < 0.86:
                break
            mapping[best] = s
        if len(mapping) == n and len(set(mapping.values())) == n:
            return [mapping[t] for t in ref]
    dims = {s: _dimension(s, kind) for s in statements}
    if "" in dims.values() or (TESTS[kind]["distinct"] and len(set(dims.values())) != n):
        return None
    prio = list(key.get("dimension_priority") or TESTS[kind]["prio"])
    if any(d not in prio for d in dims.values()):
        return None
    if qnum and qnum % 3 == 2:   # variação moderada: a mesma ordem nas 10 questões parece "perfeita demais"
        prio[0], prio[1] = prio[1], prio[0]
    return sorted(statements, key=lambda s: prio.index(dims[s]))


def _rows(page) -> list[str]:
    try:
        return page.evaluate(ROWS_JS)
    except Exception:
        return []


def _move(page, src: int, dst: int) -> None:
    """Lista de arrastar acessível: foca a alça, Espaço pega, setas movem, Espaço solta (o anúncio 'You have moved the item...')."""
    h = page.locator(f"[data-vagas-handle='{src}']").first
    time.sleep(0.6)   # a animação de soltar a frase anterior precisa terminar, senão a nova "pegada" é ignorada
    h.focus()
    time.sleep(0.4)
    page.keyboard.press("Space")
    time.sleep(0.6)
    for _ in range(abs(src - dst)):
        page.keyboard.press("ArrowUp" if dst < src else "ArrowDown")
        time.sleep(0.5)
    page.keyboard.press("Space")
    time.sleep(1.0)


def _choose_by_combo(page, idx: int, pos: int) -> None:
    """Reserva: a caixinha de posição ao lado de cada frase (lista com 1..4)."""
    c = page.locator(f"[data-vagas-combo='{idx}']").first
    if c.evaluate("e => e.tagName") == "SELECT":
        c.select_option(str(pos)); time.sleep(0.5); return
    c.click(); time.sleep(0.5)
    # cada linha tem a SUA lista 1..4 (com ids repetidos): a opção tem de ser a da mesma linha
    opt = page.locator(f"[data-vagas-row='{idx}'] [role=option]").filter(has_text=re.compile(rf"^\s*{pos}\s*$")).first
    if not opt.count():
        opt = page.locator("[role=option]:visible, li:visible").filter(has_text=re.compile(rf"^\s*{pos}\s*$")).first
    opt.click(); time.sleep(0.6)


def order_question(page, key: dict, qnum: int = 0, kind: str = "fit_cultural") -> tuple[bool, str]:
    rows = _rows(page)
    want = desired_order(rows, key, qnum, kind)
    if not want:
        return False, f"não reconheci as frases: {rows}"
    for _round in range(2):
        for i, target in enumerate(want):
            rows = _rows(page)
            if rows == want:
                break
            j = rows.index(target) if target in rows else -1
            if j < 0:
                return False, f"frase sumiu da tela: {target[:50]}"
            if j != i:
                try:
                    _move(page, j, i)
                except Exception:
                    pass
                if _rows(page)[i:i + 1] != [target]:
                    try:
                        _choose_by_combo(page, _rows(page).index(target), i + 1)
                    except Exception:
                        pass
        if _rows(page) == want:
            return True, ""
    return False, f"a ordem não ficou como o gabarito (tela: {_rows(page)})"


def _unset_rows(page) -> list[int]:
    """Linhas ainda sem número: a Gupy só mostra a posição (e libera "Próximo passo") nas frases que foram mexidas."""
    try:
        _rows(page)
        # a caixinha de posição EXISTE em todas as linhas; nas não mexidas ela está vazia (sem número)
        return page.evaluate("() => [...document.querySelectorAll('[data-vagas-row]')].filter(r => { const c = r.querySelector('[role=combobox], select');"
                             " return !c || !/\\d/.test((c.value || c.innerText || '').trim()); }).map(r => +r.getAttribute('data-vagas-row'))")
    except Exception:
        return []


def _assign_all(page, want: list[str]) -> bool:
    """Toca nas frases sem número sem mudar a ordem: troca com a vizinha e destroca (as duas ganham número)."""
    for _ in range(6):
        unset = _unset_rows(page)
        if not unset:
            return _rows(page) == want
        i = unset[-1]
        try:   # 1º: a caixinha da própria linha, escolhendo a posição em que ela já está
            _choose_by_combo(page, i, i + 1)
        except Exception:
            pass
        if i in _unset_rows(page) and _rows(page) == want:
            j = i - 1 if i > 0 else i + 1   # reserva: troca com a vizinha pelo teclado e destroca
            try:
                _move(page, i, j)
                _rows(page)
                _move(page, j, i)
            except Exception:
                pass
        if _rows(page) != want:
            return False
    return not _unset_rows(page) and _rows(page) == want


def _next_disabled(page) -> bool:
    try:
        return bool(page.evaluate(r"""() => { const b = [...document.querySelectorAll('button')].find(b => /^(pr[oó]ximo passo|finalizar)$/i.test((b.innerText||'').trim()));
            return !!b && (b.disabled || b.getAttribute('aria-disabled') === 'true' || /disabled/i.test(b.className)); }"""))
    except Exception:
        return False


def _click_text(page, rx: re.Pattern) -> bool:
    for b in page.locator("button, [role=button], a").all():
        try:
            if b.is_visible() and rx.search((b.inner_text() or "").strip()):
                b.click(); return True
        except Exception:
            continue
    return False


def _dump(page, tag: str) -> None:
    """Tela que o bot não entendeu: endereço, texto e botões em data/learn/fitcultural/ (para corrigir depois)."""
    try:
        d = DATA_DIR / "learn" / "fitcultural"; d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        btns = page.evaluate("() => [...document.querySelectorAll('button,a,[role=button]')].filter(b => b.offsetParent)"
                             ".map(b => (b.innerText || b.getAttribute('aria-label') || '').trim()).filter(Boolean).slice(0, 40)")
        frames = [f.url for f in page.frames]
        (d / f"{stamp}-{tag}.txt").write_text(f"URL: {page.url}\nFRAMES: {frames}\nBOTÕES: {btns}\n\n{page.inner_text('body')[:6000]}", encoding="utf-8")
        (d / f"{stamp}-{tag}.html").write_text(page.content()[:400000], encoding="utf-8")
    except Exception:
        pass


def fill_test(page, log=console.print, max_questions: int = 30, kind: str = "fit_cultural") -> str:
    """Página do teste já aberta. Devolve 'done' (respondido agora), 'already' (já estava) ou 'failed: motivo'."""
    key = load_key(kind)
    label = TESTS[kind]["label"]
    if not key:
        return "failed: gabarito não encontrado"
    time.sleep(2)
    body = page.inner_text("body")
    if DONE_RE.search(body):
        return "already"
    intro = 0
    for _ in range(max_questions + 10):
        body = page.inner_text("body")
        if DONE_RE.search(body):
            return "done"
        m = re.search(r"QUEST[ÃA]O\s+(\d+)\s+DE\s+(\d+)", body, re.I)
        # antes das questões: telas de introdução ("Encontre seu lugar calmo", 1-4, "Próximo") e "Vamos começar? Iniciar teste"
        if not m and intro < 10:
            # telas de transição entre blocos ("E agora, como vai funcionar? ... foco da empresa") usam "Próximo passo"
            if _click_text(page, START_RE) or (not _rows(page) and _click_text(
                    page, re.compile(r"^pr[oó]ximo( passo)?$|^avan[cç]ar$|^continuar$|^entendi$", re.I))):
                intro += 1
                time.sleep(2.0)
                continue
        if not m:
            _dump(page, "sem-questao")
            return "failed: não achei a questão na tela"
        ok, why = order_question(page, key, int(m.group(1)), kind)
        if not ok:
            _dump(page, f"questao{m.group(1)}")
            return f"failed: questão {m.group(1)}: {why}"
        want = _rows(page)
        if _unset_rows(page) and not _assign_all(page, want):
            ok, why = order_question(page, key, int(m.group(1)), kind)   # a troca bagunçou: reordena e confere de novo
            if not ok or _unset_rows(page):
                _dump(page, f"questao{m.group(1)}-sem-numero")
                return f"failed: questão {m.group(1)}: frases sem número de posição"
        log(f"  {label}: questão {m.group(1)}/{m.group(2)} ordenada")
        if _next_disabled(page):
            # a ordem inicial já era a do gabarito e o botão só libera depois de mexer na lista: mexe e devolve
            want = _rows(page)
            for a, b in ((0, 1), (1, 0)):
                try:
                    _move(page, a, b)
                except Exception:
                    pass
            if _rows(page) != want:
                ok, why = order_question(page, key, int(m.group(1)), kind)
                if not ok:
                    _dump(page, f"questao{m.group(1)}-toque")
                    return f"failed: questão {m.group(1)}: {why}"
        if not _click_text(page, NEXT_RE):
            _dump(page, f"questao{m.group(1)}-sem-avancar")
            return f"failed: questão {m.group(1)}: sem botão de avançar"
        time.sleep(2.0)
    return "failed: número de questões acima do esperado"


# página da candidatura na Gupy: cartões de etapa; etapa concluída tem o cabeçalho com "--checked" (✅ CheckCircleIcon)
STEP_JS = r"""
(stepRe) => {
  const rx = new RegExp(stepRe, 'i');
  const heads = [...document.querySelectorAll('.expandable-card__header-text')].filter(h => rx.test(h.innerText || ''));
  if (!heads.length) return {found: false};
  const done = heads.some(h => h.className.includes('--checked') || !!h.parentElement.querySelector('[data-testid=CheckCircleIcon]'));
  // o botão da etapa ("Iniciar teste"/"Ver") fica no bloco da etapa, FORA do cabeçalho expansível (GIRO.TECH, 11/11)
  const block = heads[0].closest('.timeline__card-content') || heads[0].closest('.expandable-card') || heads[0].closest('[id^=expandable-card-]');
  const btn = block && ([...block.querySelectorAll('button.expandable-card__button, a.expandable-card__button')][0] ||
                        [...block.querySelectorAll('button, a')].find(b => /iniciar|continuar|fazer|responder|^\s*ver\s*$/i.test(b.innerText || '')));
  if (btn) btn.setAttribute('data-vagas-fit', '1');
  return {found: true, done, hasButton: !!btn};
}
"""


def open_test(page, url: str, profile: dict, browser, log=console.print, kind: str = "fit_cultural") -> str:
    """Do link do e-mail até a tela do teste. Devolve 'test' (na tela do teste), 'already' (a página da candidatura já
    mostra a etapa concluída — o teste é um por candidato) ou '' (não consegui chegar)."""
    ok = _open_test(page, url, profile, browser, log, kind)
    if ok in ("already", "gone"):
        return ok
    return "test" if ok else ""


def _open_test(page, url: str, profile: dict, browser, log=console.print, kind: str = "fit_cultural"):
    """Login na Gupy (via LinkedIn) se pedir, e o botão da etapa."""
    from types import SimpleNamespace
    from .appliers.generic import GupyApplier
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    # o link do e-mail passa por rastreador e a página da candidatura só manda para o login DEPOIS de carregar (SPA):
    # espera o endereço assentar antes de decidir se precisa entrar
    def settle(seconds: int) -> bool:
        for _ in range(seconds):
            time.sleep(1)
            if re.search(r"signin|login", page.url or "") or page.locator(".expandable-card__header-text").count():
                return True
        return False

    if not settle(25):   # Gupy lenta: a página da candidatura às vezes fica em branco; recarrega uma vez
        page.reload(wait_until="domcontentloaded", timeout=45000)
        settle(25)
    if re.search(r"signin|login", page.url or ""):
        if not GupyApplier()._login_via_linkedin(SimpleNamespace(profile=profile, browser=browser), page):
            log("  fit cultural: login na Gupy não deu certo")
            return False
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        settle(25)
    if "assessments.gupy.io" in (page.url or ""):
        return True
    from urllib.parse import urlparse
    u = urlparse(page.url or "")
    if u.hostname and u.hostname.endswith("gupy.io") and "/candidates" not in u.path:
        return "gone"   # o link caiu na página de vagas da empresa: a candidatura não existe mais (vaga encerrada / recusa)
    link = page.locator(f"a[href*='assessments.gupy.io{TESTS[kind]['url']}']").first
    if link.count():
        page.goto(link.get_attribute("href"), wait_until="domcontentloaded", timeout=45000)
        time.sleep(2)
        return True
    # etapa "Fit Cultural" na página da candidatura: concluída (✅) ou botão "Ver" que abre o teste (às vezes em outra aba)
    time.sleep(2)
    # aviso da Gupy por cima da página ("Gostaríamos de te mostrar as novidades" / cookies) bloqueia o clique: recusa
    _click_text(page, re.compile(r"^(n[aã]o,? obrigad[oa]|rejeitar todos|agora n[aã]o)$", re.I))
    time.sleep(0.8)
    try:
        st = page.evaluate(STEP_JS, TESTS[kind]["step"])
    except Exception:
        st = {}
    if st.get("done"):
        return "already"
    before = len(page.context.pages)
    clicked = False
    if st.get("hasButton"):
        # pop-up de novidades (Beamer, iframe de terceiros) por cima da página intercepta o clique do mouse (Alterdata).
        # O clique tem de ser de MOUSE: o teste às vezes abre em aba nova, e clique por JS não pode abrir aba (bloqueio de pop-up)
        try:
            page.evaluate("() => document.querySelectorAll('[id^=beamer], iframe[src*=getbeamer]').forEach(e => e.remove())")
        except Exception:
            pass
        btn = page.locator("[data-vagas-fit='1']").first
        try:
            btn.scroll_into_view_if_needed(); btn.click(timeout=8000); clicked = True
        except Exception:
            try:
                btn.evaluate("e => e.click()"); clicked = True
            except Exception:
                clicked = False
    if not clicked:
        _click_text(page, re.compile(r"^(fazer|realizar|iniciar|responder) (o )?teste|^fazer etapa|^iniciar etapa|^acessar teste", re.I))
    if st.get("found") or clicked:
        # espera SEMPRE depois do clique: o teste pode abrir na mesma aba ou numa nova, e às vezes demora (Alterdata,
        # Keep Simple: a tela do teste chegou depois de o bot desistir)
        for _ in range(25):
            time.sleep(1)
            if "assessments.gupy.io" in (page.url or ""):
                return True
            target = next((p for p in page.context.pages if p is not page and "assessments.gupy.io" in (p.url or "")), None)
            if target is not None:
                page.goto(target.url, wait_until="domcontentloaded", timeout=45000); target.close()
                return True
    if "assessments.gupy.io" in (page.url or ""):
        return True
    _dump(page, "nao-abriu")
    return False
