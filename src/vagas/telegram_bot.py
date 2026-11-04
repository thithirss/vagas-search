"""Comandos do Telegram: controla e consulta o bot pelo celular.

Roda numa thread do painel (`vagas dashboard`), por long polling do getUpdates — sem webhook, sem porta aberta.
Só responde ao chat de TELEGRAM_CHAT_ID; qualquer outro chat é ignorado em silêncio. Ações que mandam
candidatura de verdade (/iniciar) pedem confirmação por botão.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime

from dotenv import dotenv_values

from .config import ROOT

COMMANDS = [
    ("status", "Situação agora: rodando?, envios de hoje, fila"),
    ("hoje", "Resumo do dia: enviadas, falhas e descartes"),
    ("enviadas", "Últimas candidaturas enviadas"),
    ("fila", "Próximas vagas da fila"),
    ("pendencias", "O que precisa de você"),
    ("retornos", "Respostas das empresas (e-mail)"),
    ("painel", "Link do painel para abrir no celular"),
    ("log", "Últimas linhas do bot"),
    ("iniciar", "Começar a rodar o dia (pede confirmação)"),
    ("parar", "Parar o bot (termina a vaga atual)"),
    ("buscar", "Buscar vagas agora, sem candidatar"),
    ("avisos", "Ligar/desligar aviso a cada candidatura"),
    ("ajuda", "Lista de comandos"),
]
KV_EACH = "tg_notify_each_application"


def _creds() -> tuple[str, str]:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        v = dotenv_values(ROOT / ".env")
        token, chat = token or v.get("TELEGRAM_BOT_TOKEN"), chat or v.get("TELEGRAM_CHAT_ID")
    return (token or "").strip(), (chat or "").strip()


def each_application_enabled(db) -> bool:
    return (db.kv_get(KV_EACH) or "1") != "0"


def e(s) -> str:
    return html.escape(str(s or ""), quote=False)


class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, str(chat_id)
        self.offset = 0
        self._stop = threading.Event()

    # ---------- API ----------
    def _call(self, method: str, payload: dict, timeout: float = 20) -> dict:
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}",
                                     data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def send(self, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> None:
        payload = {"chat_id": self.chat_id, "text": text[:3900], "parse_mode": "HTML", "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
        try:
            self._call("sendMessage", payload)
        except Exception:
            pass

    # ---------- laço ----------
    def run(self) -> None:
        try:
            self._call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in COMMANDS]})
        except Exception:
            pass
        backoff = 2
        while not self._stop.is_set():
            try:
                res = self._call("getUpdates", {"offset": self.offset, "timeout": 50, "allowed_updates": ["message", "callback_query"]}, timeout=65)
                backoff = 2
                for upd in res.get("result", []):
                    self.offset = upd["update_id"] + 1
                    try:
                        self.handle(upd)
                    except Exception as ex:   # um comando com erro não pode derrubar o laço
                        self.send(f"⚠️ Erro no comando: <code>{e(str(ex)[:300])}</code>")
            except urllib.error.HTTPError as ex:
                # 409: outro processo (outro painel aberto) já está lendo as mensagens; espera e tenta de novo
                time.sleep(30 if ex.code == 409 else backoff); backoff = min(backoff * 2, 120)
            except Exception:
                time.sleep(backoff); backoff = min(backoff * 2, 120)

    def stop(self) -> None:
        self._stop.set()

    def handle(self, upd: dict) -> None:
        if "callback_query" in upd:
            cq = upd["callback_query"]
            if str(cq.get("message", {}).get("chat", {}).get("id")) != self.chat_id:
                return
            try:
                self._call("answerCallbackQuery", {"callback_query_id": cq["id"]})
            except Exception:
                pass
            return self.on_button(cq.get("data") or "")
        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != self.chat_id:
            return   # só o dono do bot
        text = (msg.get("text") or "").strip()
        m = re.match(r"^/(\w+)(?:@\w+)?\s*(.*)$", text)
        if not m:
            return self.send("Use /ajuda para ver os comandos.")
        cmd, arg = m.group(1).lower(), m.group(2).strip()
        fn = getattr(self, f"cmd_{cmd}", None) or (self.cmd_ajuda if cmd == "start" else None)
        if not fn:
            return self.send(f"Não conheço /{e(cmd)}. Use /ajuda.")
        fn(arg)

    # ---------- dados ----------
    def _data(self):
        from .config import load_settings
        from .dashboard import Data
        return Data(load_settings())

    # ---------- comandos ----------
    def cmd_ajuda(self, arg: str = "") -> None:
        self.send("🤖 <b>Bot de vagas</b>\n\n" + "\n".join(f"/{c} — {e(d)}" for c, d in COMMANDS))

    def cmd_status(self, arg: str = "") -> None:
        from .dashboard import running_tasks
        d = self._data(); o = d.overview(); k = o["kpi"]
        run = {n: v for n, v in running_tasks().items() if n != "dashboard"}
        if run:
            n, v = next(iter(run.items()))
            estado = f"{'⏳ parando' if v.get('stopping') else '🟢 rodando'}: {e(n)} desde {e((v.get('started') or '')[11:16])}"
        else:
            estado = "⚪ parado"
        b = k.get("budget") or {}
        ia = f"R$ {b.get('spent_brl', 0):.2f} de R$ {b.get('budget_brl', 0):.0f}" if b.get("budget_brl") else f"US$ {k['cost_today_usd']:.2f} hoje"
        last = (o.get("last_discovery") or "")[:16].replace("T", " ")
        self.send(f"{estado}\n\n"
                  f"📨 Enviadas hoje: <b>{k['applied_today']}</b> / {k['cap']}\n"
                  f"📋 Na fila: <b>{k['queued']}</b>\n"
                  f"🔎 Encontradas hoje: {k['found_today']} · descartadas: {k['skipped_today']}\n"
                  f"🙋 Precisam de você: <b>{k['pending']}</b>\n"
                  f"🤖 IA no mês: {ia}\n"
                  f"🕑 Última busca: {e(last or 'nunca')}\n"
                  f"📊 Total enviadas: {k['applied_total']}",
                  buttons=[[("▶️ Iniciar", "ask_run"), ("⏹ Parar", "stop")] if not run else [("⏹ Parar", "stop"), ("📜 Log", "log")]])

    def cmd_hoje(self, arg: str = "") -> None:
        d = self._data(); today = date.today().isoformat(); c = d.db.conn
        apps = c.execute("SELECT j.title, j.company, a.applied_at FROM applications a JOIN jobs j ON j.id=a.job_id "
                         "WHERE substr(a.applied_at,1,10)=? ORDER BY a.applied_at DESC", (today,)).fetchall()
        fails = c.execute("SELECT reason FROM jobs WHERE status IN ('failed','pending_question') AND substr(updated_at,1,10)=?", (today,)).fetchall()
        skips = c.execute("SELECT reason FROM jobs WHERE status='skipped' AND substr(updated_at,1,10)=?", (today,)).fetchall()
        lines = [f"📅 <b>Hoje, {datetime.now():%d/%m}</b>", f"\n📨 <b>{len(apps)} enviadas</b>"]
        lines += [f"• {e(r['applied_at'][11:16])} {e(r['title'][:48])} — <i>{e(r['company'])}</i>" for r in apps[:15]]
        if len(apps) > 15:
            lines.append(f"… e mais {len(apps) - 15}")
        if fails:
            lines.append(f"\n⚠️ <b>{len(fails)} não concluídas</b>")
            lines += [f"• {v}× {e(k)}" for k, v in Counter((r[0] or "")[:55] for r in fails).most_common(5)]
        if skips:
            lines.append(f"\n🚫 <b>{len(skips)} descartadas</b> (fora dos seus critérios)")
            lines += [f"• {v}× {e(k)}" for k, v in Counter((r[0] or "").split("(")[0].split(":")[0][:45].strip() for r in skips).most_common(5)]
        self.send("\n".join(lines))

    def cmd_enviadas(self, arg: str = "") -> None:
        n = max(1, min(30, int(arg))) if arg.isdigit() else 10
        rows = self._data().applications()[:n]
        if not rows:
            return self.send("Nenhuma candidatura enviada ainda.")
        out = [f"📨 <b>Últimas {len(rows)} enviadas</b>"]
        for a in rows:
            link = a.get("external_url") or a.get("url") or ""
            t = f'<a href="{e(link)}">{e(a["title"][:55])}</a>' if link else e(a["title"][:55])
            out.append(f"• {e((a['applied_at'] or '')[5:16].replace('T', ' '))} · {t} — <i>{e(a['company'])}</i>")
        self.send("\n".join(out))

    def cmd_fila(self, arg: str = "") -> None:
        rows = self._data().jobs("queued", None, 200)
        rows.sort(key=lambda j: -int((j.get("classification") or {}).get("fit_score") or 0))
        if not rows:
            return self.send("📋 Fila vazia. Use /buscar para procurar vagas.")
        out = [f"📋 <b>Fila: {len(rows)} vagas</b> (maior aderência primeiro)"]
        for j in rows[:12]:
            fit = (j.get("classification") or {}).get("fit_score")
            out.append(f"• {fit or '—'} · {e(j['title'][:50])} — <i>{e(j['company'])}</i>")
        self.send("\n".join(out))

    def cmd_pendencias(self, arg: str = "") -> None:
        rows = self._data().pending()
        if not rows:
            return self.send("🙌 Nada precisa de você agora.")
        out = [f"🙋 <b>{len(rows)} pendências</b> (resolva no painel)"]
        for p in rows[:12]:
            q = re.sub(r"\s*(em:)?\s*https?://\S+", "", p["question"]).strip()
            link = f' <a href="{e(p["url"])}">abrir</a>' if p.get("url") else ""
            who = f"<i>{e(p['company'])}</i>: " if p.get("company") else ""
            out.append(f"• {who}{e(q[:110])}{link}")
        self.send("\n".join(out))

    def cmd_retornos(self, arg: str = "") -> None:
        d = self._data().responses(); c = d["counts"]; items = d["items"]
        lines = [f"📬 <b>Retornos</b> · ✅ {c.get('positivo', 0)} positivos · ⏳ {c.get('processo', 0)} em processo · ❌ {c.get('negativo', 0)} negativos"]
        todo = [i for i in items if i["action"] and not i["done"]]
        if todo:
            lines.append("\n<b>Precisam de você:</b>")
            for i in todo[:10]:
                link = f' <a href="{e(i["link"])}">e-mail</a>' if i.get("link") else ""
                lines.append(f"• <b>{e(i['company'])}</b>: {e(i['action'])}{link}")
        neg = [i for i in items if i["status"] == "negativo"][:5]
        if neg:
            lines.append("\n<b>Últimos negativos:</b> " + ", ".join(e(i["company"] or "?") for i in neg))
        last = (d.get("last_scan") or "")[:16].replace("T", " ")
        lines.append(f"\n🕑 Última leitura do e-mail: {e(last or 'nunca')}" + ("" if d["configured"] else " (leitura automática desligada: falta a senha de app no .env)"))
        self.send("\n".join(lines), buttons=[[("🔄 Verificar e-mails agora", "inbox")]] if d["configured"] else None)

    def cmd_painel(self, arg: str = "") -> None:
        from . import remote
        link = remote.current_link(self._data().db)
        if link:
            self.send(f"📱 Painel no celular (não compartilhe, o link tem a sua chave):\n{e(link)}")
        else:
            self.send("O link ainda não está pronto. Ele é criado quando o painel inicia; tente de novo em um minuto.")

    def cmd_log(self, arg: str = "") -> None:
        from .dashboard import recent_logs, running_tasks, tail_log
        run = running_tasks()
        name = next((v.get("log") for k, v in run.items() if k != "dashboard"), None) or (recent_logs(1) or [{}])[0].get("name")
        if not name:
            return self.send("Sem log ainda.")
        keep = [l for l in tail_log(name, 400).splitlines()
                if re.match(r"^(▶|\s{2,3}(applied|failed|needs_user|skipped|dry_run|already_applied)\b|Resumo:|⏹|descoberta:|\d+ analisadas)", l)]
        txt = "\n".join(l.strip() for l in keep[-18:]) or "(sem eventos ainda)"
        self.send(f"📜 <b>{e(name)}</b>\n<pre>{e(txt)[-3400:]}</pre>")

    def cmd_iniciar(self, arg: str = "") -> None:
        self.send("Começar a rodar o dia? O bot busca vagas e <b>envia candidaturas de verdade</b> até você mandar /parar.",
                  buttons=[[("✅ Sim, iniciar", "run"), ("Cancelar", "cancel")]])

    def cmd_parar(self, arg: str = "") -> None:
        self.on_button("stop")

    def cmd_buscar(self, arg: str = "") -> None:
        self.on_button("search")

    def cmd_avisos(self, arg: str = "") -> None:
        d = self._data()
        on = not each_application_enabled(d.db) if arg.lower() not in ("on", "off", "liga", "desliga") else arg.lower() in ("on", "liga")
        d.db.kv_set(KV_EACH, "1" if on else "0")
        self.send(f"🔔 Aviso a cada candidatura: <b>{'ligado' if on else 'desligado'}</b>. "
                  f"(O resumo de cada rodada continua chegando.)")

    def on_button(self, data: str) -> None:
        from .dashboard import running_tasks, start_task, stop_task
        if data == "cancel":
            return self.send("Ok, nada feito.")
        if data == "ask_run":
            return self.cmd_iniciar()
        if data == "log":
            return self.cmd_log()
        if data in ("run", "search", "inbox"):
            try:
                info = start_task(data, {})
                what = {"run": "Rodando o dia", "search": "Buscando vagas", "inbox": "Lendo os e-mails"}[data]
                self.send(f"▶️ {what} (iniciado às {e(info['started'][11:16])}). Use /status ou /log." + (" Os retornos novos chegam aqui." if data == "inbox" else ""))
            except RuntimeError as ex:
                self.send(f"Não iniciei: {e(ex)}")
            return
        if data == "stop":
            run = [k for k in running_tasks() if k != "dashboard"]
            if not run:
                return self.send("O bot já está parado.")
            for k in run:
                stop_task(k)
            self.send("⏹ Parando: o bot termina a vaga atual, fecha o navegador e sai (até ~1 min).")


_started = False


def start_in_background() -> TelegramBot | None:
    """Liga o bot de comandos se houver token e chat no .env. Uma vez por processo."""
    global _started
    token, chat = _creds()
    if _started or not token or not chat:
        return None
    _started = True
    bot = TelegramBot(token, chat)
    threading.Thread(target=bot.run, name="telegram-bot", daemon=True).start()
    return bot
