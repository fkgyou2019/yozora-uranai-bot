#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
テンプレート改善: バズ分析結果を元にナレッジベースを自動更新
- hook-lines.json のフック追加（バズ分析の best_hooks_to_copy から）
- winning-patterns.json の insights 追記（actionable_improvements から）
- improvement-log.json に改善履歴を記録
"""

import json
import os
import sys
import difflib
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


def is_similar(text_a, text_b, threshold=0.5):
    """2つのテキストの類似度を判定（50%超で類似とみなす）"""
    ratio = difflib.SequenceMatcher(None, text_a, text_b).ratio()
    return ratio > threshold


def is_duplicate(new_hook, existing_hooks, threshold=0.5):
    """新しいフックが既存フックと類似していないかチェック"""
    for hook in existing_hooks:
        if is_similar(new_hook, hook, threshold):
            return True
    return False


def collect_all_hooks(categories):
    """全カテゴリのフックを1つのリストに集約"""
    all_hooks = []
    for hooks in categories.values():
        all_hooks.extend(hooks)
    return all_hooks


def improve_hooks(hook_lines, buzz_analysis):
    """hook-lines.json にバズ分析のフックを追加"""
    best_hooks = buzz_analysis.get("best_hooks_to_copy", [])
    if not best_hooks:
        print("[INFO] best_hooks_to_copy なし。フック更新スキップ")
        return 0, []

    categories = hook_lines.get("categories", {})
    all_existing = collect_all_hooks(categories)
    added_count = 0
    change_details = []
    category_add_counts = {}

    for hook_entry in best_hooks:
        # best_hooks_to_copy の各エントリから adapted フックとカテゴリを取得
        adapted = hook_entry.get("adapted", "")
        category = hook_entry.get("category", "")

        if not adapted or not category:
            continue

        # カテゴリが存在しない場合はスキップ
        if category not in categories:
            print(f"[WARN] カテゴリ '{category}' が hook-lines.json に存在しません。スキップ")
            continue

        # 重複チェック（全カテゴリ横断）
        if is_duplicate(adapted, all_existing):
            print(f"[INFO] 類似フック既存のためスキップ: {adapted[:30]}...")
            continue

        # カテゴリに追加（先頭に追加、最大10件保持）
        categories[category].insert(0, adapted)
        if len(categories[category]) > 10:
            categories[category] = categories[category][:10]

        all_existing.append(adapted)
        added_count += 1
        category_add_counts[category] = category_add_counts.get(category, 0) + 1

    # 変更詳細を生成
    for cat, count in category_add_counts.items():
        change_details.append(f"{cat}: {count}")

    hook_lines["categories"] = categories
    return added_count, change_details


def improve_insights(winning_patterns, buzz_analysis):
    """winning-patterns.json の insights にバズ分析の改善提案を追記"""
    improvements = buzz_analysis.get("actionable_improvements", [])
    if not improvements:
        print("[INFO] actionable_improvements なし。インサイト更新スキップ")
        return 0

    existing_insights = winning_patterns.get("insights", [])
    added_count = 0

    for improvement in improvements:
        # 文字列の場合はそのまま、辞書の場合は description を取得
        if isinstance(improvement, dict):
            text = improvement.get("description", improvement.get("text", ""))
        else:
            text = str(improvement)

        if not text:
            continue

        # 重複チェック
        if is_duplicate(text, existing_insights):
            print(f"[INFO] 類似インサイト既存のためスキップ: {text[:30]}...")
            continue

        existing_insights.insert(0, text)
        added_count += 1

    # 最大10件保持（古いものは末尾から削除）
    if len(existing_insights) > 10:
        existing_insights = existing_insights[:10]

    winning_patterns["insights"] = existing_insights
    return added_count


def update_improvement_log(hooks_added, insights_added, total_analyzed, change_details):
    """改善履歴をログに記録"""
    log = load_json("state/improvement-log.json")
    if not log:
        log = {"improvements": [], "total_improvements": 0, "last_improved": None}

    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")

    # key_changes を構築
    key_changes = []
    if hooks_added > 0:
        detail = f"フック{hooks_added}件追加"
        if change_details:
            detail += f" ({', '.join(change_details)})"
        key_changes.append(detail)
    if insights_added > 0:
        key_changes.append(f"インサイト{insights_added}件追加")

    source_count = total_analyzed

    entry = {
        "date": today,
        "hooks_added": hooks_added,
        "insights_added": insights_added,
        "source_posts_analyzed": source_count,
        "key_changes": key_changes,
    }

    log["improvements"].append(entry)
    log["total_improvements"] = len(log["improvements"])
    log["last_improved"] = now.isoformat()

    save_json("state/improvement-log.json", log)
    return log


def main():
    print("[INFO] === テンプレート改善 ===")

    # 1. バズ分析結果を読み込み
    buzz_data = load_json("state/buzz-analysis.json")
    if not buzz_data:
        print("[INFO] 分析データなし。スキップ")
        return

    # buzz_analyzer.py は {"analysis": {...}, "total_analyzed": N, ...} の形式で保存
    buzz_analysis = buzz_data.get("analysis", {})
    total_analyzed = buzz_data.get("total_analyzed", 0)
    print(f"[INFO] バズ分析データ: {total_analyzed}件分析済み")

    # 2. winning-patterns.json を読み込み
    winning_patterns = load_json("state/winning-patterns.json")

    # 3. hook-lines.json を読み込み
    hook_lines = load_json("knowledge/uranai/hook-lines.json")
    if not hook_lines:
        print("[WARN] hook-lines.json が見つかりません。スキップ")
        return

    # 4A. フック更新
    hooks_added, change_details = improve_hooks(hook_lines, buzz_analysis)
    print(f"[INFO] フック追加: {hooks_added}件", end="")
    if change_details:
        print(f" ({', '.join(change_details)})")
    else:
        print()

    # 4B. インサイト更新
    insights_added = improve_insights(winning_patterns, buzz_analysis)
    print(f"[INFO] インサイト追加: {insights_added}件")

    # 保存（変更があった場合のみ）
    if hooks_added > 0:
        save_json("knowledge/uranai/hook-lines.json", hook_lines)
        print("[INFO] hook-lines.json 更新済み")

    if insights_added > 0:
        save_json("state/winning-patterns.json", winning_patterns)
        print("[INFO] winning-patterns.json 更新済み")

    # 4C. 改善ログ記録
    if hooks_added > 0 or insights_added > 0:
        update_improvement_log(hooks_added, insights_added, total_analyzed, change_details)
        print("[INFO] 改善ログ: state/improvement-log.json 更新")
    else:
        print("[INFO] 変更なし。ログ記録スキップ")

    print("[INFO] === テンプレート改善 完了 ===")


if __name__ == "__main__":
    main()
