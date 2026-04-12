#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
特定スロットの投稿を再生成する（Sheetsの再作成🔄チェック連動）

使い方:
  python regenerate_slot.py --slot 3 --memo "恋愛より仕事寄りで" --date "2026-04-13"

処理フロー:
  1. スロット番号からEXPERIMENT_TIME_SLOTS設定を取得
  2. たちこさんのメモ（修正指示）を加えてClaude APIで再生成
  3. キュー（post-queue.json）内の該当投稿を更新
  4. Sheets（sheets_client）に更新を書き込み → たちこさんが再確認
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_DIR, ".github", "scripts"))
sys.path.insert(0, PROJECT_DIR)

# generate_posts.py から必要な関数をインポート
from generate_posts import (
    EXPERIMENT_TIME_SLOTS,
    build_experiment_slot_prompt,
    call_claude_api_single,
    build_learning_block,
    load_json,
    save_json,
)

# スロット番号（1〜7）→ EXPERIMENT_TIME_SLOTS インデックス（0〜6）
SLOT_TO_INDEX = {i + 1: i for i in range(len(EXPERIMENT_TIME_SLOTS))}


def parse_args():
    parser = argparse.ArgumentParser(description="指定スロットの投稿を再生成")
    parser.add_argument("--slot", required=True, type=str, help="スロット番号（1〜7）")
    parser.add_argument("--memo", default="", help="たちこさんからの修正指示")
    parser.add_argument("--date", default="", help="対象日（YYYY-MM-DD）")
    return parser.parse_args()


def read_env():
    """api-keys.env を読み込む"""
    env_path = os.path.join(PROJECT_DIR, "config", "api-keys.env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def build_regen_prompt(slot_info: dict, today: str, memo: str, learning_block: str,
                       previous_content: str) -> str:
    """再生成用プロンプト（通常プロンプト + メモ + 前回NGの内容を追記）"""
    base_prompt = build_experiment_slot_prompt(slot_info, today, [], learning_block)

    # メモ・前回NGの情報を末尾に追記
    extra_lines = []
    if memo:
        extra_lines.append(f"\n【📝 たちこさんからの修正指示】\n{memo}")
        extra_lines.append("↑ この指示を最優先で反映してください。")

    if previous_content:
        prev_hook = previous_content.split("\n")[0][:40]
        extra_lines.append(
            f"\n【🚫 前回の投稿（これとは全く異なる内容で生成）】\n「{prev_hook}...」"
            f"\n※ 同じフック、同じ星座の組み合わせ、同じ書き出しは使わない"
        )

    if extra_lines:
        # JSONの手前に挿入
        base_prompt = base_prompt.rstrip()
        insert_pos = base_prompt.rfind("JSON形式で1件返してください")
        if insert_pos >= 0:
            base_prompt = (base_prompt[:insert_pos]
                           + "\n".join(extra_lines) + "\n\n"
                           + base_prompt[insert_pos:])
        else:
            base_prompt += "\n" + "\n".join(extra_lines)

    return base_prompt


def find_queue_item(queue: dict, slot_num: int, date_str: str):
    """キューから該当スロットのアイテムを探す"""
    # 対象スロットのtime_slot文字列
    idx = SLOT_TO_INDEX.get(slot_num)
    if idx is None:
        return None, -1
    target_slot_str = EXPERIMENT_TIME_SLOTS[idx]["slot"]
    target_hour = EXPERIMENT_TIME_SLOTS[idx]["hour"]

    # 日付ID prefix（例: post_20260413_...）
    date_id = date_str.replace("-", "") if date_str else ""

    for i, p in enumerate(queue.get("queue", [])):
        if p.get("status") not in ("queued", "skipped_time"):
            continue
        # ID で日付マッチ
        post_id = p.get("id", "")
        if date_id and date_id not in post_id:
            continue
        # time_slot でスロットマッチ
        if p.get("time_slot") == target_slot_str:
            return p, i
        # フォールバック: scheduled_hour
        if p.get("scheduled_hour") == target_hour:
            return p, i

    return None, -1


def run_quality_check(content: str) -> tuple[float, str]:
    """簡易品質チェック（文字数・行数・フック長）"""
    score = 8.0
    issues = []

    if len(content) < 100:
        score -= 1.0
        issues.append(f"文字数不足({len(content)}文字)")
    if len(content) > 350:
        score -= 0.5
        issues.append(f"文字数過多({len(content)}文字)")

    lines = [l for l in content.split("\n") if l.strip()]
    if lines:
        hook = lines[0]
        hook_clean = re.sub(r"[#＃🔮✨🌙⭐🌟].*$", "", hook).strip()
        if len(hook_clean) > 15:
            score -= 1.0
            issues.append(f"フック長すぎ({len(hook_clean)}文字: 「{hook_clean[:20]}」)")

    detail = "品質OK" if not issues else f"注意: {'; '.join(issues)}"
    return round(max(score, 5.0), 1), detail


def main():
    args = parse_args()
    read_env()

    slot_num = int(args.slot)
    memo = args.memo.strip()
    date_str = args.date.strip() or datetime.now(JST).strftime("%Y-%m-%d")

    print(f"[REGEN] スロット{slot_num} 再生成開始")
    print(f"[REGEN] 対象日: {date_str}")
    print(f"[REGEN] メモ: {memo or '（なし）'}")

    # スロット設定を取得
    idx = SLOT_TO_INDEX.get(slot_num)
    if idx is None:
        print(f"[REGEN] ERROR: スロット番号{slot_num}は1〜7の範囲外")
        sys.exit(1)
    slot_info = EXPERIMENT_TIME_SLOTS[idx]

    # APIキー確認
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[REGEN] ERROR: ANTHROPIC_API_KEY が未設定")
        sys.exit(1)

    # 今日の日付文字列（プロンプト用）
    today = datetime.now(JST).strftime("%Y年%m月%d日(%a)")

    # 学習データ読み込み
    winning = load_json("state/winning-patterns.json")
    learning_block = build_learning_block(winning)

    # キューから前回の投稿内容を取得
    queue = load_json("state/post-queue.json")
    prev_item, prev_idx = find_queue_item(queue, slot_num, date_str)
    previous_content = prev_item.get("content", "") if prev_item else ""

    if previous_content:
        print(f"[REGEN] 前回の投稿を発見: {previous_content[:40]}...")
    else:
        print("[REGEN] 前回の投稿なし（新規生成）")

    # プロンプト構築
    prompt = build_regen_prompt(slot_info, today, memo, learning_block, previous_content)

    # Claude API 呼び出し（最大2回）
    new_post = None
    for attempt in range(2):
        print(f"[REGEN] Claude API 呼び出し（試行{attempt + 1}/2）")
        result = call_claude_api_single(api_key, prompt)
        if result and result.get("content", "").strip():
            new_post = result
            break
        print(f"[REGEN] 試行{attempt + 1} 失敗 → リトライ")

    if not new_post:
        print("[REGEN] ERROR: 2回試行しても再生成失敗")
        sys.exit(1)

    new_content = new_post.get("content", "").strip()
    new_score, check_detail = run_quality_check(new_content)

    print(f"[REGEN] 新しい投稿文（スコア{new_score}）:")
    print(f"  {new_content[:80]}...")

    # キューを更新（前回のアイテムがあれば上書き、なければ追加）
    if prev_item is not None:
        queue["queue"][prev_idx]["content"] = new_content
        queue["queue"][prev_idx]["quality_score"] = new_score
        queue["queue"][prev_idx]["quality_warnings"] = [check_detail] if "注意" in check_detail else []
        queue["queue"][prev_idx]["status"] = "queued"  # スキップ済みの場合も復活
        queue["queue"][prev_idx]["regenerated"] = True
        queue["queue"][prev_idx]["regen_memo"] = memo
        print(f"[REGEN] キューのスロット{slot_num} を更新しました（index={prev_idx}）")
    else:
        # 新規追加
        new_item = {
            "id": f"post_{date_str.replace('-', '')}_{slot_info['hour']:02d}07_regen_s{slot_num:02d}",
            "content": new_content,
            "platform": "threads",
            "time_slot": slot_info["slot"],
            "scheduled_hour": slot_info["hour"],
            "pattern_name": new_post.get("pattern_name", ""),
            "category": new_post.get("category", ""),
            "hashtag": new_post.get("hashtag", "#今日の運勢"),
            "status": "queued",
            "quality_score": new_score,
            "quality_warnings": [check_detail] if "注意" in check_detail else [],
            "regenerated": True,
            "regen_memo": memo,
        }
        queue["queue"].append(new_item)
        print(f"[REGEN] 新規キューアイテムを追加しました（スロット{slot_num}）")

    save_json("state/post-queue.json", queue)
    print("[REGEN] キュー保存完了")

    # Sheets に更新を書き込む
    if os.environ.get("GOOGLE_SHEETS_CREDENTIALS"):
        from agents.shared.sheets_client import update_post_content
        success = update_post_content(
            slot_num=slot_num,
            new_content=new_content,
            new_score=new_score,
            check_detail=check_detail,
            date_str=date_str
        )
        if success:
            print(f"[REGEN] ✅ Sheetsのスロット{slot_num}を更新 → pending_review に戻しました")
        else:
            print(f"[REGEN] ⚠️ Sheets更新失敗（キューは更新済み）")
    else:
        print("[REGEN] GOOGLE_SHEETS_CREDENTIALS 未設定 → Sheets更新スキップ")

    print(f"[REGEN] ✅ スロット{slot_num} 再生成完了")


if __name__ == "__main__":
    main()
