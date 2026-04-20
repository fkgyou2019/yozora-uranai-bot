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
    print(f"[DEBUG] client_email: {creds_dict.get('client_email', 'N/A')}")
    print(f"[DEBUG] SPREADSHEET_ID: {SPREADSHEET_ID}")
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

    print(f"\n✅ Sheets更新完了: https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")


if __name__ == "__main__":
    main()
