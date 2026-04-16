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

# =========================================================
# テキスト類似度チェック（Bigram方式）
# =========================================================
SIMILARITY_THRESHOLD = 0.65  # 65%以上の類似度で重複判定


def bigram_similarity(a, b):
    """2テキスト間のBigram類似度を計算（0.0〜1.0）"""
    if not a or not b:
        return 0.0
    def make_bigrams(s):
        return set(s[i:i+2] for i in range(len(s) - 1))
    sa = make_bigrams(a)
    sb = make_bigrams(b)
    if not sa or not sb:
        return 0.0
    intersection = len(sa & sb)
    union = len(sa | sb)
    return intersection / union if union > 0 else 0.0


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
    platform = post.get("platform", "threads")
    issues = []
    content = post.get("content", "")

    # --- プラットフォーム別の閾値設定 ---
    if platform == "x":
        min_chars = 40
        max_chars = 280
        max_line_length = 22  # Xのスマホ表示は少し狭い
        min_blocks = 2
        max_block_length = 60
        max_total_lines = 12
        max_hook_length = 20
        max_emoji = 3
        max_hashtags = 2
    else:  # threads
        min_chars = 15   # 1ライナー投稿（いいね強要型等）を許容
        max_chars = 500
        max_line_length = 40  # スペース区切り1ライナー（い い ね を〜）を許容
        min_blocks = 3
        max_block_length = 80
        max_total_lines = 16
        max_hook_length = 40  # スペース区切り1ライナー対応
        max_emoji = 5
        max_hashtags = 1  # Threadsはトピックタグ1つのみ

    # --- 1. 文字数チェック ---
    char_count = len(content)
    if char_count < min_chars:
        issues.append(f"文字数不足: {char_count}文字（最低{min_chars}文字）[{platform}]")
    if char_count > max_chars:
        issues.append(f"文字数超過: {char_count}文字（最大{max_chars}文字）[{platform}]")

    # --- 2. 改行チェック ---
    lines = content.split("\n")
    non_empty_lines = [l for l in lines if l.strip()]

    for i, line in enumerate(non_empty_lines):
        if len(line) > max_line_length:
            issues.append(f"行{i+1}が長すぎ: {len(line)}文字「{line[:20]}...」（最大{max_line_length}文字）[{platform}]")
            break

    # 空行による分割チェック
    blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
    if len(blocks) < min_blocks and char_count > (60 if platform == "x" else 100):
        issues.append(f"ブロック分割不足: {len(blocks)}ブロック（最低{min_blocks}ブロック）[{platform}]")

    # 最長ブロック
    if blocks:
        longest_block = max(len(b) for b in blocks)
        if longest_block > max_block_length:
            issues.append(f"ブロックが長すぎ: {longest_block}文字（最大{max_block_length}文字）[{platform}]")

    # 全体行数チェック
    total_lines = len(lines)
    if total_lines > max_total_lines:
        issues.append(f"縦長すぎ: {total_lines}行（最大{max_total_lines}行）[{platform}]")

    # --- 3. フック（1行目）チェック ---
    first_line = non_empty_lines[0] if non_empty_lines else ""
    if len(first_line) > max_hook_length:
        issues.append(f"フックが長すぎ: {len(first_line)}文字「{first_line[:15]}...」（最大{max_hook_length}文字）[{platform}]")

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

    # --- 4b. ペルソナ違反チェック（よぞら. = 穏やか・敬語ベース） ---
    persona_ng_words = [
        # 凛（姉御キャラ）の口調
        "あんた", "黙って", "聞きなさい", "褒めてあげる",
        "しなさい", "やりな", "でしょうが", "だろうが",
        "バカ", "アホ", "ハズレ", "ダメ出し",
        # タメ口・乱暴な口調
        "だよね？", "じゃん", "マジで", "ヤバい",
        "ウケる", "草", "それな", "知らんけど",
        # よぞら.が使わない表現
        "泣く", "怒る", "ぶっちゃけ",
    ]
    for ng in persona_ng_words:
        if ng in content:
            issues.append(f"ペルソナ違反（よぞら.の口調ではない）:「{ng}」")
            break  # 1つ見つければ十分

    # ハッシュタグとその直前の文でキーワード重複チェック
    hashtag_match = re.search(r"#(\S+)", content)
    if hashtag_match:
        tag_text = hashtag_match.group(1)
        before_tag = content[:hashtag_match.start()].strip()
        last_line_before_tag = before_tag.split("\n")[-1] if before_tag else ""
        for keyword in [tag_text[i:i+3] for i in range(len(tag_text)-2)]:
            if keyword in last_line_before_tag:
                issues.append(f"締めとハッシュタグのキーワード重複:「{keyword}」")
                break

    # --- 5. ハッシュタグ/トピックタグ チェック ---
    hashtag_count = len(re.findall(r"#\S+", content))
    if hashtag_count == 0:
        issues.append(f"ハッシュタグが含まれていない[{platform}]")
    if platform == "threads" and hashtag_count > max_hashtags:
        issues.append(f"トピックタグが多すぎ: {hashtag_count}個（Threadsは{max_hashtags}個のみ）")
    if platform == "x" and hashtag_count > max_hashtags:
        issues.append(f"ハッシュタグが多すぎ: {hashtag_count}個（Xは{max_hashtags}個まで）")

    # --- 6. X専用: リンクペナルティチェック ---
    if platform == "x":
        url_pattern = re.compile(r"https?://\S+")
        if url_pattern.search(content):
            issues.append("X投稿にURL検出（リンクペナルティ-50%。リンクはaffiliate_commentに移動すべき）[x]")

    # --- 7. Threads専用: エンゲージメントベイトチェック ---
    if platform == "threads":
        engagement_bait = [
            "いいねしてね", "フォローしてね", "リポストして",
            "いいねお願い", "フォローお願い", "シェアしてね",
            # ソフトなフォロー誘導も禁止（Meta降格対象）
            "フォローして見逃さない", "フォローをお待ち",
            "フォローしてください", "フォローしていただく",
        ]
        # 占いジャンルの標準的CTA（「🔮を置いた方に」等）は除外
        spiritual_cta_pattern = re.compile(r".を置い")  # 絵文字+「を置い〜」
        for bait in engagement_bait:
            if bait in content:
                issues.append(f"Threadsエンゲージメントベイト検出:「{bait}」（降格対象）[threads]")

    # --- 7b. 答えの出し惜しみチェック（eng率0%パターン） ---
    withholding_patterns = [
        ("プロフのリンク",   "外部誘導（プロフリンク）- フォロワー少段階では無意味でペナルティ"),
        ("プロフィールから", "外部誘導（プロフリンク）"),
        ("詳しくはプロフ",   "外部誘導（プロフリンク）"),
        ("明日お届け",       "答えの出し惜しみ（「明日お届け」）- 今すぐ価値を提供すべき"),
        ("明日は詳しく",     "答えの出し惜しみ"),
        ("詳しくは明日",     "答えの出し惜しみ"),
        ("個別にお伝え",     "答えの出し惜しみ（個別対応を匂わせる）"),
        ("個別に教え",       "答えの出し惜しみ"),
        ("DMで教え",         "DM誘導（禁止）"),
        ("後ほどお伝え",     "答えの出し惜しみ"),
        ("後日お伝え",       "答えの出し惜しみ"),
        ("アドバイスをするので", "答えの出し惜しみ（アドバイスを今与えていない）"),
        ("詳しくお伝えします", "答えの出し惜しみ"),
        ("詳しく見ますね",   "答えの出し惜しみ"),
        ("また詳しく書きます", "答えの出し惜しみ"),
        ("次は詳しく",       "答えの出し惜しみ"),
    ]
    for phrase, reason in withholding_patterns:
        if phrase in content:
            issues.append(f"答えの出し惜しみ/外部誘導:「{phrase}」（{reason}）[{platform}]")
            break

    # --- 7c. 1星座限定チェック（11/12ユーザーを切り捨てる禁止パターン） ---
    if platform == "threads":
        zodiac_names = [
            "牡羊座", "おひつじ座", "牡牛座", "おうし座", "双子座", "ふたご座",
            "蟹座", "かに座", "獅子座", "しし座", "乙女座", "おとめ座",
            "天秤座", "てんびん座", "蠍座", "さそり座", "射手座", "いて座",
            "山羊座", "やぎ座", "水瓶座", "みずがめ座", "魚座", "うお座",
        ]
        found_zodiacs = set()
        for z in zodiac_names:
            if z in content:
                found_zodiacs.add(z)
        # 1星座のみ言及 かつ 「○○座さんへ」「○○座の方へ」などの直接呼びかけがある場合
        single_zodiac_address = re.search(
            r"(牡羊|牡牛|双子|蟹|獅子|乙女|天秤|蠍|射手|山羊|水瓶|魚)座(さん)?[へに。\s]",
            content[:50]  # フック（先頭50文字）でチェック
        )
        if len(found_zodiacs) == 1 and single_zodiac_address:
            issues.append(
                f"1星座限定投稿（禁止）: 「{list(found_zodiacs)[0]}」のみ対象 "
                f"- 11/12のユーザーを切り捨てる。複数星座か全体向けに書き直し[{platform}]"
            )

    # --- 8. 時間限定表現チェック ---
    risky_time_patterns = [
        (r"今夜\d+時まで", "「今夜○時まで」は投稿時刻によって矛盾する"),
        (r"今朝", "「今朝」は午後投稿で矛盾する"),
        (r"午前中に", "「午前中に」は午後投稿で矛盾する"),
    ]
    for pattern, msg in risky_time_patterns:
        if re.search(pattern, content):
            issues.append(f"時間矛盾リスク: {msg}")
            break

    # --- 8b. 禁止パターンチェック（実績データで確認済みの低パフォーマンス）---
    if platform == "threads":
        banned_patterns = [
            # 励まし型: 閲覧平均169・ER8.1%（10件実証済み）
            (r"(木星の優しい光|土星が.*微笑|春の陽射しが心強|応援メッセージ|あなたを応援|背中を押し|頑張るあなた)", "励まし型（禁止）: 閲覧平均169・ER8.1%"),
            # 抽象共感型: 閲覧平均153・ER8.9%（6件実証済み）
            (r"^(今日も頑張|自分を信じ|あなたは大丈夫|きっとうまく|前を向い)", "抽象共感型（禁止）: 閲覧平均153・ER8.9%"),
            # モヤモヤ系（共感型）
            (r"^(ここ数日.*モヤモヤ|なんか.*モヤモヤ|最近.*モヤモヤ)", "共感型フック（禁止）: リーチ低下パターン"),
        ]
        for pat, reason in banned_patterns:
            if re.search(pat, content[:80]):
                issues.append(f"禁止パターン検出: {reason}")
                break

    # --- 9. 絵文字過多チェック ---
    emoji_pattern = re.compile(
        r"[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF"
        r"\u2B50\u2728\u2764\u23E9-\u23FA\u25AA-\u25FE"
        r"\U0001FA00-\U0001FAFF]"
    )
    emoji_count = len(emoji_pattern.findall(content))
    if emoji_count > max_emoji:
        issues.append(f"絵文字多すぎ: {emoji_count}個（最大{max_emoji}個）[{platform}]")

    return issues


def is_format_issue(issue: str) -> bool:
    """フォーマット系の問題かどうか判定（True=警告のみ・投稿は通す）

    【致命的=拒否】: Bot文句・ペルソナ違反・エンゲベイト・答え出し惜しみ・
                    1星座限定・禁止パターン・類似重複・URL(X)・時間矛盾
    【フォーマット=警告のみ】: 行長・行数・文字数・ブロック数・フック長・
                              絵文字数・ハッシュタグ有無・タグ数
    """
    format_keywords = [
        "文字数",           # 文字数不足/超過
        "が長すぎ",         # 行の長さ（「行4が長すぎ」「フックが長すぎ」等）
        "縦長すぎ",         # 総行数超過
        "ブロック",         # ブロック分割
        "絵文字多すぎ",     # 絵文字数
        "ハッシュタグが含まれていない",  # ハッシュタグなし
        "トピックタグが多すぎ",          # タグ多すぎ
        "ハッシュタグが多すぎ",          # タグ多すぎ
    ]
    return any(kw in issue for kw in format_keywords)


def main():
    queue = load_json("state/post-queue.json")
    if not queue:
        queue = {"queue": []}

    pending = [p for p in queue.get("queue", []) if p.get("status") == "queued"]
    if not pending:
        print("キューが空です。チェック不要。")
        return

    print(f"品質チェック開始: {len(pending)}件")

    # =========================================================
    # --- 類似度チェック①: 既存投稿履歴との重複検出 ---
    # post-history.json の直近50件と比較し、65%以上類似なら棄却
    # =========================================================
    history = load_json("state/post-history.json")
    history_texts = []
    for hp in history.get("posts", [])[-50:]:
        t = hp.get("content", hp.get("content_preview", ""))
        if t:
            history_texts.append(t)

    print(f"  類似度チェック: 既存投稿{len(history_texts)}件と比較")
    for post in pending:
        content = post.get("content", "")
        for existing in history_texts:
            sim = bigram_similarity(content, existing)
            if sim >= SIMILARITY_THRESHOLD:
                issue = f"既存投稿と類似度{sim*100:.0f}%（閾値{SIMILARITY_THRESHOLD*100:.0f}%超）: 「{existing[:20]}...」"
                post.setdefault("_similarity_issues", []).append(issue)
                break

    # =========================================================
    # --- 類似度チェック②: バッチ内の重複検出 ---
    # 同バッチ内の他投稿と比較し、65%以上類似なら後者を棄却
    # =========================================================
    for i, post in enumerate(pending):
        if post.get("_similarity_issues"):
            continue  # 既に①で引っかかっている
        content = post.get("content", "")
        for j, other in enumerate(pending):
            if i == j:
                continue
            other_content = other.get("content", "")
            sim = bigram_similarity(content, other_content)
            if sim >= SIMILARITY_THRESHOLD:
                issue = f"バッチ内の投稿#{j+1}と類似度{sim*100:.0f}%（重複）"
                post.setdefault("_similarity_issues", []).append(issue)
                break

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

        # 類似度issueを追加
        similarity_issues = post.pop("_similarity_issues", [])
        issues = similarity_issues + issues

        if issues:
            fatal   = [i for i in issues if not is_format_issue(i)]
            warnings = [i for i in issues if is_format_issue(i)]

            if fatal:
                # コンテンツ品質違反 → 拒否
                print(f"  ❌ {pid} [{pattern}]")
                for i in fatal:
                    print(f"     → {i}")
                for w in warnings:
                    print(f"     ⚠ {w}")
                post["status"] = "rejected"
                post["rejection_reasons"] = fatal
                failed += 1
            else:
                # フォーマット警告のみ → 合格（警告を記録して通す）
                print(f"  ✅ {pid} [{pattern}] (⚠ フォーマット警告{len(warnings)}件)")
                for w in warnings:
                    print(f"     ⚠ {w}")
                post["quality_warnings"] = warnings
                passed += 1
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
