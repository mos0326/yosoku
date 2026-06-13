#!/bin/bash
# Claude Code on the web 用 SessionStart フック。
# 依存(本体 + 開発: pytest/ruff)をインストールし、テスト・Lint を実行可能にする。
set -euo pipefail

# リモート(Claude Code on the web)以外では何もしない。
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# editable + dev extras を導入(再実行しても安全)。
python -m pip install --upgrade pip >/dev/null 2>&1 || true
python -m pip install -e ".[dev]"
