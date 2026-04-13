#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
キューの投稿をGoogleスプレッドシートに書き込む。
nightly-generate.yml から生成直後に呼ばれる。

動作:
  1. state/post-queue.json から当日分のキューを読み込む
  2. 7スロット分を整形して sheets_client.write_posts_to_sheet() に渡す
  3. GOOGLE_SHEETS_CREDENTIALS が未設定の場合はスキップ（エラーにしない）
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

# スロット番号マッピング（scheduled_hour + time_slot から判定）
# 2026-04-14確定: ゴールデンタイム最適化7スロット
SLOT_NUMBER_MAP = {
    "めざまし型（06:07）":        1,
    "天体根拠型（07:07）":        2,
    "しいたけ共感型（08:07）":    3,
    "仕事アドバイス型（09:37）":  4,
    "スピ×ラッキー型（12:07）":  5,
    "哲学深掘り型（18:07）":      6,
    "夜恋愛型（20:07）":          7,
}

SLOT_TIME_MAP = {1: "06:07", 2: "07:07", 3: "08:07", 4: "09:37",
                 5: "12:07", 6: "18:07", 7: "20:07"}

SLOT_TARGET_MAP = {
    1: "全員",
    2: "全員",
    3: "①恋愛迷子",
    4: "②仕事迷子",
    5: "③スピ好き",
    6: "②③",
    7: "①恋愛迷子",
}

SLOT_DIRECTION_MAP = {1: "J", 2: "C", 3: "H", 4: "G", 5: "F", 6: "H", 7: "G"}


def load_json(path):
    full = os.path.join(PROJECT_DIR, path)
    if os.path.exists(full):
        with open(full, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_slot_num(post: dict) -> int:
    """投稿アイテムからスロット番号（1〜7）を判定"""
    time_slot = post.get("time_slot", "")
    if time_slot in SLOT_NUMBER_MAP:
        return SLOT_NUMBER_MAP[time_slot]

    # フォールバック: scheduled_hour で判定
    hour = post.get("scheduled_hour", -1)
    if hour == 6:  return 1
    if hour == 7:  return 2
    if hour == 8:  return 3
    if hour == 9:  return 4   # 09:37
    if hour == 12: return 5
    if hour == 18: return 6
    if hour == 20: return 7

    return 0  # 不明


def main():
    # GOOGLE_SHEETS_CREDENTIALS 未設定ならスキップ
    if not os.environ.get("GOOGLE_SHEETS_CREDENTIALS"):
        print("[SHEETS] GOOGLE_SHEETS_CREDENTIALS 未設定 → Sheets書き込みスキップ")
        return

    queue = load_json("state/post-queue.json")
    queued = [p for p in queue.get("queue", []) if p.get("status") == "queued"]

    if not queued:
        print("[SHEETS] キューが空 → Sheets書き込みスキップ")
        return

    today_str = datetime.now(JST).strftime("%Y-%m-%d")

    # 今日分のみ抽出（ID に今日の日付を含む）
    today_id_prefix = f"post_{today_str.replace('-', '')}"
    today_posts = [p for p in queued if today_id_prefix in p.get("id", "")]

    # 今日分が取れない場合は全queued分を使う
    if not today_posts:
        today_posts = queued
        print(f"[SHEETS] 今日ID未マッチ → 全queued {len(today_posts)}件を書き込み")
    else:
        print(f"[SHEETS] 今日({today_str})分: {len(today_posts)}件")

    # Sheetsに渡す形式に変換
    sheet_posts = []
    used_slots = set()

    for p in today_posts:
        slot_num = get_slot_num(p)
        if slot_num == 0:
            continue
        # 同一スロットが複数ある場合は最初の1件のみ
        if slot_num in used_slots:
            continue
        used_slots.add(slot_num)

        # 品質チェック詳細（warnings から生成）
        warnings = p.get("quality_warnings", [])
        check_detail = "品質OK" if not warnings else f"注意: {'; '.join(warnings[:2])}"

        sheet_posts.append({
            "slot":         slot_num,
            "time":         SLOT_TIME_MAP.get(slot_num, ""),
            "target":       SLOT_TARGET_MAP.get(slot_num, ""),
            "direction":    SLOT_DIRECTION_MAP.get(slot_num, ""),
            "content":      p.get("content", ""),
            "agent_score":  p.get("quality_score", ""),
            "check_detail": check_detail,
            "scheduled_hour": p.get("scheduled_hour", 0),
            "time_slot":    p.get("time_slot", ""),
        })

    if not sheet_posts:
        print("[SHEETS] 書き込み対象なし → スキップ")
        return

    # スロット番号でソート
    sheet_posts.sort(key=lambda x: x["slot"])

    # 書き込み
    from agents.shared.sheets_client import write_posts_to_sheet
    success = write_posts_to_sheet(sheet_posts, today_str)

    if success:
        print(f"[SHEETS] ✅ {len(sheet_posts)}件を {today_str} シートに書き込み完了")
        for p in sheet_posts:
            print(f"  スロット{p['slot']}（{p['time']}）{p['content'][:30]}...")
    else:
        print("[SHEETS] ❌ 書き込み失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
