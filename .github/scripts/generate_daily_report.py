#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
state/daily-report.json を生成するスクリプト。
experiment-hourly.yml の commit ステップ前に毎回呼ばれ、当日データを最新状態に保つ。

Claudeが「報告してください」と言われたら
  cat state/daily-report.json
の1コマンドで即座に読めるようにする。
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOOK_LIMIT = 15          # 15字以内ルール
SUCCESS_VIEWS = 500      # 再現性テスト成功基準
REPRO_TARGET = "G"       # 再現性テスト対象構造
REPRO_START = "2026-04-06"  # テスト開始日


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
    for line in content.split("\n"):
        s = line.strip()
        if s:
            return s
    return ""


def main():
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")

    history = load_json("state/post-history.json")
    hc_log  = load_json("state/health-check-log.json")

    # ── 今日の投稿 ──────────────────────────────────────────────────
    today_posts = [
        p for p in history.get("posts", [])
        if p.get("posted_at", "")[:10] == today
    ]
    today_posts.sort(key=lambda p: p.get("posted_at", ""))

    posts_out = []
    violations = []
    views_list = []

    for p in today_posts:
        content = p.get("content", "")
        hook = get_first_line(content)
        hook_len = len(hook)
        hook_ok = hook_len <= HOOK_LIMIT and hook_len > 0
        v = p.get("views", 0) or 0
        l = p.get("likes", 0) or 0
        pattern = p.get("pattern_name", "UNKNOWN")
        # 構造名抽出（例: 構造G_xxx → G）
        struct = "?"
        import re
        m = re.search(r"構造([A-Z])", pattern)
        if m:
            struct = m.group(1)

        # 投稿時刻からhourを取得
        posted_at = p.get("posted_at", "")
        try:
            dt = datetime.fromisoformat(posted_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            hour = dt.astimezone(JST).hour
        except Exception:
            hour = p.get("scheduled_hour", "?")

        if not hook_ok and hook_len > 0:
            violations.append(f"{hour}時: {hook_len}字「{hook[:20]}」")

        views_list.append(v)
        posts_out.append({
            "hour":     hour,
            "struct":   struct,
            "hook":     hook[:30],
            "hook_len": hook_len,
            "hook_ok":  hook_ok,
            "views":    v,
            "likes":    l,
        })

    # ── サマリー ─────────────────────────────────────────────────────
    total = len(posts_out)
    hook_ok_count = sum(1 for p in posts_out if p["hook_ok"])
    total_views = sum(views_list)
    avg_views = round(total_views / total, 1) if total else 0
    best = max(posts_out, key=lambda p: p["views"]) if posts_out else {}

    # ── 再現性テスト進捗（構造G・v≥500） ──────────────────────────
    repro_start_dt = datetime.fromisoformat(REPRO_START).replace(tzinfo=JST)
    days_elapsed = (now.date() - repro_start_dt.date()).days + 1

    # 直近35件の構造Gをpost-history全体から収集
    all_g_posts = [
        p for p in history.get("posts", [])
        if f"構造{REPRO_TARGET}" in p.get("pattern_name", "")
        and p.get("posted_at", "") >= REPRO_START
    ]
    g_total = len(all_g_posts)
    # v>0 のもの（閲覧数取得済み）だけで成功率を計算
    g_measured = [p for p in all_g_posts if (p.get("views", 0) or 0) > 0]
    g_success  = sum(1 for p in g_measured if (p.get("views", 0) or 0) >= SUCCESS_VIEWS)
    g_rate     = round(g_success / len(g_measured) * 100, 1) if g_measured else 0

    if g_total == 0:
        repro_status = "未開始"
    elif len(g_measured) == 0:
        repro_status = f"計測待ち（{g_total}件投稿済み・閲覧数取得待ち）"
    elif g_rate >= 70:
        repro_status = "SUCCESS: 再現性確認"
    elif g_rate >= 30:
        repro_status = f"テスト中（計測済み{len(g_measured)}件）"
    else:
        repro_status = f"WARNING: 成功率低下（計測済み{len(g_measured)}件）"

    # ── HC サマリー ───────────────────────────────────────────────
    hc_checks = [
        c for c in hc_log.get("checks", [])
        if c.get("timestamp", "")[:10] == today
    ]
    hc_runs    = len(hc_checks)
    hc_deleted = sum(
        1 for c in hc_checks
        for r in c.get("results", [])
        if r.get("deleted")
    )

    # ── 1行サマリー ───────────────────────────────────────────────
    one_line = (
        f"{total}件投稿"
        f" | フック{hook_ok_count}/{total}件OK({len(violations)}件違反)"
        f" | 平均v={avg_views}"
        f" | 構造G再現性{g_success}/{g_total}件({g_rate}%)"
        f" | HC={hc_runs}回({hc_deleted}件削除)"
    )

    # ── 出力 ──────────────────────────────────────────────────────
    report = {
        "date":         today,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        "one_line":     one_line,
        "posts":        posts_out,
        "summary": {
            "total":           total,
            "hook_ok":         hook_ok_count,
            "hook_violations": len(violations),
            "violations":      violations,
            "total_views":     total_views,
            "avg_views":       avg_views,
            "best_hook":       best.get("hook", ""),
            "best_views":      best.get("views", 0),
        },
        "reproducibility_test": {
            "target":           f"構造{REPRO_TARGET}",
            "start_date":       REPRO_START,
            "days_elapsed":     days_elapsed,
            "total_posts":      g_total,
            "measured_posts":   len(g_measured),
            "success_posts":    g_success,
            "success_rate":     g_rate,
            "success_criteria": f"v>={SUCCESS_VIEWS}",
            "status":           repro_status,
        },
        "hc": {
            "runs":    hc_runs,
            "deleted": hc_deleted,
        },
    }

    save_json("state/daily-report.json", report)
    print(f"[daily-report] {one_line}", flush=True)


if __name__ == "__main__":
    main()
