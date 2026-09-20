#!/bin/zsh
cd -- "${0:A:h}"
./.venv/bin/python scripts/decision_report.py --open
if [[ $? -ne 0 ]]; then
  read -r "reply?Press Return to close."
fi
