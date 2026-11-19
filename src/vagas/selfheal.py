"""Agente que corrige falhas de inscrição sozinho (pedido do usuário, 2026-11-19).

"Eu não quero ter que ficar falando todo dia que a assertividade está ruim… quando você pegar alguma falha, analise e já
mande para o Claude Code resolver no repositório… sem torrar meus tokens."

Fluxo (comando `vagas selfheal`, disparado pelo `vagas run` em segundo plano):
1. `candidates()` agrupa as falhas recentes por ASSINATURA (motivo normalizado + site) e descarta o que só uma pessoa
   resolve (conta/login, captcha, verificação, sem internet, vaga encerrada…). Assinatura já tratada não volta.
2. Escolhe a mais frequente (>= `selfheal.min_occurrences`) e monta um pacote: motivos, endereços, trechos do log e os
   dumps de `data/learn` (HTML/JSON/PNG das etapas) + telas de `data/audit`.
3. Copia `src/vagas`, `tests` e `CLAUDE.md` para `data/selfheal/backup/<ts>` e roda o Claude Code sem interface
   (`claude -p`, modelo e nº de turnos limitados, só leitura/edição e o comando de testes liberados; nada de git).
4. Roda TODOS os testes de novo aqui. Passou → mantém, recoloca as vagas daquela falha na fila, avisa no Telegram e
   reinicia a leva pelo painel. Falhou → devolve os arquivos do backup e registra.
Limites: `selfheal.max_runs_per_day`, `min_hours_between`, `max_turns`, `timeout_minutes`. Nada é enviado ao GitHub.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from .config import DATA_DIR, ROOT

SH_DIR = DATA_DIR / "selfheal"
LOCK = SH_DIR / "running.lock"

# falhas que só a pessoa resolve (ou que não são do robô): o agente nem olha
HUMAN_RE = re.compile(
    r"exige (conta|login)|login no portal|crie a conta|captcha|checkpoint|verifica[cç][aã]o|sem internet|ERR_INTERNET|"
    r"encerrad|n[aã]o est[aá] mais dispon|pergunta\(s\) pendentes|em pausa|teto|blacklist|exige idioma|já aplicad|"
    r"candidatura anterior|mesma vaga já|vaga afirmativa|currículo falhou: .*(budget|quota|orçamento)", re.I)


def _host(url: str | None) -> str:
    h = urlparse(url or "").netloc.lower().replace("www.", "")
    parts = h.split(".")
    if len(parts) >= 3 and parts[-2] in ("com", "net", "org", "gov") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if h else ""


def signature(reason: str | None, url: str | None, source: str = "", apply_type: str = "") -> str:
    r = re.sub(r"\d+", "N", reason or "")
    r = re.sub(r"'[^']*'|\"[^\"]*\"", "X", r)
    r = re.sub(r"\s+", " ", r).strip()[:70]
    return f"{r} @ {_host(url) or f'{source}/{apply_type}'}"


def candidates(db, settings) -> list[dict]:
    cfg = settings.selfheal
    since = (datetime.now() - timedelta(hours=cfg.window_hours)).isoformat(timespec="seconds")
    rows = db.conn.execute(
        "SELECT id, source, apply_type, company, title, reason, external_url, url, status FROM jobs "
        "WHERE status IN ('failed','queued') AND COALESCE(reason,'') != '' AND attempts >= 1 AND updated_at >= ?", (since,)).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        reason = r["reason"] or ""
        if HUMAN_RE.search(reason):
            continue
        sig = signature(reason, r["external_url"], r["source"], r["apply_type"])
        g = groups.setdefault(sig, {"signature": sig, "jobs": []})
        g["jobs"].append(dict(r))
    out = []
    for sig, g in groups.items():
        if len(g["jobs"]) < cfg.min_occurrences:
            continue
        if db.kv_get(f"selfheal:{sig}"):
            continue   # já tratada (corrigida, sem correção ou precisa de gente)
        # sem tela/HTML salvos nem trecho de log o agente só gastaria cota para dizer "sem evidência" (19/11)
        if not any(_artifacts(j["id"]) for j in g["jobs"][:6]):
            continue
        out.append(g)
    out.sort(key=lambda g: -len(g["jobs"]))
    return out


def _runs_today(db) -> int:
    try:
        d = json.loads(db.kv_get("selfheal_runs") or "{}")
    except ValueError:
        d = {}
    return int(d.get(datetime.now().date().isoformat(), 0))


def _count_run(db) -> None:
    try:
        d = json.loads(db.kv_get("selfheal_runs") or "{}")
    except ValueError:
        d = {}
    k = datetime.now().date().isoformat()
    d = {k: int(d.get(k, 0)) + 1}
    db.kv_set("selfheal_runs", json.dumps(d))
    db.kv_set("selfheal_last", datetime.now().isoformat(timespec="seconds"))


def due(db, settings) -> bool:
    cfg = settings.selfheal
    if not cfg.enabled or _running():
        return False
    if _runs_today(db) >= cfg.max_runs_per_day:
        return False
    last = db.kv_get("selfheal_last")
    if last and datetime.fromisoformat(last) + timedelta(hours=cfg.min_hours_between) > datetime.now():
        return False
    return bool(candidates(db, settings))


def _running() -> bool:
    if not LOCK.exists():
        return False
    # trava velha (processo morreu): 2 h
    return time.time() - LOCK.stat().st_mtime < 7200


def maybe_launch(db, settings, log=print) -> bool:
    """Chamado pelo laço do `vagas run`: dispara `vagas selfheal` destacado, sem segurar a leva."""
    try:
        if not due(db, settings):
            return False
    except Exception as e:
        log(f"[dim]selfheal: não deu para checar ({e})[/]")
        return False
    SH_DIR.mkdir(parents=True, exist_ok=True)
    flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0
    out = open(SH_DIR / "last_run.log", "w", encoding="utf-8")
    env = {**os.environ, "PYTHONUTF8": "1", "COLUMNS": "160"}
    env.pop("VAGAS_STOP_FLAG", None)   # o Parar da leva não pode derrubar o agente no meio
    subprocess.Popen([sys.executable, "-m", "vagas.cli", "selfheal"], cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, creationflags=flags, env=env)
    log("[cyan]🛠 agente de correção disparado em segundo plano (data/selfheal/last_run.log)[/]")
    return True


# ---------- pacote para o Claude Code ----------
def _log_excerpt(job_id: str, max_lines: int = 45) -> str:
    logs = sorted((DATA_DIR / "logs").glob("ui-run-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    for p in logs:
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        idx = [i for i, l in enumerate(lines) if job_id in l and "▶" in l]
        if idx:
            i = idx[-1]
            end = next((j for j in range(i + 1, min(len(lines), i + 200)) if "▶" in lines[j]), min(len(lines), i + max_lines))
            return f"[{p.name}]\n" + "\n".join(lines[i:end][:max_lines])
    return "(sem trecho no log)"


def _artifacts(job_id: str) -> list[str]:
    key = re.sub(r"[^a-zA-Z0-9_-]", "_", job_id)
    found = []
    for d in list((DATA_DIR / "learn").glob(f"*/{key}")) + list((DATA_DIR / "audit").glob(f"*/{key}")):
        for f in sorted(d.glob("step*.*")):
            found.append(str(f.relative_to(ROOT)).replace("\\", "/"))
    return found[:12]


def build_prompt(group: dict) -> str:
    tpl = (ROOT / ".claude" / "agents" / "corretor-de-falhas.md").read_text(encoding="utf-8")
    tpl = re.sub(r"\A---.*?---\s*", "", tpl, flags=re.S)   # frontmatter do agente
    parts = [tpl, "\n\n## Falha a analisar\n", f"Assinatura: `{group['signature']}` — {len(group['jobs'])} ocorrência(s).\n"]
    for j in group["jobs"][:4]:
        parts.append(f"\n### {j['id']} — {j['title']} · {j['company']}\n- motivo: {j['reason']}\n- endereço: {j['external_url'] or j['url']}\n"
                     f"- arquivos: {', '.join(_artifacts(j['id'])) or '(nenhum dump)'}\n\n```\n{_log_excerpt(j['id'])}\n```\n")
    return "".join(parts)


# ---------- backup / restauração ----------
_TREES = ("src/vagas", "tests")


def _backup() -> Path:
    b = SH_DIR / "backup" / datetime.now().strftime("%Y%m%d-%H%M%S")
    for t in _TREES:
        shutil.copytree(ROOT / t, b / t, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "CLAUDE.md", b / "CLAUDE.md")
    return b


def _restore(b: Path) -> None:
    for t in _TREES:
        cur = ROOT / t
        for f in cur.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts and not (b / t / f.relative_to(cur)).exists():
                f.unlink()   # arquivo novo criado pelo agente
        shutil.copytree(b / t, cur, dirs_exist_ok=True)
    shutil.copy2(b / "CLAUDE.md", ROOT / "CLAUDE.md")


def _changed(b: Path) -> list[str]:
    out = []
    for t in _TREES:
        cur = ROOT / t
        for f in cur.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            old = b / t / f.relative_to(cur)
            if not old.exists() or old.read_bytes() != f.read_bytes():
                out.append(str(f.relative_to(ROOT)).replace("\\", "/"))
    if (b / "CLAUDE.md").read_bytes() != (ROOT / "CLAUDE.md").read_bytes():
        out.append("CLAUDE.md")
    return out


def _tests() -> tuple[bool, str]:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900, creationflags=flags)
    ok = p.returncode == 0
    q = subprocess.run([sys.executable, "scripts/checa_nomes.py"], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300, creationflags=flags)
    ok = ok and q.returncode == 0
    return ok, ((p.stderr or "")[-1500:] + (q.stdout or "")[-500:])


def _claude(prompt: str, settings) -> dict:
    cfg = settings.selfheal
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("Claude Code não instalado")
    py = ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
    allowed = ["Read", "Grep", "Glob", "Edit", "Write", f"Bash({py} -m unittest:*)", f"Bash({py} scripts/checa_nomes.py:*)"]
    cmd = [exe, "-p", "--output-format", "json", "--model", cfg.model, "--max-turns", str(cfg.max_turns),
           "--permission-mode", "acceptEdits", "--allowedTools", ",".join(allowed),
           "--disallowedTools", "Bash(git:*),Bash(curl:*),Bash(pip:*),Bash(npm:*),WebFetch,WebSearch"]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
                       timeout=cfg.timeout_minutes * 60, creationflags=flags)
    try:
        env = json.loads(p.stdout or "{}")
    except ValueError:
        env = {"result": p.stdout, "is_error": True}
    text = env.get("result") or ""
    m = re.findall(r"\{[^{}]*\"status\"[^{}]*\}", text, flags=re.S)
    verdict = {}
    if m:
        try:
            verdict = json.loads(m[-1])
        except ValueError:
            verdict = {}
    verdict.setdefault("status", "no_fix" if not env.get("is_error") else "error")
    verdict.setdefault("summary", text[-600:])
    verdict["cost_usd"] = env.get("total_cost_usd")
    verdict["turns"] = env.get("num_turns")
    return verdict


def _restart_run(log) -> None:
    import urllib.request
    base = "http://127.0.0.1:8765"
    def post(path, data):
        req = urllib.request.Request(base + path, json.dumps(data).encode(), {"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=30))
    try:
        post("/api/stop", {"kind": "run"})
        for _ in range(40):
            time.sleep(5)
            st = json.load(urllib.request.urlopen(base + "/api/prefs", timeout=30))
            if not st.get("run_running"):
                break
        post("/api/task", {"kind": "run", "args": {}})
        log("leva reiniciada com o código novo")
    except Exception as e:
        log(f"não consegui reiniciar a leva pelo painel ({e}); vale no próximo reinício")


def run_once(db, settings, log=print) -> dict | None:
    from .notify import notify
    groups = candidates(db, settings)
    if not groups:
        log("nenhuma falha corrigível nova")
        return None
    SH_DIR.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    try:
        g = groups[0]
        _count_run(db)
        log(f"analisando: {g['signature']} ({len(g['jobs'])}x)")
        backup = _backup()
        prompt = build_prompt(g)
        (SH_DIR / "last_prompt.md").write_text(prompt, encoding="utf-8")
        try:
            verdict = _claude(prompt, settings)
        except Exception as e:
            verdict = {"status": "error", "summary": str(e)[:300]}
        changed = _changed(backup)
        tests_ok, test_out = (_tests() if changed else (True, ""))
        if changed and not tests_ok:
            _restore(backup)
            verdict["status"], verdict["summary"] = "reverted", f"{verdict.get('summary', '')[:300]} | testes falharam, mudança desfeita: {test_out[-400:]}"
            changed = []
        if verdict["status"] == "fixed" and not changed:
            verdict["status"] = "no_fix"
        record = {"at": datetime.now().isoformat(timespec="seconds"), "signature": g["signature"], "jobs": [j["id"] for j in g["jobs"]],
                  "files": changed, "backup": str(backup.relative_to(ROOT)), **verdict}
        db.kv_set(f"selfheal:{g['signature']}", json.dumps(record, ensure_ascii=False))
        with open(SH_DIR / "history.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        log(json.dumps(record, ensure_ascii=False, indent=1))
        if changed:
            ids = [j["id"] for j in g["jobs"]]
            db.conn.executemany("UPDATE jobs SET status='queued', reason=NULL, attempts=0 WHERE id=? AND status IN ('failed','queued')", [(i,) for i in ids])
            db.conn.commit()
            notify(f"🛠 Corrigi sozinho: {verdict.get('summary', '')[:300]}\nFalha: {g['signature']} ({len(ids)} vagas, de volta na fila)\n"
                   f"Arquivos: {', '.join(changed)[:200]}")
            if settings.selfheal.auto_restart:
                _restart_run(log)
        elif verdict["status"] == "human":
            notify(f"🙋 Falha que precisa de você: {g['signature']}\n{verdict.get('human_action') or verdict.get('summary', '')[:300]}")
        return record
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass
