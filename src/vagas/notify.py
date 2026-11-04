"""Notificações opcionais via Telegram (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID no .env)."""
from __future__ import annotations

import json
import os
import urllib.request

from dotenv import dotenv_values

from .config import ROOT


def notify(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        # O painel pode estar aberto desde antes da configuração do Telegram.
        # Não deixar variáveis vazias herdadas esconderem os valores novos do arquivo.
        values = dotenv_values(ROOT / ".env")
        token = token or values.get("TELEGRAM_BOT_TOKEN")
        chat = chat or values.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat, "text": text[:3900], "disable_web_page_preview": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception:
        return False
