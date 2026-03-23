#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
コメント自動返信: 投稿へのコメントにClaude APIで返信を生成し、Threads APIで投稿
30分おきにGitHub Actionsで実行
"""

import json
import os
import random
import re
import sys
import urllib.request
import urllib.parse
import urllib.error
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


def threads_get(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def threads_reply(text, reply_to_id, user_id, access_token):
    url = f"https://graph.threads.net/v1.0/{user_id}/threads"
    params = {
        "media_type": "TEXT",
        "text": text,
        "reply_to_id": reply_to_id,
        "auto_publish_text": "true",
        "access_token": access_token,
    }
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result.get("id")


def generate_reply(comment_text, original_post_text, commenter_name, recent_replies, api_key):
    now = datetime.now(JST)
    hour = now.hour
    if 5 <= hour < 11:
        time_context = "現在は朝です。朝らしい爽やかな挨拶を入れてもOK（「おはようございます」等）"
    elif 11 <= hour < 17:
        time_context = "現在は昼です。"
    elif 17 <= hour < 22:
        time_context = "現在は夜です。夜らしい挨拶を入れてもOK（「こんばんは」「夜分に」等）"
    else:
        time_context = "現在は深夜です。「遅い時間にありがとうございます」等の気遣いを入れてもOK"

    # 直近の返信を渡して重複を防ぐ
    recent_block = ""
    if recent_replies:
        recent_block = "【直近の返信（これと同じ言い回しは絶対に使うな）】\n"
        for r in recent_replies[-5:]:
            recent_block += f"・{r[:40]}\n"

    # 絵文字コメントの種類別ヒント
    emoji_hint = ""
    stripped = comment_text.strip()
    if len(stripped) <= 3 and not any(c.isalpha() or ('\u3040' <= c <= '\u309f') or ('\u30a0' <= c <= '\u30ff') or ('\u4e00' <= c <= '\u9fff') for c in stripped):
        emoji_map = {
            "🔮": "水晶玉→占いへの関心。「見えてきましたよ」「導きが届きます」系",
            "✨": "キラキラ→ポジティブ。「輝きが増しますね」「その光が広がります」系",
            "🌙": "月→夜空・神秘。「月の力が味方してます」「静かな流れが来ます」系",
            "🌸": "桜→春・恋愛。「春の風が吹いてきましたね」系",
            "🍀": "四葉→幸運。「ご縁が近づいてますよ」系",
            "⭐": "星→希望。「星の導きがありますよ」系",
        }
        for emoji, hint in emoji_map.items():
            if emoji in stripped:
                emoji_hint = f"\n※ この絵文字の解釈ヒント: {hint}"
                break

    prompt = f"""あなたは占いSNSアカウント「よぞら.」の運営者・月詠（つくよみ）です。
穏やかで温かい人柄。フレンドリーだが、ほんの少し神秘的。

【コメントしてくれた人】@{commenter_name} さん

【元の投稿（あなたが書いたもの）】
{original_post_text[:200]}

【届いたコメント】
{comment_text}{emoji_hint}

【{time_context}】

{recent_block}
【返信ルール】
1. 冒頭に「@{commenter_name} さん\\n」から始めること（必須。さんの後に必ず改行\\n）
2. 1-2行の短い返信（40-60文字が理想。名前行は文字数に含めない）
3. 温かく、でも毎回違う表現で
4. 絵文字は1個まで（🌙✨🔮⭐のいずれか）
5. 以下の表現は禁止（Bot臭くなるため）:
   - 「受け取ってくださり」（多用されすぎ）
   - 「ありがとうございます」で始める（毎回同じに見える）
   - 「良い流れが届きますように」（定型文）
6. 代わりに使える表現例:
   - 「嬉しいです」「感謝です」「心強いです」
   - 「○○さんの直感、冴えてますね」
   - 「星が微笑んでますよ」「素敵なタイミングですね」
   - 「その想い、きっと届きますよ」
7. 相手のコメントに文章がある場合は、その内容に具体的に触れる
8. 相手の星座が分かれば星座に触れる
9. 直近の返信と絶対に同じ言い回しを使わない

返信文のみを出力。「@{commenter_name} さん\\n本文」の形式で。余計な説明不要。"""

    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 200,
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

    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    return result["content"][0]["text"].strip()


def main():
    access_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    user_id = os.environ.get("THREADS_USER_ID", "")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if not all([access_token, user_id, api_key]):
        # ローカルのapi-keys.envから読む
        env_path = os.path.join(PROJECT_DIR, "config", "api-keys.env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip()
                        if k == "THREADS_ACCESS_TOKEN" and not access_token:
                            access_token = v
                        elif k == "THREADS_USER_ID" and not user_id:
                            user_id = v
                        elif k == "ANTHROPIC_API_KEY" and not api_key:
                            api_key = v

    if not all([access_token, user_id, api_key]):
        print("ERROR: 必要な環境変数が未設定")
        sys.exit(1)

    # 返信済みコメントIDを記録するファイル
    replied = load_json("state/replied-comments.json")
    if not replied:
        replied = {"replied_ids": [], "last_checked": None}

    replied_ids = set(replied.get("replied_ids", []))

    # 最近の投稿を取得（直近10件）
    posts_url = (
        f"https://graph.threads.net/v1.0/{user_id}/threads"
        f"?fields=id,text,timestamp&limit=10&access_token={access_token}"
    )

    try:
        posts_data = threads_get(posts_url)
    except Exception as e:
        print(f"投稿取得エラー: {e}")
        sys.exit(1)

    posts = posts_data.get("data", [])
    total_replied = 0
    total_skipped = 0
    max_replies_per_run = 5  # 1回の実行で最大5件返信（スパム防止）
    recent_replies = replied.get("recent_reply_texts", [])  # 直近の返信文を保持

    for post in posts:
        if total_replied >= max_replies_per_run:
            break

        post_id = post["id"]
        post_text = post.get("text", "")

        # この投稿へのコメントを取得
        replies_url = (
            f"https://graph.threads.net/v1.0/{post_id}/replies"
            f"?fields=id,text,username,timestamp&access_token={access_token}"
        )

        try:
            replies_data = threads_get(replies_url)
        except urllib.error.HTTPError:
            continue
        except Exception:
            continue

        comments = replies_data.get("data", [])

        for comment in comments:
            if total_replied >= max_replies_per_run:
                break

            comment_id = comment["id"]
            comment_text = comment.get("text", "")
            comment_user = comment.get("username", "")

            # 自分のコメントはスキップ
            if comment_user == "yozora.uranai":
                continue

            # 既に返信済みならスキップ
            if comment_id in replied_ids:
                continue

            # 空コメントはスキップ
            if not comment_text.strip():
                continue

            # 70%の確率で返信（人間は全レスしない）
            # ただし、文章コメント（絵文字以外）には必ず返信
            is_text_comment = any(
                c.isalpha() or ('\u3040' <= c <= '\u309f') or ('\u30a0' <= c <= '\u30ff') or ('\u4e00' <= c <= '\u9fff') for c in comment_text
            )
            if not is_text_comment and random.random() > 0.7:
                replied_ids.add(comment_id)  # スキップしたことは記録
                total_skipped += 1
                print(f"  ⏭ @{comment_user}: 「{comment_text[:10]}」（スキップ）")
                continue

            # Claude APIで返信生成（名前・履歴・時間帯を渡す）
            try:
                reply_text = generate_reply(
                    comment_text, post_text,
                    comment_user, recent_replies, api_key
                )
            except Exception as e:
                print(f"  [WARN] 返信生成エラー: {e}")
                continue

            # Threads APIで返信投稿
            try:
                reply_id = threads_reply(reply_text, comment_id, user_id, access_token)
                replied_ids.add(comment_id)
                recent_replies.append(reply_text)
                total_replied += 1
                print(f"  ✅ @{comment_user}: 「{comment_text[:20]}」→ 「{reply_text[:40]}」")
            except Exception as e:
                print(f"  [WARN] 返信投稿エラー: {e}")
                continue

    # 返信済みIDと直近返信テキストを保存
    replied["replied_ids"] = list(replied_ids)[-500:]
    replied["recent_reply_texts"] = recent_replies[-20:]  # 直近20件の返信文を保持
    replied["last_checked"] = datetime.now(JST).isoformat()
    save_json("state/replied-comments.json", replied)

    print(f"\n返信完了: {total_replied}件 / スキップ{total_skipped}件（累計{len(replied['replied_ids'])}件）")


if __name__ == "__main__":
    main()
