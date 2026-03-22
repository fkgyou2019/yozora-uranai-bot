#!/bin/bash
# ==================================================
# エージェント1: リサーチャー
# テーマツリーの穴を分析し、占いネタを収集・構造化
# ==================================================

source "$(dirname "$0")/common.sh"
load_env
check_kill_switch

log "INFO" "リサーチャーエージェント開始"
update_agent_status "researcher" "running"

# --- ユーザープロンプトをPythonで構築・保存 ---
$PYTHON "$PROJECT_DIR/agents/build_researcher_prompt.py" "$PROJECT_DIR"
if [ $? -ne 0 ]; then
    log "ERROR" "リサーチャープロンプト構築に失敗"
    update_agent_status "researcher" "error"
    exit 1
fi

# --- Claude API 呼び出し ---
log "INFO" "Claude API呼び出し中..."
RESULT=$(call_claude "$PROJECT_DIR/prompts/researcher-prompt.md" "$PROJECT_DIR/state/_tmp_researcher_user_prompt.txt" 4096)

# --- 結果をファイルに保存してPythonで解析 ---
echo "$RESULT" > "$PROJECT_DIR/state/_tmp_claude_result.txt"
$PYTHON "$PROJECT_DIR/agents/parse_json_result.py" "$PROJECT_DIR/state/_tmp_claude_result.txt" "$PROJECT_DIR/state/research-results.json"

if [ $? -eq 0 ]; then
    log "INFO" "リサーチ結果を保存: state/research-results.json"
    update_agent_status "researcher" "idle"
else
    log "ERROR" "Claude APIからの応答がJSONとして解析できません"
    update_agent_status "researcher" "error"
    exit 1
fi

# 一時ファイル削除
rm -f "$PROJECT_DIR/state/_tmp_researcher_user_prompt.txt" "$PROJECT_DIR/state/_tmp_claude_result.txt" 2>/dev/null

log "INFO" "リサーチャーエージェント完了"
