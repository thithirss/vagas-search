"""Retornos das empresas no e-mail: positivo (próxima etapa), negativo, em processo.

Lê o Gmail por IMAP com uma SENHA DE APP do Google (`GMAIL_APP_PASSWORD` no .env; o endereço vem de
`GMAIL_ADDRESS` ou do e-mail do perfil). A caixa é aberta só para leitura e as mensagens são baixadas com
BODY.PEEK: nada é marcado como lido, respondido, movido ou apagado. A busca usa a mesma sintaxe do Gmail
(X-GM-RAW) do levantamento manual de 29/10/2026, e a classificação é por regras, sem IA.

Cada e-mail entra uma vez em `email_responses` (chave = id da mensagem no Gmail, o mesmo `msg-f` dos links
do Gmail), casado com a candidatura pela empresa ou pelo título da vaga.
"""
from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import os
import re
import unicodedata
from datetime import date, datetime, timedelta
from html import unescape

from dotenv import dotenv_values

from .config import ROOT

IMAP_HOST = "imap.gmail.com"
QUERY = ('(candidatura OR "processo seletivo" OR inscrição OR inscricao OR entrevista OR vaga OR etapa OR '
         'application OR applying OR interview) -category:promotions -in:sent')

# confirmações que a própria plataforma manda no envio (não são retorno da empresa) e e-mails de divulgação
SKIP_SUBJECT_RE = re.compile(r"sua candidatura foi enviada|candidatura enviada [àa]|inscri[cç][aã]o via indeed|"
                             r"candidate-se agora|novas vagas|vagas para voc[eê]|est[aá] contratando|alerta|concursos?", re.I)
SKIP_SENDER_RE = re.compile(r"jobalert|newsletters?-noreply|messages-noreply|notifications-noreply|updates-noreply|"
                            r"@em\.linkedin|marketing|promo|pratiqueconcursos|alura\.com|dio\.me|temu|news", re.I)

NEG_RE = re.compile(r"infelizmente|optamos por n[aã]o|n[aã]o (vamos )?(seguir|prosseguir|avan[cç]ar)|outr[oa]s candidat|"
                    r"n[aã]o foi selecionad|n[aã]o (ser[aá]|foi) poss[ií]vel (seguir|avan[cç]ar)|"
                    r"vaga (foi )?(finalizada|encerrada|cancelada|preenchida)|n[aã]o est[aá] mais dispon[ií]vel|"
                    r"unfortunately|not (to )?(move|moving) forward|other candidates|application_rejected", re.I)
POS_RE = re.compile(r"convite para (a |uma )?(entrevista|pr[oó]xima etapa|avalia[cç][aã]o)|convidad[oa] para|"
                    r"pr[oó]xima etapa|etapa .{0,60}desbloqueada|aprovad[oa] para|pr[eé]-entrevista|"
                    r"agendar (a |uma |sua )?(entrevista|conversa)|teste (t[eé]cnico|de racioc[ií]nio|online|de perfil)|"
                    r"desafio t[eé]cnico|case t[eé]cnico|selecionad[oa] para|interview invitation|next step", re.I)
PROC_RE = re.compile(r"confirma[cç][aã]o (de|da) (inscri|candidatura)|recebemos (a |sua )|em an[aá]lise|"
                     r"obrigad[oa] por se candidatar|thank you for applying|boas[- ]vindas|"
                     r"candidatura (foi )?(vista|visualizada|recebida)|falta um passo|chegou por aqui", re.I)


CONDITIONAL_RE = re.compile(r"\b(se (seu|o seu|voc[eê]|a conversa|tudo)|caso (seu|voc[eê])|ser[aá] convidad|poder[aá] ser|como funciona|"
                            r"etapas? (do|de nosso|de) processo|resumo das etapas|if (you|your)|will be invited)\b", re.I)


def credentials() -> tuple[str, str]:
    env = dotenv_values(ROOT / ".env")
    addr = os.environ.get("GMAIL_ADDRESS") or env.get("GMAIL_ADDRESS") or ""
    pwd = os.environ.get("GMAIL_APP_PASSWORD") or env.get("GMAIL_APP_PASSWORD") or ""
    if not addr:
        try:
            import yaml
            prof = yaml.safe_load((ROOT / "config" / "profile.yaml").read_text(encoding="utf-8")) or {}
            addr = (prof.get("personal") or {}).get("email") or ""
        except Exception:
            pass
    return addr.strip(), pwd.replace(" ", "").strip()   # o Google mostra a senha de app em blocos com espaço


def configured() -> bool:
    return all(credentials())


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def classify(subject: str, body: str) -> tuple[str, str]:
    """(status, o que fazer). status: positivo | negativo | processo | '' (não é retorno)."""
    text = f"{subject}\n{body}"
    if NEG_RE.search(text):
        return "negativo", ""
    if re.search(r"falta um passo", text, re.I):
        return "processo", "Concluir a etapa pendente (link no e-mail)"
    # o assunto manda: "Boas vindas!" que descreve as etapas do processo não é convite (IOB, 30/10). No corpo, frase
    # condicional ("se seu perfil for compatível, você será convidado...") também não conta.
    if PROC_RE.search(subject) and not POS_RE.search(subject):
        return "processo", ""
    firm = " ".join(s for s in re.split(r"(?<=[.!?\n])\s+", body) if not CONDITIONAL_RE.search(s))
    if POS_RE.search(f"{subject}\n{firm}"):
        t = _fold(text)
        if "whatsapp" in t:
            act = "Iniciar a conversa no WhatsApp (link no e-mail)"
        elif re.search(r"teste|desafio|case", t):
            act = "Fazer o teste ou desafio (link no e-mail)"
        elif "entrevista" in t or "interview" in t:
            act = "Fazer ou agendar a entrevista (link no e-mail)"
        else:
            act = "Fazer a próxima etapa na plataforma"
        return "positivo", act
    if PROC_RE.search(text):
        return "processo", ""
    return "", ""


def llm_classify(llm, subject: str, body: str) -> tuple[str, str]:
    """Segunda opinião do modelo barato, só quando as regras não decidem e o e-mail é de empresa com candidatura.
    Respeita o teto mensal de IA (BudgetExceeded vira 'sem classificação')."""
    try:
        out = llm.json(model=llm.cfg.cheap_model, purpose="classify_email", temperature=0, max_tokens=120,
                       system=("Você classifica e-mails de processos seletivos recebidos por um candidato. Responda JSON "
                               '{"status": "positivo"|"negativo"|"processo"|"nenhum", "acao": "texto curto ou vazio"}. '
                               "positivo = convite para entrevista, teste, próxima etapa ou pedido de contato; negativo = recusa "
                               "ou vaga encerrada; processo = confirmação de recebimento ou candidatura em análise; nenhum = não é "
                               "sobre uma candidatura. 'acao' diz o que o candidato precisa fazer, se algo."),
                       user=f"Assunto: {subject}\n\n{body[:2500]}")
    except Exception:
        return "", ""
    st = str(out.get("status") or "").lower()
    return (st, str(out.get("acao") or "")[:140]) if st in ("positivo", "negativo", "processo") else ("", "")


_STOP = {"ltda", "sa", "s", "a", "brasil", "brazil", "group", "grupo", "tecnologia", "tech", "software", "digital",
         "solucoes", "em", "de", "do", "da", "e", "the", "inc", "jobs", "venha", "ser", "somos", "quem"}


_GENERIC_TITLE = {"desenvolvedor", "desenvolvedora", "developer", "pessoa", "analista", "engenheiro", "engenheira", "engineer",
                  "software", "pleno", "junior", "senior", "full", "stack", "fullstack", "full-stack", "web", "remoto", "remote",
                  "sistemas", "programador", "desenvolvimento", "vaga", "jr.", "pl.", "sr."}


def _has_word(w: str, hay: str) -> bool:
    """Palavra inteira, inclusive com símbolo nas pontas (c#, .net), onde \\b não funciona."""
    return re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", hay) is not None


def _company_tokens(name: str) -> list[str]:
    parts = [t for t in re.split(r"[^a-z0-9]+", _fold(name)) if t and t not in _STOP]
    toks = [t for t in parts if len(t) >= 3]
    return toks or [t for t in parts if len(t) == 2]   # "Grupo CM": a sigla curta é o único nome


def match_application(db, sender: str, subject: str, body: str) -> tuple[str | None, str]:
    """Candidatura mais provável: empresa citada no remetente/assunto/corpo, desempate pelo título da vaga."""
    hay = _fold(f"{sender} {subject} {body[:4000]}")
    best, best_score, best_company = None, 0.0, ""
    for r in db.conn.execute("SELECT a.job_id, j.company, j.title, a.applied_at FROM applications a JOIN jobs j ON j.id=a.job_id "
                             "ORDER BY a.applied_at DESC").fetchall():
        score = 0.0
        toks = _company_tokens(r["company"] or "")
        if toks and all(_has_word(t, hay) for t in toks):
            score += 3
        elif toks and any(len(t) >= 5 and _has_word(t, hay) for t in toks):
            score += 1.5
        # palavra inteira ("back"/"end" não casam dentro de "backend"); termos curtos só com símbolo (c#, .net)
        twords = [w.strip(".") if not w.startswith(".") else w for w in re.split(r"[^a-z0-9#+.]+", _fold(r["title"] or ""))]
        twords = [w for w in twords if len(w) >= 3 or re.search(r"[#+.]", w)]
        if twords:
            hit = sum(1 for w in twords if _has_word(w, hay)) / len(twords)
            # o título pesa mais que a empresa: no banco a empresa às vezes é o slogan ("VENHA SER #SANGUELARANJA" =
            # FCamara) ou um texto da página ("Quem somos" = Banco ABC). Sem a empresa, só vale título com palavra
            # específica (Backend, .NET, Angular...): "Desenvolvedor Full Stack" sozinho casaria com qualquer e-mail.
            specific = [w for w in twords if w not in _GENERIC_TITLE]
            spec_ok = bool(specific) and all(_has_word(w, hay) for w in specific)
            if hit >= 0.75 and (score > 0 or spec_ok):
                score += 3.2 * hit
            elif hit >= 0.6 and score > 0:
                score += 2 * hit
        if score > best_score:
            best, best_score, best_company = r["job_id"], score, r["company"]
    return (best, best_company) if best_score >= 2.5 else (None, "")


def _decode(s) -> str:
    if not s:
        return ""
    out = []
    for part, enc in email.header.decode_header(s):
        out.append(part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part)
    return "".join(out)


def _text_of(msg) -> str:
    plain, html = "", ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ct = part.get_content_type()
        if part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            txt = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ct == "text/plain" and not plain:
            plain = txt
        elif ct == "text/html" and not html:
            html = txt
    if not plain and html:
        # o LinkedIn marca a recusa só nos links (email_jobs_application_rejected): guarda os hrefs no texto
        hrefs = " ".join(set(re.findall(r"application_rejected", html)))
        plain = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", html)) + " " + hrefs
    elif "application_rejected" in html:
        plain += " application_rejected"
    return re.sub(r"\s+", " ", unescape(plain)).strip()


def save_response(db, *, msg_key, received_at, sender, subject, snippet, status, summary, action, link, company, job_id) -> bool:
    cur = db.conn.execute(
        "INSERT OR IGNORE INTO email_responses (msg_key, received_at, sender, subject, snippet, status, summary, action, link, company, job_id, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (msg_key, received_at, sender, subject, snippet, status, summary, action, link, company, job_id, datetime.now().isoformat(timespec="seconds")))
    db.conn.commit()
    return cur.rowcount > 0


def scan(db, days: int | None = None, log=print, llm=None) -> dict:
    """Lê o Gmail desde a última varredura (ou `days` dias) e grava os retornos novos. Devolve contagens e os novos."""
    addr, pwd = credentials()
    if not addr or not pwd:
        raise RuntimeError("falta GMAIL_APP_PASSWORD no .env (senha de app do Google)")
    last = db.kv_get("inbox_last_scan")
    since = (date.today() - timedelta(days=days)) if days else (
        (datetime.fromisoformat(last).date() - timedelta(days=1)) if last else date.today() - timedelta(days=14))
    query = f"after:{since:%Y/%m/%d} {QUERY}"
    out = {"lidos": 0, "novos": [], "positivo": 0, "negativo": 0, "processo": 0}
    M = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        M.login(addr, pwd)
        M.select("INBOX", readonly=True)   # somente leitura: nada muda na caixa
        # X-GM-RAW aceita a sintaxe de busca do Gmail; o literal UTF-8 cobre os acentos ("inscrição")
        M.literal = query.encode("utf-8")
        typ, data = M.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
        uids = (data[0] or b"").split() if typ == "OK" else []
        uids = _unseen_uids(M, db, uids[-300:])   # lendo a cada 30 min: só baixa o corpo do que ainda não está no banco
        for uid in uids:
            typ, parts = M.uid("FETCH", uid, "(X-GM-MSGID X-GM-THRID BODY.PEEK[])")
            if typ != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            meta, raw = parts[0][0].decode(errors="replace"), parts[0][1]
            mm = re.search(r"X-GM-MSGID (\d+)", meta); tm = re.search(r"X-GM-THRID (\d+)", meta)
            msgid, thrid = (mm.group(1) if mm else ""), (tm.group(1) if tm else "")
            key = f"gm:{msgid}" if msgid else f"uid:{uid.decode()}"
            if db.conn.execute("SELECT 1 FROM email_responses WHERE msg_key=?", (key,)).fetchone():
                continue
            msg = email.message_from_bytes(raw)
            subject, sender = _decode(msg.get("Subject")), _decode(msg.get("From"))
            out["lidos"] += 1
            if SKIP_SUBJECT_RE.search(subject) or SKIP_SENDER_RE.search(sender):
                mark_seen(db, key)
                continue
            body = _text_of(msg)
            status, action = classify(subject, body)
            job_id, company = match_application(db, sender, subject, body)
            if not status and job_id and llm is not None:
                status, action = llm_classify(llm, subject, body)   # regra não decidiu: o modelo barato dá a segunda opinião
            if not status:
                mark_seen(db, key)
                continue
            if not job_id and status == "processo":
                mark_seen(db, key)
                continue   # confirmação genérica sem candidatura conhecida: não é retorno útil
            try:
                received = email.utils.parsedate_to_datetime(msg.get("Date")).astimezone().isoformat(timespec="seconds")
            except Exception:
                received = datetime.now().isoformat(timespec="seconds")
            link = f"https://mail.google.com/mail/?authuser={addr}#all/thread-f:{thrid}|msg-f:{msgid}" if msgid else ""
            snippet = body[:280]
            if save_response(db, msg_key=key, received_at=received, sender=sender, subject=subject, snippet=snippet, status=status,
                             summary=subject[:200], action=action, link=link, company=company, job_id=job_id):
                out[status] += 1
                out["novos"].append({"status": status, "subject": subject, "company": company, "action": action})
        db.kv_set("inbox_last_scan", datetime.now().isoformat(timespec="seconds"))
    finally:
        try:
            M.logout()
        except Exception:
            pass
    log(f"e-mail: {out['lidos']} lidos · {out['positivo']} positivos · {out['negativo']} negativos · {out['processo']} em processo (novos)")
    return out


def notify_new(found: dict) -> None:
    """Telegram: cada retorno positivo novo vai na hora; negativos e confirmações só no resumo."""
    from .notify import notify
    for n in found.get("novos", []):
        if n["status"] == "positivo":
            notify(f"🎉 Retorno positivo: {n['company'] or n['subject'][:60]}\n{n['action']}")
    neg, proc = found.get("negativo", 0), found.get("processo", 0)
    if neg or proc:
        notify(f"📬 E-mail: {neg} recusa(s) e {proc} atualização(ões) de processo novas. Veja em Retornos no painel.")


def _unseen_uids(M, db, uids: list) -> list:
    """Pede só o X-GM-MSGID (barato) e devolve os UIDs cujo e-mail ainda não foi analisado."""
    if not uids:
        return []
    typ, data = M.uid("FETCH", b",".join(uids), "(X-GM-MSGID)")
    if typ != "OK":
        return uids
    known = {r[0] for r in db.conn.execute("SELECT msg_key FROM email_responses")}
    seen = {r[0] for r in db.conn.execute("SELECT msg_key FROM email_seen")} if _has_seen_table(db) else set()
    out = []
    for item in data or []:
        line = (item[0] if isinstance(item, tuple) else item) or b""
        line = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        um, mm = re.search(r"UID (\d+)", line), re.search(r"X-GM-MSGID (\d+)", line)
        if not um:
            continue
        key = f"gm:{mm.group(1)}" if mm else f"uid:{um.group(1)}"
        if key not in known and key not in seen:
            out.append(um.group(1).encode())
    return out


def _has_seen_table(db) -> bool:
    db.conn.execute("CREATE TABLE IF NOT EXISTS email_seen (msg_key TEXT PRIMARY KEY, seen_at TEXT)")
    return True


def mark_seen(db, key: str) -> None:
    """E-mail lido que não virou retorno (newsletter, alerta de vaga): não baixar de novo daqui a 30 min."""
    _has_seen_table(db)
    db.conn.execute("INSERT OR IGNORE INTO email_seen (msg_key, seen_at) VALUES (?, ?)", (key, datetime.now().isoformat(timespec="seconds")))
    db.conn.commit()


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def due(db, minutes: int = 30) -> bool:
    """Passou o intervalo desde a última leitura (ou tentativa que falhou). Data no futuro (relógio/UTC antigo) conta como vencida."""
    now = datetime.now()
    for k in ("inbox_last_scan", "inbox_last_try"):
        t = _parse(db.kv_get(k))
        if t and now - timedelta(minutes=minutes) < t <= now:
            return False
    return True


def due_today(db) -> bool:   # compatibilidade: chamadores antigos
    return due(db, 24 * 60)
