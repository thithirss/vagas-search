@echo off
rem Só abre o painel (http://127.0.0.1:8765), sem iniciar o bot. O painel continua rodando em segundo plano.
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo Ambiente Python nao encontrado em .venv. Consulte INSTRUCOES_WINDOWS.md.
    pause
    exit /b 1
)

set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
"%PYTHON%" "%~dp0scripts\iniciar_bot.py" --so-painel
if errorlevel 1 pause
