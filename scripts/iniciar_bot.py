"""Inicialização local por clique; usa o controle de processos já existente no painel."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
# O painel é local; não encaminhar estas chamadas pelo proxy da sessão.
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
WINDOWS = os.name == "nt"


def lock_file(handle) -> None:
    """Trava exclusiva (espera) para dois cliques seguidos não abrirem dois painéis."""
    if WINDOWS:
        import msvcrt
        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:   # LK_LOCK desiste após ~10 s; continua esperando
                time.sleep(0.5)
    import fcntl
    fcntl.flock(handle, fcntl.LOCK_EX)


def api(path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(URL + path, data=body, headers={"Content-Type": "application/json"})
    try:
        with HTTP.open(request, timeout=3) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            message = json.load(error).get("error", str(error))
        except (ValueError, AttributeError):
            message = str(error)
        raise RuntimeError(message) from error


def dashboard_ready() -> bool:
    try:
        result = api("/api/tasks")
        return isinstance(result.get("running"), dict) and isinstance(result.get("recent"), list)
    except (OSError, ValueError, RuntimeError, AttributeError):
        return False


def ensure_dashboard(logs: Path) -> None:
    if dashboard_ready():
        return
    log_path = logs / "inicializacao-painel.log"
    detach = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if WINDOWS
              else {"start_new_session": True})
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    with log_path.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "vagas.cli", "dashboard"],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            close_fds=True, env=env, **detach,
        )
    for _ in range(40):
        if dashboard_ready():
            return
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    raise RuntimeError(f"Não foi possível abrir o painel. Consulte {log_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inicia o bot e abre o painel local.")
    parser.add_argument("--verificar", action="store_true", help="confere o ambiente sem iniciar buscas/candidaturas")
    parser.add_argument("--so-painel", action="store_true", help="só abre o painel, sem iniciar o bot")
    args = parser.parse_args()
    os.chdir(ROOT)
    from vagas.config import load_settings
    from vagas.resume.profile import load_profile

    settings = load_settings()
    load_profile(settings.profile_path)
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Preencha OPENAI_API_KEY no arquivo .env antes de iniciar.")
    if args.verificar:
        print("Ambiente, configuração, perfil e presença da chave conferidos. Nenhuma busca ou candidatura iniciada.")
        print(f"Janela diária: {' às '.join(settings.limits.active_hours)}. Painel: {URL}")
        return

    logs = ROOT / "data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "inicializacao.lock").open("a+") as lock:
        lock_file(lock)
        ensure_dashboard(logs)
        running = api("/api/tasks")["running"]
        if args.so_painel:
            print(f"Painel: {URL} (o bot não foi iniciado; use os botões do painel)")
            if not webbrowser.open(URL):
                print(f"Abra o navegador e acesse {URL}")
            return
        if "run" in running:
            print("O bot já está rodando. Abrindo o painel existente.")
        else:
            info = api("/api/task", {"kind": "run", "args": {}})
            print(f"Bot iniciado. Log: {logs / info['log']}")
        print(f"Painel: {URL}")
        print("O bot envia candidaturas e respeita a janela e os limites configurados.")
        print("Para encerrar, clique em Parar no painel. Fechar a aba não encerra o bot.")
        if not webbrowser.open(URL):
            print(f"Abra o navegador e acesse {URL}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Falha na inicialização: {error}", file=sys.stderr)
        sys.exit(1)
