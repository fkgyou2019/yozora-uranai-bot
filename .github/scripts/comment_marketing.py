#!/usr/bin/env python3
"""
Threads コメント営業スクリプト
占い関連投稿にAI生成コメントを投稿しフォロワーを増やす
"""

import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
import random

MAX_COMMENTS_PER_RUN = 3        # 1実行あたり最大件数
COMMENT_INTERVAL_SEC = 200      # コメント間隔（秒）
MAX_DAILY = 30                  # 1日の上限
STATE_FILE = "state/comment-marketing.json"
BASE_URL = "https://graph.threads.net/v1.0"
JST = timezone(timedelta(hours=9))

KEYWORDS = [
    "占い", "タロット", "運勢", "恋愛占い",
    "星座", "スピリチュアル", "恋愛運", "仕事運",
    "人生相談", "今日の運勢", "引き寄せ", "直感",
    "縁", "運命", "カード", "霊感"
]

NEGATIVE_KEYWORDS = [
    "信じない", "詐欺", "怪しい", "批判", "嘘",
    "インチキ", "偽物", "騙", "悪徳", "やめろ"
]


def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "daily_count": 0,
            "daily_date": "",
            "total_count": 0,
            "paused_until": None,
            "our_username": None,
            "commented_post_ids": [],
            "recent_comments": [],
            "last_run": None
        }
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    os.makedirs("state", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_credentials():
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    user_id = os.environ.get("THREADS_USER_ID", "")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if not token or not user_id:
        for env_file in ["api-keys.env", "config/api-keys.env"]:
            if os.path.exists(env_file):
                with open(env_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        if k == "THREADS_ACCESS_TOKEN" and not token:
                            token = v
                        elif k == "THREADS_USER_ID" and not user_id:
                            user_id = v
                        elif k == "ANTHROPIC_API_KEY" and not api_key:
                            api_key = v
                break

    return token, user_id, api_key


def get_our_username(user_id, token):
    url = f"{BASE_URL}/{user_id}?fields=username&access_token={token}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("username", "")
    except Exception as e:
        print(f"[WARN] ユーザー名取得失敗: {e}")
        return ""


def keyword_search(keyword, token):
    """キーワードで最新の公開投稿を検索"""
    params = urllib.parse.urlencode({
        "q": keyword,
        "search_type": "RECENT",
        "fields": "id,text,username,timestamp",
        "limit": 25,
        "access_token": token
    })
    url = f"{BASE_URL}/keyword_search?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
            return data.get("data", [])
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[ERROR] 検索失敗 ({keyword}): {e.code} {body[:200]}")
        return []
    except Exception as e:
        print(f"[ERROR] 検索例外 ({keyword}): {e}")
        return []


def is_relevant_post(text):
    """投稿が営業対象として適切か判定"""
    if not text or len(text.strip()) < 10:
        return False
    for neg in NEGATIVE_KEYWORDS:
        if neg in text:
            return False
    return True


def generate_comment(post_text, api_key):
    """Claude APIで投稿に合わせたコメントを生成"""
    truncated = post_text[:200]

    prompt = f"""あなたはThreadsユーザーです。以下の投稿に対して、自然で温かいコメントを日本語で1文書いてください。

ルール（必ず守ること）：
- 30〜80文字以内
- 投稿内容に具体的に反応する（コピペ感を出さない）
- 温かく、共感的、または興味深そうなトーン
- 宣伝・リンク・ハッシュタグは絶対に含めない
- 自己紹介やサービス案内をしない
- 普通のユーザーとして自然に会話に参加する感覚

投稿内容：
{truncated}

コメント文のみ返してください（説明・引用符・改行不要）："""

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 150,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
            text = data["content"][0]["text"].strip()
            text = text.strip('"\'「」').replace("\n", " ").strip()
            return text[:100]
    except Exception as e:
        print(f"[ERROR] コメント生成失敗: {e}")
        return None


def post_comment(user_id, post_id, comment_text, token):
    """Threads APIでコメントを投稿（auto_publish_text=true で1ステップ）"""
    url = f"{BASE_URL}/{user_id}/threads"
    params = urllib.parse.urlencode({
        "media_type": "TEXT",
        "text": comment_text,
        "reply_to_id": post_id,
        "auto_publish_text": "true",
        "access_token": token
    })
    req = urllib.request.Request(
        url,
        data=params.encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
        return data.get("id")


def main():
    state = load_state()
    token, user_id, api_key = load_credentials()

    if not token or not user_id or not api_key:
        print("[ERROR] 認証情報が不足しています")
        return

    # 一時停止チェック
    if state.get("paused_until"):
        try:
            paused_until = datetime.fromisoformat(state["paused_until"])
            if datetime.now(JST) < paused_until:
                print(f"[SKIP] 一時停止中（{state['paused_until']} まで）")
                return
        except Exception:
            pass
        state["paused_until"] = None

    # 日次カウントリセット
    today = datetime.now(JST).strftime("%Y-%m-%d")
    if state.get("daily_date") != today:
        state["daily_count"] = 0
        state["daily_date"] = today

    if state.get("daily_count", 0) >= MAX_DAILY:
        print(f"[SKIP] 本日の上限 {MAX_DAILY} 件に到達済み")
        return

    # 自アカウントのユーザー名取得（初回のみ）
    if not state.get("our_username"):
        state["our_username"] = get_our_username(user_id, token)
        print(f"[INFO] 自アカウント: @{state['our_username']}")

    our_username = state.get("our_username", "")
    commented_ids = set(state.get("commented_post_ids", [])[-2000:])
    comment_count = 0

    # キーワードをランダムに3つ選択（多様性確保）
    selected_keywords = random.sample(KEYWORDS, min(3, len(KEYWORDS)))

    for keyword in selected_keywords:
        if comment_count >= MAX_COMMENTS_PER_RUN:
            break
        if state.get("daily_count", 0) >= MAX_DAILY:
            break

        print(f"\n[SEARCH] キーワード: 「{keyword}」")
        posts = keyword_search(keyword, token)
        if not posts:
            continue

        random.shuffle(posts)

        for post in posts:
            if comment_count >= MAX_COMMENTS_PER_RUN:
                break

            post_id = post.get("id", "")
            post_text = post.get("text", "")
            username = post.get("username", "")

            if not post_id or not post_text:
                continue
            if post_id in commented_ids:
                continue
            if our_username and username == our_username:
                continue
            if not is_relevant_post(post_text):
                continue

            print(f"[TARGET] @{username}: {post_text[:60]}...")

            comment = generate_comment(post_text, api_key)
            if not comment:
                continue

            print(f"[COMMENT] 生成: {comment}")

            try:
                comment_id = post_comment(user_id, post_id, comment, token)
                if comment_id:
                    print(f"[OK] 投稿成功 comment_id={comment_id}")

                    commented_ids.add(post_id)
                    state["daily_count"] = state.get("daily_count", 0) + 1
                    state["total_count"] = state.get("total_count", 0) + 1

                    if "recent_comments" not in state:
                        state["recent_comments"] = []
                    state["recent_comments"].append({
                        "comment_id": comment_id,
                        "target_post_id": post_id,
                        "target_username": username,
                        "comment_text": comment,
                        "posted_at": datetime.now(JST).isoformat(),
                        "status": "active"
                    })
                    # 最新200件のみ保持
                    state["recent_comments"] = state["recent_comments"][-200:]
                    comment_count += 1
                    save_state(state)

                    if comment_count < MAX_COMMENTS_PER_RUN:
                        print(f"[WAIT] {COMMENT_INTERVAL_SEC}秒待機...")
                        time.sleep(COMMENT_INTERVAL_SEC)

            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                print(f"[ERROR] 投稿失敗 {e.code}: {body[:200]}")
                if e.code in (32, 429):
                    print("[WARN] レート制限検知。実行を中止します")
                    state["commented_post_ids"] = list(commented_ids)[-2000:]
                    state["last_run"] = datetime.now(JST).isoformat()
                    save_state(state)
                    return
                continue
            except Exception as e:
                print(f"[ERROR] 予期せぬエラー: {e}")
                continue

    state["commented_post_ids"] = list(commented_ids)[-2000:]
    state["last_run"] = datetime.now(JST).isoformat()
    save_state(state)

    print(f"\n[DONE] 今回: {comment_count}件 / 本日合計: {state['daily_count']}件")


if __name__ == "__main__":
    main()
