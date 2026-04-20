#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
競合分析データをGoogle Sheetsに書き出す。
account-timeline.yml の extract_competitor_buzz ステップ後に実行される。

必要シークレット:
  GOOGLE_SHEETS_CREDENTIALS  - GCPサービスアカウントのJSONキー（文字列）
  GOOGLE_SHEETS_ID           - スプレッドシートID
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUZZ_PATH = os.path.join(PROJECT_DIR, "state", "competitor-buzz-references.json")
SPREADSHEET_ID = os.environ.get("GOOGLE_SHEETS_ID", "124lW4BIn11nMBxiStcquHBKWUGv362vUWBUCNpWDa9k")


def get_gc():
    import gspread

    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
    if not creds_json:
        raise RuntimeError("GOOGLE_SHEETS_CREDENTIALS が未設定です")

    creds_dict = json.loads(creds_json)
    return gspread.service_account_from_dict(creds_dict)


def ensure_sheet(spreadsheet, title: str):
    """シートが存在しなければ作成して返す"""
    try:
        return spreadsheet.worksheet(title)
    except Exception:
        return spreadsheet.add_worksheet(title=title, rows=1000, cols=20)


def write_sheet(ws, rows: list[list]):
    ws.clear()
    if rows:
        ws.update(rows, value_input_option="RAW")


def main():
    if not os.path.exists(BUZZ_PATH):
        print(f"[WARN] {BUZZ_PATH} が存在しません。スキップします。")
        sys.exit(0)

    with open(BUZZ_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    gc = get_gc()
    ss = gc.open_by_key(SPREADSHEET_ID)

    updated_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    # ── Sheet 1: 投稿一覧 ──────────────────────────────────
    posts = data.get("top_overall", [])
    # カテゴリ別も追加（重複除去）
    seen_hooks = {p["first_line"] for p in posts}
    for cat_posts in data.get("top_by_category", {}).values():
        for p in cat_posts:
            if p["first_line"] not in seen_hooks:
                posts.append(p)
                seen_hooks.add(p["first_line"])

    post_rows = [["アカウント", "フック（1行目）", "投稿全文（300字）", "いいね", "返信", "ER", "フックパターン", "構造", "カテゴリ"]]
    for p in sorted(posts, key=lambda x: x.get("pseudo_er", 0), reverse=True):
        post_rows.append([
            f"@{p.get('handle', '')}",
            p.get("first_line", ""),
            p.get("text", "").replace("\n", " "),
            p.get("likes", 0),
            p.get("replies", 0),
            round(p.get("pseudo_er", 0) * 100, 1),  # %表示
            p.get("hook_pattern", ""),
            p.get("structure", ""),
            p.get("category", ""),
        ])

    ws1 = ensure_sheet(ss, "投稿一覧")
    write_sheet(ws1, post_rows)
    print(f"[OK] 投稿一覧: {len(post_rows)-1}件")

    # ── Sheet 2: フックパターン分析 ────────────────────────
    hook_summary = data.get("hook_pattern_summary", {})
    hook_rows = [["フックパターン", "件数", "平均ER%", "例①", "例②", "例③"]]
    for pattern, v in sorted(hook_summary.items(), key=lambda x: x[1].get("avg_er", 0), reverse=True):
        examples = v.get("examples", [])
        ex = [e.get("first_line", "") for e in examples]
        while len(ex) < 3:
            ex.append("")
        hook_rows.append([
            pattern,
            v.get("count", 0),
            round(v.get("avg_er", 0) * 100, 1),
            ex[0], ex[1], ex[2],
        ])

    ws2 = ensure_sheet(ss, "フックパターン分析")
    write_sheet(ws2, hook_rows)
    print(f"[OK] フックパターン分析: {len(hook_rows)-1}件")

    # ── Sheet 3: 構造分析 ──────────────────────────────────
    struct_summary = data.get("structure_summary", {})
    struct_rows = [["構造タイプ", "件数", "平均ER%", "本文例①（120字）", "本文例②（120字）"]]
    for struct, v in sorted(struct_summary.items(), key=lambda x: x[1].get("avg_er", 0), reverse=True):
        examples = v.get("examples", [])
        ex = [e.get("text_preview", "") for e in examples]
        while len(ex) < 2:
            ex.append("")
        struct_rows.append([
            struct,
            v.get("count", 0),
            round(v.get("avg_er", 0) * 100, 1),
            ex[0], ex[1],
        ])

    ws3 = ensure_sheet(ss, "構造分析")
    write_sheet(ws3, struct_rows)
    print(f"[OK] 構造分析: {len(struct_rows)-1}件")

    # ── Sheet 4: サマリ ────────────────────────────────────
    guidance = data.get("writer_guidance", {})
    summary_rows = [
        ["更新日時", updated_at],
        ["収集アカウント数", data.get("source_accounts", 0)],
        ["総投稿数", data.get("total_posts", 0)],
        ["バズ投稿数", data.get("buzz_posts_count", 0)],
        [],
        ["── インサイト ──"],
        [guidance.get("insight", "")],
        [],
        ["── ER上位フックパターン（順位） ──"],
    ]
    for i, hp in enumerate(guidance.get("top_hook_patterns_by_er", []), 1):
        summary_rows.append([f"{i}位", hp])

    summary_rows += [[], ["── ER上位構造タイプ ──"]]
    for i, st in enumerate(guidance.get("top_structures_by_er", []), 1):
        summary_rows.append([f"{i}位", st])

    summary_rows += [[], ["── 高ERフック文例 ──"]]
    for ex in guidance.get("strong_first_line_examples", []):
        summary_rows.append([ex])

    ws4 = ensure_sheet(ss, "サマリ")
    write_sheet(ws4, summary_rows)
    print(f"[OK] サマリ書き込み完了")

    # ── Sheet 5: コメントログ（直近200件）────────────────────
    comment_log_path = os.path.join(PROJECT_DIR, "state", "comment-log.json")
    if os.path.exists(comment_log_path):
        with open(comment_log_path, "r", encoding="utf-8") as f:
            comment_data = json.load(f)
        logs = comment_data.get("logs", [])[-200:]

        log_rows = [["日時", "コメント種別", "コメント本文", "コメントユーザー", "投稿フック", "投稿パターン", "返信済み", "返信内容"]]
        for entry in reversed(logs):  # 新しい順
            log_rows.append([
                entry.get("logged_at", "")[:16].replace("T", " "),
                entry.get("comment_type", ""),
                entry.get("comment_text", ""),
                f"@{entry.get('commenter', '')}",
                entry.get("post_hook", ""),
                entry.get("post_pattern", ""),
                "✅" if entry.get("replied") else "−",
                entry.get("reply_text", "")[:60],
            ])

        ws5 = ensure_sheet(ss, "コメントログ")
        write_sheet(ws5, log_rows)
        print(f"[OK] コメントログ: {len(log_rows)-1}件")

        # ── Sheet 6: コメント集計 ──────────────────────────────
        from collections import defaultdict, Counter

        # 種別分布
        type_counter = Counter(e.get("comment_type", "不明") for e in logs)
        # パターン×種別クロス集計
        pattern_type: dict = defaultdict(lambda: defaultdict(int))
        for e in logs:
            pattern_type[e.get("post_pattern", "不明")][e.get("comment_type", "不明")] += 1

        all_types = sorted(type_counter.keys())
        agg_rows = [["投稿パターン"] + all_types + ["合計"]]
        for pattern, type_counts in sorted(pattern_type.items(), key=lambda x: -sum(x[1].values())):
            row = [pattern] + [type_counts.get(t, 0) for t in all_types]
            row.append(sum(type_counts.values()))
            agg_rows.append(row)
        # 合計行
        total_row = ["【合計】"] + [type_counter.get(t, 0) for t in all_types]
        total_row.append(sum(type_counter.values()))
        agg_rows.append(total_row)

        ws6 = ensure_sheet(ss, "コメント集計")
        write_sheet(ws6, agg_rows)
        print(f"[OK] コメント集計: {len(agg_rows)-1}パターン")
    else:
        print("[INFO] comment-log.json 未生成（コメントデータ蓄積待ち）")

    # ── Sheet 7: 未使用高ERパターン（②）──────────────────────
    history_path = os.path.join(PROJECT_DIR, "state", "post-history.json")
    if os.path.exists(history_path):
        with open(history_path, "r", encoding="utf-8") as f:
            history_data = json.load(f)
        used_patterns = {p.get("pattern_name", "") for p in history_data.get("posts", [])}

        hook_summary = data.get("hook_pattern_summary", {})
        unused_rows = [["競合フックパターン", "競合平均ER%", "競合件数", "yozora使用状況", "競合文例①", "競合文例②"]]
        for pattern, v in sorted(hook_summary.items(), key=lambda x: x[1].get("avg_er", 0), reverse=True):
            # 類似パターン名がpost-historyにあるか簡易チェック
            pattern_lower = pattern.replace("型", "").replace("系", "")
            is_used = any(pattern_lower in p for p in used_patterns)
            status = "使用中" if is_used else "⚠️ 未使用"
            examples = [e.get("first_line", "") for e in v.get("examples", [])]
            while len(examples) < 2:
                examples.append("")
            unused_rows.append([
                pattern,
                round(v.get("avg_er", 0) * 100, 1),
                v.get("count", 0),
                status,
                examples[0],
                examples[1],
            ])

        ws7 = ensure_sheet(ss, "未使用高ERパターン")
        write_sheet(ws7, unused_rows)
        print(f"[OK] 未使用高ERパターン: {len(unused_rows)-1}件")

    # ── Sheet 8: トレンド監視（⑤）────────────────────────────
    trend_path = os.path.join(PROJECT_DIR, "state", "competitor-buzz-trend.json")
    if os.path.exists(trend_path):
        with open(trend_path, "r", encoding="utf-8") as f:
            trend_data = json.load(f)
        snapshots = trend_data.get("snapshots", [])

        if len(snapshots) >= 2:
            latest = snapshots[-1]
            prev = snapshots[-2]
            latest_hooks = latest.get("hook_pattern_summary", {})
            prev_hooks = prev.get("hook_pattern_summary", {})
            all_patterns = set(latest_hooks) | set(prev_hooks)

            trend_rows = [["フックパターン", f"直近({latest['date']})", f"前回({prev['date']})", "変化(pt)", "トレンド"]]
            changes = []
            for pattern in all_patterns:
                cur_er = latest_hooks.get(pattern, {}).get("avg_er", 0) * 100
                prv_er = prev_hooks.get(pattern, {}).get("avg_er", 0) * 100
                diff = round(cur_er - prv_er, 1)
                trend = "🔺急上昇" if diff >= 5 else "📈上昇" if diff > 0 else "📉下降" if diff < -5 else "→横ばい"
                if pattern not in prev_hooks:
                    trend = "🆕新出現"
                changes.append([pattern, round(cur_er, 1), round(prv_er, 1), diff, trend])
            changes.sort(key=lambda x: x[3], reverse=True)
            trend_rows += changes
        else:
            trend_rows = [["状態"], [f"スナップショット蓄積中（現在{len(snapshots)}日分）。2日分以上で比較開始"]]

        ws8 = ensure_sheet(ss, "トレンド監視")
        write_sheet(ws8, trend_rows)
        print(f"[OK] トレンド監視シート更新")

    # ── Sheet 9: 返信品質（④）────────────────────────────────
    if os.path.exists(comment_log_path):
        logs = json.load(open(comment_log_path, "r", encoding="utf-8")).get("logs", [])
        if logs:
            from collections import defaultdict

            # 返信率：コメント種別別
            type_stats: dict = defaultdict(lambda: {"total": 0, "replied": 0, "repeat": 0})
            for e in logs:
                t = e.get("comment_type", "不明")
                type_stats[t]["total"] += 1
                if e.get("replied"):
                    type_stats[t]["replied"] += 1
                if e.get("is_repeat"):
                    type_stats[t]["repeat"] += 1

            quality_rows = [["コメント種別", "件数", "返信済み", "返信率%", "リピーター数", "リピーター率%"]]
            for t, s in sorted(type_stats.items(), key=lambda x: -x[1]["total"]):
                total = s["total"]
                replied = s["replied"]
                repeat = s["repeat"]
                quality_rows.append([
                    t, total, replied,
                    round(replied / total * 100, 1) if total else 0,
                    repeat,
                    round(repeat / total * 100, 1) if total else 0,
                ])

            # パターン別リピーター率
            pattern_repeat: dict = defaultdict(lambda: {"total": 0, "repeat": 0})
            for e in logs:
                p = e.get("post_pattern", "不明")
                pattern_repeat[p]["total"] += 1
                if e.get("is_repeat"):
                    pattern_repeat[p]["repeat"] += 1

            quality_rows += [[], ["── 投稿パターン別リピーター率 ──"], ["投稿パターン", "コメント数", "リピーター数", "リピーター率%"]]
            for p, s in sorted(pattern_repeat.items(), key=lambda x: -x[1]["total"]):
                total = s["total"]
                repeat = s["repeat"]
                quality_rows.append([
                    p, total, repeat,
                    round(repeat / total * 100, 1) if total else 0,
                ])

            ws9 = ensure_sheet(ss, "返信品質")
            write_sheet(ws9, quality_rows)
            print(f"[OK] 返信品質シート更新")

    print(f"\n✅ Sheets更新完了: https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")


if __name__ == "__main__":
    main()
