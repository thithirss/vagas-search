"""Acesso ao painel pelo celular: túnel rápido da Cloudflare (`tools/cloudflared.exe`) + chave secreta.

Ao subir, o painel abre `cloudflared tunnel --url http://127.0.0.1:<porta>`, lê o endereço
`https://<algo>.trycloudflare.com` que ele imprime e manda para o Telegram já com a chave (`?k=...`).
O endereço muda a cada início; a chave fica no banco (`kv remote_key`) e só muda se for apagada.

Proteção: toda requisição que chega pelo túnel traz o cabeçalho `Cf-Connecting-Ip`; sem a chave (no `?k=`
ou no cookie que o primeiro acesso grava) o painel responde 401. Acesso local (127.0.0.1, sem esse cabeçalho)
continua livre, como antes.
"""
from __future__ import annotations

import hmac
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

from .config import ROOT

EXE = ROOT / "tools" / "cloudflared.exe"
COOKIE = "vagas_k"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def key(db) -> str:
    k = db.kv_get("remote_key")
    if not k:
        k = secrets.token_urlsafe(24)
        db.kv_set("remote_key", k)
    return k


def is_remote(headers) -> bool:
    return bool(headers.get("Cf-Connecting-Ip") or headers.get("Cf-Ray"))


def authorized(headers, query: dict, db) -> tuple[bool, bool]:
    """(liberado, gravar_cookie). Compara em tempo constante."""
    k = key(db)
    given = query.get("k") or ""
    if given and hmac.compare_digest(given, k):
        return True, True
    for part in (headers.get("Cookie") or "").split(";"):
        name, _, val = part.strip().partition("=")
        if name == COOKIE and hmac.compare_digest(val, k):
            return True, False
    return False, False


def cookie_header(db) -> str:
    return f"{COOKIE}={key(db)}; Path=/; Max-Age=2592000; HttpOnly; Secure; SameSite=Lax"


DENIED_HTML = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
               "<title>Acesso negado</title><body style='font-family:system-ui;padding:40px;color:#333'>"
               "<h2>Acesso negado</h2><p>Abra o painel pelo link que chegou no seu Telegram (/painel reenvia o link).</p>")


def _kill_old_tunnels() -> None:
    """Túnel que sobrou de um painel anterior (encerrado à força) segura recursos: encerra pelo caminho do exe."""
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"name='cloudflared.exe'\" | Where-Object { $_.ExecutablePath -eq '"
              + str(EXE) + "' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    except Exception:
        pass


def start_tunnel(port: int, db, on_url=None) -> bool:
    """Abre o túnel em segundo plano; quando o endereço aparece, grava em kv e chama on_url(link_com_chave)."""
    if not EXE.exists():
        return False
    _kill_old_tunnels()
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    proc = subprocess.Popen([str(EXE), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", creationflags=flags)

    def watch():
        sent = False
        for line in proc.stdout:
            m = URL_RE.search(line)
            if m and not sent:
                sent = True
                url = m.group(0)
                link = f"{url}/?k={key(db)}"
                db.kv_set("remote_url", url)
                db.kv_set("remote_url_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
                if on_url:
                    try:
                        on_url(link)
                    except Exception:
                        pass
    threading.Thread(target=watch, name="cloudflared", daemon=True).start()
    return True


def current_link(db) -> str | None:
    url = db.kv_get("remote_url")
    return f"{url}/?k={key(db)}" if url else None
