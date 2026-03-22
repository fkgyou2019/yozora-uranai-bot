#!/bin/bash
# ==================================================
# 緊急停止スクリプト（KILL_SWITCH）
# 使い方: bash scripts/kill-switch.sh [on|off]
# ==================================================

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd -W)"
STATUS_FILE="$PROJECT_DIR/state/system-status.json"
PYTHON="/c/Users/fkgyo/AppData/Local/Programs/Python/Python312/python.exe"
if [ ! -f "$PYTHON" ]; then
    PYTHON="python"
    command -v python3 &> /dev/null && PYTHON="python3"
fi

case "${1:-on}" in
    on|ON|1|true)
        $PYTHON -c "
import json
with open('$STATUS_FILE', 'r', encoding='utf-8') as f:
    data = json.load(f)
data['kill_switch'] = True
with open('$STATUS_FILE', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print('[KILL_SWITCH] ON - 全エージェント停止')
"
        ;;
    off|OFF|0|false)
        $PYTHON -c "
import json
with open('$STATUS_FILE', 'r', encoding='utf-8') as f:
    data = json.load(f)
data['kill_switch'] = False
data['consecutive_errors'] = 0
for agent in data.get('agents', {}).values():
    agent['error_count'] = 0
    agent['status'] = 'idle'
with open('$STATUS_FILE', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print('[KILL_SWITCH] OFF - システム再開可能')
"
        ;;
    status)
        $PYTHON -c "
import json
with open('$STATUS_FILE', 'r', encoding='utf-8') as f:
    data = json.load(f)
ks = data.get('kill_switch', False)
print(f'KILL_SWITCH: {\"ON\" if ks else \"OFF\"}')
for name, info in data.get('agents', {}).items():
    print(f'  {name}: {info.get(\"status\", \"unknown\")} (errors: {info.get(\"error_count\", 0)})')
"
        ;;
    *)
        echo "Usage: $0 [on|off|status]"
        exit 1
        ;;
esac
