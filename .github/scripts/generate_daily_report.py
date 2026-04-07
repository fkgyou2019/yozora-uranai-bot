#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
state/daily-report.json を生成するスクリプト。
experiment-hourly.yml の commit ステップ前に毎回呼ばれ、当日データを最新状態に保つ。

「報告してください」→ daily-report.json を読むだけで即完了。

各投稿に: 閲覧数・いいね数・ER・コメント数・パターン・フック を含める。
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8")
JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOOK_LIMIT    = 15   # フック15字以内ルール
SUCCESS_VIEWS = 200  # 再現性テスト成功基準 v≥200（上位30%=良い投稿）
REPRO_TARGET  = "G"  # 再現性テスト対象構造
REPRO_START   = "2026-04-06"
REPRO_JUDGE_H = 24   # 判定タイミング: 投稿から24h経過後のみ計測対象


def load_json(rel):
    full = os.path.join(PROJECT_DIR, rel)
    if os.path.exists(full):
        with open(full, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(rel, data):
    full = os.path.join(PROJECT_DIR, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_first_line(content):
    for line in (content or "").split("\n"):
        s = line.strip()
        if s:
            return s
    return ""


def extract_struct(pattern_name):
    m = re.search(r"構造([A-Z])", pattern_name)
    return m.group(1) if m else "?"


def get_latest_metrics(tracker_snap):
    """growth-tracker スナップショットから最新メトリクスを取得"""
    hourly = tracker_snap.get("hourly", [])
    if not hourly:
        return {}
    latest = hourly[-1]
    views   = latest.get("views", 0) or 0
    likes   = latest.get("likes", 0) or 0
    replies = latest.get("replies", 0) or 0
    reposts = latest.get("reposts", 0) or 0
    er      = latest.get("er", 0) or 0
    return {"views": views, "likes": likes, "replies": replies,
            "reposts": reposts, "engagement_rate": er}


def main():
    now   = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")

    history  = load_json("state/post-history.json")
    perf     = load_json("state/performance-data.json")
    tracker  = load_json("state/post-growth-tracker.json")
    hc_log   = load_json("state/health-check-log.json")

    # ── performance-data を platform_post_id でインデックス化 ────────
    perf_map = {}
    for p in perf.get("posts", []):
        pid = p.get("platform_post_id", "")
        if pid:
            perf_map[pid] = p

    # ── growth-tracker を post_id でインデックス化（優先参照） ────────
    tracker_map = tracker.get("snapshots", {})

    # ── 今日の投稿を post-history から取得 ──────────────────────────
    today_posts = [
        p for p in history.get("posts", [])
        if p.get("posted_at", "")[:10] == today
    ]
    today_posts.sort(key=lambda p: p.get("posted_at", ""))

    posts_out  = []
    violations = []
    views_list = []

    for p in today_posts:
        content     = p.get("content", "")
        pattern     = p.get("pattern_name", "UNKNOWN")
        platform_id = p.get("platform_post_id", "")

        # フック
        hook     = get_first_line(content)
        hook_len = len(hook)
        hook_ok  = 0 < hook_len <= HOOK_LIMIT

        # 投稿時刻 → hour
        posted_at = p.get("posted_at", "")
        try:
            dt   = datetime.fromisoformat(posted_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            hour = dt.astimezone(JST).hour
        except Exception:
            hour = p.get("scheduled_hour", "?")

        # metrics（growth-tracker 優先 → performance-data フォールバック）
        post_id = p.get("id", "")
        tracker_snap = tracker_map.get(post_id, {})
        if tracker_snap.get("hourly"):
            m = get_latest_metrics(tracker_snap)
        else:
            pd = perf_map.get(platform_id, {})
            m  = pd.get("metrics", {})
        views    = m.get("views",    0) or 0
        likes    = m.get("likes",    0) or 0
        replies  = m.get("replies",  0) or 0
        reposts  = m.get("reposts",  0) or 0
        er       = m.get("engagement_rate", 0) or 0
        measured = views > 0  # True = 閲覧数取得済み

        # フック違反
        if not hook_ok and hook_len > 0:
            violations.append(f"{hour}時: {hook_len}字「{hook[:25]}」")

        views_list.append(views)
        posts_out.append({
            "hour":     hour,
            "struct":   extract_struct(pattern),
            "pattern":  pattern,
            "hook":     hook[:40],
            "hook_len": hook_len,
            "hook_ok":  hook_ok,
            "views":    views,
            "likes":    likes,
            "comments": replies,
            "reposts":  reposts,
            "er":       round(er, 2),
            "measured": measured,
        })

    # ── サマリー ─────────────────────────────────────────────────────
    total        = len(posts_out)
    measured_cnt = sum(1 for p in posts_out if p["measured"])
    hook_ok_cnt  = sum(1 for p in posts_out if p["hook_ok"])
    total_views  = sum(p["views"] for p in posts_out)
    avg_views    = round(total_views / measured_cnt, 1) if measured_cnt else 0
    best         = max(posts_out, key=lambda p: p["views"]) if posts_out else {}

    # ── 再現性テスト進捗（構造G・v≥200・投稿24h経過後のみ判定） ──────
    repro_start_dt = datetime.fromisoformat(REPRO_START).replace(tzinfo=JST)
    days_elapsed   = (now.date() - repro_start_dt.date()).days + 1

    all_g = [
        p for p in history.get("posts", [])
        if f"構造{REPRO_TARGET}" in p.get("pattern_name", "")
        and p.get("posted_at", "") >= REPRO_START
    ]
    g_total = len(all_g)

    def get_views_for(p):
        snap = tracker_map.get(p.get("id", ""), {})
        if snap.get("hourly"):
            return get_latest_metrics(snap).get("views", 0) or 0
        return (perf_map.get(p.get("platform_post_id", ""), {})
                        .get("metrics", {}).get("views", 0) or 0)

    def is_24h_elapsed(p):
        try:
            pt = datetime.fromisoformat(p.get("posted_at", ""))
            if pt.tzinfo is None:
                pt = pt.replace(tzinfo=JST)
            return (now - pt).total_seconds() >= REPRO_JUDGE_H * 3600
        except Exception:
            return False

    # 24h経過済みの投稿のみを判定対象とする
    g_judged  = [p for p in all_g if is_24h_elapsed(p)]
    g_measured = [p for p in g_judged if get_views_for(p) > 0]
    g_success  = sum(1 for p in g_measured if get_views_for(p) >= SUCCESS_VIEWS)
    g_rate = round(g_success / len(g_measured) * 100, 1) if g_measured else 0
    g_pending  = len(all_g) - len(g_judged)  # 24h未満の投稿数

    if g_total == 0:
        repro_status = "未開始"
    elif not g_judged:
        repro_status = f"判定待ち（全{g_total}件が24h未満）"
    elif not g_measured:
        repro_status = f"計測待ち（{len(g_judged)}件が24h経過・views取得中）"
    elif g_rate >= 70:
        repro_status = f"SUCCESS: 再現性確認（{g_success}/{len(g_measured)}件）"
    elif g_rate >= 30:
        repro_status = f"テスト中（{g_success}/{len(g_measured)}件成功・{g_pending}件判定待ち）"
    else:
        repro_status = f"WARNING: 成功率低下（{g_success}/{len(g_measured)}件・{g_pending}件判定待ち）"

    # ── HC サマリー ───────────────────────────────────────────────
    hc_checks  = [c for c in hc_log.get("checks", []) if c.get("timestamp", "")[:10] == today]
    hc_deleted = sum(1 for c in hc_checks for r in c.get("results", []) if r.get("deleted"))

    # ── 1行サマリー ───────────────────────────────────────────────
    one_line = (
        f"{today} | {total}件投稿 ({measured_cnt}件計測済み)"
        f" | フックOK {hook_ok_cnt}/{total}件"
        + (f" | 違反: {', '.join(violations)}" if violations else "")
        + (f" | 平均v={avg_views}" if measured_cnt else " | 閲覧数取得待ち")
        + f" | 構造G再現性: {repro_status}"
        + (f" | HC削除: {hc_deleted}件" if hc_deleted else "")
    )

    # ── 出力 ──────────────────────────────────────────────────────
    report = {
        "date":         today,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        "one_line":     one_line,
        "posts":        posts_out,
        "summary": {
            "total":            total,
            "measured":         measured_cnt,
            "hook_ok":          hook_ok_cnt,
            "hook_violations":  len(violations),
            "violations":       violations,
            "total_views":      total_views,
            "avg_views":        avg_views,
            "best_hook":        best.get("hook", ""),
            "best_views":       best.get("views", 0),
            "best_er":          best.get("er", 0),
        },
        "reproducibility_test": {
            "target":           f"構造{REPRO_TARGET}",
            "start_date":       REPRO_START,
            "days_elapsed":     days_elapsed,
            "total_posts":      g_total,
            "judged_posts":     len(g_judged),
            "pending_posts":    g_pending,
            "measured_posts":   len(g_measured),
            "success_posts":    g_success,
            "success_rate":     g_rate,
            "success_criteria": f"v>={SUCCESS_VIEWS} (24h後判定)",
            "status":           repro_status,
        },
        "hc": {
            "runs":    len(hc_checks),
            "deleted": hc_deleted,
        },
    }

    save_json("state/daily-report.json", report)
    print(f"[daily-report] {one_line}", flush=True)


if __name__ == "__main__":
    main()
