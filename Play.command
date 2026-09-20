#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
./.venv/bin/python -m controller play
if [ "$?" -ne 0 ]; then
  read -r '?Press Return to close.'
fi
