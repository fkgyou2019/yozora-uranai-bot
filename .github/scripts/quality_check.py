#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
品質チェッカー: AIの自己採点に頼らず、ルールベースで機械的に検証
不合格の投稿はキューから除外する
"""

import json
import os
import re
import sys

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


def check_post(post):
    """1件の投稿を検証。問題点のリストを返す（空なら合格）"""
    issues = []
    content = post.get("content", "")

    # --- 1. 文字数チェック ---
    char_count = len(content)
    if char_count < 80:
        issues.append(f"文字数不足: {char_count}文字（最低80文字）")
    if char_count > 500:
        issues.append(f"文字数超過: {char_count}文字（最大500文字）")

    # --- 2. 改行チェック ---
    lines = content.split("\n")
    non_empty_lines = [l for l in lines if l.strip()]

    # 25文字以上の行（スマホで自動折り返しが入る）
    for i, line in enumerate(non_empty_lines):
        if len(line) > 25:
            issues.append(f"行{i+1}が長すぎ: {len(line)}文字「{line[:20]}...」（最大25文字）")
            break

    # 空行による分割チェック
    blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
    if len(blocks) < 3 and char_count > 100:
        issues.append(f"ブロック分割不足: {len(blocks)}ブロック（最低3ブロック）")

    # 最長ブロック
    if blocks:
        longest_block = max(len(b) for b in blocks)
        if longest_block > 80:
            issues.append(f"ブロックが長すぎ: {longest_block}文字（最大80文字）")

    # 全体行数チェック（空行含む）
    total_lines = len(lines)
    if total_lines > 16:
        issues.append(f"縦長すぎ: {total_lines}行（最大16行）")

    # --- 3. フック（1行目）チェック ---
    first_line = non_empty_lines[0] if non_empty_lines else ""
    if len(first_line) > 22:
        issues.append(f"フックが長すぎ: {len(first_line)}文字「{first_line[:15]}...」（最大22文字）")

    # --- 4. Bot臭さチェック ---
    bot_phrases = [
        "いかがでしたか",
        "参考にしてくださいね",
        "チェックしてみてね",
        "参考にしてみてください",
        "ぜひお試しください",
    ]
    for phrase in bot_phrases:
        if phrase in content:
            issues.append(f"Bot定型文を検出:「{phrase}」")

    # ハッシュタグとその直前の文でキーワード重複チェック
    hashtag_match = re.search(r"#(\S+)", content)
    if hashtag_match:
        tag_text = hashtag_match.group(1)
        # ハッシュタグの直前のブロックを取得
        before_tag = content[:hashtag_match.start()].strip()
        last_line_before_tag = before_tag.split("\n")[-1] if before_tag else ""
        # 2文字以上の共通キーワードチェック
        for keyword in [tag_text[i:i+3] for i in range(len(tag_text)-2)]:
            if keyword in last_line_before_tag:
                issues.append(f"締めとハッシュタグのキーワード重複:「{keyword}」")
                break

    # --- 5. ハッシュタグ存在チェック ---
    if "#" not in content:
        issues.append("ハッシュタグが含まれていない")

    # --- 6. 時間限定表現チェック ---
    # バッチ生成時は何時に投稿されるか不明なので、
    # 特定時刻を含む表現は危険（矛盾リスク）
    risky_time_patterns = [
        (r"今夜\d+時まで", "「今夜○時まで」は投稿時刻によって矛盾する"),
        (r"今朝", "「今朝」は午後投稿で矛盾する"),
        (r"午前中に", "「午前中に」は午後投稿で矛盾する"),
    ]
    for pattern, msg in risky_time_patterns:
        if re.search(pattern, content):
            issues.append(f"時間矛盾リスク: {msg}")
            break

    # --- 7. 絵文字過多チェック ---
    emoji_pattern = re.compile(
        r"[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF"
        r"\u2B50\u2728\u2764\u23E9-\u23FA\u25AA-\u25FE"
        r"\U0001FA00-\U0001FAFF]"
    )
    emoji_count = len(emoji_pattern.findall(content))
    if emoji_count > 5:
        issues.append(f"絵文字多すぎ: {emoji_count}個（最大5個）")

    return issues


def main():
    queue = load_json("state/post-queue.json")
    if not queue:
        queue = {"queue": []}

    pending = [p for p in queue.get("queue", []) if p.get("status") == "queued"]
    if not pending:
        print("キューが空です。チェック不要。")
        return

    print(f"品質チェック開始: {len(pending)}件")

    # --- バッチ全体のCTA多様性チェック ---
    cta_emojis = []
    for post in pending:
        content = post.get("content", "")
        emoji_cta = re.search(r"「(.)」を置", content)
        if emoji_cta:
            cta_emojis.append(emoji_cta.group(1))
    if len(cta_emojis) > 0:
        from collections import Counter
        cta_counts = Counter(cta_emojis)
        for emoji, count in cta_counts.items():
            if count > 1:
                print(f"  ⚠ CTA絵文字「{emoji}」が{count}件で重複（多様性不足）")

    # --- パターン多様性チェック ---
    pattern_counts = {}
    for post in pending:
        pname = post.get("pattern_name", "不明")
        pattern_counts[pname] = pattern_counts.get(pname, 0) + 1
    for pname, count in pattern_counts.items():
        if count > 3:
            print(f"  ⚠ パターン「{pname}」が{count}件（偏り注意）")

    passed = 0
    failed = 0

    for post in pending:
        pid = post.get("id", "?")
        pattern = post.get("pattern_name", "?")
        issues = check_post(post)

        if issues:
            print(f"  ❌ {pid} [{pattern}]")
            for issue in issues:
                print(f"     → {issue}")
            post["status"] = "rejected"
            post["rejection_reasons"] = issues
            failed += 1
        else:
            print(f"  ✅ {pid} [{pattern}]")
            passed += 1

    # rejectedをキューから除外
    queue["queue"] = [p for p in queue["queue"] if p.get("status") != "rejected"]
    save_json("state/post-queue.json", queue)

    print(f"\n結果: {passed}件合格 / {failed}件不合格（除外済み）")

    if passed == 0:
        print("ERROR: 合格投稿が0件。再生成が必要。")
        sys.exit(1)


if __name__ == "__main__":
    main()
