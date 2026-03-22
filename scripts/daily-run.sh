#!/bin/bash
# ==================================================
# 毎日の自動実行メインスクリプト
# cronで毎朝6:00に実行: 0 6 * * * bash /path/to/scripts/daily-run.sh
#
# 実行フロー:
# 1. KILL_SWITCH チェック
# 2. フェッチャー: 昨日の投稿メトリクス回収
# 3. アナリスト: メトリクス分析 → フィードバック生成
# 4. リサーチャー: テーマの穴を埋めるネタ収集
# 5. ライター: ネタ+フィードバックから10本の投稿を生成
# 6. スーパーバイザー: 全体チェック
# ==================================================

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd -W)"
AGENTS_DIR="$PROJECT_DIR/agents"
LOG_FILE="$PROJECT_DIR/state/daily-run.log"

PYTHON="/c/Users/fkgyo/AppData/Local/Programs/Python/Python312/python.exe"
if [ ! -f "$PYTHON" ]; then
    PYTHON="python"
    command -v python3 &> /dev/null && PYTHON="python3"
fi

# PATHにPythonを追加（Windows環境用）
export PATH="/c/Users/fkgyo/AppData/Local/Programs/Python/Python312:/c/Users/fkgyo/AppData/Local/Programs/Python/Python312/Scripts:$PATH"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "=========================================="
log "Daily Run 開始"
log "=========================================="

# --- KILL_SWITCH チェック ---
KILL=$($PYTHON -c "
import json
with open('$PROJECT_DIR/state/system-status.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
print(data.get('kill_switch', False))
" 2>/dev/null)

if [ "$KILL" = "True" ]; then
    log "[KILL_SWITCH] ON - 全停止中。再開するには: bash scripts/kill-switch.sh off"
    exit 0
fi

# --- Step 1: フェッチャー（メトリクス回収）---
log "[Step 1/5] フェッチャー実行中..."
$PYTHON "$AGENTS_DIR/fetcher.py" >> "$LOG_FILE" 2>&1
if [ $? -ne 0 ]; then
    log "[WARNING] フェッチャーでエラー発生（続行）"
fi

# --- Step 2: アナリスト（分析・フィードバック）---
log "[Step 2/5] アナリスト実行中..."
bash "$AGENTS_DIR/analyst.sh" >> "$LOG_FILE" 2>&1
if [ $? -ne 0 ]; then
    log "[WARNING] アナリストでエラー発生（続行）"
fi

# --- Step 3: リサーチャー（ネタ収集）---
log "[Step 3/5] リサーチャー実行中..."
bash "$AGENTS_DIR/researcher.sh" >> "$LOG_FILE" 2>&1
if [ $? -ne 0 ]; then
    log "[WARNING] リサーチャーでエラー発生（続行）"
fi

# --- Step 4: ライター（投稿生成）---
log "[Step 4/5] ライター実行中..."
bash "$AGENTS_DIR/writer.sh" >> "$LOG_FILE" 2>&1
if [ $? -ne 0 ]; then
    log "[ERROR] ライターでエラー発生"
fi

# --- Step 5: スーパーバイザー（全体チェック）---
log "[Step 5/5] スーパーバイザー実行中..."
bash "$AGENTS_DIR/supervisor.sh" >> "$LOG_FILE" 2>&1

log "=========================================="
log "Daily Run 完了"
log "=========================================="

# --- キューの状態を表示 ---
$PYTHON -c "
import json
with open('$PROJECT_DIR/state/post-queue.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
queued = [p for p in data.get('queue', []) if p.get('status') == 'queued']
print(f'投稿キュー: {len(queued)}件待機中')
" 2>/dev/null
