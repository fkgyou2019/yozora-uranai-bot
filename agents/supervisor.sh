#!/bin/bash
# ==================================================
# エージェント6: スーパーバイザー
# 全体監視・異常検知・緊急停止
# ==================================================

source "$(dirname "$0")/common.sh"

log "INFO" "スーパーバイザーエージェント開始"
update_agent_status "supervisor" "running"

STATUS=$(read_json "$PROJECT_DIR/state/system-status.json")
HISTORY=$(read_json "$PROJECT_DIR/state/post-history.json")
QUEUE=$(read_json "$PROJECT_DIR/state/post-queue.json")
SAFETY=$(read_json "$PROJECT_DIR/config/safety.json")

# --- チェック実行 ---
$PYTHON << PYEOF
import json
import sys
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
now = datetime.now(JST)

project_dir = "$PROJECT_DIR"

def load(path):
    try:
        with open(f"{project_dir}/{path}", "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save(path, data):
    with open(f"{project_dir}/{path}", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

status = load("state/system-status.json")
history = load("state/post-history.json")
queue = load("state/post-queue.json")
safety = load("config/safety.json")

checks = []
overall = "normal"
actions = []

# 1. KILL_SWITCH
if status.get("kill_switch", False):
    checks.append({"item": "KILL_SWITCH", "status": "error", "detail": "KILL_SWITCHがON - 全停止中"})
    overall = "stopped"
else:
    checks.append({"item": "KILL_SWITCH", "status": "ok", "detail": "OFF"})

# 2. 連続エラー
consecutive = status.get("consecutive_errors", 0)
max_errors = safety.get("error_handling", {}).get("max_consecutive_errors", 3)
if consecutive >= max_errors:
    checks.append({"item": "連続エラー", "status": "error", "detail": f"{consecutive}回連続エラー（上限{max_errors}）"})
    overall = "critical"
    status["kill_switch"] = True
    actions.append("KILL_SWITCH をONに設定（連続エラー上限到達）")
elif consecutive > 0:
    checks.append({"item": "連続エラー", "status": "warning", "detail": f"{consecutive}回（上限{max_errors}）"})
    if overall == "normal":
        overall = "warning"
else:
    checks.append({"item": "連続エラー", "status": "ok", "detail": "0回"})

# 3. 投稿数チェック
daily_count = status.get("daily_post_count", 0)
daily_limit = safety.get("posting_safety", {}).get("max_posts_per_day", 15)
if daily_count >= daily_limit:
    checks.append({"item": "日次投稿数", "status": "warning", "detail": f"{daily_count}/{daily_limit} (上限到達)"})
    if overall == "normal":
        overall = "warning"
else:
    checks.append({"item": "日次投稿数", "status": "ok", "detail": f"{daily_count}/{daily_limit}"})

# 4. 投稿間隔チェック
posts = history.get("posts", [])
if len(posts) >= 2:
    last_two = posts[-2:]
    try:
        t1 = datetime.fromisoformat(last_two[0]["posted_at"])
        t2 = datetime.fromisoformat(last_two[1]["posted_at"])
        if t1.tzinfo is None: t1 = t1.replace(tzinfo=JST)
        if t2.tzinfo is None: t2 = t2.replace(tzinfo=JST)
        interval = abs((t2 - t1).total_seconds())
        min_interval = safety.get("posting_safety", {}).get("min_interval_seconds", 3600)
        if interval < min_interval:
            checks.append({"item": "投稿間隔", "status": "warning", "detail": f"直近2件の間隔: {int(interval)}秒（最低{min_interval}秒）"})
            if overall == "normal":
                overall = "warning"
        else:
            checks.append({"item": "投稿間隔", "status": "ok", "detail": f"直近間隔: {int(interval)}秒"})
    except:
        checks.append({"item": "投稿間隔", "status": "ok", "detail": "チェック不可（日時解析エラー）"})
else:
    checks.append({"item": "投稿間隔", "status": "ok", "detail": "投稿2件未満"})

# 5. キュー残量
queued = [p for p in queue.get("queue", []) if p.get("status") == "queued"]
if len(queued) == 0:
    checks.append({"item": "キュー残量", "status": "warning", "detail": "投稿キューが空"})
    if overall == "normal":
        overall = "warning"
else:
    checks.append({"item": "キュー残量", "status": "ok", "detail": f"{len(queued)}件"})

# 6. 各エージェントの状態
for agent_name, agent_data in status.get("agents", {}).items():
    agent_status = agent_data.get("status", "unknown")
    error_count = agent_data.get("error_count", 0)
    if error_count >= 3:
        checks.append({"item": f"エージェント:{agent_name}", "status": "error", "detail": f"エラー{error_count}回"})
        if overall in ("normal", "warning"):
            overall = "critical"
    elif agent_status == "error":
        checks.append({"item": f"エージェント:{agent_name}", "status": "warning", "detail": f"直近エラー（累計{error_count}回）"})
        if overall == "normal":
            overall = "warning"
    else:
        checks.append({"item": f"エージェント:{agent_name}", "status": "ok", "detail": agent_status})

# 結果出力・保存
report = {
    "check_time": now.isoformat(),
    "overall_status": overall,
    "checks": checks,
    "actions_taken": actions,
    "recommendations": []
}

if overall == "warning":
    report["recommendations"].append("次回チェック時に改善がなければcriticalに昇格")
if len(queued) == 0:
    report["recommendations"].append("ライターエージェントを実行して投稿キューを補充")

# 状態保存
save("state/system-status.json", status)

# レポート出力
print(json.dumps(report, ensure_ascii=False, indent=2))

# ログファイルにも追記
log_file = f"{project_dir}/state/supervisor-log.jsonl"
with open(log_file, "a", encoding="utf-8") as f:
    f.write(json.dumps(report, ensure_ascii=False) + "\n")

PYEOF

update_agent_status "supervisor" "idle"
log "INFO" "スーパーバイザーエージェント完了"
