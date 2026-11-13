"""Motores de IA que o USUÁRIO escolhe para tarefas longas (tela LinkedIn).

- openai      : API da OpenAI (a mesma do resto do bot; conta no teto do mês)
- claude_code : Claude Code instalado e logado (`claude -p`), usa a assinatura da pessoa
- anthropic   : API da Anthropic (ANTHROPIC_API_KEY no .env)
- codex       : Codex CLI instalado e logado (`codex exec`), usa a assinatura do ChatGPT

Todos recebem o MESMO pedido e devolvem o MESMO JSON; a checagem de fatos roda depois, no bot.
Os CLIs rodam numa pasta vazia (data/linkedin/work) para não carregarem o projeto inteiro como contexto,
sem ferramentas de escrita, e o prompt vai pela entrada padrão (sem limite de linha de comando do Windows).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from .config import DATA_DIR

WORK = DATA_DIR / "linkedin" / "work"

LABELS = {
    "openai": "ChatGPT (API da OpenAI)",
    "claude_code": "Claude (Claude Code instalado)",
    "anthropic": "Claude (API da Anthropic)",
    "codex": "Codex (CLI da OpenAI)",
}


def _which(name: str) -> str | None:
    return shutil.which(name)


# instalação pelo painel (botão "Instalar"): pacote npm oficial de cada CLI e onde cada um guarda o login
CLI = {
    "codex": {"exe": "codex", "pkg": "@openai/codex", "auth": Path.home() / ".codex" / "auth.json",
              "login": ["login"], "login_hint": "entre com a sua conta do ChatGPT na janela que abrir"},
    "claude_code": {"exe": "claude", "pkg": "@anthropic-ai/claude-code", "auth": Path.home() / ".claude" / ".credentials.json",
                    "login": [], "login_hint": "na janela que abrir, digite /login e entre com a sua conta da Anthropic"},
}
INSTALL_LOG = DATA_DIR / "linkedin" / "install.log"


def logged_in(tool: str) -> bool | None:
    c = CLI.get(tool)
    return c["auth"].exists() if c else None


def install(tool: str, timeout: int = 900) -> str:
    """`npm i -g <pacote>` (precisa do Node.js). Roda no processo do painel: nunca chamar a partir do app do Claude,
    porque lá a AppData é isolada e o executável iria para o lugar errado (CLAUDE.md, armadilha do MSIX)."""
    c = CLI[tool]
    npm = _which("npm")
    if not npm:
        raise RuntimeError("Node.js não instalado: baixe em https://nodejs.org (versão LTS), reinicie o painel e tente de novo")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    p = subprocess.run([npm, "install", "-g", c["pkg"]], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=flags)
    INSTALL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(INSTALL_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n=== npm install -g {c['pkg']} (código {p.returncode})\n{p.stdout[-4000:]}\n{p.stderr[-4000:]}\n")
    if p.returncode != 0:
        raise RuntimeError(f"npm falhou (código {p.returncode}): {(p.stderr or p.stdout).strip()[-300:]}")
    if not _which(c["exe"]):
        raise RuntimeError("instalou, mas o comando não apareceu no PATH: reinicie o painel (abrir_painel.bat)")
    return "instalado" + ("" if logged_in(tool) else f"; falta o login: {c['login_hint']}")


def open_login(tool: str) -> str:
    """Abre uma janela de terminal com o login do CLI (é interativo: a pessoa entra com a própria conta)."""
    c = CLI[tool]
    exe = _which(c["exe"])
    if not exe:
        raise RuntimeError("instale primeiro")
    if os.name == "nt":
        subprocess.Popen(["cmd", "/c", "start", f"Login {c['exe']}", "cmd", "/k", exe, *c["login"]],
                         creationflags=subprocess.CREATE_NO_WINDOW)
        return c["login_hint"]
    return f"rode no terminal: {c['exe']} {' '.join(c['login'])}".strip()


def available(settings) -> list[dict]:
    """Motores e se estão prontos nesta máquina (com o motivo quando não estão)."""
    cfg = settings.linkedin
    out = []
    key = bool(os.environ.get("OPENAI_API_KEY"))
    out.append({"id": "openai", "label": LABELS["openai"], "ok": key, "images": key, "billing": "api",
                "why": "" if key else "falta OPENAI_API_KEY no .env", "model": cfg.openai_model})
    npm = bool(_which("npm"))

    def cli(tool: str, label_model: str) -> dict:
        installed = bool(_which(CLI[tool]["exe"]))
        logged = logged_in(tool)
        why = ("" if installed and logged else
               "instalado, falta fazer login" if installed else
               "não instalado" + ("" if npm else " (e falta o Node.js: nodejs.org)"))
        return {"id": tool, "label": LABELS[tool], "ok": installed and bool(logged), "images": False, "billing": "assinatura",
                "why": why, "model": label_model, "installed": installed, "logged_in": logged, "can_install": npm and not installed,
                "can_login": installed and not logged, "npm": npm}

    out.append(cli("claude_code", cfg.claude_code_model or "padrão do Claude Code"))
    ak = bool(os.environ.get("ANTHROPIC_API_KEY"))
    out.append({"id": "anthropic", "label": LABELS["anthropic"], "ok": ak, "images": False, "billing": "api",
                "why": "" if ak else "falta ANTHROPIC_API_KEY no .env", "model": cfg.anthropic_model})
    out.append(cli("codex", cfg.codex_model or "padrão do Codex"))
    return out


def parse_json(text: str) -> dict:
    """Extrai o primeiro objeto JSON da resposta (CLIs às vezes cercam com ``` ou texto)."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        return json.loads(t)
    except Exception:
        pass
    start = t.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except Exception:
                        break
        start = t.find("{", start + 1)
    raise ValueError("a IA não devolveu um JSON válido")


def _run_cli(cmd: list[str], prompt: str, timeout: int, out_file: Path | None = None) -> str:
    WORK.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(WORK), timeout=timeout, creationflags=flags)
    if out_file is not None and out_file.exists() and out_file.stat().st_size:
        return out_file.read_text(encoding="utf-8", errors="replace")
    if p.returncode != 0 and not (p.stdout or "").strip():
        raise RuntimeError(f"{cmd[0]} saiu com código {p.returncode}: {(p.stderr or '').strip()[:300]}")
    return p.stdout


def run(engine: str, *, system: str, user: str, settings, llm=None, purpose: str = "linkedin",
        max_tokens: int = 6000, model: str | None = None) -> dict:
    """Roda o pedido no motor escolhido e devolve {"data": dict, "engine", "model"}."""
    cfg = settings.linkedin
    prompt = f"{system}\n\n---\n\n{user}\n\nResponda SOMENTE com o JSON pedido, sem texto antes ou depois."
    if engine == "openai":
        if llm is None:
            raise RuntimeError("API da OpenAI indisponível")
        m = model or cfg.openai_model
        data = llm.json(model=m, system=system, user=user, purpose=purpose, temperature=0.4, max_tokens=max_tokens)
        return {"data": data, "engine": engine, "model": m}
    if engine == "claude_code":
        exe = _which("claude")
        if not exe:
            raise RuntimeError("Claude Code não instalado")
        cmd = [exe, "-p", "--output-format", "json"]
        if model or cfg.claude_code_model:
            cmd += ["--model", model or cfg.claude_code_model]
        raw = _run_cli(cmd, prompt, cfg.cli_timeout_s)
        try:
            env = json.loads(raw)
            text = env.get("result") or ""
            if env.get("is_error"):
                raise RuntimeError(f"Claude Code: {text[:300]}")
        except json.JSONDecodeError:
            text = raw
        return {"data": parse_json(text), "engine": engine, "model": model or cfg.claude_code_model or "claude-code"}
    if engine == "codex":
        exe = _which("codex")
        if not exe:
            raise RuntimeError("Codex não instalado")
        WORK.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".txt", dir=str(WORK)); os.close(fd)
        out = Path(tmp)
        try:
            cmd = [exe, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--output-last-message", str(out)]
            if model or cfg.codex_model:
                cmd += ["--model", model or cfg.codex_model]
            cmd.append("-")
            text = _run_cli(cmd, prompt, cfg.cli_timeout_s, out_file=out)
        finally:
            out.unlink(missing_ok=True)
        return {"data": parse_json(text), "engine": engine, "model": model or cfg.codex_model or "codex"}
    if engine == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("falta ANTHROPIC_API_KEY no .env")
        m = model or cfg.anthropic_model
        if llm is not None and llm.budget is not None and not llm.budget.allows(purpose):
            from .budget import BudgetExceeded
            raise BudgetExceeded(llm.budget.why_blocked(purpose))
        body = json.dumps({"model": m, "max_tokens": max_tokens, "system": system,
                           "messages": [{"role": "user", "content": user + "\n\nResponda SOMENTE com o JSON pedido."}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=cfg.cli_timeout_s) as r:
            resp = json.loads(r.read().decode("utf-8"))
        text = "".join(b.get("text", "") for b in resp.get("content") or [] if b.get("type") == "text")
        u = resp.get("usage") or {}
        if llm is not None and llm.db is not None:
            llm.db.add_llm_usage(m, purpose, int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                                 int(u.get("cache_read_input_tokens") or 0))
        return {"data": parse_json(text), "engine": engine, "model": m}
    raise ValueError(f"motor desconhecido: {engine}")
