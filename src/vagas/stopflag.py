"""Parada graciosa pedida pelo painel.

O painel grava `data/logs/stop-<pid>.flag`; o processo confere nas pausas (`nap`) e entre vagas
(`requested`) e sai limpo: termina a vaga atual, fecha o Chrome e registra no log. Um vigia em
segundo plano interrompe o processo (como um Ctrl+C) se ele não chegar a um ponto seguro dentro
do tempo de graça — por exemplo, preso num formulário. Matar na hora deixava a vaga presa em
`in_progress` e o log terminava sem explicação.
"""
from __future__ import annotations

import _thread
import os
import threading
import time

from .config import DATA_DIR

LOGS = DATA_DIR / "logs"
_started = False


def flag_path(pid: int | None = None):
    # O painel passa o caminho em VAGAS_STOP_FLAG: no Windows o python.exe do .venv é só um lançador que abre
    # o interpretador de verdade como OUTRO processo, então o PID que o painel conhece não é o os.getpid() daqui.
    if pid is None and os.environ.get("VAGAS_STOP_FLAG"):
        from pathlib import Path
        return Path(os.environ["VAGAS_STOP_FLAG"])
    return LOGS / f"stop-{pid or os.getpid()}.flag"


def requested() -> bool:
    try:
        return flag_path().exists()
    except OSError:
        return False


class StopRequested(KeyboardInterrupt):
    """Herda de KeyboardInterrupt para os mesmos `except`/`finally` que tratam Ctrl+C fecharem tudo direito."""


def nap(secs: float) -> None:
    """time.sleep que acorda em até 1 s quando o painel pede para parar."""
    end = time.monotonic() + max(0.0, secs)
    while True:
        if requested():
            raise StopRequested()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(1.0, left))


def start_watchdog(grace_seconds: float) -> None:
    """Se o pedido de parada chegar e o processo não sair sozinho em `grace_seconds`, interrompe o fluxo principal."""
    global _started
    if _started:
        return
    _started = True
    # Processo aberto pelo painel no Windows (CREATE_NEW_PROCESS_GROUP) nasce com o Ctrl+C ignorado, e aí
    # interrupt_main() não faz nada; religa o tratador padrão (só funciona na thread principal)
    try:
        import signal
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except (ValueError, OSError):
        pass

    def watch():
        while not requested():
            time.sleep(1)
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            time.sleep(1)
        _thread.interrupt_main()

    threading.Thread(target=watch, name="vagas-stop-watchdog", daemon=True).start()


def clear(pid: int | None = None) -> None:
    try:
        flag_path(pid).unlink()
    except OSError:
        pass
