#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ライターのユーザープロンプトを構築"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding='utf-8')

PROJECT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_json(path):
    full = os.path.join(PROJECT_DIR, path)
    if os.path.exists(full):
        with open(full, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

research = load_json("state/research-results.json")
feedback = load_json("state/analyst-feedback.json")
persona = load_json("knowledge/uranai/persona.json")
patterns = load_json("knowledge/uranai/post-patterns.json")
hooks = load_json("knowledge/uranai/hook-lines.json")
ng_words = load_json("knowledge/uranai/ng-words.json")
asp_links = load_json("knowledge/uranai/asp-links.json")
safety = load_json("config/safety.json")
history = load_json("state/post-history.json")

# アフィリエイト有効/無効チェック
affiliate_enabled = safety.get("posting_safety", {}).get("affiliate_enabled", True)

# 直近3件のパターン取得（重複回避用）
recent_posts = history.get("posts", [])[-3:]
recent_patterns = [p.get("pattern_name", "") for p in recent_posts]

today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

prompt = f"""以下の情報をもとに、Threads向けの投稿を10本生成してください。

## 今日の日付
{today}

## リサーチ結果（ネタ）
{json.dumps(research, ensure_ascii=False, indent=2)}

## アナリストのフィードバック
{json.dumps(feedback, ensure_ascii=False, indent=2)}

## ペルソナ設定
{json.dumps(persona, ensure_ascii=False, indent=2)}

## 直近3件で使用した投稿パターン（これらは避ける）
{json.dumps(recent_patterns, ensure_ascii=False)}

## 投稿パターン一覧（この中から選択）
{json.dumps(patterns, ensure_ascii=False, indent=2)}

## フック（1行目）パターン集（構造を参考に）
{json.dumps(hooks, ensure_ascii=False, indent=2)}

## NGワード（これらの表現は絶対に使わない）
{json.dumps(ng_words.get('absolute_ng', {}), ensure_ascii=False, indent=2)}

## ASPリンク情報（アフィリエイト投稿1本に使用）
{json.dumps(asp_links.get('campaigns', [])[:2], ensure_ascii=False, indent=2)}

## 重要なルール
1. 10本生成{'（アフィリエイト投稿は禁止。全て純粋なコンテンツ投稿にすること）' if not affiliate_enabled else '（うちアフィリエイト投稿は1本まで）'}
2. 各投稿を6項目で自己採点（各10点満点、平均7.0以上でパス）
3. 直近3件と同じパターンは使わない
4. NGワードは絶対に使わない
5. Threads用は150〜300文字（500文字以内厳守）
6. 出力はJSON形式のみ（説明文不要）
7. 各投稿にはユニークなid（"post_001"等）、content（本文にハッシュタグ含む）、pattern_name、theme、hashtag（空文字）、quality_score（average含む）を含む
{'8. is_affiliateは全てfalseにすること' if not affiliate_enabled else '8. アフィリエイト投稿にはis_affiliate: true、affiliate_comment（PRリンク付きコメント文）を含む'}

## Bot臭さ排除ルール（最重要）
1. ハッシュタグはcontentの末尾に自然に組み込む（hashtagフィールドは空文字）
2. 締めの文とハッシュタグのキーワードが重複してはいけない
   NG: "あなたの運勢は？\\n\\n#今日の運勢"（「運勢」が連続→Bot臭い）
   OK: "あなたはどのタイプ？\\n\\n#今日の運勢"（キーワードが異なる→自然）
3. 毎回同じ締め文を使わない。10件全て異なる締め方にする
4. 「いかがでしたか？」「参考にしてくださいね」等のBot定型文は禁止
5. 人間が書いたように見える、自然で個性的な文体にする

## 📱 スマホ改行ルール（最重要・違反=不合格）
以下のルールに1つでも違反した投稿は不合格。必ず書き直すこと。

### 絶対ルール
- 1行は最大25文字（\\nで改行を入れる）
- 改行なし30文字以上のベタ打ちは禁止
- 空行（\\n\\n）で本文を3〜5ブロックに分割
- 1ブロックは最大40文字（2行以内）
- フック（1行目）は20文字以内

### contentフィールドの改行表現
- JSONのcontentフィールドでは \\n を使って改行を表現する
- 空行は \\n\\n で表現する

### 構造テンプレート（必ずこの形式に従う）
[フック20文字以内]\\n\\n[ブロック1: 25-40文字]\\n\\n[ブロック2: 25-40文字]\\n\\n[ブロック3: 25-40文字]\\n\\n[締め20-30文字]

### NG例（こう書いたら不合格）
content: "春分を過ぎた今、新しい流れが加速しています。焦らず一歩ずつ進むことで大きな成果が得られるでしょう。"
→ 50文字以上改行なしはスマホで読めない。絶対禁止。

### OK例（こう書くこと）
content: "12星座中、たった2つだけ。\\n\\n春分を過ぎた今、\\n人生の転機が訪れる星座があります。\\n\\nそれは…蟹座と射手座です。\\n\\nこの2星座が今週やるべきこと、\\n知りたくないですか？"
"""

output_path = os.path.join(PROJECT_DIR, "state", "_tmp_writer_user_prompt.txt")
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(prompt)

print("OK")
