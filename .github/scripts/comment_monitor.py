#!/usr/bin/env python3
"""
コメントモニタリング & 自律保護スクリプト
- 投稿済みコメントの存在を確認
- プラットフォーム削除を検知
- 閾値超えで残コメント一括削除 + 24h停止
"""

import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

STATE_FILE = "state/comment-marketing.json"
BASE_URL = "https://graph.threads.net/v1.0"
JST = timezone(timedelta(hours=9))

# 保護モード発動閾値
DELETION_THRESHOLD = 2      # 1時間以内にN件削除されたら保護モードへ
DELETION_WINDOW_HOURS = 1
PAUSE_HOURS = 24            # 停止時間


def load_state():
    if not os.path.exists(STATE_FILE):
        print("[INFO] 状態ファイルなし。スキップ")
        return None
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_token():
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not token:
        for env_file in ["api-keys.env", "config/api-keys.env"]:
            if os.path.exists(env_file):
                with open(env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("THREADS_ACCESS_TOKEN="):
                            token = line.split("=", 1)[1]
                            break
                if token:
                    break
    return token


def check_comment_exists(comment_id, token):
    """コメントがまだ存在するか確認する"""
    url = f"{BASE_URL}/{comment_id}?fields=id&access_token={token}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            return "id" in data
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            return False  # 削除済み
        # その他のHTTPエラーは「存在する」とみなす（誤検知防止）
        return True
    except Exception:
        return True  # ネットワーク等のエラーは「存在する」とみなす


def delete_comment(comment_id, token):
    """自分のコメントを削除する"""
    url = f"{BASE_URL}/{comment_id}?access_token={token}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            success = data.get("success", False)
            print(f"[DELETE] {comment_id}: {'成功' if success else 'レスポンス異常'}")
            return success
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"[DELETE] {comment_id}: 既に存在しない")
            return True
        body = e.read().decode("utf-8", errors="replace")
        print(f"[DELETE] {comment_id}: エラー {e.code} {body[:100]}")
        return False
    except Exception as e:
        print(f"[DELETE] {comment_id}: 例外 {e}")
        return False


def main():
    state = load_state()
    if state is None:
        return

    token = load_token()
    if not token:
        print("[ERROR] トークンが取得できません")
        return

    recent_comments = state.get("recent_comments", [])
    if not recent_comments:
        print("[INFO] 監視対象コメントなし")
        return

    now = datetime.now(JST)

    # 直近24時間のアクティブコメントのみ確認
    check_targets = [
        c for c in recent_comments
        if c.get("status") == "active"
        and c.get("posted_at")
        and (now - datetime.fromisoformat(c["posted_at"])).total_seconds() < 86400
    ]

    if not check_targets:
        print("[INFO] 確認対象なし（24時間以内のアクティブコメントなし）")
        return

    print(f"[MONITOR] {len(check_targets)}件のコメントを確認中...")

    newly_deleted_count = 0

    for c in check_targets:
        comment_id = c["comment_id"]
        exists = check_comment_exists(comment_id, token)

        if not exists:
            print(f"[DETECT] プラットフォーム削除を検知: {comment_id} (@{c.get('target_username', '?')})")
            c["status"] = "deleted_by_platform"
            c["deletion_detected_at"] = now.isoformat()
            newly_deleted_count += 1

        time.sleep(1.5)  # API負荷軽減

    # 直近1時間の削除数をカウント（今回の検知分を含む）
    window_start = now - timedelta(hours=DELETION_WINDOW_HOURS)
    recent_deletions = [
        c for c in recent_comments
        if c.get("status") == "deleted_by_platform"
        and c.get("deletion_detected_at")
        and datetime.fromisoformat(c["deletion_detected_at"]) > window_start
    ]

    print(f"[MONITOR] 直近{DELETION_WINDOW_HOURS}時間の削除数: {len(recent_deletions)}件")

    # 保護モード判定
    if len(recent_deletions) >= DELETION_THRESHOLD:
        print(f"[ALERT] !! 削除閾値({DELETION_THRESHOLD}件)超過 → アカウント保護モード発動 !!")

        # アクティブなコメントを全削除
        active_to_delete = [
            c for c in recent_comments
            if c.get("status") == "active"
        ]
        print(f"[PROTECT] {len(active_to_delete)}件のコメントを予防削除します...")

        for c in active_to_delete:
            deleted = delete_comment(c["comment_id"], token)
            if deleted:
                c["status"] = "deleted_by_us"
                c["deletion_detected_at"] = now.isoformat()
            time.sleep(2)

        # 24時間停止
        pause_until = (now + timedelta(hours=PAUSE_HOURS)).isoformat()
        state["paused_until"] = pause_until
        print(f"[PAUSE] {PAUSE_HOURS}時間停止設定: {pause_until} まで")

    elif newly_deleted_count > 0:
        print(f"[WARN] {newly_deleted_count}件の削除を検知（閾値未達。監視継続）")
    else:
        print(f"[OK] 全コメント正常（削除なし）")

    save_state(state)
    print("[MONITOR] 完了")


if __name__ == "__main__":
    main()
