#!/usr/bin/env python3
"""
コメントモニタリング & 自律回復スクリプト
1. 投稿済みコメントの存在を確認
2. プラットフォーム削除を検知
3. 閾値超えで: 原因分析 → 再発防止策適用 → 短時間クールダウンで自動再開
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
DELETION_THRESHOLD = 2       # 1時間以内にN件削除されたら保護モードへ
DELETION_WINDOW_HOURS = 1
COOLDOWN_HOURS = 1           # 再発防止策適用後のクールダウン（24h→1hに短縮）

# 調整パラメータの上下限
MIN_INTERVAL_SEC = 200
MAX_INTERVAL_SEC = 600
MIN_DAILY_LIMIT = 10
DEFAULT_DAILY_LIMIT = 30
MIN_PER_RUN = 1
DEFAULT_PER_RUN = 3


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
    """コメントがまだ存在するか確認"""
    url = f"{BASE_URL}/{comment_id}?fields=id&access_token={token}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            return "id" in data
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            return False  # 削除済み
        return True  # 他エラーは存在するとみなす（誤検知防止）
    except Exception:
        return True


def delete_comment(comment_id, token):
    """自分のコメントを削除"""
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


def analyze_deletion_cause(deleted_comments, all_comments):
    """
    削除されたコメントを分析して原因を特定する。
    返り値: {"causes": [...], "recommendations": [...]}
    """
    causes = []
    recommendations = []

    if not deleted_comments:
        return {"causes": [], "recommendations": []}

    # --- 分析1: 投稿間隔が短すぎないか ---
    posted_times = []
    for c in deleted_comments:
        if c.get("posted_at"):
            try:
                posted_times.append(datetime.fromisoformat(c["posted_at"]))
            except Exception:
                pass

    if len(posted_times) >= 2:
        posted_times.sort()
        intervals = [
            (posted_times[i + 1] - posted_times[i]).total_seconds()
            for i in range(len(posted_times) - 1)
        ]
        avg_interval = sum(intervals) / len(intervals)
        if avg_interval < 180:
            causes.append(f"投稿間隔が短すぎる（平均{avg_interval:.0f}秒）")
            recommendations.append("increase_interval")

    # --- 分析2: 同一ユーザーへの連続コメントがないか ---
    usernames = [c.get("target_username", "") for c in deleted_comments]
    if len(usernames) != len(set(usernames)):
        causes.append("同一ユーザーへの複数コメント")
        recommendations.append("deduplicate_users")

    # --- 分析3: コメント文が短すぎないか（30文字未満は薄いと判定されやすい） ---
    short_comments = [
        c for c in deleted_comments
        if len(c.get("comment_text", "")) < 25
    ]
    if short_comments:
        causes.append(f"コメントが短すぎる（{len(short_comments)}件が25文字未満）")
        recommendations.append("require_longer_comments")

    # --- 分析4: 削除率（全コメントに対する割合） ---
    active_total = len([c for c in all_comments if c.get("status") in ("active", "deleted_by_platform")])
    deletion_rate = len(deleted_comments) / active_total if active_total > 0 else 0
    if deletion_rate >= 0.3:
        causes.append(f"削除率が高い（{deletion_rate*100:.0f}%）")
        recommendations.append("reduce_daily_limit")

    # 原因が不明の場合のデフォルト対処
    if not causes:
        causes.append("原因不明（プラットフォームの一時的な判定の可能性）")
        recommendations.append("increase_interval")
        recommendations.append("reduce_daily_limit")

    return {"causes": causes, "recommendations": list(set(recommendations))}


def apply_prevention_measures(state, analysis):
    """
    分析結果に基づいて再発防止策をstateに記録する。
    comment_marketing.py がstateから読み込んで動的に適用する。
    返り値: 適用した対策の説明リスト
    """
    measures = []
    recs = analysis.get("recommendations", [])

    # コメント間隔を延長
    if "increase_interval" in recs:
        current = state.get("adjusted_interval_sec", MIN_INTERVAL_SEC)
        new_val = min(MAX_INTERVAL_SEC, current + 100)
        state["adjusted_interval_sec"] = new_val
        measures.append(f"コメント間隔: {current}秒 → {new_val}秒")

    # 1日上限を削減
    if "reduce_daily_limit" in recs:
        current = state.get("adjusted_daily_limit", DEFAULT_DAILY_LIMIT)
        new_val = max(MIN_DAILY_LIMIT, current - 5)
        state["adjusted_daily_limit"] = new_val
        measures.append(f"1日上限: {current}件 → {new_val}件")

    # 1実行あたりの件数を削減
    current_run = state.get("adjusted_per_run", DEFAULT_PER_RUN)
    new_run = max(MIN_PER_RUN, current_run - 1)
    if new_run != current_run:
        state["adjusted_per_run"] = new_run
        measures.append(f"1実行件数: {current_run}件 → {new_run}件")

    # 同一ユーザー除外フラグ
    if "deduplicate_users" in recs:
        state["strict_user_dedup"] = True
        measures.append("同一ユーザーへの重複コメントを厳格に禁止")

    # より長いコメントを要求するフラグ
    if "require_longer_comments" in recs:
        state["min_comment_length"] = 35
        measures.append("コメント最小文字数を35文字に引き上げ")

    return measures


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
            print(f"[DETECT] プラットフォーム削除: {comment_id} (@{c.get('target_username', '?')})")
            c["status"] = "deleted_by_platform"
            c["deletion_detected_at"] = now.isoformat()
            newly_deleted_count += 1

        time.sleep(1.5)

    # 直近1時間の削除数をカウント
    window_start = now - timedelta(hours=DELETION_WINDOW_HOURS)
    recent_deletions = [
        c for c in recent_comments
        if c.get("status") == "deleted_by_platform"
        and c.get("deletion_detected_at")
        and datetime.fromisoformat(c["deletion_detected_at"]) > window_start
    ]

    print(f"[MONITOR] 直近{DELETION_WINDOW_HOURS}時間の削除数: {len(recent_deletions)}件")

    # --- 保護モード発動 ---
    if len(recent_deletions) >= DELETION_THRESHOLD:
        print(f"\n[ALERT] !! 削除閾値({DELETION_THRESHOLD}件)超過 → 保護モード発動 !!")

        # Step1: 残りのアクティブコメントを予防削除
        active_to_delete = [c for c in recent_comments if c.get("status") == "active"]
        print(f"[PROTECT] Step1: {len(active_to_delete)}件を予防削除...")
        for c in active_to_delete:
            if delete_comment(c["comment_id"], token):
                c["status"] = "deleted_by_us"
                c["deletion_detected_at"] = now.isoformat()
            time.sleep(2)

        # Step2: 原因分析
        print(f"\n[ANALYZE] Step2: 削除原因を分析中...")
        analysis = analyze_deletion_cause(recent_deletions, recent_comments)
        print(f"[ANALYZE] 特定された原因:")
        for cause in analysis["causes"]:
            print(f"  - {cause}")

        # Step3: 再発防止策の適用
        print(f"\n[FIX] Step3: 再発防止策を適用中...")
        measures = apply_prevention_measures(state, analysis)
        if measures:
            print(f"[FIX] 適用した対策:")
            for m in measures:
                print(f"  - {m}")
        else:
            print(f"[FIX] 追加対策なし（既に最小設定）")

        # 分析・対策をstateに記録
        if "recovery_history" not in state:
            state["recovery_history"] = []
        state["recovery_history"].append({
            "detected_at": now.isoformat(),
            "deletion_count": len(recent_deletions),
            "causes": analysis["causes"],
            "measures": measures,
            "resumed_at": None
        })
        # 直近10件のみ保持
        state["recovery_history"] = state["recovery_history"][-10:]

        # Step4: 短時間クールダウン後に自動再開
        cooldown_until = (now + timedelta(hours=COOLDOWN_HOURS)).isoformat()
        state["paused_until"] = cooldown_until
        print(f"\n[RESUME] Step4: {COOLDOWN_HOURS}時間クールダウン後に自動再開 → {cooldown_until}")
        print(f"[RESUME] 再開時は調整済み設定で稼働します（停止ではなく戦略変更）")

        # 最新の履歴に再開予定時刻を記録
        state["recovery_history"][-1]["resume_scheduled_at"] = cooldown_until

    elif newly_deleted_count > 0:
        print(f"[WARN] {newly_deleted_count}件の削除を検知（閾値未達。監視継続）")
    else:
        print(f"[OK] 全コメント正常（削除なし）")

    save_state(state)
    print("\n[MONITOR] 完了")


if __name__ == "__main__":
    main()
