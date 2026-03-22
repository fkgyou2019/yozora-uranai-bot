#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions用: キューが空なら Claude API で投稿を10件生成
"""

import json
import os
import re
import sys
import urllib.request
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


def main():
    queue = load_json("state/post-queue.json")
    if not queue:
        queue = {"queue": []}

    pending = [p for p in queue.get("queue", []) if p.get("status") == "queued"]
    if pending:
        print(f"キューに{len(pending)}件残っています。生成スキップ。")
        return

    print("キューが空のため、Claude APIで10件生成します...")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY が未設定")
        sys.exit(1)

    # knowledge読み込み
    persona = load_json("knowledge/uranai/persona.json")
    history = load_json("state/post-history.json")
    if not history:
        history = {"posts": []}

    used_patterns = [p.get("pattern_name", "") for p in history.get("posts", [])[-15:]]
    today = datetime.now(JST).strftime("%Y年%m月%d日(%a)")

    prompt = f"""あなたは占いSNSアカウント「よぞら.」の投稿ライターです。

【ペルソナ】
名前: 月詠（つくよみ）
トーン: 穏やかで神秘的、でも親しみやすい。敬語ベース。
絵文字: 🔮✨🌙⭐ を控えめに使用（1投稿に2-3個まで）

【今日の日付】{today}

【直近で使用済みのパターン（重複を避ける）】
{json.dumps(used_patterns[-5:], ensure_ascii=False)}

【📱 スマホ改行ルール（最重要・違反=不合格）】
以下のルールに1つでも違反した投稿は不合格。必ず書き直すこと。

1. 1行は最大25文字（\\nで改行を入れる）
2. 改行なし30文字以上のベタ打ちは絶対禁止
3. 空行（\\n\\n）で本文を3〜5ブロックに分割
4. 1ブロックは最大40文字（2行以内）
5. フック（1行目）は20文字以内

【構造テンプレート（必ずこの形式に従う）】
[フック20文字以内]\\n\\n[ブロック1: 25-40文字]\\n\\n[ブロック2: 25-40文字]\\n\\n[ブロック3: 25-40文字]\\n\\n[締め20-30文字]

【NG例（こう書いたら不合格）】
content: "春分を過ぎた今、新しい流れが加速しています。焦らず一歩ずつ進むことで大きな成果が得られるでしょう。"
→ 50文字以上改行なしはスマホで読めない。絶対禁止。

【OK例（こう書くこと）】
content: "12星座中、たった2つだけ。\\n\\n春分を過ぎた今、\\n人生の転機が訪れる星座があります。\\n\\nそれは…蟹座と射手座です。\\n\\nこの2星座が今週やるべきこと、\\n知りたくないですか？"

【Bot臭さ排除ルール（最重要）】
1. ハッシュタグはcontentの末尾に自然に組み込む（別フィールドにしない）
2. 締めの文とハッシュタグのキーワードが重複してはいけない
   NG: "あなたの運勢は？\\n\\n#今日の運勢"（「運勢」が連続→Bot臭い）
   OK: "あなたはどのタイプ？\\n\\n#今日の運勢"（キーワードが異なる→自然）
3. ハッシュタグは投稿の最後に1つだけ、空行を挟んで配置
4. 毎回同じ締め文を使わない。10件全て異なる締め方にする
5. 「いかがでしたか？」「参考にしてくださいね」等のBot定型文は禁止
6. 人間が書いたように見える、自然で個性的な文体にする

【生成ルール】
1. 10件生成。全て純粋なコンテンツ投稿（アフィリエイトなし）
2. 各投稿は150-300文字
3. 具体的な星座名を含める
4. 「占い師として」等の権威表現を使わない
5. ハッシュタグはcontentフィールドの末尾に含める（hashtagフィールドは空文字にする）
6. 使用可能ハッシュタグ: #今日の運勢 #恋愛運 #金運 #仕事運 #タロット #星座占い

【使用可能パターン】
星座別アドバイス型, コメント誘導型, タロットワンオラクル型, ランキング型,
共感・あるある型, リスト・まとめ型, 短文完結型, 暴露・ぶっちゃけ型,
問いかけ型, 数字インパクト型, 時事・季節連動型, ストーリー型,
曜日・時間帯限定型, 比較型, 保存推奨型

【出力形式】厳密にJSON配列のみ。余計なテキスト不要。
[
  {{
    "pattern_name": "パターン名",
    "category": "カテゴリ",
    "content": "投稿本文（\\nで改行）",
    "hashtag": "",
    "quality_score": {{"hook":8,"usefulness":8,"specificity":8,"tempo":8,"persona_match":8,"mobile_readability":8,"average":8.0}}
  }}
]
"""

    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    text = result["content"][0]["text"]
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        print("ERROR: JSON抽出失敗")
        print(text[:500])
        sys.exit(1)

    posts = json.loads(m.group())
    today_str = datetime.now(JST).strftime("%Y%m%d")
    post_count = len(history.get("posts", []))

    for i, p in enumerate(posts):
        p["id"] = f"post_{today_str}_{post_count + i + 1:03d}"
        p["platform"] = "threads"
        p["is_affiliate"] = False
        p["affiliate_comment"] = None
        p["status"] = "queued"
        queue["queue"].append(p)

    save_json("state/post-queue.json", queue)
    print(f"生成完了: {len(posts)}件をキューに追加")


if __name__ == "__main__":
    main()
