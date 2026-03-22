#!/bin/bash
# ==================================================
# 共通ユーティリティ関数
# 全エージェントから source して使用
# ==================================================

# プロジェクトルート（Windows形式パス: Pythonでの日本語パス対応）
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -W)"

# Python パス（Windows環境: App Aliasではなく実体を指定）
PYTHON="/c/Users/fkgyo/AppData/Local/Programs/Python/Python312/python.exe"
if [ ! -f "$PYTHON" ]; then
    PYTHON="python"
    command -v python3 &> /dev/null && PYTHON="python3"
fi

# 環境変数の読み込み（Pythonで解析: 日本語パス対応）
load_env() {
    local env_file="$PROJECT_DIR/config/api-keys.env"
    local exports
    exports=$($PYTHON -c "
import os
env_file = '$env_file'
if not os.path.exists(env_file):
    print('ERROR')
    exit(1)
with open(env_file, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            print(f'{key.strip()}={value.strip()}')
")
    if [ "$exports" = "ERROR" ] || [ -z "$exports" ]; then
        echo "[ERROR] api-keys.env が見つかりません。config/api-keys.env.example をコピーして設定してください。"
        exit 1
    fi
    while IFS='=' read -r key value; do
        # Windows改行コード(\r)を除去
        key=$(echo "$key" | tr -d '\r')
        value=$(echo "$value" | tr -d '\r')
        export "$key=$value"
    done <<< "$exports"
}

# KILL_SWITCH チェック
check_kill_switch() {
    local kill_switch
    kill_switch=$($PYTHON -c "
import json, os
path = os.path.join(r'$PROJECT_DIR', 'state', 'system-status.json')
with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)
print(data.get('kill_switch', False))
")
    if [ "$kill_switch" = "True" ]; then
        echo "[KILL_SWITCH] システムは停止中です。"
        exit 0
    fi
}

# エージェントステータス更新
update_agent_status() {
    local agent_name="$1"
    local status="$2"  # running / idle / error
    $PYTHON "$PROJECT_DIR/agents/update_status.py" "$PROJECT_DIR" "$agent_name" "$status"
}

# Claude API 呼び出し（Haiku）
# 使い方: call_claude "システムプロンプトファイルパス" "ユーザープロンプトファイルパス" [max_tokens]
# ※ 日本語パス対応のため、プロンプトはファイルパスで渡す
call_claude() {
    local sys_prompt_file="$1"
    local usr_prompt_file="$2"
    local max_tokens="${3:-4096}"

    $PYTHON "$PROJECT_DIR/agents/claude_caller.py" "$sys_prompt_file" "$usr_prompt_file" "$max_tokens"
}

# JSON ファイルの読み込み（Pythonで読み込み: 日本語パス対応）
read_json() {
    local file="$1"
    $PYTHON -c "
import os, sys
path = '$file'
if os.path.exists(path):
    with open(path, 'r', encoding='utf-8') as f:
        print(f.read())
else:
    print('{}')
" 2>/dev/null || echo "{}"
}

# ログ出力
log() {
    local level="$1"
    local message="$2"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $message"
}
