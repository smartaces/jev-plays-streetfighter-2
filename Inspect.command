#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
./.venv/bin/python -m controller inspect
if [ "$?" -ne 0 ]; then
  read -r '?Press Return to close.'
fi
