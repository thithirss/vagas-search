"""Queda de internet não é falha da vaga (2026-11-19).

Em 18/11, das 16h às 18h, a internet caiu e o bot continuou: 317 vagas viraram `failed` com
"net::ERR_INTERNET_DISCONNECTED" (cada uma gastou as tentativas em segundos). Agora erro de rede com a internet
fora do ar devolve a tentativa, deixa a vaga como estava e o bot espera a conexão voltar.
"""
from __future__ import annotations

import re
import socket
import time

from rich.console import Console

console = Console()

# erros do Chromium que dizem "o computador está sem rede" (não "o site caiu")
NET_ERROR_RE = re.compile(r"ERR_INTERNET_DISCONNECTED|ERR_NETWORK_CHANGED|ERR_NETWORK_IO_SUSPENDED|ERR_ADDRESS_UNREACHABLE|"
                          r"ERR_NAME_NOT_RESOLVED|ERR_NAME_RESOLUTION_FAILED|ERR_CONNECTION_TIMED_OUT|ERR_TIMED_OUT|"
                          r"getaddrinfo failed|Temporary failure in name resolution|Network is unreachable|"
                          r"Failed to establish a new connection|Max retries exceeded", re.I)

_PROBES = (("1.1.1.1", 443), ("8.8.8.8", 53), ("www.google.com", 443))


def online(timeout: float = 3.0) -> bool:
    for host, port in _PROBES:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def is_network_error(err) -> bool:
    return bool(NET_ERROR_RE.search(str(err or "")))


def offline_error(err) -> bool:
    """Erro de rede E a internet realmente fora do ar (um site com DNS quebrado não conta)."""
    return is_network_error(err) and not online()


def wait_online(check_every: float = 30, log=console.print) -> None:
    """Bloqueia até a internet voltar (respeita o Parar do painel)."""
    from . import stopflag
    if online():
        return
    log("[yellow]sem internet: as vagas ficam como estão; espero a conexão voltar[/]")
    start = time.time()
    while not online():
        if stopflag.requested():
            raise stopflag.StopRequested()
        stopflag.nap(check_every)
    log(f"[green]internet de volta depois de {int((time.time() - start) // 60)} min[/]")
