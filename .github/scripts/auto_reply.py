#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
コメント自動返信: 投稿へのコメントにClaude APIで返信を生成し、Threads APIで投稿
30分おきにGitHub Actionsで実行
"""

import json
import os
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


def generate_reply(comment_text, original_post_text, api_key):
    prompt = f"""あなたは占いSNSアカウント「よぞら.」の運営者です。
ペルソナ: 月詠（つくよみ）。穏やかで温かい、でも神秘的。

以下のコメントに返信してください。

【元の投稿（あなたが書いたもの）】
{original_post_text[:200]}

【届いたコメント】
{comment_text}

【返信ルール】
1. 1-3行の短い返信（50文字以内が理想）
2. 温かく、感謝を込めて
3. 相手の星座が分かれば星座に触れる
4. 絵文字は1個まで
5. 「ありがとうございます」のバリエーションを毎回変える
6. 占い師っぽい言い回しを少し入れる（「素敵な流れですね」「星が味方してますよ」等）
7. 定型文っぽくならないこと
8. コメントが絵文字だけ（🔮、✨等）の場合は「受け取ってくださりありがとうございます🌙 良い流れが届きますように」のような短い返信

返信文のみを出力。余計な説明不要。"""

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
    max_replies_per_run = 5  # 1回の実行で最大5件返信（スパム防止）

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

            # Claude APIで返信生成
            try:
                reply_text = generate_reply(comment_text, post_text, api_key)
            except Exception as e:
                print(f"  [WARN] 返信生成エラー: {e}")
                continue

            # Threads APIで返信投稿
            try:
                reply_id = threads_reply(reply_text, comment_id, user_id, access_token)
                replied_ids.add(comment_id)
                total_replied += 1
                print(f"  ✅ @{comment_user}: 「{comment_text[:20]}」→ 「{reply_text[:30]}」")
            except Exception as e:
                print(f"  [WARN] 返信投稿エラー: {e}")
                continue

    # 返信済みIDを保存（直近500件まで保持）
    replied["replied_ids"] = list(replied_ids)[-500:]
    replied["last_checked"] = datetime.now(JST).isoformat()
    save_json("state/replied-comments.json", replied)

    print(f"\n返信完了: {total_replied}件（累計{len(replied['replied_ids'])}件）")


if __name__ == "__main__":
    main()
