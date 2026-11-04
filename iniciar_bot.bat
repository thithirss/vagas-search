@echo off
rem Equivalente Windows do iniciar_bot.sh: abre o painel e inicia o bot (duplo clique).
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
"%PYTHON%" "%~dp0scripts\iniciar_bot.py" %*
set result=%errorlevel%
if not "%result%"=="0" pause
exit /b %result%
