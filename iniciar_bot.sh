#!/usr/bin/env bash
set -u

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$PROJECT_DIR" || exit 1

PYTHON="$PROJECT_DIR/.venv-linux/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo 'Ambiente Python não encontrado em .venv-linux. Consulte INSTRUCOES_LOCAL.md.'
    if [[ -t 0 ]]; then read -r -p 'Pressione Enter para fechar… ' _; fi
    exit 1
fi

export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1
"$PYTHON" "$PROJECT_DIR/scripts/iniciar_bot.py" "$@"
result=$?
if [[ "$result" -ne 0 && -t 0 ]]; then
    read -r -p 'Pressione Enter para fechar… ' _
fi
exit "$result"
