#!/bin/bash
# ==================================================
# エージェント2: アナリスト
# 過去投稿データを分析し、ライターへのフィードバックを生成
# ==================================================

source "$(dirname "$0")/common.sh"
load_env
check_kill_switch

log "INFO" "アナリストエージェント開始"
update_agent_status "analyst" "running"

# --- 投稿数チェック（Pythonで実行）---
POST_COUNT=$($PYTHON -c "
import json, os
path = os.path.join(r'$PROJECT_DIR', 'state', 'post-history.json')
if os.path.exists(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    print(len(data.get('posts', [])))
else:
    print(0)
")

if [ "$POST_COUNT" -lt 5 ]; then
    log "INFO" "投稿数が少ないためデフォルトフィードバックを生成"
    $PYTHON "$PROJECT_DIR/agents/build_analyst_default.py" "$PROJECT_DIR"
    update_agent_status "analyst" "idle"
    log "INFO" "アナリストエージェント完了（デフォルト）"
    exit 0
fi

# --- ユーザープロンプトをPythonで構築 ---
$PYTHON "$PROJECT_DIR/agents/build_analyst_prompt.py" "$PROJECT_DIR"
if [ $? -ne 0 ]; then
    log "ERROR" "アナリストプロンプト構築に失敗"
    update_agent_status "analyst" "error"
    exit 1
fi

# --- Claude API 呼び出し ---
log "INFO" "Claude API呼び出し中..."
RESULT=$(call_claude "$PROJECT_DIR/prompts/analyst-prompt.md" "$PROJECT_DIR/state/_tmp_analyst_user_prompt.txt" 4096)

# --- 結果をファイルに保存してPythonで解析 ---
echo "$RESULT" > "$PROJECT_DIR/state/_tmp_claude_result.txt"
$PYTHON "$PROJECT_DIR/agents/parse_json_result.py" "$PROJECT_DIR/state/_tmp_claude_result.txt" "$PROJECT_DIR/state/analyst-feedback.json"

if [ $? -eq 0 ]; then
    log "INFO" "分析結果を保存: state/analyst-feedback.json"
    update_agent_status "analyst" "idle"
else
    log "ERROR" "分析結果の解析に失敗"
    update_agent_status "analyst" "error"
    exit 1
fi

# 一時ファイル削除
rm -f "$PROJECT_DIR/state/_tmp_analyst_user_prompt.txt" "$PROJECT_DIR/state/_tmp_claude_result.txt" 2>/dev/null

log "INFO" "アナリストエージェント完了"
