"""Próximas etapas dos processos seletivos: o que o bot faz sozinho depois do retorno da empresa.

O leitor de e-mail (`inbox.py`) marca cada retorno com o TIPO de etapa (`kind`), o link direto (`action_url`) e o prazo
(`deadline`). Aqui, a cada volta do `vagas run`:

- Fit Cultural e Mapeamento comportamental da Gupy: o bot responde, sem IA, com o gabarito/prioridade do usuário
  (`fitcultural.py`) ou marca como feito quando a Gupy já mostra a etapa concluída.
- Inscrição que a empresa diz não ter sido finalizada: a vaga volta para a fila e o bot conclui (vaga que exige
  inglês continua de fora, regra do usuário).
- Entrevista e conversa no WhatsApp: kit de preparação curto (IA barata, só fatos do perfil) no Telegram e no painel.
- Recrutador pedindo resposta por e-mail: rascunho no Telegram; só é ENVIADO quando o usuário toca em "Enviar"
  (ou clica no painel). Nada sai sozinho.
- Testes com resposta certa (raciocínio, técnico) e entrevistas ficam com o usuário: lista com link e
  prazo, resumo de manhã e lembrete na véspera do prazo.
"""
from __future__ import annotations

import email.utils
import json
import re
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage

from rich.console import Console

from . import inbox

console = Console()
USER_KINDS = ("comportamental", "logica", "tecnico", "entrevista", "whatsapp", "etapa", "fit_cultural")
PREP_KINDS = ("entrevista", "whatsapp")
AUTO_KINDS = ("fit_cultural", "comportamental")   # questionários de autodescrição da Gupy: o bot responde (fitcultural.py)
TEST_LABEL = {"fit_cultural": "Fit Cultural", "comportamental": "Mapeamento comportamental"}


def open_steps(db) -> list[dict]:
    rows = db.conn.execute(
        "SELECT r.*, j.title, j.description FROM email_responses r LEFT JOIN jobs j ON j.id=r.job_id "
        "WHERE COALESCE(r.kind,'')!='' AND COALESCE(r.done,0)=0 ORDER BY COALESCE(NULLIF(r.deadline,''),'9999'), r.received_at").fetchall()
    return [dict(r) for r in rows]


def _bot():
    from .telegram_bot import TelegramBot, _creds
    token, chat = _creds()
    return TelegramBot(token, chat) if token and chat else None


def _e(s) -> str:
    from .telegram_bot import e
    return e(s)


def fmt_step(r: dict, with_prep: bool = False) -> str:
    label = inbox.KIND_LABEL.get(r.get("kind") or "", "Próxima etapa")
    dl = r.get("deadline") or ""
    prazo = f" · ⏰ até {dl[8:10]}/{dl[5:7]}" if dl else ""
    title = f" — {_e((r.get('title') or '')[:60])}" if r.get("title") else ""
    lines = [f"<b>{_e(label)}</b>{prazo}\n🏢 {_e(r.get('company') or '?')}{title}"]
    if r.get("action_url"):
        lines.append(f'👉 <a href="{_e(r["action_url"])}">abrir a etapa</a>' + (f' · <a href="{_e(r["link"])}">e-mail</a>' if r.get("link") else ""))
    elif r.get("link"):
        lines.append(f'👉 <a href="{_e(r["link"])}">abrir o e-mail</a>')
    if with_prep and r.get("prep"):
        lines.append(f"\n📝 <b>Preparação</b>\n{_e(r['prep'][:2500])}")
    return "\n".join(lines)


def _done_btn(r: dict) -> list[list[tuple[str, str]]]:
    row = [("✅ Já fiz", f"done:{r['id']}")]
    if r.get("prep"):
        row.append(("📝 Preparação", f"prep:{r['id']}"))
    return [row]


# ---------- IA: kit de preparação e rascunho de resposta ----------
def _profile_brief(profile: dict) -> str:
    p = profile or {}
    exps = [f"{x.get('title')} na {x.get('company')} ({x.get('start')}–{x.get('end') or 'atual'}): " + "; ".join((x.get("bullets") or [])[:3])
            for x in (p.get("experiences") or [])[:4]]
    sk = p.get("skills") or {}
    return json.dumps({"headline": p.get("headline"), "resumo": p.get("summary"), "experiencias": exps,
                       "tecnologias": (sk.get("primary") or []) + (sk.get("secondary") or []),
                       "fatos": {k: (p.get("facts") or {}).get(k) for k in ("availability", "notice_period", "work_models_accepted",
                                                                             "led_teams", "contract_preference", "accepts_pj")}},
                      ensure_ascii=False)[:5000]


PREP_SYSTEM = (
    "Você é um mentor de carreira que prepara um desenvolvedor para uma entrevista. Português do Brasil, direto, sem clichês. "
    "Use SÓ fatos do perfil dado; nunca invente experiência, número, empresa ou tecnologia. Quando a vaga pede algo que o perfil "
    "não tem, diga isso e mostre como responder com honestidade, ligando ao que ele já domina. Responda JSON "
    '{"texto": "..."} com estes blocos, separados por linha em branco, cada um com um título numa linha própria:\n'
    "Sobre a vaga — 4 itens: o que a empresa provavelmente faz, o que o dia a dia da vaga parece envolver, o que mais pesa na seleção.\n"
    "Seu ponto forte para esta vaga — 3 itens, cada um um fato do perfil e por que ele importa aqui.\n"
    "Pontos de atenção — 2 a 3 itens: lacunas reais entre perfil e vaga e a frase honesta para cada uma.\n"
    "Perguntas comportamentais — 5, cada uma com uma resposta-modelo curta EM PRIMEIRA PESSOA (situação, ação, resultado) usando fatos do perfil.\n"
    "Perguntas técnicas — 5 sobre a stack da vaga, cada uma com os pontos que a resposta precisa ter.\n"
    "Para perguntar ao entrevistador — 3.\n"
    'Itens começam com "• "; resposta-modelo começa com "→ ".'
)


def make_prep(llm, profile: dict, r: dict, model: str | None = None) -> str:
    """Preparação da entrevista. `model` vem da escolha na tela Entrevistas (padrão: o modelo barato do bot)."""
    out = llm.json(model=model or llm.cfg.cheap_model, purpose="prep_kit", temperature=0.3, max_tokens=2600, system=PREP_SYSTEM,
                   user=(f"Empresa: {r.get('company')}\nVaga: {r.get('title')}\nEtapa: {inbox.KIND_LABEL.get(r.get('kind') or '', '')}\n"
                         f"Descrição da vaga e mensagens do recrutador:\n{(r.get('description') or '')[:5000]}\n\n"
                         f"Perfil do candidato:\n{_profile_brief(profile)}"))
    return str(out.get("texto") or "").strip()


# tokens típicos de uma preparação (medidos no teste de 13/11/2026; raciocínio incluído na saída)
PREP_EST_IN, PREP_EST_OUT = 2200, 2400


def make_reply_draft(llm, profile: dict, r: dict) -> str:
    out = llm.json(model=llm.cfg.cheap_model, purpose="reply_draft", temperature=0.2, max_tokens=500,
                   system=("Você escreve, em nome do candidato, a resposta a um e-mail de recrutador. Português do Brasil, cordial e "
                           "curta (até 8 linhas), assinada com o primeiro nome. Use SÓ fatos do perfil. Se o recrutador pedir algo que "
                           "o perfil não tem (data/horário específico, documento), escreva [PREENCHER: o que falta] no lugar, nunca invente. "
                           'Responda JSON {"resposta": "..."}.'),
                   user=(f"E-mail recebido de {r.get('sender')}\nAssunto: {r.get('subject')}\n\n{(r.get('snippet') or '')[:3000]}\n\n"
                         f"Vaga: {r.get('title') or '?'} na {r.get('company') or '?'}\n"
                         f"Nome do candidato: {((profile or {}).get('personal') or {}).get('full_name')}\nPerfil:\n{_profile_brief(profile)}"))
    return str(out.get("resposta") or "").strip()


def send_reply(db, rid: int) -> str:
    """Envia o rascunho aprovado pelo usuário (botão do Telegram ou do painel). Responde na mesma conversa."""
    r = db.conn.execute("SELECT * FROM email_responses WHERE id=?", (rid,)).fetchone()
    if not r or not (r["reply_draft"] or "").strip():
        return "sem rascunho para enviar"
    if (r["reply_state"] or "").startswith("enviado"):
        return "já foi enviado"
    if "[PREENCHER" in r["reply_draft"]:
        return "o rascunho tem [PREENCHER]: complete no painel antes de enviar"
    name, to = email.utils.parseaddr(r["reply_to"] or r["sender"] or "")
    if not to or inbox.NOREPLY_RE.search(to):
        return f"endereço de resposta inválido ({to or 'vazio'})"
    addr, pwd = inbox.credentials()
    if not addr or not pwd:
        return "falta GMAIL_APP_PASSWORD no .env"
    msg = EmailMessage()
    msg["From"], msg["To"] = addr, email.utils.formataddr((name, to))
    subj = r["subject"] or ""
    msg["Subject"] = subj if re.match(r"^\s*(re|res|resp):", subj, re.I) else f"Re: {subj}"
    if r["message_id"]:
        msg["In-Reply-To"] = r["message_id"]; msg["References"] = r["message_id"]
    msg.set_content(r["reply_draft"])
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:   # mesma senha de app do IMAP
        s.login(addr, pwd)
        s.send_message(msg)
    db.conn.execute("UPDATE email_responses SET reply_state=?, done=1 WHERE id=?",
                    (f"enviado {datetime.now().isoformat(timespec='minutes')}", rid))
    db.conn.commit()
    return "enviado"


# ---------- automações ----------
def refinish(db, log=console.print) -> list[str]:
    """Inscrição que a empresa diz não ter sido finalizada volta para a fila (o bot conclui na próxima volta)."""
    out, seen = [], set()
    for r in db.conn.execute("SELECT r.id, r.job_id, r.company FROM email_responses r WHERE r.kind='finalizar' AND COALESCE(r.done,0)=0 "
                             "AND COALESCE(r.auto_state,'')='' AND r.job_id IS NOT NULL").fetchall():
        if r["job_id"] in seen:   # 2º aviso da mesma vaga: o UPDATE abaixo já marcou todos
            continue
        seen.add(r["job_id"])
        job = db.get_job(r["job_id"])
        state = ""
        if not job:
            state = "vaga não encontrada no banco"
        else:
            langs = [str(l).lower() for l in ((job.classification or {}).get("required_languages") or [])]
            if any(not l.startswith(("port", "pt")) for l in langs):
                state = "não refeita: a vaga exige outro idioma"
            elif job.status in ("queued", "in_progress"):
                state = "já estava na fila"
            else:
                db.kv_set(f"refinish:{job.id}", "1")
                job.status, job.reason, job.attempts = "queued", "empresa avisou que a inscrição não foi finalizada: concluindo", 0
                db.save_job(job)
                state = "voltou para a fila"
                out.append(job.id)
                log(f"[cyan]etapas:[/] {r['company']}: inscrição não finalizada, de volta à fila")
        # todos os avisos da mesma vaga recebem o mesmo estado (a empresa costuma mandar 2 e-mails)
        db.conn.execute("UPDATE email_responses SET auto_state=?, done=CASE WHEN ?='voltou para a fila' OR ?='já estava na fila' THEN 1 ELSE done END "
                        "WHERE kind='finalizar' AND job_id=?", (state, state, state, r["job_id"]))
    db.conn.commit()
    return out


def fit_cultural(db, browser, profile: dict, log=console.print) -> int:
    """Questionários de autodescrição da Gupy que o bot responde sozinho, sem IA: Fit Cultural e Mapeamento comportamental."""
    from . import fitcultural
    from .config import load_settings
    fc = load_settings().fitcultural
    kinds = [k for k, on in (("fit_cultural", fc.enabled), ("comportamental", fc.behavioral_enabled)) if on]
    if not kinds:
        return 0
    n = 0
    rows = db.conn.execute(f"SELECT * FROM email_responses WHERE kind IN ({','.join('?' * len(kinds))}) AND COALESCE(done,0)=0 "
                           "AND COALESCE(auto_state,'')='' AND COALESCE(action_url,'')!='' AND action_url LIKE '%gupy%' "
                           "ORDER BY received_at DESC", kinds).fetchall()
    for r in rows:
        page = browser.page()
        kind = r["kind"]
        label = TEST_LABEL.get(kind, kind)
        try:
            opened = fitcultural.open_test(page, r["action_url"], profile, browser, log, kind)
            res = {"test": None, "already": "already", "gone": "gone"}.get(opened, "failed: não consegui abrir o teste pela página da candidatura")
            if opened == "test":
                res = fitcultural.fill_test(page, log, kind=kind)
        except Exception as ex:
            res = f"failed: {type(ex).__name__}: {str(ex)[:120]}"
        done = res in ("done", "already", "gone")
        state = {"done": "respondido pelo bot com o seu gabarito", "already": "a Gupy já mostra o teste respondido",
                 "gone": "candidatura encerrada (o link leva para a página de vagas)"}.get(res, res[8:])
        db.conn.execute("UPDATE email_responses SET auto_state=?, done=? WHERE id=?", (state[:200], 1 if done else 0, r["id"]))
        db.conn.commit()
        log(f"[cyan]etapas:[/] {label} {r['company']}: {state}")
        b = _bot()
        if b:
            if done:
                b.send(f"🧭 <b>{_e(label)}</b> — {_e(r['company'])}: {_e(state)}.")
            else:
                b.send(f"⚠️ <b>{_e(label)}</b> — {_e(r['company'])}: não consegui responder sozinho ({_e(state)}).\n"
                       + fmt_step(dict(r)), buttons=_done_btn(dict(r)))
        n += 1
        if not done:
            break   # uma falha por volta: evita repetir o mesmo erro em todas
    return n


def _new_ai_work(db, llm, profile: dict, log) -> None:
    for r in open_steps(db):
        try:
            if r["kind"] in PREP_KINDS and not r.get("prep") and llm is not None:
                db.conn.execute("UPDATE email_responses SET prep=? WHERE id=?", (make_prep(llm, profile, r), r["id"]))
                db.conn.commit()
            if r["kind"] == "resposta" and not r.get("reply_draft") and llm is not None:
                draft = make_reply_draft(llm, profile, r)
                db.conn.execute("UPDATE email_responses SET reply_draft=?, reply_state='rascunho' WHERE id=?", (draft, r["id"]))
                db.conn.commit()
                b = _bot()
                if b:
                    b.send(f"✉️ <b>{_e(r['company'] or r['sender'])}</b> pediu resposta:\n<i>{_e((r['subject'] or '')[:120])}</i>\n\n"
                           f"<b>Rascunho:</b>\n{_e(draft)}\n\nEnvio só se você tocar em Enviar.",
                           buttons=[[("📤 Enviar", f"send:{r['id']}"), ("✖️ Descartar", f"drop:{r['id']}")]])
        except Exception as ex:   # sem orçamento/IA: segue sem kit nem rascunho
            log(f"[yellow]etapas: IA indisponível para {r.get('company')}: {str(ex)[:80]}[/]")
            break


def announce_new(db, ids: list[int]) -> None:
    """Etapa nova chegou agora: avisa com link, prazo e (se houver) a preparação."""
    b = _bot()
    if not b or not ids:
        return
    for r in open_steps(db):
        gupy_fit = r["kind"] in AUTO_KINDS and "gupy" in (r.get("action_url") or "")   # esses o bot responde sozinho
        if r["id"] in ids and r["kind"] in USER_KINDS and not gupy_fit:
            b.send("🎯 <b>Nova etapa para você</b>\n" + fmt_step(r, with_prep=True), buttons=_done_btn(r))


def daily_digest(db, hour: int = 8) -> bool:
    today = date.today().isoformat()
    if datetime.now().hour < hour or db.kv_get("steps_digest_day") == today:
        return False
    recent = (date.today() - timedelta(days=21)).isoformat()   # etapa antiga sem prazo provavelmente já passou
    steps = [r for r in open_steps(db) if r["kind"] in USER_KINDS
             and ((r.get("received_at") or "") >= recent or (r.get("deadline") or "") >= today)]
    db.kv_set("steps_digest_day", today)
    b = _bot()
    if not b or not steps:
        return False
    lines = [f"☀️ <b>Bom dia! {len(steps)} etapa(s) esperando você</b>"]
    for r in steps[:12]:
        dl = r.get("deadline") or ""
        prazo = f" ⏰ {dl[8:10]}/{dl[5:7]}" if dl else ""
        link = f' <a href="{_e(r["action_url"] or r["link"])}">abrir</a>' if (r.get("action_url") or r.get("link")) else ""
        lines.append(f"• {_e(inbox.KIND_LABEL.get(r['kind'], ''))} — <i>{_e((r['company'] or '?')[:30])}</i>{prazo}{link}")
    lines.append("\nUse /etapas para marcar o que já fez.")
    b.send("\n".join(lines))
    return True


def deadline_reminders(db) -> int:
    b = _bot(); n = 0
    soon = (date.today() + timedelta(days=1)).isoformat()
    for r in open_steps(db):
        dl = r.get("deadline") or ""
        if dl and dl <= soon and (r.get("reminded") or "") != dl:
            if b:
                quando = "HOJE" if dl <= date.today().isoformat() else "amanhã"
                b.send(f"⏰ <b>Prazo {quando}</b>\n" + fmt_step(r, with_prep=True), buttons=_done_btn(r))
            db.conn.execute("UPDATE email_responses SET reminded=? WHERE id=?", (dl, r["id"]))
            n += 1
    db.conn.commit()
    return n


def tick(db, llm, profile: dict, browser=None, new_ids: list[int] | None = None, log=console.print) -> None:
    """Uma volta das próximas etapas (chamada pelo `vagas run` depois de ler o e-mail)."""
    refinish(db, log)
    _new_ai_work(db, llm, profile, log)
    announce_new(db, new_ids or [])
    daily_digest(db)
    deadline_reminders(db)
    if browser is not None:
        fit_cultural(db, browser, profile, log)
