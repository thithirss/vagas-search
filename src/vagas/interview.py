"""Entrevistas: cadastro (por conversa no painel ou pelo convite que chega no e-mail) e o treino de cada uma.

O usuário cola a mensagem do recrutador ou escreve "empresa, vaga, quando" na caixa da tela Entrevistas. Daqui:
- `create_from_text` acha a vaga no banco (candidaturas enviadas primeiro), tira do texto salário, endereço, horário,
  modelo de trabalho e data, e grava em `interviews`;
- `sync_from_responses` cria a entrevista sozinha quando o leitor de e-mail marca um convite (kind entrevista/whatsapp);
- `build_kit` monta o treino SEM IA: cartões "Sobre você" a partir do perfil (`config/profile.yaml`), baralhos técnicos
  conforme a stack da vaga, quiz, simulado com checklist e roteiro (pitch, pretensão do nível da vaga, perguntas).
  A preparação por IA (`prep`) é opcional, por botão, e usa só fatos do perfil (`nextsteps.make_prep`).
Nada aqui inventa experiência: o que não está no perfil vira frase honesta ("ainda não usei X em produção").
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timedelta

# ---------- conteúdo técnico (fixo) ----------
DECK_PY_BRIDGE = [
    ("Rotas do Laravel → em Python?", "Flask / FastAPI usam decoradores:\n<pre>@app.get(\"/clientes\")\ndef listar():\n    return [{\"id\": 1, \"nome\": \"Ana\"}]</pre>FastAPI devolve JSON automático e gera a documentação em /docs."),
    ("Composer → em Python?", "<b>pip</b> instala pacotes; o <code>requirements.txt</code> lista as dependências (como o composer.json).\nAmbiente isolado: <code>python -m venv .venv</code>."),
    ("Eloquent (ORM) → em Python?", "<b>SQLAlchemy</b> (Flask/FastAPI) ou o <b>ORM do Django</b>.\nMigrations: Alembic ou <code>manage.py migrate</code>, como o <code>php artisan migrate</code>."),
    ("PHPUnit → em Python?", "<b>pytest</b>: funções <code>test_...</code> com <code>assert</code>.\n<pre>def test_soma():\n    assert soma(2, 3) == 5</pre>"),
    ("Http::get() / axios → em Python?", "<pre>import requests\nr = requests.get(url, timeout=10)\ndados = r.json()</pre>"),
    ("Laravel é completo. E em Python?", "<b>Django</b>: completo (ORM, admin, autenticação), como o Laravel.\n<b>Flask</b>: leve, você escolhe as peças.\n<b>FastAPI</b>: moderno, ótimo para APIs, usa tipos."),
]
DECK_PY = [
    ("Lista × tupla × dicionário × set", "<b>lista</b> <code>[1, 2]</code>: ordenada, muda.\n<b>tupla</b> <code>(1, 2)</code>: não muda.\n<b>dict</b> <code>{\"a\": 1}</code>: chave → valor.\n<b>set</b> <code>{1, 2}</code>: sem repetição."),
    ("O que é list comprehension?", "<pre>pares = [n * 2 for n in nums if n > 0]</pre>Igual a um for com if e append, numa linha."),
    ("Como tratar erro em Python?", "<pre>try:\n    r = requests.get(url, timeout=5)\n    r.raise_for_status()\nexcept requests.RequestException as e:\n    print(\"falhou:\", e)</pre>"),
    ("O que são *args e **kwargs?", "<code>*args</code>: argumentos posicionais extras (tupla).\n<code>**kwargs</code>: nomeados extras (dict)."),
    ("O que é um decorador?", "Função que embrulha outra para acrescentar comportamento (log, autenticação, rota). O <code>@app.get</code> é um decorador."),
    ("Ler uma planilha e somar por loja", "<pre>import pandas as pd\ndf = pd.read_excel(\"vendas.xlsx\")\ndf.groupby(\"loja\")[\"valor\"].sum()</pre>"),
    ("<code>==</code> × <code>is</code>", "<code>==</code> compara valor; <code>is</code> compara se é o MESMO objeto. Use <code>if x is None:</code>."),
    ("Ler e escrever arquivo com segurança", "<pre>with open(\"dados.txt\", encoding=\"utf-8\") as f:\n    linhas = f.readlines()</pre><code>with</code> fecha o arquivo mesmo com erro."),
]
DECK_JS = [
    ("<code>==</code> × <code>===</code>", "<code>===</code> compara valor E tipo; <code>==</code> converte antes. Use <code>===</code>."),
    ("<code>let</code> × <code>const</code> × <code>var</code>", "<code>const</code>: não reatribui · <code>let</code>: reatribui, escopo de bloco · <code>var</code>: escopo de função (evite)."),
    ("Promise e async/await", "<pre>const r = await fetch(url)\nconst dados = await r.json()</pre>Erro com try/catch em volta do await."),
    ("map, filter, reduce", "<pre>nums.filter(n => n > 0).map(n => n * 2)\nnums.reduce((s, n) => s + n, 0)</pre>"),
    ("Estado e props (React/Vue)", "Props vêm do pai e são só leitura; estado é do componente e, ao mudar, re-renderiza."),
]
DECK_GENERAL = [
    ("Códigos HTTP mais cobrados", "<b>200</b> ok · <b>201</b> criado · <b>204</b> sem conteúdo\n<b>400</b> requisição errada · <b>401</b> não autenticado · <b>403</b> sem permissão · <b>404</b> não encontrado · <b>422</b> validação\n<b>500</b> erro no servidor."),
    ("Métodos REST", "<b>GET</b> buscar · <b>POST</b> criar · <b>PUT</b> substituir · <b>PATCH</b> alterar parte · <b>DELETE</b> remover."),
    ("INNER JOIN × LEFT JOIN", "<b>INNER</b>: só o que casa nas duas tabelas.\n<b>LEFT</b>: tudo da esquerda, com NULL onde não casa (ex.: clientes que nunca compraram)."),
    ("Para que serve um índice no banco?", "Acelerar buscas e JOINs numa coluna. Custa um pouco nas escritas e ocupa espaço."),
    ("O que é CI/CD?", "<b>CI</b>: a cada push roda build e testes. <b>CD</b>: publica automaticamente o que passou."),
    ("Fluxo de Git", "Branch por tarefa → commits pequenos → pull request → revisão → merge. Saber resolver conflito."),
    ("Como integrar dois sistemas por API?", "1. Ler a documentação (autenticação, limites). 2. Cliente HTTP com timeout e nova tentativa. 3. Converter para o nosso formato. 4. Gravar sem duplicar (chave única). 5. Agendar e registrar log/erros. 6. Testar em homologação."),
]
QUIZ_PY = [
    {"q": "O que este código imprime?", "code": "nums = [1, -2, 3]\nprint([n * 2 for n in nums if n > 0])", "o": ["[2, -4, 6]", "[2, 6]", "[1, 3]", "Erro"], "r": 1, "e": "Filtra os positivos (1 e 3) e dobra."},
    {"q": "Qual estrutura NÃO permite alterar os itens?", "o": ["lista", "dicionário", "tupla", "set"], "r": 2, "e": "Tupla é imutável."},
    {"q": "Como instalar as dependências do requirements.txt?", "o": ["pip install requirements.txt", "pip install -r requirements.txt", "python requirements.txt", "composer install"], "r": 1, "e": "O -r lê a lista do arquivo."},
    {"q": "Jeito certo de testar se x é None?", "o": ["if x == None:", "if x is None:", "if x === None:", "if None(x):"], "r": 1, "e": "None é único: compare identidade com is."},
    {"q": "O que imprime?", "code": "d = {\"a\": 1}\nprint(d.get(\"b\", 0))", "o": ["KeyError", "None", "0", "b"], "r": 2, "e": ".get devolve o padrão quando a chave não existe."},
    {"q": "Em FastAPI, o que @app.get(\"/clientes\") faz?", "o": ["Busca no banco", "Registra a função como rota GET /clientes", "Importa um módulo", "Cria tabela"], "r": 1, "e": "Decorador de rota, como Route::get no Laravel."},
    {"q": "Biblioteca mais usada para planilhas?", "o": ["requests", "pandas", "pytest", "flask"], "r": 1, "e": "pandas: read_excel, groupby."},
    {"q": "O que imprime?", "code": "x = [1, 2]\ny = x\ny.append(3)\nprint(x)", "o": ["[1, 2]", "[1, 2, 3]", "[3]", "Erro"], "r": 1, "e": "x e y são a MESMA lista; copie com x.copy()."},
    {"q": "O que imprime?", "code": "def f(*args):\n    return len(args)\nprint(f(1, 2, 3))", "o": ["1", "3", "(1, 2, 3)", "Erro"], "r": 1, "e": "*args junta os argumentos numa tupla."},
    {"q": "Como se escreve um teste no pytest?", "o": ["class Test extends TestCase", "função test_algo() com assert", "@test no arquivo", "it('faz algo')"], "r": 1, "e": "Funções com prefixo test_ e assert."},
]
QUIZ_GENERAL = [
    {"q": "Código HTTP para “criado com sucesso”?", "o": ["200", "201", "204", "302"], "r": 1, "e": "201 Created, depois de um POST."},
    {"q": "Qual JOIN mostra também os clientes que nunca compraram?", "o": ["INNER JOIN", "clientes LEFT JOIN vendas", "CROSS JOIN", "nenhum"], "r": 1, "e": "LEFT mantém todos da esquerda."},
    {"q": "Usuário não logado acessa rota protegida. Código mais adequado?", "o": ["401", "404", "500", "201"], "r": 0, "e": "401 = não autenticado; 403 = autenticado sem permissão."},
    {"q": "Qual método HTTP para alterar só um campo?", "o": ["GET", "PUT", "PATCH", "DELETE"], "r": 2, "e": "PATCH altera parte; PUT substitui o recurso."},
    {"q": "O que o CI faz a cada push?", "o": ["Publica direto em produção", "Roda build e testes", "Apaga a branch", "Nada"], "r": 1, "e": "Integração contínua: build + testes automáticos."},
]
QUIZ_JS = [
    {"q": "O que imprime?", "code": "console.log(0 == '0', 0 === '0')", "o": ["true true", "true false", "false true", "false false"], "r": 1, "e": "== converte o tipo; === não."},
    {"q": "O que imprime?", "code": "console.log([1, 2, 3].map(n => n * 2))", "o": ["[1,2,3]", "[2,4,6]", "6", "undefined"], "r": 1, "e": "map devolve um array novo."},
]

STOP = {"de", "da", "do", "das", "dos", "e", "a", "o", "para", "com", "em", "na", "no", "vaga", "oportunidade", "aqui", "sim", "nao",
        "ola", "bom", "dia", "tudo", "bem", "meu", "nome", "trabalho", "rh", "grupo", "recebemos", "seu", "sua", "curriculo"}


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- entender o texto do usuário / recrutador ----------
def parse_message(text: str) -> dict:
    t = text or ""
    f = _fold(t)
    out: dict = {}
    m = re.search(r"sal[aá]rio\*?\s*[:\-]?\s*\*?\s*(R\$\s?[\d.]+(?:,\d{2})?)", t, re.I) or re.search(r"(R\$\s?[\d.]{4,}(?:,\d{2})?)", t)
    if m:
        out["salary"] = m.group(1).replace("  ", " ")
    m = re.search(r"endere[cç]o\*?\s*[:\-]?\s*\*?\s*:?\s*([^\n]{6,120})", t, re.I)
    if m:
        out["location"] = m.group(1).strip(" *:")
    m = re.search(r"hor[aá]rio\*?\s*[:\-]?\s*\*?\s*:?\s*([^\n]{4,80})", t, re.I)
    if m:
        out["hours"] = m.group(1).strip(" *:")
    if re.search(r"\bh[ií]brid", f):
        out["mode"] = "híbrido"
    elif re.search(r"\bremot|home ?office", f):
        out["mode"] = "remoto"
    elif out.get("location") or re.search(r"presencial", f):
        out["mode"] = "presencial"
    # quando: "hoje 14h", "amanhã às 10:30", "dia 20/11 15h"
    when = ""
    d = None
    if re.search(r"\bhoje\b", f):
        d = date.today()
    elif re.search(r"\bamanha\b", f):
        d = date.today() + timedelta(days=1)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)
    if m and not d:
        try:
            y = int(m.group(3)) + (2000 if m.group(3) and len(m.group(3)) == 2 else 0) if m.group(3) else date.today().year
            d = date(y, int(m.group(2)), int(m.group(1)))
        except ValueError:
            d = None
    # a hora vale só perto da data ("hoje às 14h"); "de 08h as 18h" é o horário de trabalho, não a entrevista
    hm = re.search(r"(?:hoje|amanha|\d{1,2}/\d{1,2}(?:/\d{2,4})?)[^\n]{0,25}?\b(\d{1,2})\s*(?:h|:)\s*(\d{2})?", f)
    if d:
        when = d.isoformat()
        if hm:
            when += f" {int(hm.group(1)):02d}:{hm.group(2) or '00'}"
    out["when"] = when
    # empresa / vaga ditas no texto
    m = re.search(r"(?:oportunidade|vaga|cargo|posi[cç][aã]o)\s+(?:de|para)\s+\*?([A-Za-zÀ-ú0-9 #+./|()-]{4,70})", t, re.I)
    if m:
        out["title"] = re.split(r"[.\n*]|\s{2,}", m.group(1))[0].strip()
    m = re.search(r"(?:RH|recrutamento|time|equipe)\s+d[oa]\s+([A-ZÀ-Ú][\wÀ-ú&.\- ]{1,40})", t)
    if m:
        out["company"] = re.split(r"[.,!\n]|\s(?:e|que|para)\s", m.group(1))[0].strip(" .")
    return out


def find_job(db, text: str) -> dict | None:
    """Vaga do banco citada no texto. Candidaturas enviadas pesam mais (é delas que vem o convite)."""
    from .inbox import _company_tokens, match_application
    ft = _fold(text)
    job_id, _ = match_application(db, "", text[:300], text)
    if job_id:
        r = db.conn.execute("SELECT id, company, title FROM jobs WHERE id=?", (job_id,)).fetchone()
        # só vale se a EMPRESA aparece no texto: "Stone, Desenvolvedor Backend" casava com outra empresa pelo título
        if r and any(re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", ft) for t in _company_tokens(r["company"] or "")):
            return dict(r)
    words = [w for w in re.findall(r"[a-z0-9#+.]{3,}", ft) if w not in STOP]
    if not words:
        return None
    best, score = None, 0
    for r in db.conn.execute("SELECT j.id, j.company, j.title, (SELECT 1 FROM applications a WHERE a.job_id=j.id) applied FROM jobs j "
                             "ORDER BY applied DESC, j.discovered_at DESC LIMIT 4000").fetchall():
        comp, title = _fold(r["company"] or ""), _fold(r["title"] or "")
        ctoks = [w for w in re.findall(r"[a-z0-9]{3,}", comp) if w not in STOP]
        if not ctoks or not all(w in words for w in ctoks[:2]):
            continue
        hits = sum(1 for w in re.findall(r"[a-z0-9#+.]{3,}", title) if w in words and w not in STOP and w not in ("desenvolvedor", "developer", "analista"))
        if not hits:   # empresa certa com cargo diferente (Stone Backend × Stone Mobile Sênior) não serve
            continue
        s = 3 + hits + (2 if r["applied"] else 0)
        if s > score:
            best, score = dict(r), s
    return best


def create_from_text(db, text: str) -> int:
    """Caixa de conversa da tela Entrevistas: mensagem do recrutador ou "empresa, vaga, quando"."""
    info = parse_message(text)
    job = find_job(db, text)
    company = (job or {}).get("company") or info.get("company") or ""
    title = (job or {}).get("title") or info.get("title") or ""
    if not company and not title:
        # "Acme, Desenvolvedor Python, amanhã 14h": primeira parte = empresa, segunda = vaga
        parts = [p.strip() for p in re.split(r"[,;\n]", text) if p.strip()]
        company = parts[0][:60] if parts else "Entrevista"
        title = parts[1][:80] if len(parts) > 1 else ""
    desc = text.strip()
    if job:
        jd = db.conn.execute("SELECT description FROM jobs WHERE id=?", (job["id"],)).fetchone()
        if jd and (jd[0] or "").strip() and jd[0].strip() not in desc:
            desc += "\n\n— Descrição da vaga —\n" + jd[0].strip()
    cur = db.conn.execute(
        "INSERT INTO interviews (job_id, company, title, scheduled_at, mode, salary, location, description, notes, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?, 'aberta', ?)",
        ((job or {}).get("id"), company, title, info.get("when", ""), info.get("mode", ""), info.get("salary", ""),
         info.get("location", ""), desc[:12000], info.get("hours", ""), _now()))
    db.conn.commit()
    return cur.lastrowid


def sync_from_responses(db) -> int:
    """Convite de entrevista que chegou no e-mail vira entrevista sozinho (uma vez por e-mail)."""
    n = 0
    for r in db.conn.execute(
            "SELECT r.id, r.company, r.job_id, r.deadline, r.kind, r.snippet, r.subject, j.title, j.description FROM email_responses r "
            "LEFT JOIN jobs j ON j.id=r.job_id WHERE r.kind IN ('entrevista','whatsapp') "
            "AND NOT EXISTS (SELECT 1 FROM interviews i WHERE i.response_id=r.id)").fetchall():
        db.conn.execute(
            "INSERT OR IGNORE INTO interviews (job_id, response_id, company, title, scheduled_at, mode, description, status, created_at) "
            "VALUES (?,?,?,?,?,?,?, 'aberta', ?)",
            (r["job_id"], r["id"], r["company"] or "", r["title"] or r["subject"] or "", r["deadline"] or "",
             "WhatsApp" if r["kind"] == "whatsapp" else "", ((r["snippet"] or "") + "\n\n" + (r["description"] or "")).strip()[:12000], _now()))
        n += 1
    db.conn.commit()
    return n


# ---------- o treino ----------
def _skill_level(profile: dict, tech: str) -> str:
    sk = profile.get("skills") or {}
    t = _fold(tech).replace(".js", "").replace(" ", "")
    for level in ("primary", "secondary", "also_experienced", "familiar"):
        if any(_fold(s).replace(".js", "").replace(" ", "") == t for s in sk.get(level) or []):
            return level
    return ""


def _job_stack(db, iv: dict) -> list[str]:
    stack: list[str] = []
    if iv.get("job_id"):
        r = db.conn.execute("SELECT classification FROM jobs WHERE id=?", (iv["job_id"],)).fetchone()
        try:
            stack = list((json.loads(r[0] or "{}") or {}).get("stack") or []) if r else []
        except Exception:
            stack = []
    text = _fold(f"{iv.get('title')} {iv.get('description')}")
    for tech in ("python", "php", "laravel", "java", "javascript", "typescript", "react", "vue", "node", "c#", ".net", "go", "flutter", "sql"):
        if re.search(rf"(?<![a-z]){re.escape(tech)}(?![a-z])", text) and tech not in [_fold(s) for s in stack]:
            stack.append(tech)
    return [TECH_NAME.get(_fold(s), s) for s in stack]


TECH_NAME = {"python": "Python", "php": "PHP", "laravel": "Laravel", "java": "Java", "javascript": "JavaScript", "typescript": "TypeScript",
             "react": "React", "vue": "Vue.js", "node": "Node.js", "c#": "C#", ".net": ".NET", "go": "Go", "flutter": "Flutter", "sql": "SQL"}


def _seniority(db, iv: dict) -> str:
    from types import SimpleNamespace
    from .qa import job_seniority
    cls = {}
    if iv.get("job_id"):
        r = db.conn.execute("SELECT classification FROM jobs WHERE id=?", (iv["job_id"],)).fetchone()
        try:
            cls = json.loads(r[0] or "{}") or {} if r else {}
        except Exception:
            cls = {}
    try:
        # job_seniority assume "senior" quando nada diz o nível (bom para filtrar candidatura); aqui nível não dito = vazio
        lvl = job_seniority(SimpleNamespace(title=iv.get("title") or "",
                                            classification={"seniority": cls.get("seniority") or "unspecified"}))
    except Exception:
        lvl = cls.get("seniority") or ""
    return lvl if lvl in ("junior", "mid", "senior") else ""


def _pitch(p: dict) -> str:
    exps = p.get("experiences") or []
    cur = next((e for e in exps if not e.get("end")), exps[0] if exps else {})
    same = [e for e in exps if e.get("company") == cur.get("company")]
    first = min(same, key=lambda e: str(e.get("start") or "9999")) if same else cur
    sk = (p.get("skills") or {}).get("primary") or []
    parts = []
    if cur:
        since = f" desde {str(first.get('start'))[:4]}" if first.get("start") else ""
        promo = f" (entrei como {first.get('title')} e fui efetivado)" if first is not cur and first.get("title") else ""
        parts.append(f"Sou {cur.get('title')} na {cur.get('company')}{since}{promo}.")
    if sk:
        parts.append(f"Trabalho principalmente com {', '.join(sk[:5])}.")
    bullets = cur.get("bullets") or []
    lead = next((b for b in bullets if re.search(r"lider|orient|mentor", _fold(b))), "")
    if lead:
        parts.append(lead.rstrip(".") + ".")
    prev = [e for e in exps if e.get("company") != cur.get("company")][:2]
    if prev:
        parts.append("Antes, " + "; ".join(f"{e.get('title')} na {e.get('company')}" for e in prev) + ".")
    before = (p.get("facts") or {}).get("leadership_before_it")
    if before:
        parts.append(f"Antes da TI: {str(before).rstrip('.')}.")
    edu = (p.get("education") or [{}])[0]
    if edu.get("degree"):
        parts.append(f"{edu.get('degree')} ({edu.get('status') or ''}).".replace(" ()", ""))
    return " ".join(parts)


def _salary_line(p: dict, level: str, iv: dict) -> str:
    f = p.get("facts") or {}
    lvl = level or (f.get("preferred_seniority") or "junior")
    key = {"junior": "junior", "mid": "mid", "senior": "senior"}.get(lvl, "junior")
    keys = [key] + [k for k in ("junior", "mid", "senior") if k != key]   # falta a do nível? usa a que existir
    clt = next((f.get(f"salary_expectation_{k}_clt") for k in keys if f.get(f"salary_expectation_{k}_clt")), f.get("salary_expectation_clt") or "")
    pj = next((f.get(f"salary_expectation_{k}_pj") for k in keys if f.get(f"salary_expectation_{k}_pj")), f.get("salary_expectation_pj") or "")
    if not clt and not pj:
        s = "Diga um número que você aceite e deixe espaço para negociar (configure a pretensão no vagas setup)."
        return s + (f"\nA vaga informa {iv['salary']}." if iv.get("salary") else "")
    s = f"“Minha pretensão para CLT é em torno de {clt}" + (f" (PJ, {pj})" if pj else "") + ". Considerando o pacote de benefícios, estou aberto a conversar.”"
    if iv.get("salary"):
        s += f"\nA vaga informa {iv['salary']}: não feche número na primeira resposta."
    return s


def _cards_about(p: dict, iv: dict, stack: list[str], level: str) -> list[dict]:
    exps = p.get("experiences") or []
    cur = next((e for e in exps if not e.get("end")), exps[0] if exps else {})
    allb = [b for e in exps for b in (e.get("bullets") or [])]
    hard = next((b for b in allb if re.search(r"refator|performance|otimiz|test|legad|bug|migr", _fold(b))), allb[0] if allb else "")
    lead_fact = (p.get("facts") or {}).get("led_teams") or ""
    company, title = iv.get("company") or "a empresa", iv.get("title") or "a vaga"
    cards = [
        {"q": "Se apresente em 1 minuto.", "a": _pitch(p), "checks": ["Cargo e empresa atual", "Tecnologias principais", "Liderança ou resultado", "Trajetória", "O que busca agora"]},
        {"q": "Conte um problema técnico difícil que você resolveu.", "a": f"Use um fato seu: <b>{hard}</b>\nConte em ordem: situação → o que VOCÊ fez → resultado.", "checks": ["Situação", "O que EU fiz", "Resultado concreto"]},
        {"q": "Por que quer sair do emprego atual?", "a": "Crescer na área da vaga, aprender um domínio novo e assumir mais responsabilidade.\nNunca fale mal da empresa atual.", "checks": ["Falei do que busco", "Não falei mal de ninguém"]},
        {"q": f"Por que quer trabalhar na {company}?", "a": f"Ligue o que a {company} faz ao que você quer aprender. Cite algo concreto da empresa ou da vaga ({title}).", "checks": ["Citei algo da empresa", "Liguei ao meu objetivo"]},
        {"q": "Onde você se vê em 3 anos?", "a": "Um nível acima do atual, forte na stack da vaga, dono de entregas de ponta a ponta e ajudando quem está chegando.", "checks": ["Objetivo realista", "Ligado à vaga"]},
        {"q": "Como você trabalha em equipe? E em conflito?", "a": "Rituais do time (dailies, revisão de código). Em divergência técnica: discuto com dados, escuto a outra opção e sigo a decisão do time.", "checks": ["Exemplo real", "Mostrei escuta"]},
        {"q": "Qual sua pretensão salarial?", "a": _salary_line(p, level, iv), "checks": ["Falei um número", "Deixei espaço para negociar"]},
        {"q": "Tem alguma pergunta para nós?", "a": "SEMPRE tenha: sistemas e stack do time, quem seria seu líder técnico, modelo de trabalho, crescimento depois da efetivação.", "checks": ["Fiz pelo menos 2 perguntas"]},
    ]
    if lead_fact or any(re.search(r"lider|orient", _fold(b)) for b in allb):
        cards.insert(2, {"q": "Fale de quando você liderou algo.", "a": lead_fact or next(b for b in allb if re.search(r"lider|orient", _fold(b))),
                         "checks": ["Contexto", "Minha responsabilidade", "Resultado"]})
    # experiência com cada tecnologia da vaga: forte se está no perfil; honesta se não está
    weak = []
    generic = {"apis rest", "api rest", "rest", "ci/cd", "git", "sql", "testes automatizados", "microsservicos", "scrum", "agile"}
    for tech in [t for t in stack if _fold(t) not in generic][:4]:
        lv = _skill_level(p, tech)
        if lv == "primary":
            a = f"Uso {tech} no dia a dia em {cur.get('company') or 'meu trabalho atual'}. Dê um exemplo de entrega e um detalhe técnico."
        elif lv in ("secondary", "also_experienced"):
            a = f"Tenho experiência com {tech} (não é minha stack principal). Conte onde usou e o que fez."
        else:
            weak.append(tech)
            main = ", ".join(((p.get("skills") or {}).get("primary") or [])[:3])
            a = (f"Seja honesto: {tech} ainda é {'básico' if lv == 'familiar' else 'novo'} para mim; minha stack principal é {main}.\n"
                 "O que transfere: APIs REST, SQL, integrações, Git e testes. Mostre que aprende rápido com um exemplo real da sua trajetória.")
        cards.append({"q": f"Qual sua experiência com {tech}?", "a": a, "checks": ["Fui honesto sobre o nível", "Dei exemplo concreto"]})
    if weak:
        cards.append({"q": "Qual seu ponto fraco?", "a": f"{weak[0]} ainda não é minha especialidade. O que estou fazendo: estudando e ligando cada conceito ao que já domino.\nPonto fraco real + o que você faz sobre ele.",
                      "checks": ["Ponto fraco real", "O que faço para melhorar"]})
    return cards


def build_kit(db, profile: dict, iv: dict) -> dict:
    stack = _job_stack(db, iv)
    level = _seniority(db, iv)
    sf = [_fold(s) for s in stack]
    about = _cards_about(profile or {}, iv, stack, level)
    cards = [{"d": "Sobre você", "q": c["q"], "a": c["a"]} for c in about]
    quiz = list(QUIZ_GENERAL)
    if "python" in sf:
        prim = [_fold(s) for s in ((profile or {}).get("skills") or {}).get("primary") or []]
        if any(x in prim for x in ("laravel", "php")):
            cards += [{"d": "Laravel ↔ Python", "q": q, "a": a} for q, a in DECK_PY_BRIDGE]
        cards += [{"d": "Python", "q": q, "a": a} for q, a in DECK_PY]
        quiz = QUIZ_PY + quiz
    if any(x in sf for x in ("javascript", "typescript", "react", "vue", "node", "angular")):
        cards += [{"d": "JavaScript", "q": q, "a": a} for q, a in DECK_JS]
        quiz += QUIZ_JS
    cards += [{"d": "Técnico geral", "q": q, "a": a} for q, a in DECK_GENERAL]
    ask = ["Que sistemas o time desenvolve ou integra hoje?", "Como é o time e quem seria meu líder técnico?",
           "Qual a stack e como é o processo (revisão de código, testes, deploy)?", "Como funciona crescimento e revisão salarial depois da efetivação?"]
    if (iv.get("mode") or "") == "presencial":
        ask.insert(3, "Existe possibilidade de modelo híbrido?")
    weak = [t for t in stack if _skill_level(profile or {}, t) not in ("primary", "secondary", "also_experienced")]
    return {
        "stack": stack, "level": level, "cards": cards,
        "sim": [{"q": c["q"], "a": c["a"], "checks": c.get("checks") or []} for c in about],
        "quiz": quiz,
        "plan": {"pitch": _pitch(profile or {}), "salary": _salary_line(profile or {}, level, iv), "ask": ask,
                 "honest": (f"Sobre {', '.join(weak)}: seja honesto sobre o nível e ligue ao que você já domina." if weak else ""),
                 "checklist": ["Ler o pitch em voz alta 3 vezes", "Flashcards de “Sobre você” até marcar todos como Sabia",
                               "3 rodadas do Simulado com cronômetro", "Quiz com pelo menos 70% de acerto", "Separar 3 perguntas para fazer",
                               "Testar câmera, microfone e internet 15 min antes"]},
    }


def list_interviews(db) -> list[dict]:
    sync_from_responses(db)
    rows = db.conn.execute("SELECT * FROM interviews ORDER BY CASE status WHEN 'aberta' THEN 0 WHEN 'feita' THEN 1 ELSE 2 END, "
                           "COALESCE(NULLIF(scheduled_at,''),'9999'), id DESC").fetchall()
    return [dict(r) for r in rows]


def get(db, iid: int, profile: dict) -> dict | None:
    r = db.conn.execute("SELECT * FROM interviews WHERE id=?", (iid,)).fetchone()
    if not r:
        return None
    iv = dict(r)
    if iv.get("job_id"):
        a = db.conn.execute("SELECT applied_at, answers, ats FROM applications WHERE job_id=?", (iv["job_id"],)).fetchone()
        if a:
            try:
                ans = [{"q": x.get("q"), "a": x.get("a")} for x in json.loads(a["answers"] or "[]") if x.get("q")]
            except Exception:
                ans = []
            iv["application"] = {"applied_at": a["applied_at"], "ats": a["ats"], "answers": ans[:30]}
    iv["kit"] = build_kit(db, profile, iv)
    return iv


def update(db, iid: int, fields: dict) -> None:
    allowed = {"company", "title", "scheduled_at", "mode", "salary", "location", "description", "notes", "status", "prep",
               "prep_model", "prep_cost"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if sets:
        db.conn.execute(f"UPDATE interviews SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), iid))
        db.conn.commit()


def prep_models(settings) -> dict:
    """Modelos oferecidos na tela, com custo ESTIMADO por preparação (preço oficial × tokens típicos) e o saldo do mês."""
    from .budget import Budget, price
    from .nextsteps import PREP_EST_IN, PREP_EST_OUT
    l = settings.llm
    out = []
    for m in l.prep_models:
        pin, _, pout = price(m)
        brl = (PREP_EST_IN * pin + PREP_EST_OUT * pout) / 1e6 * l.usd_brl
        out.append({"model": m, "brl": round(brl, 4), "default": m == l.cheap_model})
    return {"models": out, "usd_brl": l.usd_brl}


def budget_left(db, settings) -> dict:
    from .budget import Budget
    l = settings.llm
    try:
        return Budget(db, l.monthly_budget_brl, l.usd_brl, since=l.budget_since, overrides_brl=l.budget_overrides_brl).status()
    except Exception:
        return {}


def make_prep(db, iid: int, llm, profile: dict, model: str | None = None) -> dict:
    """Preparação por IA (botão, modelo escolhido na tela). Só fatos do perfil (nextsteps.make_prep). Devolve o texto e o
    custo REAL da chamada (somado do registro de uso), que fica gravado na entrevista."""
    from .budget import call_cost_usd
    from .nextsteps import make_prep as _mk
    iv = dict(db.conn.execute("SELECT * FROM interviews WHERE id=?", (iid,)).fetchone())
    mark = db.conn.execute("SELECT COALESCE(MAX(id), 0) FROM llm_usage").fetchone()[0]
    text = _mk(llm, profile, {"company": iv.get("company"), "title": iv.get("title"), "kind": "entrevista", "description": iv.get("description")},
               model=model)
    used = db.conn.execute("SELECT model, prompt_tokens, completion_tokens, COALESCE(cached_tokens, 0) FROM llm_usage WHERE id > ?",
                           (mark,)).fetchall()
    usd = sum(call_cost_usd(r[0], r[1], r[2], r[3]) for r in used)
    real_model = used[-1][0] if used else (model or llm.cfg.cheap_model)
    brl = round(usd * llm.cfg.usd_brl, 4)
    update(db, iid, {"prep": text, "prep_model": real_model, "prep_cost": f"{brl:.4f}"})
    return {"prep": text, "model": real_model, "brl": brl, "tokens": sum((r[1] or 0) + (r[2] or 0) for r in used)}
