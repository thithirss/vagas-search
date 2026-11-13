"""Painel local: servidor HTTP com API JSON sobre o SQLite + página única (dashboard_static/index.html)."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from .config import CONFIG_DIR, DATA_DIR, ROOT, Settings, load_settings
from .db import DB

STATIC = Path(__file__).parent / "dashboard_static"
import threading
_COURSES_LOCK = threading.Lock()   # cliques rápidos no painel não podem gravar courses.yaml ao mesmo tempo
# preços por modelo: a fonte única é vagas/budget.py (página oficial da OpenAI); aqui só (entrada, saída) para a tela "IA e custos"
from .budget import PRICES as _BUDGET_PRICES
PRICES = {m: (v[0], v[2]) for m, v in _BUDGET_PRICES.items()}


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def reason_key(r: str | None) -> str:
    r = r or ""
    if r.startswith("não é vaga de dev"): return "não é vaga de dev"
    if "inglês" in r or "descrição em en" in r: return "descrição em inglês"
    if "não é remoto" in r: return "presencial ou híbrida"
    if re.search(r"mais de \d+h", r): return "mais de 24h"
    if "exige idioma" in r: return "exige outro idioma"
    if "eliminat" in r: return "requisito eliminatório"
    if "blacklist" in r or "título contém" in r: return "lista de bloqueio"
    if "já aplicad" in r: return "duplicada"
    if "ignorado" in r or "whatsapp" in r.lower(): return "site ignorado"
    if "pulada manualmente" in r: return "pulada por você"
    return (r.split(":")[0] or "outro")[:40]


def _job(row) -> dict:
    j = dict(row)
    try:
        j["classification"] = json.loads(j.get("classification") or "{}")
    except Exception:
        j["classification"] = {}
    return j


class Data:
    def __init__(self, settings: Settings):
        self.s = settings
        self.db = DB(settings.db_path)

    # ---------- visão geral ----------
    def _budget_status(self) -> dict:
        """Gasto de IA do mês contra o teto (R$), para o painel mostrar quanto ainda cabe."""
        try:
            from .budget import Budget
            l = self.s.llm
            return Budget(self.db, l.monthly_budget_brl, l.usd_brl, since=l.budget_since, overrides_brl=l.budget_overrides_brl).status()
        except Exception:
            return {}

    def overview(self) -> dict:
        c = self.db.conn
        today = date.today().isoformat()
        # sem a descrição: com milhares de vagas, carregar o texto de todas deixava o painel lento (0,8 s, picos de 18 s)
        jobs = [_job(r) for r in c.execute("SELECT id, status, discovered_at, attempts, ats, source, apply_type, title, company, reason, "
                                           "classification FROM jobs").fetchall()]
        apps = [dict(r) for r in c.execute("SELECT * FROM applications ORDER BY applied_at DESC").fetchall()]
        by_status = Counter(j["status"] for j in jobs)
        apps_today = [a for a in apps if (a["applied_at"] or "")[:10] == today]
        # ticks do dia: candidaturas + tentativas (eventos)
        ticks = [{"t": a["applied_at"][11:16], "kind": "applied", "job_id": a["job_id"]} for a in apps_today]
        for r in c.execute("SELECT at, message, job_id FROM events WHERE substr(at,1,10)=? AND level='info' AND message LIKE 'failed:%'", (today,)):
            ticks.append({"t": r["at"][11:16], "kind": "failed", "job_id": r["job_id"]})
        for r in c.execute("SELECT at, message, job_id FROM events WHERE substr(at,1,10)=? AND message LIKE 'needs_user:%'", (today,)):
            ticks.append({"t": r["at"][11:16], "kind": "pending", "job_id": r["job_id"]})
        titles = {j["id"]: (j["title"], j["company"]) for j in jobs}
        for t in ticks:
            t["title"], t["company"] = titles.get(t["job_id"], ("", ""))
        ticks.sort(key=lambda x: x["t"])
        days = [(date.today() - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
        per_day = {d: {"applied": 0, "found": 0, "queued": 0} for d in days}
        for a in apps:
            d = (a["applied_at"] or "")[:10]
            if d in per_day: per_day[d]["applied"] += 1
        for j in jobs:
            d = (j["discovered_at"] or "")[:10]
            if d in per_day:
                per_day[d]["found"] += 1
                if j["status"] in ("queued", "applied", "failed", "pending_question", "in_progress", "dry_run"): per_day[d]["queued"] += 1
        reasons = Counter(reason_key(j["reason"]) for j in jobs if j["status"] == "skipped" and (j["discovered_at"] or "")[:10] == today)
        stacks: Counter = Counter()
        for j in jobs:
            if j["status"] != "skipped" and (j["discovered_at"] or "")[:10] == today:
                for st in j["classification"].get("stack") or []: stacks[str(st).strip().lower()] += 1
        ats: dict[str, Counter] = defaultdict(Counter)
        for j in jobs:
            if not j["attempts"]: continue
            key = j["ats"] or ("linkedin easy apply" if j["source"] == "linkedin" and j.get("apply_type") == "easy_apply" else ("indeed apply" if j.get("apply_type") == "indeed_apply" else j["source"]))
            ats[key][j["status"]] += 1
        tok = c.execute("SELECT model, SUM(prompt_tokens) p, SUM(completion_tokens) c FROM llm_usage WHERE substr(at,1,10)=? GROUP BY model", (today,)).fetchall()
        cost_today = sum(self._cost(r["model"], r["p"], r["c"]) for r in tok)
        running = self._running()
        return {
            "now": datetime.now().strftime("%d/%m %H:%M"), "today": today,
            "kpi": {
                "applied_today": len(apps_today), "cap": self.s.limits.max_applications_per_day, "caps": self.s.limits.max_per_source_per_day,
                "applied_total": len(apps), "found_today": sum(1 for j in jobs if (j["discovered_at"] or "")[:10] == today),
                "queued": by_status.get("queued", 0) + by_status.get("new", 0), "pending": len(self.db.pending_questions()),
                "failed": by_status.get("failed", 0), "skipped_today": sum(reasons.values()), "cost_today_usd": round(cost_today, 3),
                "budget": self._budget_status(),
                # retornos do e-mail: quantos pedem ação sua (entrevista, teste, etapa) e ainda não foram marcados como feitos
                "responses_todo": c.execute("SELECT COUNT(*) FROM email_responses WHERE COALESCE(action,'')<>'' AND done=0").fetchone()[0],
                "responses_positive": c.execute("SELECT COUNT(*) FROM email_responses WHERE status='positivo'").fetchone()[0],
            },
            "ticks": ticks, "days": days, "per_day": per_day, "reasons": reasons.most_common(12), "stacks": stacks.most_common(14),
            "ats": {k: dict(v) for k, v in sorted(ats.items(), key=lambda x: -sum(x[1].values()))},
            "last_discovery": self.db.kv_get("last_discovery"), "running": running,
            # saudação da Visão do dia: primeiro nome (perfil em memória) e a próxima entrevista marcada
            "first_name": ((self.profile().get("personal") or {}).get("first_name") or ""),
            "next_interview": next((dict(r) for r in c.execute(
                "SELECT id, company, title, scheduled_at FROM interviews WHERE status='aberta' AND COALESCE(scheduled_at,'')>=? "
                "ORDER BY scheduled_at LIMIT 1", (today,)).fetchall()), None),
            "active_hours": list(self.s.limits.active_hours),
            # funil das vagas encontradas hoje: mostra onde o volume se perde (descartes por critério x falhas)
            "funnel": dict(Counter(j["status"] for j in jobs if (j["discovered_at"] or "")[:10] == today)),
            "recent_apps": [{"t": (a["applied_at"] or "")[5:16].replace("T", " "), "job_id": a["job_id"],
                             "title": titles.get(a["job_id"], ("", ""))[0], "company": titles.get(a["job_id"], ("", ""))[1],
                             "today": (a["applied_at"] or "")[:10] == today} for a in apps[:8]],
        }

    def _running(self) -> dict:
        # pid + "parando": o topo do painel mostra "parando…" e o botão "Forçar agora" durante a parada graciosa
        return {k: {"pid": v.get("pid"), "stopping": bool(v.get("stopping"))} for k, v in running_tasks().items() if k != "dashboard"}

    def _cost(self, model: str, p: int | None, c: int | None) -> float:
        from .budget import call_cost_usd
        return call_cost_usd(model, p or 0, c or 0)

    # ---------- vagas ----------
    def jobs(self, status: str | None, q: str | None, limit: int = 300, today_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM jobs"; conds, args = [], []
        if status and status != "all":
            if status == "queued": conds.append("status IN ('queued','new')")
            else: conds.append("status=?"); args.append(status)
        if q:
            conds.append("(lower(title) LIKE ? OR lower(company) LIKE ? OR lower(reason) LIKE ?)"); like = f"%{q.lower()}%"; args += [like, like, like]
        if today_only: conds.append("substr(discovered_at,1,10)=?"); args.append(date.today().isoformat())
        if conds: sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY COALESCE(applied_at, updated_at, discovered_at) DESC LIMIT ?"; args.append(limit)
        rows = [_job(r) for r in self.db.conn.execute(sql, args).fetchall()]
        from .airlines import is_airline
        for j in rows:
            j.pop("description", None); j["reason_key"] = reason_key(j.get("reason")) if j["status"] == "skipped" else None
            j["airline"] = is_airline(j.get("company"), self.s.airlines)   # trilha de companhia aérea: selo no painel
        return rows

    def job(self, job_id: str) -> dict | None:
        r = self.db.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not r: return None
        j = _job(r)
        app = self.db.conn.execute("SELECT * FROM applications WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
        j["application"] = dict(app) if app else None
        if j["application"]:
            try: j["application"]["answers"] = json.loads(j["application"]["answers"] or "[]")
            except Exception: j["application"]["answers"] = []
        j["events"] = [dict(e) for e in self.db.conn.execute("SELECT at, level, message, data FROM events WHERE job_id=? ORDER BY id DESC LIMIT 40", (job_id,)).fetchall()]
        j["pending"] = [dict(p) for p in self.db.conn.execute("SELECT id, question, field_type, options, suggested, answered_at, answer FROM pending_questions WHERE job_id=? ORDER BY id DESC", (job_id,)).fetchall()]
        j["resume_rel"] = self._rel(j.get("resume_path"))
        ev = DATA_DIR / "evidence" / (re.sub(r"[^a-zA-Z0-9_-]", "_", job_id) + ".png")
        j["evidence_rel"] = self._rel(str(ev)) if ev.exists() else None
        learn = DATA_DIR / "learn"
        j["learn"] = []
        if learn.exists():
            for d in learn.glob(f"*/{re.sub(r'[^a-zA-Z0-9_-]', '_', job_id)}"):
                for png in sorted(d.glob("step*.png")):
                    j["learn"].append({"ats": d.parent.name, "file": self._rel(str(png)), "step": png.stem})
        return j

    def _rel(self, p: str | None) -> str | None:
        if not p: return None
        try:
            return str(Path(p).resolve().relative_to(DATA_DIR.resolve()))
        except Exception:
            return None

    # ---------- outros ----------
    def applications(self) -> list[dict]:
        rows = self.db.conn.execute("SELECT a.id, a.job_id, a.source, a.ats, a.applied_at, a.resume_path, a.answers, j.title, j.company, j.url, j.external_url, j.classification FROM applications a JOIN jobs j ON j.id=a.job_id ORDER BY a.applied_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try: d["answers"] = json.loads(d["answers"] or "[]")
            except Exception: d["answers"] = []
            try: d["classification"] = json.loads(d["classification"] or "{}")
            except Exception: d["classification"] = {}
            d["resume_rel"] = self._rel(d["resume_path"])
            from .airlines import is_airline
            d["airline"] = is_airline(d.get("company"), self.s.airlines); out.append(d)
        return out

    def pending(self) -> list[dict]:
        out = []
        for r in self.db.pending_questions():
            d = dict(r); j = self.db.get_job(d["job_id"]) if d["job_id"] else None
            d["title"], d["company"] = (j.title, j.company) if j else ("", "")
            try: d["options"] = json.loads(d["options"] or "[]")
            except Exception: d["options"] = []
            # aviso (link para criar conta, questionário manual) x pergunta de formulário de verdade
            d["is_note"] = (d.get("field_type") in ("link", "email")) or bool(re.search(r"^(o site|a |confirme a candidatura|question[aá]rio d)", (d.get("question") or ""), re.I))
            d["job_status"] = j.status if j else None
            # link para agir: o `suggested` dos avisos guarda a URL; senão a que estiver no texto; senão o site da vaga
            m = re.search(r"https?://\S+", d.get("question") or "")
            sug = d.get("suggested") or ""
            d["url"] = sug if sug.startswith("http") else (m.group(0).rstrip(".,)") if m else ((j.external_url or j.url) if j else ""))
            d["job_url"] = j.url if j else ""
            # rádio sem rótulo chega como ["on", "on"]: sem opções reais, mostra Sim/Não
            opts = [o for o in dict.fromkeys(d["options"]) if str(o).strip().lower() not in ("on", "off", "true", "false", "")]
            if len(opts) < 2 and d.get("field_type") in ("radio", "select", "combobox"):
                opts = ["Sim", "Não"]
            d["options"] = opts
            if d.get("suggested") and d["suggested"] not in opts and not d["is_note"]:
                d["suggested"] = "" if d["suggested"].lower() in ("on", "off") else d["suggested"]
            out.append(d)
        return out

    def dismiss_pending(self, qid: int, skip_job: bool) -> None:
        """Marca a pendência como resolvida; com skip_job=True a vaga é descartada e não volta para a fila."""
        r = self.db.conn.execute("SELECT job_id FROM pending_questions WHERE id=?", (qid,)).fetchone()
        self.db.answer_pending(qid, "descartado no painel")
        if r and r[0] and skip_job:
            self.db.set_status(r[0], "skipped", "descartada por você no painel")

    def responses(self) -> dict:
        """Retornos das empresas lidos do e-mail (vagas/inbox.py), do mais recente para o mais antigo."""
        from . import inbox
        rows = self.db.conn.execute(
            "SELECT r.*, j.title, j.url AS job_url, j.external_url, a.applied_at FROM email_responses r "
            "LEFT JOIN jobs j ON j.id=r.job_id LEFT JOIN applications a ON a.job_id=r.job_id "
            "ORDER BY r.received_at DESC").fetchall()
        items = [dict(r) for r in rows]
        counts = Counter(i["status"] for i in items)
        return {"items": items, "counts": dict(counts), "configured": inbox.configured(),
                "last_scan": self.db.kv_get("inbox_last_scan"), "last_try": self.db.kv_get("inbox_last_try"),
                "applied_total": self.db.conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0],
                "todo": sum(1 for i in items if i["action"] and not i["done"])}

    def set_response_done(self, rid: int, done: bool) -> None:
        self.db.conn.execute("UPDATE email_responses SET done=? WHERE id=?", (1 if done else 0, rid))
        self.db.conn.commit()

    def resolve_pending(self, qid: int) -> None:
        """Aviso resolvido por você (ex.: logou no portal): fecha a pendência e devolve a vaga para a fila."""
        r = self.db.conn.execute("SELECT job_id FROM pending_questions WHERE id=?", (qid,)).fetchone()
        self.db.answer_pending(qid, "resolvido no painel")
        j = self.db.get_job(r[0]) if r and r[0] else None
        if j and j.status not in ("applied", "skipped"):
            j.status, j.reason, j.attempts = "queued", None, 0
            self.db.save_job(j)

    def answer(self, qid: int, answer: str) -> None:
        from .qa import AnswerBank
        r = self.db.conn.execute("SELECT * FROM pending_questions WHERE id=?", (qid,)).fetchone()
        if not r: return
        self.db.answer_pending(qid, answer)
        bank = AnswerBank(self.s.answers_path); bank.add(r["question"], answer, "user", options=json.loads(r["options"] or "[]"))
        self.db.requeue_pending_jobs()

    def courses(self) -> dict:
        p = CONFIG_DIR / "courses.yaml"
        return (yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None) or {"stacks": {}}

    def set_course(self, stack: str, name: str, status: str) -> None:
        p = CONFIG_DIR / "courses.yaml"
        with _COURSES_LOCK:
            data = self.courses()
            for c in data["stacks"].get(stack) or []:
                if c.get("name") == name:
                    c["status"] = status
                    if status == "done" and not c.get("year"): c["year"] = str(date.today().year)
            _atomic_write(p, "# Cursos por stack. status: todo | in_progress | done (+ year).\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=110))

    def llm(self) -> dict:
        rows = self.db.conn.execute("SELECT substr(at,1,10) d, model, purpose, SUM(prompt_tokens) p, SUM(completion_tokens) c, COUNT(*) n FROM llm_usage GROUP BY d, model, purpose ORDER BY d DESC").fetchall()
        out = []
        for r in rows:
            out.append({"day": r["d"], "model": r["model"], "purpose": r["purpose"], "prompt": r["p"], "completion": r["c"], "calls": r["n"], "usd": round(self._cost(r["model"], r["p"], r["c"]), 4)})
        return {"rows": out, "prices": PRICES}

    def events(self, limit: int = 200) -> list[dict]:
        rows = self.db.conn.execute("SELECT e.at, e.level, e.job_id, e.message, j.title, j.company FROM events e LEFT JOIN jobs j ON j.id=e.job_id ORDER BY e.id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    _profile_cache: tuple = ((), {})

    def profile(self) -> dict:
        """Perfil carregado como no bot (para o treino de entrevistas). Ler leva ~1,8 s (junta o pool de cursos), então fica
        em memória e só é relido quando o profile.yaml ou o courses.yaml mudam."""
        try:
            from .resume.profile import load_profile
            if not self.s.profile_path.exists():
                return {}
            stamp = tuple(p.stat().st_mtime for p in (self.s.profile_path, CONFIG_DIR / "courses.yaml") if p.exists())
            if Data._profile_cache[0] != stamp:
                Data._profile_cache = (stamp, load_profile(self.s.profile_path))
            return Data._profile_cache[1]
        except Exception:
            return {}

    def counts(self) -> dict:
        """Contadores do menu (atualizados a cada 30 s em qualquer tela): só agregados em SQL, sem carregar vagas."""
        c, today = self.db.conn, date.today().isoformat()
        st = dict(c.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
        return {
            "kpi": {"pending": len(self.db.pending_questions()),
                    "responses_todo": c.execute("SELECT COUNT(*) FROM email_responses WHERE COALESCE(action,'')<>'' AND done=0").fetchone()[0],
                    "applied_total": c.execute("SELECT COUNT(*) FROM applications").fetchone()[0],
                    "queued": st.get("queued", 0) + st.get("new", 0),
                    "skipped_today": c.execute("SELECT COUNT(*) FROM jobs WHERE status='skipped' AND substr(discovered_at,1,10)=?", (today,)).fetchone()[0],
                    "interviews_open": c.execute("SELECT COUNT(*) FROM interviews WHERE status='aberta'").fetchone()[0]},
            "running": self._running(), "active_hours": list(self.s.limits.active_hours), "last_discovery": self.db.kv_get("last_discovery"),
        }

    def settings(self) -> dict:
        s = self.s
        prof = yaml.safe_load(s.profile_path.read_text(encoding="utf-8")) if s.profile_path.exists() else {}
        bank = yaml.safe_load(s.answers_path.read_text(encoding="utf-8")) if s.answers_path.exists() else {}
        return {
            "discovery": s.discovery.model_dump(), "limits": s.limits.model_dump(), "filters": s.filters.model_dump(), "apply": s.apply.model_dump(),
            "resume": s.resume.model_dump(), "courses": s.courses.model_dump(), "ats": s.ats.model_dump(), "llm": s.llm.model_dump(),
            "searches_total": len(s.all_searches()),
            "profile": {"name": (prof.get("personal") or {}).get("full_name"), "headline": prof.get("headline"), "skills": prof.get("skills"), "facts": {k: v for k, v in (prof.get("facts") or {}).items()}, "experiences": [(e.get("company"), e.get("title"), e.get("start"), e.get("end")) for e in prof.get("experiences") or []]},
            "answers": (bank or {}).get("answers") or [],
            "env": {k: bool(os.environ.get(k)) for k in ("OPENAI_API_KEY", "GUPY_PASSWORD", "ATS_SIGNUP_PASSWORD", "TELEGRAM_BOT_TOKEN")},
        }


# ---------- tarefas (comandos disparados pelo painel) ----------
LOGS = DATA_DIR / "logs"
BROWSER_TASKS = {"search", "prepare", "apply", "run", "resume", "linkedin"}   # usam o perfil do navegador: um por vez


WINDOWS = os.name == "nt"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if WINDOWS:   # no Windows os.kill(pid, 0) MATA o processo (TerminateProcess); consulta pela API
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not (k32.GetExitCodeProcess(h, ctypes.byref(code)) and code.value == 259):   # STILL_ACTIVE
                return False
            # o Windows REUSA números de processo: o pid gravado de um bot que já saiu pode ser agora de outro
            # programa (11/11: o painel via "rodando" e o atalho não iniciava o bot). Só vale se ainda for um Python.
            buf = ctypes.create_unicode_buffer(1024); size = ctypes.c_ulong(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return Path(buf.value).name.lower().startswith("python")
            return True
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists() and stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
            return False
        return True
    except Exception:
        return False


def running_tasks() -> dict:
    out = {}
    for pf in LOGS.glob("*.pid"):
        try:
            info = json.loads(pf.read_text()) if pf.read_text().strip().startswith("{") else {"pid": int(pf.read_text().strip())}
        except Exception:
            continue
        pid = int(info.get("pid", 0))
        if _pid_alive(pid):
            info["stopping"] = (LOGS / (info.get("stop_flag") or f"stop-{pid}.flag")).exists()
            out[pf.stem] = info
    return out


def start_task(kind: str, args: dict, ignore_busy: tuple = ()) -> dict:
    import shlex, sys
    running = {k: v for k, v in running_tasks().items() if k not in ignore_busy}
    if kind in BROWSER_TASKS and any(k in BROWSER_TASKS for k in running):
        busy = ", ".join(k for k in running if k in BROWSER_TASKS)
        raise RuntimeError(f"já existe um processo usando o navegador ({busy}). Pare-o antes ou aguarde terminar.")
    py = sys.executable
    base = [py, "-m", "vagas.cli"]
    if kind == "search":
        cmd = base + ["search"]
    elif kind == "prepare":
        cmd = base + ["prepare"]
    elif kind == "apply":
        cmd = base + ["apply"]
        if args.get("limit"): cmd += ["--limit", str(int(args["limit"]))]
        if args.get("dry_run"): cmd += ["--dry-run"]
        for jid in args.get("jobs") or []: cmd += ["--job", jid]
        if args.get("force"): cmd += ["--force"]
    elif kind == "run":
        cmd = base + ["run"]
    elif kind == "resume":
        cmd = base + ["resume", args["job"], "--force"]
    elif kind == "inbox":
        cmd = base + ["retornos"] + (["--dias", str(int(args["dias"]))] if args.get("dias") else [])
    elif kind == "linkedin":
        cmd = base + ["linkedin", "ler" if args.get("action") == "ler" else "aplicar"]
        if args.get("item"): cmd += ["--item", str(args["item"])]
        if args.get("text_file"): cmd += ["--texto-arquivo", str(args["text_file"])]
        if args.get("file"): cmd += ["--arquivo", str(args["file"])]
        if args.get("resume_run"): cmd += ["--religar"]
        if args.get("auto"): cmd += ["--auto"]
        if args.get("batch_file"): cmd += ["--lote", str(args["batch_file"])]
    else:
        raise RuntimeError("comando desconhecido")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = LOGS / f"ui-{kind}-{ts}.log"
    LOGS.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUNBUFFERED="1",
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8",   # log em arquivo no Windows seria cp1252 e quebraria nos emojis
               COLUMNS="160",   # sem terminal o rich corta em 80 colunas e quebra as linhas do log no meio
               VAGAS_WAIT_HUMAN="0")   # tarefa do painel nunca tem ninguém no teclado (no Windows o NUL finge ser terminal)
    if args.get("any_hour"):
        env["VAGAS_ANY_HOUR"] = "1"    # rodar fora da janela ativa (pedido explícito)
    detach = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if WINDOWS
              else {"start_new_session": True})
    # aviso de parada com nome próprio da tarefa (não o PID: no Windows o PID do .venv\python.exe é o do lançador)
    stop_flag = LOGS / f"stop-{kind}-{ts}.flag"
    env["VAGAS_STOP_FLAG"] = str(stop_flag)
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=open(log, "w"), stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL,   # tarefa do painel roda sem ninguém no teclado:
                            env=env, **detach)   # sem tty, o checkpoint espera pouco e pula
    info = {"pid": proc.pid, "kind": kind, "cmd": " ".join(shlex.quote(c) for c in cmd[2:]), "log": log.name, "started": datetime.now().isoformat(timespec="seconds"), "args": args,
            "stop_flag": stop_flag.name}
    (LOGS / f"{kind}.pid").write_text(json.dumps(info))
    return info


STOP_HARD_AFTER = 150   # s: o processo tem 90 s de graça (apply/run) para terminar a vaga atual; depois disso, força


# ---------- tela LinkedIn ----------
def linkedin_browser(args: dict) -> None:
    """Tarefa de navegador da tela LinkedIn (ler/aplicar). O navegador do bot é um só: se a leva estiver rodando,
    pausa com parada graciosa, faz a tarefa e religa a leva no fim. Roda numa thread do painel."""
    import time
    from . import linkedin_profile as lp

    def work():
        was_running = "run" in running_tasks()
        try:
            if was_running:
                lp.set_status("pausando", "pausando as candidaturas para usar o navegador do bot…")
                stop_task("run")
                for _ in range(90):
                    if "run" not in running_tasks():
                        break
                    time.sleep(2)
            busy = [k for k in running_tasks() if k in BROWSER_TASKS]
            if busy:
                lp.set_status("erro", f"navegador ocupado por: {', '.join(busy)}")
                raise RuntimeError("ocupado")
            # quem religa a leva é a PRÓPRIA tarefa no fim (--religar): se o painel reiniciar no meio, a leva volta igual
            start_task("linkedin", dict(args, resume_run=was_running))
        except Exception as e:
            if str(e) != "ocupado":
                lp.set_status("erro", str(e)[:200])
            if was_running and "run" not in running_tasks():
                try:
                    start_task("run", {})
                except Exception:
                    pass

    threading.Thread(target=work, daemon=True).start()


def linkedin_state(data) -> dict:
    from . import engines, interview, linkedin_profile as lp
    s = data.s
    try:
        mkt = lp.market(data.db, data.profile(), s)
    except Exception as e:
        mkt = {"error": str(e)[:120], "top": [], "missing": [], "have": [], "jobs": 0}
    q = s.linkedin.image_quality
    return {"engines": engines.available(s), "analysis": lp.analysis(), "current": lp.current(), "market": mkt,
            "status": lp.status(), "images": lp.images(), "styles": list(lp.BANNER_STYLES),
            "image_cost": {k: lp.image_cost_brl(s, k, 0) for k in ("low", "medium", "high")}, "image_ref_brl": round(lp.IMAGE_INPUT_EST_USD * s.llm.usd_brl, 3), "image_quality": q,
            "budget": interview.budget_left(data.db, s), "openai_models": s.llm.prep_models,
            "default_engine": s.linkedin.engine, "running": list(running_tasks()),
            "auto_apply": s.linkedin.auto_apply, "history": lp.history()[:10]}


def linkedin_post(action: str, payload: dict, data) -> tuple[dict, int]:
    """POST /api/linkedin/<ação>. Devolve (json, código)."""
    from . import engines, linkedin_profile as lp
    s = data.s
    st = lp.status().get("state")
    if action in ("analyze", "image-ai", "read", "apply", "install") and st in ("trabalhando", "lendo", "aplicando", "pausando"):
        return {"error": f"espere a tarefa atual terminar ({lp.status().get('msg', '')})"}, 409
    if action == "analyze":
        engine = str(payload.get("engine") or s.linkedin.engine)
        eng = next((e for e in engines.available(s) if e["id"] == engine), None)
        if not eng or not eng["ok"]:
            return {"error": f"motor indisponível: {(eng or {}).get('why') or engine}"}, 400
        model = str(payload.get("model") or "").strip() or None
        if engine == "openai" and model and model not in s.llm.prep_models:
            return {"error": f"modelo não permitido: {model}"}, 400

        def go():
            from .llm import LLM
            llm = LLM(s.llm, data.db) if os.environ.get("OPENAI_API_KEY") else None
            st2 = lp.analyze(engine, settings=s, db=data.db, profile=data.profile(), llm=llm, model=model)
            return f"análise pronta com {engines.LABELS.get(engine, engine)} em {st2['seconds']} s"
        linkedin_bg(go, f"analisando com {engines.LABELS.get(engine, engine)} (pode levar 1–3 min)…")
        return {"ok": True}, 200
    if action in ("install", "login"):
        tool = str(payload.get("tool") or "")
        if tool not in engines.CLI:
            return {"error": "ferramenta inválida"}, 400
        if action == "login":
            try:
                return {"ok": True, "msg": engines.open_login(tool)}, 200
            except Exception as e:
                return {"error": str(e)}, 400
        linkedin_bg(lambda: f"{engines.LABELS[tool]}: {engines.install(tool)}",
                    f"instalando {engines.LABELS[tool]} (npm, pode levar 1–3 min)…")
        return {"ok": True}, 200
    if action == "read":
        lp.set_status("pausando", "preparando o navegador do bot…")
        linkedin_browser({"action": "ler"})
        return {"ok": True}, 200
    if action == "paste":
        return {"current": lp.save_pasted(str(payload.get("headline") or ""), str(payload.get("about") or ""),
                                          str(payload.get("page_text") or ""))}, 200
    if action in ("apply", "undo"):
        # um item ({item, value|name}) ou vários ({items: [...]}); "undo" reaplica o que estava antes (history.json)
        if action == "undo":
            h = lp.history()
            k = int(payload.get("index", 0))
            if not (0 <= k < len(h)) or not h[k].get("old"):
                return {"error": "nada para desfazer nesse item"}, 400
            e = h[k]
            raw = [{"item": e["item"], "value": e["old"]} if e["item"] in ("headline", "about") else {"item": e["item"], "name": e["old"]}]
        else:
            raw = payload.get("items") or [payload]
        items = []
        for it in raw:
            item = str(it.get("item") or "")
            if item not in ("headline", "about", "photo", "banner"):
                return {"error": f"item inválido: {item}"}, 400
            if item in ("headline", "about"):
                value = str(it.get("value") or "").strip()
                if not value:
                    return {"error": f"texto vazio em {item}"}, 400
                limit = 220 if item == "headline" else 2600
                if len(value) > limit:
                    return {"error": f"{item} passa de {limit} caracteres (o LinkedIn recusa)"}, 400
                items.append({"item": item, "value": value})
            else:
                name = str(it.get("name") or "")
                if not re.fullmatch(r"[\w.-]+\.(png|jpe?g|webp)", name) or not (lp.IMG / name).exists():
                    return {"error": "escolha uma imagem gerada"}, 400
                items.append({"item": item, "file": str(lp.IMG / name)})
        f = lp.DIR / "lote.json"; f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        auto = bool(payload.get("auto", s.linkedin.auto_apply)) or action == "undo"
        lp.set_status("pausando", "preparando o navegador do bot…")
        linkedin_browser({"action": "aplicar", "batch_file": str(f), "auto": auto})
        return {"ok": True}, 200
    if action == "mark":
        return {"analysis": lp.mark_applied(str(payload.get("item") or ""), bool(payload.get("value", True)))}, 200
    if action == "banner":
        bg = str(payload.get("bg") or "")
        bg_file = str(lp.IMG / bg) if bg and re.fullmatch(r"[\w.-]+\.png", bg) and (lp.IMG / bg).exists() else ""
        techs = payload.get("techs")
        p = lp.banner(data.profile(), str(payload.get("style") or "gradiente"), title=str(payload.get("title") or ""),
                      techs=[str(t) for t in techs][:7] if isinstance(techs, list) else None,
                      tagline=str(payload.get("tagline") or ""), bg_file=bg_file)
        return {"name": p.name}, 200
    if action == "upload":
        p = lp.save_upload(str(payload.get("data") or ""))
        return {"name": p.name}, 200
    if action == "photo":
        name = str(payload.get("name") or "")
        if not re.fullmatch(r"[\w.-]+\.(png|jpe?g|webp)", name) or not (lp.IMG / name).exists():
            return {"error": "envie uma foto primeiro"}, 400
        kw = {k: float(payload[k]) for k in ("x", "y", "zoom", "brightness", "contrast", "saturate") if k in payload}
        p = lp.photo_improve(lp.IMG / name, bg=str(payload.get("bg") or "") if re.fullmatch(r"#[0-9a-fA-F]{3,8}", str(payload.get("bg") or "")) else "", **kw)
        return {"name": p.name}, 200
    if action == "image-ai":
        if not os.environ.get("OPENAI_API_KEY"):
            return {"error": "imagem por IA só pela API da OpenAI (falta OPENAI_API_KEY)"}, 400
        mode = str(payload.get("mode") or "")
        if mode not in lp.PROMPTS:
            return {"error": "modo inválido"}, 400
        q = str(payload.get("quality") or s.linkedin.image_quality)
        if q not in lp.IMAGE_COST_USD:
            return {"error": "qualidade inválida"}, 400
        names = [n for n in (payload.get("names") or []) if re.fullmatch(r"[\w.-]+\.(png|jpe?g|webp)", str(n)) and (lp.IMG / n).exists()]
        if mode != "capa" and not names:
            return {"error": "envie pelo menos uma foto sua"}, 400

        def go():
            from .llm import LLM
            p = lp.image_ai(mode, settings=s, llm=LLM(s.llm, data.db), refs=[lp.IMG / n for n in names[:3]], quality=q)
            if mode == "capa":   # o fundo gerado vira a capa com o seu texto por cima
                lp.banner(data.profile(), "gradiente", bg_file=str(p))
            return f"imagem pronta ({p.name})"
        linkedin_bg(go, "gerando a imagem (até 1 min)…")
        return {"ok": True}, 200
    return {"error": "ação desconhecida"}, 404


def linkedin_bg(fn, busy_msg: str) -> None:
    """Tarefa demorada sem navegador (análise por IA, imagem por IA) numa thread; o estado vai para status.json."""
    from . import linkedin_profile as lp
    lp.set_status("trabalhando", busy_msg)

    def work():
        try:
            msg = fn()
            lp.set_status("ok", msg or "pronto")
        except Exception as e:
            lp.set_status("erro", f"{type(e).__name__}: {str(e)[:220]}")

    threading.Thread(target=work, daemon=True).start()


def stop_task(kind: str, force: bool = False) -> bool:
    """Parada graciosa: grava o aviso que o processo confere entre vagas (vagas.stopflag) e só força o
    encerramento se ele não sair sozinho em STOP_HARD_AFTER s. `force=True` encerra na hora."""
    import time
    pf = LOGS / f"{kind}.pid"
    if not pf.exists():
        return False
    try:
        txt = pf.read_text().strip(); info = json.loads(txt) if txt.startswith("{") else {"pid": int(txt)}
        pid = int(info["pid"])
    except Exception:
        return False
    if not _pid_alive(pid):
        return False
    flag = LOGS / (info.get("stop_flag") or f"stop-{pid}.flag")
    if force:
        ok = _kill(pid)
        try: flag.unlink()
        except OSError: pass
        return ok
    flag.write_text(datetime.now().isoformat(timespec="seconds"))

    def escalate():
        end = time.monotonic() + STOP_HARD_AFTER
        while time.monotonic() < end:
            if not _pid_alive(pid):
                break
            time.sleep(1)
        else:
            _kill(pid)
        try:
            flag.unlink()
        except OSError:
            pass

    threading.Thread(target=escalate, daemon=True).start()
    return True


def _kill(pid: int) -> bool:
    import signal, time
    if WINDOWS:   # sem grupos POSIX: derruba a árvore (python + Chrome filho) para não sobrar janela segurando o perfil
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(20):
            if not _pid_alive(pid):
                return True
            time.sleep(0.25)
        return not _pid_alive(pid)
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try: os.kill(pid, signal.SIGTERM)
        except Exception: return False
    for _ in range(20):
        time.sleep(0.25)
        if not _pid_alive(pid):
            return True
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass
    return True


def open_in_bot_browser(url: str) -> None:
    """Chrome comum (sem automação) no perfil do bot, para você logar/criar conta num portal.
    Mesmo caminho do `vagas login --normal`: sites como o Indeed recusam o navegador automatizado."""
    if not re.match(r"^https?://", url or ""):
        raise RuntimeError("link inválido")
    if any(k in BROWSER_TASKS for k in running_tasks()):
        raise RuntimeError("o bot está usando o navegador agora; pare a tarefa antes de abrir o site")
    from .cli import _system_chrome
    s = load_settings()
    exe = _system_chrome(s)
    if not exe:
        raise RuntimeError("Google Chrome não encontrado")
    from .config import resolve
    detach = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True})
    subprocess.Popen([str(exe), f"--user-data-dir={resolve(s.browser.user_data_dir)}", "--no-first-run",
                      "--no-default-browser-check", "--new-window", url],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detach)


def tail_log(name: str, n: int = 200) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name or ""):
        return ""
    p = LOGS / name
    if not p.exists():
        return ""
    try:
        data = p.read_bytes()[-120_000:].decode("utf-8", errors="ignore")
    except Exception:
        return ""
    return "\n".join(data.splitlines()[-n:])


def recent_logs(limit: int = 15) -> list[dict]:
    out = []
    for p in sorted(LOGS.glob("ui-*.log"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        out.append({"name": p.name, "kind": p.name.split("-")[1], "at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"), "size": p.stat().st_size})
    return out


def serve(settings: Settings, port: int = 8765) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading as _th
    # o perfil leva ~2 s para carregar (pool de cursos): lê em segundo plano para a 1ª entrevista abrir na hora
    _th.Thread(target=lambda: Data(settings).profile(), name="perfil", daemon=True).start()

    class H(BaseHTTPRequestHandler):
        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

        def _file(self, path: Path, ctype: str | None = None):
            if not path.exists():
                self.send_response(404); self.end_headers(); return
            body = path.read_bytes(); ctype = ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

        def _guard(self, qs: dict, data) -> bool:
            """Pelo túnel (celular) só entra quem tem a chave; acesso local segue livre. True = pode seguir."""
            from . import remote
            if not remote.is_remote(self.headers):
                return True
            ok, set_cookie = remote.authorized(self.headers, qs, data.db)
            if not ok:
                body = remote.DENIED_HTML.encode("utf-8")
                self.send_response(401); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                return False
            if set_cookie:   # primeiro acesso com ?k=: grava o cookie e tira a chave da barra de endereço
                self.send_response(302); self.send_header("Set-Cookie", remote.cookie_header(data.db)); self.send_header("Location", urlparse(self.path).path or "/"); self.end_headers()
                return False
            return True

        def do_GET(self):
            u = urlparse(self.path); qs = {k: v[0] for k, v in parse_qs(u.query).items()}
            data = Data(load_settings())
            if not self._guard(qs, data):
                return
            try:
                if u.path in ("/", "/index.html"): return self._file(STATIC / "index.html", "text/html; charset=utf-8")
                if u.path == "/api/overview": return self._json(data.overview())
                if u.path == "/api/counts": return self._json(data.counts())
                if u.path == "/api/linkedin": return self._json(linkedin_state(data))
                if u.path == "/api/linkedin/status":
                    from . import linkedin_profile as lp
                    return self._json({"status": lp.status(), "running": list(running_tasks())})
                if u.path == "/api/linkedin/img":
                    from . import linkedin_profile as lp
                    name = qs.get("name", "")
                    if not re.fullmatch(r"[\w.-]+\.(png|jpe?g|webp)", name):
                        return self._json({"error": "arquivo inválido"}, 400)
                    return self._file(lp.IMG / name)
                if u.path == "/api/jobs": return self._json(data.jobs(qs.get("status"), qs.get("q"), int(qs.get("limit", 300)), qs.get("today") == "1"))
                if u.path.startswith("/api/job/"):
                    j = data.job(unquote(u.path[len("/api/job/"):])); return self._json(j) if j else self._json({"error": "não encontrada"}, 404)
                if u.path == "/api/applications": return self._json(data.applications())
                if u.path == "/api/pending": return self._json(data.pending())
                if u.path == "/api/responses": return self._json(data.responses())
                if u.path == "/api/interviews":
                    from . import interview
                    return self._json({"items": interview.list_interviews(data.db)})
                if u.path == "/api/interview":
                    from . import interview
                    iv = interview.get(data.db, int(qs.get("id", 0)), data.profile())
                    if iv:   # opções de modelo da preparação, com custo estimado e saldo do teto do mês
                        iv["prep_options"] = interview.prep_models(data.s)
                        iv["budget"] = interview.budget_left(data.db, data.s)
                    return self._json(iv) if iv else self._json({"error": "entrevista não encontrada"}, 404)
                if u.path == "/api/courses": return self._json(data.courses())
                if u.path == "/api/llm": return self._json(data.llm())
                if u.path == "/api/events": return self._json(data.events(int(qs.get("limit", 200))))
                if u.path == "/api/settings": return self._json(data.settings())
                if u.path == "/api/tasks": return self._json({"running": running_tasks(), "recent": recent_logs()})
                if u.path == "/api/log": return self._json({"name": qs.get("name", ""), "text": tail_log(qs.get("name", ""), int(qs.get("tail", 200)))})
                if u.path == "/file":
                    rel = qs.get("p", ""); target = (DATA_DIR / rel).resolve()
                    if not str(target).startswith(str(DATA_DIR.resolve())): self.send_response(403); self.end_headers(); return
                    return self._file(target)
                self.send_response(404); self.end_headers()
            except Exception as e:
                self._json({"error": str(e)}, 500)

        def do_POST(self):
            u = urlparse(self.path); n = int(self.headers.get("Content-Length") or 0)
            try: payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception: payload = {}
            from . import remote
            if remote.is_remote(self.headers) and not remote.authorized(self.headers, {}, Data(load_settings()).db)[0]:
                return self._json({"error": "acesso negado"}, 401)   # ação pelo túnel só com o cookie da chave
            data = Data(load_settings())
            try:
                if u.path == "/api/task":
                    try:
                        return self._json(start_task(payload.get("kind", ""), payload.get("args") or {}))
                    except RuntimeError as e:
                        return self._json({"error": str(e)}, 409)
                if u.path == "/api/stop":
                    return self._json({"ok": stop_task(payload.get("kind", ""), bool(payload.get("force")))})
                if u.path == "/api/answer":
                    ans = str(payload.get("answer") or "").strip()
                    if not ans:
                        return self._json({"error": "escreva uma resposta antes de enviar"}, 400)
                    data.answer(int(payload["id"]), ans); return self._json({"ok": True})
                if u.path == "/api/pending/dismiss":
                    data.dismiss_pending(int(payload["id"]), bool(payload.get("skip_job"))); return self._json({"ok": True})
                if u.path == "/api/response/done":
                    data.set_response_done(int(payload["id"]), bool(payload.get("done", True))); return self._json({"ok": True})
                if u.path == "/api/interview/new":
                    # caixa de conversa da tela Entrevistas: mensagem do recrutador ou "empresa, vaga, quando"
                    from . import interview
                    text = str(payload.get("text") or "").strip()
                    if len(text) < 3:
                        return self._json({"error": "escreva a empresa e a vaga, ou cole a mensagem do recrutador"}, 400)
                    return self._json({"id": interview.create_from_text(data.db, text)})
                if u.path == "/api/interview/update":
                    from . import interview
                    interview.update(data.db, int(payload["id"]), payload); return self._json({"ok": True})
                if u.path.startswith("/api/linkedin/"):
                    return self._json(*linkedin_post(u.path.rsplit("/", 1)[1], payload, data))
                if u.path == "/api/interview/prep":
                    # preparação por IA (opcional): só fatos do perfil, respeita o teto mensal
                    from . import interview
                    from .llm import LLM
                    model = str(payload.get("model") or "").strip() or data.s.llm.cheap_model
                    if model not in data.s.llm.prep_models and model != data.s.llm.cheap_model:
                        return self._json({"error": f"modelo não permitido: {model} (lista em settings.yaml, llm.prep_models)"}, 400)
                    try:
                        res = interview.make_prep(data.db, int(payload["id"]), LLM(data.s.llm, data.db), data.profile(), model=model)
                    except Exception as ex:
                        return self._json({"error": f"IA indisponível ({type(ex).__name__}: {str(ex)[:120]})"}, 400)
                    res["budget"] = interview.budget_left(data.db, data.s)
                    return self._json(res)
                if u.path == "/api/response/send":
                    # resposta ao recrutador: só sai com este clique (ou o botão "Enviar" do Telegram)
                    from . import nextsteps
                    rid = int(payload["id"])
                    if isinstance(payload.get("draft"), str) and payload["draft"].strip():
                        data.db.conn.execute("UPDATE email_responses SET reply_draft=? WHERE id=?", (payload["draft"].strip(), rid))
                        data.db.conn.commit()
                    res = nextsteps.send_reply(data.db, rid)
                    return self._json({"ok": True, "result": res} if res == "enviado" else {"error": res}, 200 if res == "enviado" else 400)
                if u.path == "/api/pending/resolved":
                    data.resolve_pending(int(payload["id"])); return self._json({"ok": True})
                if u.path == "/api/open":
                    try:
                        open_in_bot_browser(str(payload.get("url") or "")); return self._json({"ok": True})
                    except RuntimeError as e:
                        return self._json({"error": str(e)}, 409)
                if u.path == "/api/course":
                    data.set_course(payload["stack"], payload["name"], payload["status"]); return self._json({"ok": True})
                m = re.match(r"^/api/job/(.+)/(skip|requeue)$", u.path)
                if m:
                    jid, action = unquote(m.group(1)), m.group(2)
                    if action == "skip": data.db.set_status(jid, "skipped", "pulada manualmente (painel)")
                    else:
                        j = data.db.get_job(jid)
                        if j: j.status, j.reason, j.attempts = "queued", None, 0; data.db.save_job(j)
                    return self._json({"ok": True})
                self._json({"error": "rota desconhecida"}, 404)
            except Exception as e:
                self._json({"error": str(e)}, 500)

        def log_message(self, *a):
            pass

    # link para o celular: túnel da Cloudflare + chave; o link vai para o Telegram assim que o túnel sobe
    if settings.remote_access:
        try:
            from . import remote
            from .notify import notify
            db = Data(settings).db
            if remote.start_tunnel(port, db, on_url=lambda link: notify(f"📱 Painel no celular (link novo a cada início, não compartilhe):\n{link}")):
                print("acesso remoto: abrindo túnel (o link vai para o Telegram)")
        except Exception as e:
            print(f"acesso remoto: não iniciou ({e})")
    # comandos do Telegram rodam junto com o painel (só se TELEGRAM_BOT_TOKEN/CHAT_ID estiverem no .env)
    try:
        from .telegram_bot import start_in_background
        if start_in_background():
            print("telegram: comandos ativos (/ajuda no seu chat)")
    except Exception as e:
        print(f"telegram: não iniciou ({e})")
    # só esta máquina: o painel mostra dados pessoais e dispara candidaturas, não pode ficar aberto na rede
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
