#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日次PDCAサイクル実行スクリプト
毎日21:30 JSTに実行。直近7日間の実績を分析し winning-patterns.json を更新する。
generate_posts.py が翌日以降の投稿生成でこのデータを参照して自動改善する。

Plan: 前日データに基づいて翌日戦略を決定
Do:   generate_posts.py が実行（別ワークフロー）
Check: daily-report.yml が実行（21:05 JST）
Act:  このスクリプトが実行（21:30 JST）→ winning-patterns.json 更新
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOOKBACK_DAYS = 7       # 分析対象日数
MIN_SAMPLES   = 3       # パターン採用に必要な最小サンプル数
ER_STRONG     = 15.0    # 強パターン判定ER閾値（%）
ER_WEAK       = 7.0     # 弱パターン判定ER閾値（%）
AVOID_STREAK  = 3       # 何日連続でER<ER_WEAKなら回避対象にするか


def load_json(path):
    full = os.path.join(PROJECT_DIR, path)
    if os.path.exists(full):
        with open(full, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path, data):
    full = os.path.join(PROJECT_DIR, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_reports(days: int) -> list[dict]:
    """直近N日分のレポートを新しい順で返す"""
    reports = []
    today = datetime.now(JST).date()
    reports_dir = os.path.join(PROJECT_DIR, "state", "reports")
    for i in range(1, days + 1):
        target = (today - timedelta(days=i)).isoformat()
        path = os.path.join(reports_dir, f"{target}.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                d["_date"] = target
                reports.append(d)
            except Exception:
                pass
    return reports


def aggregate_patterns(reports: list[dict]) -> dict:
    """
    複数日のレポートからパターン別集計を返す。
    戻り値: {pattern_name: {total_views, total_er, count, days_active, daily_ers}}
    """
    stats: dict = defaultdict(lambda: {
        "total_views": 0.0,
        "total_er": 0.0,
        "count": 0,
        "days_active": 0,
        "daily_ers": [],
    })
    for report in reports:
        patterns = report.get("patterns", {})
        for pattern_name, pdata in patterns.items():
            if not pattern_name or pdata.get("count", 0) == 0:
                continue
            s = stats[pattern_name]
            s["total_views"] += pdata.get("avg_views", 0) * pdata.get("count", 1)
            s["total_er"]    += pdata.get("avg_er", 0) * pdata.get("count", 1)
            s["count"]       += pdata.get("count", 1)
            s["days_active"] += 1
            s["daily_ers"].append(pdata.get("avg_er", 0))
    return dict(stats)


def calc_trend(daily_ers: list[float]) -> str:
    """直近3日以上あれば前半/後半比較でトレンドを返す"""
    if len(daily_ers) < 3:
        return "データ不足"
    mid = len(daily_ers) // 2
    recent_avg = sum(daily_ers[:mid]) / mid
    older_avg  = sum(daily_ers[mid:]) / (len(daily_ers) - mid)
    diff = recent_avg - older_avg
    if diff >= 3:
        return "↑上昇中"
    if diff <= -3:
        return "↓下降中"
    return "→横ばい"


def build_top_patterns(stats: dict, n: int = 8) -> list[dict]:
    """ER降順のトップパターンリストを生成"""
    ranked = []
    for name, s in stats.items():
        if s["count"] < MIN_SAMPLES:
            continue
        avg_views = s["total_views"] / s["count"]
        avg_er    = s["total_er"]    / s["count"]
        weight    = min(10, max(1, round(avg_er / 3)))  # ER÷3 → weight(1-10)
        ranked.append({
            "pattern": name,
            "avg_views": round(avg_views, 1),
            "avg_er": round(avg_er, 2),
            "avg_engagement": round(avg_er, 2),  # generate_posts.py互換
            "count": s["count"],
            "trend": calc_trend(s["daily_ers"]),
            "weight": weight,
        })
    ranked.sort(key=lambda x: x["avg_er"], reverse=True)
    return ranked[:n]


def build_slot_best(reports: list[dict]) -> dict:
    """時間帯別の最良パターンを返す {hour_str: pattern_name}"""
    # post-history.json から時間帯×パターンのER実績を収集
    history = load_json("state/post-history.json")
    hour_pattern: dict = defaultdict(lambda: defaultdict(lambda: {"total_er": 0.0, "count": 0}))

    for post in history.get("posts", []):
        pa = post.get("posted_at", "")
        if not pa:
            continue
        try:
            dt = datetime.fromisoformat(pa)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            hour = str(dt.astimezone(JST).hour)
        except Exception:
            continue

        pattern = post.get("pattern_name", "")
        metrics = post.get("metrics", {})
        er = metrics.get("engagement_rate", 0) or 0
        views = metrics.get("views", 0) or 0
        if views < 10 or not pattern:
            continue
        hour_pattern[hour][pattern]["total_er"] += er
        hour_pattern[hour][pattern]["count"]    += 1

    slot_best = {}
    for hour, patterns in hour_pattern.items():
        best_name, best_data = max(
            patterns.items(),
            key=lambda x: x[1]["total_er"] / max(x[1]["count"], 1)
        )
        count = best_data["count"]
        if count >= 2:
            slot_best[hour] = {
                "best_pattern":  best_name,
                "avg_eng":       round(best_data["total_er"] / count, 2),
                "avg_views":     0.0,  # post-historyにviews情報がなければ0
                "sample_count":  count,
            }
    return slot_best


def build_avoid_patterns(stats: dict, prev_winning: dict) -> list[str]:
    """
    直近でER低迷が続くパターンを回避リストに入れる。
    AVOID_STREAK日連続でER<ER_WEAKなら回避対象。
    """
    avoid = []
    for name, s in stats.items():
        if s["days_active"] < AVOID_STREAK:
            continue
        # 直近AVOID_STREAK日分のERが全てER_WEAK未満か
        recent = s["daily_ers"][:AVOID_STREAK]
        if len(recent) >= AVOID_STREAK and all(e < ER_WEAK for e in recent):
            avoid.append(name)
    return avoid


def build_insights(top_patterns: list[dict], avoid_patterns: list[str],
                   reports: list[dict]) -> list[str]:
    """人間が読めるインサイト文を生成"""
    insights = []
    today = datetime.now(JST).strftime("%Y-%m-%d")
    insights.append(f"【{today} 自動更新】直近{LOOKBACK_DAYS}日間の実績に基づき戦略を更新しました。")

    if top_patterns:
        top = top_patterns[0]
        insights.append(
            f"現在最強パターン: 「{top['pattern'][:30]}」"
            f"（平均ER {top['avg_er']}%・{top['trend']}）"
        )
    if len(top_patterns) >= 2:
        second = top_patterns[1]
        insights.append(
            f"2位: 「{second['pattern'][:30]}」（平均ER {second['avg_er']}%）"
        )
    if avoid_patterns:
        insights.append(
            f"低パフォーマンスで回避推奨: {len(avoid_patterns)}パターン"
            f"（{avoid_patterns[0][:20]}... など）"
        )

    # 直近7日のトレンド
    if len(reports) >= 3:
        recent_er  = sum(r.get("summary", {}).get("avg_er", 0) for r in reports[:3]) / 3
        older_er   = sum(r.get("summary", {}).get("avg_er", 0) for r in reports[3:6]) / max(len(reports[3:6]), 1)
        diff = recent_er - older_er
        if diff >= 2:
            insights.append(f"全体ERトレンド: 上昇中（直近3日 {recent_er:.1f}% vs 前3日 {older_er:.1f}%）")
        elif diff <= -2:
            insights.append(f"全体ERトレンド: 下降中（直近3日 {recent_er:.1f}% vs 前3日 {older_er:.1f}%）→ 戦略見直し推奨")
        else:
            insights.append(f"全体ERトレンド: 横ばい（直近3日平均 {recent_er:.1f}%）")
    return insights


def build_action_log_entry(today_str: str, top_patterns: list[dict],
                            avoid_patterns: list[str], prev_winning: dict) -> dict:
    """前回との差分から実施したアクションを記録"""
    actions = []
    prev_top = [p.get("pattern", "") for p in prev_winning.get("top_patterns", [])]
    new_tops = [p["pattern"] for p in top_patterns[:3]]

    for p in new_tops:
        if p not in prev_top:
            actions.append(f"新規採用: 「{p[:30]}」")
    for p in prev_top:
        if p not in new_tops:
            actions.append(f"上位から外れた: 「{p[:30]}」")
    for p in avoid_patterns:
        actions.append(f"回避リストに追加: 「{p[:30]}」")
    if not actions:
        actions.append("大きな変更なし（継続運用）")

    return {
        "date": today_str,
        "actions": actions,
        "top3": new_tops[:3],
        "avoid_count": len(avoid_patterns),
    }


def main():
    today_str = datetime.now(JST).strftime("%Y-%m-%d")
    print(f"[PDCA] {today_str} 日次PDCAサイクル開始")

    # ── データ収集 ──
    reports = load_reports(LOOKBACK_DAYS)
    if not reports:
        print("[PDCA] レポートなし → スキップ")
        return
    print(f"[PDCA] {len(reports)}日分のレポートを読み込み")

    # ── 分析 ──
    stats        = aggregate_patterns(reports)
    top_patterns = build_top_patterns(stats)
    slot_best    = build_slot_best(reports)
    avoid        = build_avoid_patterns(stats, {})
    insights     = build_insights(top_patterns, avoid, reports)

    print(f"[PDCA] パターン分析: {len(stats)}種・上位{len(top_patterns)}件・回避{len(avoid)}件")

    # ── winning-patterns.json 更新（既存フィールドを保持しつつ上書き） ──
    winning = load_json("state/winning-patterns.json")
    prev_winning = json.loads(json.dumps(winning))  # deep copy for diff

    data_count = sum(s["count"] for s in stats.values())
    confidence = "high" if data_count >= 30 else "medium" if data_count >= 10 else "low"

    # generate_posts.py が参照するフィールドを更新
    winning["last_updated"]   = today_str
    winning["data_count"]     = data_count
    winning["confidence"]     = confidence
    winning["top_patterns"]   = top_patterns
    winning["pattern_ranking"] = [
        {"pattern": p["pattern"], "avg_er": p["avg_er"], "avg_views": p["avg_views"]}
        for p in top_patterns
    ]
    winning["insights"] = insights
    winning["auto_analysis"] = {
        "slot_best_pattern":    slot_best,
        "auto_avoid_patterns":  avoid,
        "last_run":             today_str,
    }
    save_json("state/winning-patterns.json", winning)
    print(f"[PDCA] winning-patterns.json 更新完了（信頼度: {confidence}・データ{data_count}件）")

    # ── アクションログ ──
    action_log = load_json("state/pdca-action-log.json")
    entries = action_log.get("entries", [])
    entry = build_action_log_entry(today_str, top_patterns, avoid, prev_winning)
    entries = [e for e in entries if e.get("date") != today_str]  # 同日は上書き
    entries.append(entry)
    entries = entries[-60:]  # 直近60日分
    save_json("state/pdca-action-log.json", {"entries": entries})

    print(f"[PDCA] アクションログ保存:")
    for a in entry["actions"]:
        print(f"  → {a}")

    # ── Google Sheets PDCA履歴シート更新 ──
    _export_pdca_to_sheets(entries, top_patterns, avoid, insights, today_str)

    print(f"\n[PDCA] 完了 ✅ 明日の generate_posts.py は更新済み戦略で動きます")

    # ── セッション現状把握ファイル更新 ──
    try:
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            "update_session_context",
            pathlib.Path(__file__).parent / "update_session_context.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.main()
    except Exception as e:
        print(f"[PDCA] session-context更新エラー（続行）: {e}")


def _export_pdca_to_sheets(entries, top_patterns, avoid, insights, today_str):
    """Google SheetsのPDCA履歴シートを更新"""
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
    sheet_id   = os.environ.get("GOOGLE_SHEETS_ID",
                                "124lW4BIn11nMBxiStcquHBKWUGv362vUWBUCNpWDa9k")
    if not creds_json:
        print("[PDCA] GOOGLE_SHEETS_CREDENTIALS 未設定 → Sheets書き込みスキップ")
        return

    try:
        import gspread
        gc = gspread.service_account_from_dict(json.loads(creds_json))
        ss = gc.open_by_key(sheet_id)

        # ── PDCA履歴シート ──
        rows = [["日付", "アクション内容", "上位パターン", "回避パターン数"]]
        for e in reversed(entries):
            rows.append([
                e.get("date", ""),
                " / ".join(e.get("actions", [])),
                " > ".join(e.get("top3", [])),
                e.get("avoid_count", 0),
            ])
        ws = _ensure_sheet(ss, "PDCA履歴")
        ws.clear()
        ws.update(rows, value_input_option="RAW")

        # ── 現在の戦略シート ──
        strategy_rows = [
            ["更新日", today_str],
            [],
            ["── 現在の上位パターン ──"],
            ["パターン", "平均ER%", "平均閲覧", "件数", "トレンド", "重み"],
        ]
        for p in top_patterns:
            strategy_rows.append([
                p["pattern"], p["avg_er"], p["avg_views"], p["count"], p["trend"], p["weight"]
            ])
        strategy_rows += [[], ["── 回避推奨パターン ──"]]
        for a in avoid:
            strategy_rows.append([a])
        strategy_rows += [[], ["── インサイト ──"]]
        for ins in insights:
            strategy_rows.append([ins])

        ws2 = _ensure_sheet(ss, "現在の戦略")
        ws2.clear()
        ws2.update(strategy_rows, value_input_option="RAW")

        print("[PDCA] Sheets更新完了（PDCA履歴・現在の戦略）")
    except Exception as e:
        print(f"[PDCA] Sheets更新エラー（続行）: {e}")


def _ensure_sheet(ss, title):
    try:
        return ss.worksheet(title)
    except Exception:
        return ss.add_worksheet(title=title, rows=200, cols=10)


if __name__ == "__main__":
    main()
