#!/bin/bash
# ==================================================
# 投稿スケジューラー
# cronで2時間おきに実行: 0 8,10,12,14,16,18,20,22,0 * * * bash /path/to/scripts/post-scheduler.sh
# キューから1件ずつ取り出して投稿
# ==================================================

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd -W)"
PYTHON="/c/Users/fkgyo/AppData/Local/Programs/Python/Python312/python.exe"
if [ ! -f "$PYTHON" ]; then
    PYTHON="python"
    command -v python3 &> /dev/null && PYTHON="python3"
fi

export PATH="/c/Users/fkgyo/AppData/Local/Programs/Python/Python312:/c/Users/fkgyo/AppData/Local/Programs/Python/Python312/Scripts:$PATH"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ポスター実行"
$PYTHON "$PROJECT_DIR/agents/poster.py"
