"""vagas — auto-aplicador pessoal de vagas."""
import os
import sys

__version__ = "0.1.0"

# Navegadores do Playwright/Patchright dentro do projeto, não em %LOCALAPPDATA%\ms-playwright: no Windows o
# app do Claude isola a AppData (MSIX), e o que foi instalado por lá fica invisível para o bot aberto pelo atalho
# ("Executable doesn't exist" no headless shell ao gerar o PDF do currículo, 29/10).
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".playwright-browsers"))

if os.name == "nt":   # Windows: console/log em cp1252 quebra nos emojis e símbolos (▶, ⏳, ✓) das mensagens
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
