#!/usr/bin/env bash
# The whole bargames end-to-end suite, one command.
#
#   tests/e2e/run.sh                          # everything
#   tests/e2e/run.sh -k barrier               # a slice
#   tests/e2e/run.sh tests/e2e/test_admin.py  # one file
#   tests/e2e/run.sh --headed                 # watch it happen
#
# Needs the project venv with playwright installed:
#   uv pip install --python .venv/bin/python playwright pytest-playwright
#   .venv/bin/playwright install chromium
#
# It boots its own uvicorn on a free port with the local-dev environment
# (cookie path "/", Secure off, a temp selfie volume and state file), so
# nothing here touches a running instance.
set -u
cd "$(dirname "$0")/../.."
PYTEST=(.venv/bin/pytest -q --tb=short -p no:cacheprovider)
if [[ " $* " == *" tests/"* ]]; then
  exec "${PYTEST[@]}" "$@"
else
  exec "${PYTEST[@]}" tests/e2e "$@"
fi
