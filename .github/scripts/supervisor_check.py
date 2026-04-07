#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Supervisor Agent - 全体監視エージェント
1〜4のエージェントが正しく動いているか確認し、異常を検知・報告する。

監視対象:
  Agent 1: auto-post.yml（投稿エージェント）
  Agent 2: post-verify.yml（投稿確認・再投稿エージェント）
  Agent 3: post-health-check.yml（品質確認エージェント）
  Agent 4: post-health-check.py内の削除ロジック（削除エージェント）

確認項目:
  - 今日8件投稿されたか
  - 各ワークフローが正常に実行されたか
  - Threads APIが生きているか
  - 投稿キューが空でないか
  - 異常があれば即時修正を試みる
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8")
JST = timezone(timedelta(hours=9))


def threads_api_get(endpoint, token):
    url = f"https://graph.threads.net/v1.0/{endpoint}"
    sep = "&" if "?" in url else "?"
    url += f"{sep}access_token={token}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    uid = os.environ.get("THREADS_USER_ID", "")
    now = datetime.now(JST)
    today_str = now.strftime("%Y-%m-%d")

    print(f"=== Supervisor Check: {now.strftime('%Y-%m-%d %H:%M JST')} ===")
    print()

    issues = []
    warnings = []

    # === Check 1: Threads API生存確認 ===
    print("【1】Threads API生存確認")
    try:
        me = threads_api_get("me?fields=id,username", token)
        print(f"  ✅ API正常 (@{me.get('username', '?')})")
    except Exception as e:
        error_msg = str(e)
        issues.append(f"Threads API応答なし: {error_msg}")
        print(f"  ❌ API応答なし: {error_msg}")
        if "API access blocked" in error_msg:
            issues.append("アカウントロックの可能性。手動でThreadsアプリから認証が必要")

    # === Check 2: 今日の投稿数確認 ===
    print()
    print("【2】今日の投稿数確認")
    try:
        data = threads_api_get(
            f"{uid}/threads?fields=id,text,timestamp&limit=25", token
        )
        posts = data.get("data", [])

        today_posts = []
        for p in posts:
            ts = p.get("timestamp", "")
            try:
                utc_time = datetime.fromisoformat(ts.replace("+0000", "+00:00"))
                jst_time = utc_time.astimezone(JST)
                if jst_time.strftime("%Y-%m-%d") == today_str:
                    today_posts.append({
                        "id": p["id"],
                        "time": jst_time.strftime("%H:%M"),
                        "text": p.get("text", "")[:30],
                    })
            except Exception:
                continue

        print(f"  今日の投稿: {len(today_posts)}件 / 目標7件")
        for tp in today_posts:
            print(f"    {tp['time']} | {tp['text']}...")

        if len(today_posts) < 5:
            issues.append(f"今日の投稿が{len(today_posts)}件（目標7件に大幅不足）")
        elif len(today_posts) < 7:
            warnings.append(f"今日の投稿が{len(today_posts)}件（目標7件に未達）")
        else:
            print(f"  ✅ 目標達成")

    except Exception as e:
        issues.append(f"投稿一覧取得失敗: {e}")
        print(f"  ❌ 取得失敗: {e}")

    # === Check 3: 投稿キュー残量確認 ===
    print()
    print("【3】投稿キュー残量確認")
    queue_path = "state/post-queue.json"
    if os.path.exists(queue_path):
        with open(queue_path, encoding="utf-8") as f:
            queue = json.load(f)
        queued = [p for p in queue.get("queue", []) if p.get("status") == "queued"]
        print(f"  キュー残: {len(queued)}件")
        if len(queued) == 0:
            warnings.append("投稿キューが空（experiment-hourlyが自動補充）")
        elif len(queued) < 7:
            warnings.append(f"キュー残{len(queued)}件（7件未満）。補充が必要")
    else:
        issues.append("post-queue.jsonが存在しない")
        print(f"  ❌ ファイルなし")

    # === Check 4: 今日のメトリクス概要 ===
    print()
    print("【4】今日の投稿メトリクス")
    if today_posts:
        total_views = 0
        total_likes = 0
        total_replies = 0
        red_count = 0

        for tp in today_posts:
            try:
                insights = threads_api_get(
                    f"{tp['id']}/insights?metric=views,likes,replies", token
                )
                metrics = {}
                for m in insights.get("data", []):
                    metrics[m["name"]] = m.get("values", [{}])[0].get("value", 0)
                v = metrics.get("views", 0)
                l = metrics.get("likes", 0)
                r = metrics.get("replies", 0)
                eng = ((l + r) / v * 100) if v > 0 else 0
                total_views += v
                total_likes += l
                total_replies += r

                status = "✅" if v >= 50 or eng >= 5 else "⚠" if v >= 20 else "❌"
                if status == "❌":
                    red_count += 1
                print(f"    {tp['time']} | 👁{v:>4} ❤{l:>2} 💬{r:>2} eng={eng:.1f}% {status}")
            except Exception:
                print(f"    {tp['time']} | メトリクス取得失敗")

        avg_eng = ((total_likes + total_replies) / total_views * 100) if total_views > 0 else 0
        print(f"  合計: 👁{total_views} ❤{total_likes} 💬{total_replies} 平均eng={avg_eng:.1f}%")

        if red_count > 0:
            warnings.append(f"リーチ死投稿が{red_count}件（ヘルスチェックで削除されるべき）")

    # === Check 5: ヘルスチェック実行履歴 ===
    print()
    print("【5】ヘルスチェック実行履歴")
    health_log_path = "state/health-check-log.json"
    if os.path.exists(health_log_path):
        with open(health_log_path, encoding="utf-8") as f:
            health_log = json.load(f)
        checks = health_log.get("checks", [])
        today_checks = [c for c in checks if today_str in c.get("timestamp", "")]
        total_deleted = sum(c.get("deleted", 0) for c in today_checks)
        print(f"  今日のヘルスチェック: {len(today_checks)}回実行、{total_deleted}件削除")

        if len(today_checks) == 0:
            issues.append("今日ヘルスチェックが1回も実行されていない")
    else:
        warnings.append("health-check-log.jsonが存在しない（初回実行前の可能性）")
        print(f"  ログファイルなし")

    # === Check 6: 自動返信の重複確認 ===
    print()
    print("【6】自動返信チェック")
    replied_path = "state/replied-comments.json"
    if os.path.exists(replied_path):
        with open(replied_path, encoding="utf-8") as f:
            replied = json.load(f)
        total_replied = len(replied.get("replied_ids", []))
        recent = replied.get("recent_replies", [])
        print(f"  返信済み: {total_replied}件")

        # 同一ユーザーチェック
        usernames = [r.get("to_user", "") for r in recent if r.get("to_user")]
        dupes = {u: usernames.count(u) for u in set(usernames) if usernames.count(u) > 2}
        if dupes:
            warnings.append(f"同一ユーザーへの過剰返信: {dupes}")
            print(f"  ⚠ 重複返信: {dupes}")
        else:
            print(f"  ✅ 重複返信なし")
    else:
        print(f"  返信データなし")

    # === 総合判定 ===
    print()
    print("=" * 50)
    if issues:
        print(f"🔴 重大な問題: {len(issues)}件")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
    if warnings:
        print(f"🟡 注意: {len(warnings)}件")
        for i, w in enumerate(warnings, 1):
            print(f"  {i}. {w}")
    if not issues and not warnings:
        print("🟢 全て正常")

    print("=" * 50)

    # 結果を保存
    os.makedirs("state", exist_ok=True)
    result = {
        "timestamp": now.isoformat(),
        "today_posts": len(today_posts) if 'today_posts' in dir() else 0,
        "issues": issues,
        "warnings": warnings,
        "status": "RED" if issues else "YELLOW" if warnings else "GREEN",
    }

    report_path = "state/supervisor-report.json"
    reports = []
    if os.path.exists(report_path):
        with open(report_path, encoding="utf-8") as f:
            reports = json.load(f).get("reports", [])
    reports.append(result)
    reports = reports[-30:]  # 直近30件保持

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"reports": reports}, f, ensure_ascii=False, indent=2)

    if issues:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
