#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
投稿ヘルスチェック
投稿1時間後にメトリクスを検証し、基準未達なら削除→再投稿トリガー。

検証基準（1時間後）:
  RED（即削除・再投稿）:
    - views < 20
    - views >= 20 かつ likes == 0 かつ replies == 0
  YELLOW（様子見・ログのみ）:
    - views 20-50 かつ engagement < 3%
  GREEN（合格）:
    - views >= 50 または engagement >= 5%

追加チェック:
  - 同一ユーザーへの重複返信検知
  - 投稿時刻と内容の矛盾検知（例: 23時に「今朝の運勢」）
"""
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8")
JST = timezone(timedelta(hours=9))

# --- 設定 ---
RED_VIEWS_THRESHOLD = 20
RED_ZERO_ENGAGEMENT_VIEWS = 20
YELLOW_VIEWS_MAX = 50
YELLOW_ENG_THRESHOLD = 3.0
GREEN_VIEWS_MIN = 50
GREEN_ENG_MIN = 5.0
CHECK_WINDOW_MINUTES = 180  # 投稿後30-180分の投稿を対象


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), "../../config/api-keys.env")
    env_path = os.path.normpath(env_path)
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def threads_api_get(endpoint, token):
    url = f"https://graph.threads.net/v1.0/{endpoint}"
    if "?" in url:
        url += f"&access_token={token}"
    else:
        url += f"?access_token={token}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def threads_api_delete(post_id, token):
    url = f"https://graph.threads.net/v1.0/{post_id}?access_token={token}"
    req = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_post_insights(post_id, token):
    """投稿のメトリクスを取得"""
    try:
        data = threads_api_get(
            f"{post_id}/insights?metric=views,likes,replies,reposts,quotes", token
        )
        metrics = {}
        for m in data.get("data", []):
            metrics[m["name"]] = m.get("values", [{}])[0].get("value", 0)
        return metrics
    except Exception as e:
        print(f"  [WARN] メトリクス取得失敗 {post_id}: {e}")
        return None


def check_duplicate_replies(post_id, token):
    """同一ユーザーへの重複返信を検知"""
    try:
        data = threads_api_get(
            f"{post_id}/replies?fields=id,username,text&limit=50", token
        )
        replies = data.get("data", [])
        user_counts = {}
        for r in replies:
            username = r.get("username", "")
            if username == "yozora.uranai":
                continue  # 自分の返信は除外
            user_counts[username] = user_counts.get(username, 0) + 1

        duplicates = {u: c for u, c in user_counts.items() if c > 1}
        return duplicates
    except Exception:
        return {}


def check_time_content_mismatch(post_text, posted_hour):
    """投稿時刻と内容の矛盾を検知"""
    mismatches = []

    morning_words = ["今朝", "おはよう", "朝の", "モーニング"]
    night_words = ["今夜", "今晩", "おやすみ", "寝る前"]
    time_specific = []

    # 「○時までに」パターン
    import re
    time_match = re.findall(r"(\d{1,2})時まで", post_text)
    for t in time_match:
        deadline = int(t)
        if posted_hour >= deadline:
            mismatches.append(f"「{deadline}時までに」だが{posted_hour}時に投稿")

    # 朝の内容を夜に投稿
    if posted_hour >= 18:
        for w in morning_words:
            if w in post_text:
                mismatches.append(f"夜{posted_hour}時に「{w}」を含む投稿")

    # 夜の内容を朝に投稿
    if posted_hour < 15:
        for w in night_words:
            if w in post_text:
                mismatches.append(f"朝{posted_hour}時に「{w}」を含む投稿")

    return mismatches


def evaluate_post(views, likes, replies):
    """RED / YELLOW / GREEN を判定"""
    engagement = ((likes + replies) / views * 100) if views > 0 else 0

    if views < RED_VIEWS_THRESHOLD:
        return "RED", f"views={views} < {RED_VIEWS_THRESHOLD}"

    if views >= RED_ZERO_ENGAGEMENT_VIEWS and likes == 0 and replies == 0:
        return "RED", f"views={views}だがlikes=0, replies=0"

    if views < YELLOW_VIEWS_MAX and engagement < YELLOW_ENG_THRESHOLD:
        return "YELLOW", f"views={views}, eng={engagement:.1f}% < {YELLOW_ENG_THRESHOLD}%"

    if views >= GREEN_VIEWS_MIN or engagement >= GREEN_ENG_MIN:
        return "GREEN", f"views={views}, eng={engagement:.1f}%"

    return "YELLOW", f"views={views}, eng={engagement:.1f}% (判定保留)"


def main():
    load_env()
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    uid = os.environ.get("THREADS_USER_ID", "")

    if not token or not uid:
        print("ERROR: THREADS_ACCESS_TOKEN or THREADS_USER_ID not set")
        sys.exit(1)

    now = datetime.now(JST)
    print(f"ヘルスチェック開始: {now.strftime('%Y-%m-%d %H:%M JST')}")

    # 最新投稿を取得
    try:
        data = threads_api_get(
            f"{uid}/threads?fields=id,text,timestamp&limit=10", token
        )
    except Exception as e:
        print(f"ERROR: 投稿一覧取得失敗: {e}")
        sys.exit(1)

    posts = data.get("data", [])
    if not posts:
        print("投稿がありません")
        sys.exit(0)

    deleted_count = 0
    checked_count = 0
    results = []

    for p in posts:
        pid = p["id"]
        text = p.get("text", "")
        ts = p.get("timestamp", "")

        # UTC → JST
        try:
            utc_time = datetime.fromisoformat(ts.replace("+0000", "+00:00"))
            jst_time = utc_time.astimezone(JST)
        except Exception:
            continue

        # 投稿後60-90分の投稿のみ対象
        age_minutes = (now - jst_time).total_seconds() / 60
        if age_minutes < 30 or age_minutes > CHECK_WINDOW_MINUTES:
            continue

        checked_count += 1
        posted_hour = jst_time.hour
        print(f"\n--- {jst_time.strftime('%H:%M')} の投稿 (ID={pid}) ---")
        print(f"  内容: {text[:40]}...")

        # 1. メトリクス検証
        metrics = get_post_insights(pid, token)
        if metrics is None:
            print("  メトリクス取得失敗。スキップ。")
            continue

        views = metrics.get("views", 0)
        likes = metrics.get("likes", 0)
        replies = metrics.get("replies", 0)

        status, reason = evaluate_post(views, likes, replies)
        print(f"  views={views} likes={likes} replies={replies}")
        print(f"  判定: {status} ({reason})")

        # 2. 重複返信チェック
        if replies > 0:
            duplicates = check_duplicate_replies(pid, token)
            if duplicates:
                print(f"  ⚠ 重複返信検知: {duplicates}")

        # 3. 時刻-内容矛盾チェック
        mismatches = check_time_content_mismatch(text, posted_hour)
        if mismatches:
            for mm in mismatches:
                print(f"  ⚠ 時刻矛盾: {mm}")
            if status != "RED":
                status = "RED"
                reason += " + 時刻矛盾"

        # 4. RED判定なら削除
        if status == "RED":
            print(f"  → 削除実行...")
            try:
                result = threads_api_delete(pid, token)
                if result.get("success"):
                    print(f"  ✅ 削除成功")
                    deleted_count += 1
                else:
                    print(f"  ❌ 削除失敗: {result}")
            except Exception as e:
                print(f"  ❌ 削除エラー: {e}")

        results.append({
            "post_id": pid,
            "time": jst_time.isoformat(),
            "views": views,
            "likes": likes,
            "replies": replies,
            "status": status,
            "reason": reason,
            "deleted": status == "RED",
        })

    print(f"\n=== 結果 ===")
    print(f"チェック対象: {checked_count}件")
    print(f"削除: {deleted_count}件")

    # 結果をファイルに保存
    os.makedirs("state", exist_ok=True)
    check_log_path = "state/health-check-log.json"
    if os.path.exists(check_log_path):
        with open(check_log_path, encoding="utf-8") as f:
            log = json.load(f)
    else:
        log = {"checks": []}

    log["checks"].append({
        "timestamp": now.isoformat(),
        "checked": checked_count,
        "deleted": deleted_count,
        "results": results,
    })

    # 直近100件のみ保持
    log["checks"] = log["checks"][-100:]

    with open(check_log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    # 削除があった場合、再投稿フラグファイルを作成
    if deleted_count > 0:
        with open("state/needs-repost.flag", "w") as f:
            f.write(str(deleted_count))
        print(f"\n再投稿フラグ作成: {deleted_count}件分")

    sys.exit(0)


if __name__ == "__main__":
    main()
