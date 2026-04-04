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
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- 設定 ---
# Phase 1: 3h+ 初速不振
RED_VIEWS_THRESHOLD = 10          # views < 10 → 到達ゼロ = 即削除
RED_ZERO_ENGAGEMENT_VIEWS = 30    # views≥30 かつ likes=0 かつ replies=0 → ゾンビ投稿

YELLOW_VIEWS_MAX = 50
YELLOW_ENG_THRESHOLD = 3.0
GREEN_VIEWS_MIN = 50
GREEN_ENG_MIN = 5.0

# 最小チェック年齢: 180分（3時間未満はスキップ）
MIN_AGE_MINUTES = 180

# Phase 2: 12h+ 中間評価（ゼロ共感・低閲覧のみ削除）
MID_AGE_MINUTES = 720         # 12時間
MID_AGE_VIEWS_MAX = 50        # views≤50
MID_AGE_LIKES_MAX = 0         # likes=0
MID_AGE_REPLIES_MAX = 0       # replies=0

# Phase 3: 24h+ 終了判定（共感ほぼゼロ）
LONG_AGE_MINUTES = 1440       # 24時間（旧:12h → 24hに延長）
LONG_AGE_VIEWS_MAX = 100      # views≤100
LONG_AGE_LIKES_MAX = 3        # likes≤3（旧:≤10 → 緩和。likes≥4は財産候補として保護）
LONG_AGE_REPLIES_MAX = 0      # replies=0

# 時刻矛盾保護しきい値: likes≥3 or replies≥1 なら時刻矛盾でも削除しない
TIME_MISMATCH_PROTECT_LIKES = 3
TIME_MISMATCH_PROTECT_REPLIES = 1

# --- DELETE レート制限管理 ---
# Threads APIのDELETE上限は24時間で約100件
# ヘルスチェック用に温存するため、1回のヘルスチェックで消費する上限を設ける
DELETE_RATE_LIMIT_FILE = None  # main()でPROJECT_DIRを使って設定
MAX_DELETES_PER_RUN = 5        # 1回のヘルスチェックで削除する最大件数
MAX_DELETES_PER_DAY = 20       # 1日の削除上限（レート制限の1/5を上限として安全マージン確保）


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


def load_delete_count_today(rate_file):
    """本日の削除件数を取得"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    if os.path.exists(rate_file):
        with open(rate_file, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") == today:
            return data.get("count", 0)
    return 0


def record_delete(rate_file):
    """削除1件をカウント"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    count = load_delete_count_today(rate_file) + 1
    with open(rate_file, "w", encoding="utf-8") as f:
        json.dump({"date": today, "count": count}, f)
    return count


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


def evaluate_post(views, likes, replies, age_minutes=0):
    """RED / YELLOW / GREEN を判定

    削除フェーズ:
      Phase 1 (3h+):  views < 10  /  views≥30 かつ likes=0 かつ replies=0
      Phase 2 (12h+): views≤50 かつ likes=0 かつ replies=0
      Phase 3 (24h+): views≤100 かつ likes≤3 かつ replies=0
    """
    engagement = ((likes + replies) / views * 100) if views > 0 else 0

    # Phase 3: 24h+ 終了判定（財産にならない確定）
    if (age_minutes >= LONG_AGE_MINUTES
            and views <= LONG_AGE_VIEWS_MAX
            and likes <= LONG_AGE_LIKES_MAX
            and replies <= LONG_AGE_REPLIES_MAX):
        return "RED", f"24h経過でviews={views}≤{LONG_AGE_VIEWS_MAX}, likes={likes}≤{LONG_AGE_LIKES_MAX}, replies=0"

    # Phase 2: 12h+ 中間評価（低閲覧かつゼロ共感のみ）
    if (age_minutes >= MID_AGE_MINUTES
            and views <= MID_AGE_VIEWS_MAX
            and likes <= MID_AGE_LIKES_MAX
            and replies <= MID_AGE_REPLIES_MAX):
        return "RED", f"12h経過でviews={views}≤{MID_AGE_VIEWS_MAX}, likes=0, replies=0"

    # Phase 1: 初速不振
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

    # DELETE レート制限管理ファイル
    rate_file = os.path.join(PROJECT_DIR, "state/delete-rate.json")
    delete_today = load_delete_count_today(rate_file)
    print(f"本日の削除実績: {delete_today}/{MAX_DELETES_PER_DAY}件")
    if delete_today >= MAX_DELETES_PER_DAY:
        print(f"⚠ 本日の削除上限({MAX_DELETES_PER_DAY}件)に達しています。削除をスキップします。")

    now = datetime.now(JST)
    print(f"ヘルスチェック開始: {now.strftime('%Y-%m-%d %H:%M JST')}")

    # 最新投稿を取得（72h以内の投稿をカバーするため25件取得）
    # limit=10だと1日12件投稿時に古い投稿が未チェックになる問題を修正
    try:
        data = threads_api_get(
            f"{uid}/threads?fields=id,text,timestamp&limit=25", token
        )
    except Exception as e:
        print(f"ERROR: 投稿一覧取得失敗: {e}")
        sys.exit(1)

    posts = data.get("data", [])
    print(f"投稿{len(posts)}件取得")
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

        # UTC → JST（複数フォーマット対応）
        try:
            # Threads APIの返すフォーマットに対応
            ts_normalized = ts.replace("+0000", "+00:00").replace("Z", "+00:00")
            utc_time = datetime.fromisoformat(ts_normalized)
            # タイムゾーン情報がない場合はUTCとして扱う
            if utc_time.tzinfo is None:
                utc_time = utc_time.replace(tzinfo=timezone.utc)
            jst_time = utc_time.astimezone(JST)
        except Exception as e:
            print(f"  投稿 {pid}: タイムスタンプ解析失敗 ts='{ts}' error={e}")
            continue

        # 投稿後MIN_AGE_MINUTES以降の投稿をチェック（上限なし）
        age_minutes = (now - jst_time).total_seconds() / 60
        in_window = age_minutes >= MIN_AGE_MINUTES
        status_mark = "チェック対象" if in_window else f"対象外(age={age_minutes:.0f}分 < {MIN_AGE_MINUTES}分・まだ新しい)"
        print(f"  投稿 {jst_time.strftime('%H:%M')} JST (age={age_minutes:.0f}分) → {status_mark}")

        if not in_window:
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

        status, reason = evaluate_post(views, likes, replies, age_minutes)
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
            # likes≥3 or replies≥1 の場合は時刻矛盾でも削除しない（高エンゲ投稿保護）
            if likes >= TIME_MISMATCH_PROTECT_LIKES or replies >= TIME_MISMATCH_PROTECT_REPLIES:
                print(f"  → 時刻矛盾あるが高エンゲ（likes={likes}, replies={replies}）→ 保護")
            elif status != "RED":
                status = "RED"
                reason += " + 時刻矛盾"

        # 4. RED判定なら削除（レート制限時は pending-deletions に積む）
        actually_deleted = False
        if status == "RED":
            delete_today = load_delete_count_today(rate_file)
            if delete_today >= MAX_DELETES_PER_DAY:
                print(f"  ⚠ 本日の削除上限到達 → pending-deletions.json に積む")
                pd_path = os.path.join(PROJECT_DIR, "state/pending-deletions.json")
                pd = json.load(open(pd_path, encoding="utf-8")) if os.path.exists(pd_path) else {"pending": []}
                existing_ids = {x["post_id"] for x in pd["pending"]}
                if pid not in existing_ids:
                    pd["pending"].append({"post_id": pid, "reason": reason, "queued_at": now.isoformat(), "text_preview": text[:40]})
                    with open(pd_path, "w", encoding="utf-8") as f:
                        json.dump(pd, f, ensure_ascii=False, indent=2)
            else:
                print(f"  → 削除実行（本日 {delete_today+1}/{MAX_DELETES_PER_DAY}件目）...")
            try:
                result = threads_api_delete(pid, token)
                if result.get("success"):
                    total = record_delete(rate_file)
                    print(f"  ✅ 削除成功: {pid} (本日累計 {total}/{MAX_DELETES_PER_DAY}件)")
                    deleted_count += 1
                    actually_deleted = True
                else:
                    print(f"  ❌ 削除失敗（API応答）: {result}")
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                if "does not exist" in body or '"error_subcode": 33' in body or '"error_subcode":33' in body:
                    # 既に削除済み
                    print(f"  ✅ 既に削除済み: {pid}")
                    deleted_count += 1
                    actually_deleted = True
                elif "rate limit" in body.lower() or e.code == 429 or '"code": 613' in body or '"code":613' in body:
                    print(f"  ⚠ レート制限中 → pending-deletions.json に積む")
                    # 削除待ちキューに保存（次回ヘルスチェック時に再試行）
                    pd_path = os.path.join(PROJECT_DIR, "state/pending-deletions.json")
                    if os.path.exists(pd_path):
                        with open(pd_path, encoding="utf-8") as f:
                            pd = json.load(f)
                    else:
                        pd = {"pending": []}
                    # 重複追加しない
                    existing_ids = {x["post_id"] for x in pd["pending"]}
                    if pid not in existing_ids:
                        pd["pending"].append({
                            "post_id": pid,
                            "reason": reason,
                            "queued_at": now.isoformat(),
                            "text_preview": text[:40],
                        })
                        with open(pd_path, "w", encoding="utf-8") as f:
                            json.dump(pd, f, ensure_ascii=False, indent=2)
                        print(f"  → pending-deletions.json に追加: {pid}")
                else:
                    print(f"  ❌ 削除エラー HTTP {e.code}: {body[:120]}")
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
            "deleted": actually_deleted,
        })

    # --- pending-deletions の再試行 ---
    pd_path = os.path.join(PROJECT_DIR, "state/pending-deletions.json")
    if os.path.exists(pd_path):
        with open(pd_path, encoding="utf-8") as f:
            pd = json.load(f)
        pending = pd.get("pending", [])
        if pending:
            print(f"\n--- pending-deletions 再試行: {len(pending)}件 ---")
            still_pending = []
            for item in pending:
                pid2 = item["post_id"]
                # 日次上限チェック
                delete_today = load_delete_count_today(rate_file)
                if delete_today >= MAX_DELETES_PER_DAY:
                    print(f"  ⚠ 本日の削除上限到達。残り {len(pending) - pending.index(item)}件は明日以降に延期")
                    still_pending.extend(pending[pending.index(item):])
                    break
                try:
                    result = threads_api_delete(pid2, token)
                    if result.get("success"):
                        total = record_delete(rate_file)
                        print(f"  ✅ 遅延削除成功: {pid2} ({item['text_preview']}...) 本日累計{total}件")
                        deleted_count += 1
                    else:
                        print(f"  ❌ 遅延削除失敗: {result}")
                        still_pending.append(item)
                except urllib.error.HTTPError as e:
                    body = e.read().decode("utf-8", errors="replace")
                    if "rate limit" in body.lower() or '"code": 613' in body or '"code":613' in body:
                        print(f"  ⚠ まだレート制限中: {pid2}")
                        still_pending.append(item)
                    elif "does not exist" in body or '"error_subcode": 33' in body or '"error_subcode":33' in body:
                        # 既に削除済み（他の手段で削除されたか、期限切れ）→ pending から除去
                        print(f"  ✅ 既に削除済み（存在しない）: {pid2}")
                        deleted_count += 1
                    else:
                        print(f"  ❌ 削除エラー HTTP {e.code}: {body[:120]}")
                        still_pending.append(item)
                except Exception as e:
                    print(f"  ❌ 削除エラー: {pid2} {e}")
                    still_pending.append(item)
            pd["pending"] = still_pending
            with open(pd_path, "w", encoding="utf-8") as f:
                json.dump(pd, f, ensure_ascii=False, indent=2)

    # RED判定件数（削除成否に関わらずカウント）
    red_count = sum(1 for r in results if r.get("status") == "RED")

    print(f"\n=== 結果 ===")
    print(f"チェック対象: {checked_count}件")
    print(f"RED（低パフォーマンス）: {red_count}件")
    print(f"削除成功: {deleted_count}件 / 削除失敗（レート制限等）: {red_count - deleted_count}件")

    # 結果をファイルに保存
    os.makedirs("state", exist_ok=True)
    check_log_path = os.path.join(PROJECT_DIR, "state/health-check-log.json")
    if os.path.exists(check_log_path):
        with open(check_log_path, encoding="utf-8") as f:
            log = json.load(f)
    else:
        log = {"checks": []}

    log["checks"].append({
        "timestamp": now.isoformat(),
        "checked": checked_count,
        "red": red_count,
        "deleted": deleted_count,
        "results": results,
    })

    # 直近100件のみ保持
    log["checks"] = log["checks"][-100:]

    with open(check_log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    # RED判定があれば再投稿フラグを作成
    # ★ 削除成否に関わらず再投稿する（投稿数という財産を守るため）
    # ★ 削除失敗分は pending-deletions.json に積み、レート制限リセット後に自動削除
    if red_count > 0:
        flag_path = os.path.join(PROJECT_DIR, "state/needs-repost.flag")
        with open(flag_path, "w") as f:
            f.write(str(red_count))
        print(f"\n[REPOST] 再投稿フラグ作成: {red_count}件分")
        print(f"[REPOST] 削除成功={deleted_count}件 / 削除待ち={red_count - deleted_count}件")
        print(f"[REPOST] → 代替投稿を即時実行してカウントを維持します")

    sys.exit(0)


if __name__ == "__main__":
    main()
