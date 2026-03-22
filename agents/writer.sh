#!/bin/bash
# ==================================================
# エージェント3: ライター（メインエンジン）
# ネタ+フィードバック+ナレッジから投稿を生成
# ==================================================

source "$(dirname "$0")/common.sh"
load_env
check_kill_switch

log "INFO" "ライターエージェント開始"
update_agent_status "writer" "running"

# --- ユーザープロンプトをPythonで構築・保存 ---
$PYTHON "$PROJECT_DIR/agents/build_writer_prompt.py" "$PROJECT_DIR"
if [ $? -ne 0 ]; then
    log "ERROR" "ライタープロンプト構築に失敗"
    update_agent_status "writer" "error"
    exit 1
fi

# --- Claude API 呼び出し（大きめのmax_tokens） ---
log "INFO" "Claude API呼び出し中（投稿10本生成）..."
RESULT=$(call_claude "$PROJECT_DIR/prompts/writer-prompt.md" "$PROJECT_DIR/state/_tmp_writer_user_prompt.txt" 8192)

# --- 結果をファイルに保存 ---
echo "$RESULT" > "$PROJECT_DIR/state/_tmp_claude_result.txt"

# --- 結果をパース → キューに追加 ---
$PYTHON "$PROJECT_DIR/agents/process_writer_result.py" "$PROJECT_DIR"

if [ $? -eq 0 ]; then
    log "INFO" "投稿キューを更新: state/post-queue.json"
    update_agent_status "writer" "idle"
else
    log "ERROR" "投稿生成結果の解析に失敗"
    update_agent_status "writer" "error"
    rm -f "$PROJECT_DIR/state/_tmp_writer_user_prompt.txt" "$PROJECT_DIR/state/_tmp_claude_result.txt" 2>/dev/null
    exit 1
fi

# 一時ファイル削除
rm -f "$PROJECT_DIR/state/_tmp_writer_user_prompt.txt" "$PROJECT_DIR/state/_tmp_claude_result.txt" 2>/dev/null

log "INFO" "ライターエージェント完了"
