#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
セッション現状把握ファイル生成スクリプト
state/session-context.json を最新状態に更新する。

Claudeがセッション開始時にこのファイルを読むことで、
たちこさんに状況説明を求めずに現状を自力で把握できる。

呼び出し元:
  - daily_pdca_cycle.py（毎晩21:30 JST）
  - auto_reply.py（返信実行後）
  - 単体実行も可: python .github/scripts/update_session_context.py
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta, date

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load(path, default=None):
    full = os.path.join(PROJECT_DIR, path)
    if os.path.exists(full):
        try:
            with open(full, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            pass
    return default if default is not None else {}


def _save(path, data):
    full = os.path.join(PROJECT_DIR, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# 投稿実績（直近7日のreportから集計）
# ─────────────────────────────────────────────
def _posting_stats():
    today = datetime.now(JST).date()
    reports_dir = os.path.join(PROJECT_DIR, "state", "reports")
    total_posts = 0
    total_er = 0.0
    total_views = 0.0
    days_with_data = 0
    last_report_date = None

    for i in range(1, 8):
        target = (today - timedelta(days=i)).isoformat()
        path = os.path.join(reports_dir, f"{target}.json")
        if os.path.exists(path):
            try:
                r = json.load(open(path, encoding="utf-8-sig"))
                summary = r.get("summary", {})
                cnt = summary.get("posts_total", summary.get("total_posts", 0))
                er = summary.get("avg_er", 0)
                views = summary.get("avg_views", 0)
                if cnt > 0:
                    total_posts += cnt
                    total_er += er
                    total_views += views
                    days_with_data += 1
                    if last_report_date is None:
                        last_report_date = target
            except Exception:
                pass

    avg_er = round(total_er / days_with_data, 2) if days_with_data else 0
    avg_views = round(total_views / days_with_data, 1) if days_with_data else 0

    # キュー残数
    queue = _load("state/post-queue.json", {"queue": []})
    queue_remaining = len([p for p in queue.get("queue", []) if p.get("status") == "queued"])

    # 直近投稿日時（post-history.json）
    history = _load("state/post-history.json", {"posts": []})
    posts = sorted(
        [p for p in history.get("posts", []) if p.get("posted_at")],
        key=lambda x: x["posted_at"], reverse=True
    )
    last_posted_at = posts[0].get("posted_at", "不明")[:16] if posts else "不明"

    return {
        "last_7days_total_posts": total_posts,
        "last_7days_avg_er": avg_er,
        "last_7days_avg_views": avg_views,
        "days_with_report": days_with_data,
        "last_report_date": last_report_date or "なし",
        "queue_remaining": queue_remaining,
        "last_posted_at": last_posted_at,
    }


# ─────────────────────────────────────────────
# 自動返信実績（comment-log.json）
# ─────────────────────────────────────────────
def _reply_stats():
    data = _load("state/comment-log.json", {"logs": []})
    logs = data.get("logs", [])

    today = datetime.now(JST).date()
    cutoff = (today - timedelta(days=7)).isoformat()
    recent_logs = [e for e in logs if e.get("logged_at", "") >= cutoff]

    total = len(recent_logs)
    replied = sum(1 for e in recent_logs if e.get("replied"))
    unreplied = total - replied
    reply_rate = round(replied / total * 100, 1) if total else 0

    type_counter = Counter(e.get("comment_type", "不明") for e in recent_logs)
    top_types = [f"{t}:{c}件" for t, c in type_counter.most_common(3)]

    last_run = logs[-1].get("logged_at", "不明")[:16] if logs else "なし"

    # エラー検出（unreplied が多すぎる場合）
    issues = []
    if total > 0 and reply_rate < 50:
        issues.append(f"返信率が低い ({reply_rate}%) → auto_reply.py の動作確認推奨")
    if unreplied >= 5:
        issues.append(f"未返信コメント {unreplied}件 → 手動確認または再実行推奨")

    return {
        "last_7days_comments": total,
        "last_7days_replied": replied,
        "unreplied_count": unreplied,
        "reply_rate_pct": reply_rate,
        "top_comment_types": top_types,
        "last_log_at": last_run,
        "issues": issues,
    }


# ─────────────────────────────────────────────
# PDCA・戦略状況
# ─────────────────────────────────────────────
def _pdca_stats():
    winning = _load("state/winning-patterns.json")
    action_log = _load("state/pdca-action-log.json", {"entries": []})
    entries = action_log.get("entries", [])
    last_entry = entries[-1] if entries else {}

    top_patterns = winning.get("top_patterns", [])
    top1 = top_patterns[0] if top_patterns else {}
    top2 = top_patterns[1] if len(top_patterns) > 1 else {}

    avoid = winning.get("auto_analysis", {}).get("auto_avoid_patterns", [])

    return {
        "last_updated": winning.get("last_updated", "未実行"),
        "confidence": winning.get("confidence", "不明"),
        "data_count": winning.get("data_count", 0),
        "top1_pattern": top1.get("pattern", "なし")[:40],
        "top1_er": top1.get("avg_er", 0),
        "top1_trend": top1.get("trend", ""),
        "top2_pattern": top2.get("pattern", "なし")[:40],
        "top2_er": top2.get("avg_er", 0),
        "avoid_count": len(avoid),
        "avoid_patterns": [a[:30] for a in avoid[:3]],
        "last_actions": last_entry.get("actions", [])[:3],
    }


# ─────────────────────────────────────────────
# ワークフロー最終実行状況（system-statusから推定）
# ─────────────────────────────────────────────
def _workflow_status():
    status = _load("state/system-status.json")
    history = _load("state/post-history.json", {"posts": []})
    posts = history.get("posts", [])

    today = datetime.now(JST).date()
    today_str = today.isoformat()

    # 今日の投稿数
    today_posts = [p for p in posts if p.get("posted_at", "")[:10] == today_str]

    # 連続エラー数
    consecutive_errors = status.get("consecutive_errors", 0)
    kill_switch = status.get("kill_switch", False)

    issues = []
    if kill_switch:
        issues.append("⚠️ kill_switch が TRUE → 投稿停止中！")
    if consecutive_errors >= 3:
        issues.append(f"⚠️ 連続エラー {consecutive_errors}回 → poster.py 要確認")
    if len(today_posts) == 0:
        # 今日の午後以降なら警告
        jst_now = datetime.now(JST)
        if jst_now.hour >= 13:
            issues.append(f"⚠️ 今日の投稿が0件 ({today_str}) → スケジュール確認推奨")

    return {
        "kill_switch": kill_switch,
        "consecutive_errors": consecutive_errors,
        "today_post_count": len(today_posts),
        "issues": issues,
    }


# ─────────────────────────────────────────────
# 推奨アクション生成
# ─────────────────────────────────────────────
def _build_next_actions(posting, reply, pdca, workflow):
    actions = []

    # 緊急アラート
    if workflow["kill_switch"]:
        actions.append("🚨 【緊急】kill_switch=true → 投稿完全停止。原因を確認して false に戻す")
    if workflow["consecutive_errors"] >= 3:
        actions.append(f"🚨 連続エラー{workflow['consecutive_errors']}回 → poster.py / Threads API 要確認")

    # 返信関連
    if reply["unreplied_count"] >= 5:
        actions.append(f"💬 未返信コメント{reply['unreplied_count']}件 → auto_reply.py を手動実行推奨")
    if reply["reply_rate_pct"] < 50 and reply["last_7days_comments"] > 0:
        actions.append(f"💬 返信率{reply['reply_rate_pct']}% → auto_reply.py の動作確認")

    # PDCA関連
    if pdca["last_updated"] == "未実行":
        actions.append("📊 PDCAがまだ1度も実行されていない → daily-pdca.yml の確認推奨")
    elif pdca["confidence"] == "low":
        actions.append(f"📊 PDCA信頼度low ({pdca['data_count']}件) → データ蓄積中。正常")

    # 投稿関連
    if posting["queue_remaining"] <= 2:
        actions.append(f"📝 キュー残{posting['queue_remaining']}件 → nightly-generate を手動実行推奨")
    if posting["days_with_report"] == 0:
        actions.append("📈 過去7日のレポートなし → daily-report.yml の動作確認推奨")

    if not actions:
        actions.append("✅ 特に問題なし。通常運用中。")

    return actions


# ─────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────
def build_session_context() -> dict:
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    posting = _posting_stats()
    reply = _reply_stats()
    pdca = _pdca_stats()
    workflow = _workflow_status()
    next_actions = _build_next_actions(posting, reply, pdca, workflow)

    # 全issuesを集約
    all_issues = (
        reply.get("issues", []) +
        workflow.get("issues", [])
    )
    overall = "ERROR" if workflow["kill_switch"] or workflow["consecutive_errors"] >= 3 \
              else "WARNING" if all_issues \
              else "OK"

    return {
        "_readme": "Claudeがセッション開始時に読む現状把握ファイル。各ワークフローが自動更新する。",
        "generated_at": now_str,
        "overall_status": overall,
        "issues": all_issues,
        "next_actions": next_actions,
        "posting": posting,
        "auto_reply": {
            **{k: v for k, v in reply.items() if k != "issues"},
        },
        "pdca": pdca,
        "workflow": workflow,
    }


def main():
    ctx = build_session_context()
    _save("state/session-context.json", ctx)

    print(f"[SESSION-CTX] {ctx['generated_at']} 更新完了 (overall: {ctx['overall_status']})")
    if ctx["issues"]:
        for issue in ctx["issues"]:
            print(f"  ⚠️  {issue}")
    print(f"  投稿: 直近7日 {ctx['posting']['last_7days_total_posts']}件 / 平均ER {ctx['posting']['last_7days_avg_er']}%")
    print(f"  返信: 直近7日 {ctx['auto_reply']['last_7days_comments']}件コメント / 返信率 {ctx['auto_reply']['reply_rate_pct']}%")
    print(f"  PDCA: 最終更新 {ctx['pdca']['last_updated']} / 信頼度 {ctx['pdca']['confidence']}")
    print(f"  キュー残: {ctx['posting']['queue_remaining']}件")
    print("  次にやること:")
    for a in ctx["next_actions"]:
        print(f"    → {a}")


if __name__ == "__main__":
    main()
