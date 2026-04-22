#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
コメント自動返信: 投稿へのコメントに固定LINE誘導文を返信し、Threads APIで投稿
（Claude API不使用・コスト0。全コメント共通文）
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


def classify_comment_type(text: str) -> str:
    if not text or not text.strip():
        return "空"
    stripped = text.strip()
    is_text = any(c.isalpha() or '\u3040' <= c <= '\u9fff' for c in stripped)
    if not is_text:
        return "絵文字のみ"
    zodiac_names = ["牡羊", "おひつじ", "牡牛", "おうし", "双子", "ふたご", "蟹", "かに",
                    "獅子", "しし", "乙女", "おとめ", "天秤", "てんびん", "蠍", "さそり",
                    "射手", "いて", "山羊", "やぎ", "水瓶", "みずがめ", "魚", "うお"]
    for z in zodiac_names:
        if z in stripped:
            return "星座言及"
    if re.search(r'[？?]|ですか|でしょう|教えて', stripped):
        return "質問"
    if re.search(r'ありがと|感謝|嬉しい|良かった|当たっ', stripped):
        return "感謝・的中報告"
    if re.search(r'楽しみ|期待|なれます|なりたい|そうなってほし|当たってほし|実現|願', stripped):
        return "期待・願望"
    if re.search(r'わかる|当てはまる|そうそう|その通り|確かに|私も|まさに|私かも', stripped):
        return "共感・共鳴"
    return "その他"


def build_post_pattern_map() -> dict:
    """post-history.json から platform_post_id → {pattern_name, hook} を返す"""
    history = load_json("state/post-history.json")
    pattern_map = {}
    for post in history.get("posts", []):
        pid = post.get("platform_post_id")
        if pid:
            content = post.get("content", "")
            first_line = content.split("\n")[0][:60] if content else ""
            pattern_map[pid] = {
                "pattern_name": post.get("pattern_name", "不明"),
                "hook": first_line,
            }
    return pattern_map


def append_comment_log(entry: dict):
    """state/comment-log.json にコメントを追記（最大1000件）"""
    path = "state/comment-log.json"
    data = load_json(path)
    logs = data.get("logs", [])
    existing_ids = {l["comment_id"] for l in logs}
    if entry["comment_id"] not in existing_ids:
        logs.append(entry)
    logs = logs[-1000:]
    save_json(path, {"logs": logs})


def threads_get(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def threads_reply(text, reply_to_id, user_id, access_token):
    """Threads APIで返信を投稿する。reply_to_idには元の投稿IDを指定すること（コメントIDではない）。"""
    url = f"https://graph.threads.net/v1.0/{user_id}/threads"
    params = {
        "media_type": "TEXT",
        "text": text,
        "reply_to_id": reply_to_id,
        "auto_publish_text": "true",
        "access_token": access_token,
    }
    print(f"  [DEBUG] reply_to_id={reply_to_id} (投稿ID)")
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result.get("id")
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass
        print(f"  [ERROR] Threads API {e.code}: reply_to_id={reply_to_id}, body={error_body}")
        raise


def generate_reply(comment_text=None, original_post_text=None, commenter_name=None, recent_replies=None, api_key=None):
    """固定LINE誘導文を返す（Claude API不使用・コスト0）"""
    return (
        "せっかくのご縁なので\n"
        "あなたの恋の流れを、霊視で視させてください✨\n"
        "ルナ姉のアイコンをタップした先の\n"
        "固定投稿からお受け取りください🌙\n"
        "https://lin.ee/Y4Pyykb"
    )


def main():
    access_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    user_id = os.environ.get("THREADS_USER_ID", "")

    if not all([access_token, user_id]):
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

    if not all([access_token, user_id]):
        print("ERROR: 必要な環境変数が未設定 (THREADS_ACCESS_TOKEN, THREADS_USER_ID)")
        sys.exit(1)

    # 返信済みコメントIDを記録するファイル
    replied = load_json("state/replied-comments.json")
    if not replied:
        replied = {"replied_ids": [], "last_checked": None}

    replied_ids = set(replied.get("replied_ids", []))

    # 最近の投稿を取得（直近20件）
    posts_url = (
        f"https://graph.threads.net/v1.0/{user_id}/threads"
        f"?fields=id,text,timestamp&limit=20&access_token={access_token}"
    )

    try:
        posts_data = threads_get(posts_url)
    except Exception as e:
        print(f"投稿取得エラー: {e}")
        sys.exit(1)

    posts = posts_data.get("data", [])
    total_replied = 0
    total_skipped = 0
    max_replies_per_run = 15  # 1回の実行で最大15件返信
    recent_replies = replied.get("recent_reply_texts", [])  # 直近の返信文を保持
    consecutive_errors = {}  # 投稿IDごとの連続400エラー回数
    post_pattern_map = build_post_pattern_map()
    pending_log = {}  # comment_id → ログエントリ（返信後にフラグ更新）

    # リピーター判定用：過去にコメントしたユーザー一覧
    existing_logs = load_json("state/comment-log.json").get("logs", [])
    known_commenters = {e["commenter"] for e in existing_logs}

    for post in posts:
        if total_replied >= max_replies_per_run:
            break

        post_id = post["id"]
        post_text = post.get("text", "")
        post_info = post_pattern_map.get(post_id, {"pattern_name": "不明", "hook": post_text.split("\n")[0][:60] if post_text else ""})

        # この投稿へのコメントを全件取得（ページネーション対応）
        comments = []
        next_url = (
            f"https://graph.threads.net/v1.0/{post_id}/replies"
            f"?fields=id,text,username,timestamp&limit=50&access_token={access_token}"
        )
        page_count = 0
        while next_url and page_count < 5:  # 最大5ページ（250件）
            try:
                replies_data = threads_get(next_url)
            except urllib.error.HTTPError as e:
                print(f"  [WARN] コメント取得HTTPエラー (post={post_id}, page={page_count+1}): {e.code}")
                break
            except Exception as e:
                print(f"  [WARN] コメント取得エラー (post={post_id}): {e}")
                break
            comments.extend(replies_data.get("data", []))
            next_url = replies_data.get("paging", {}).get("next")
            page_count += 1

        if not comments:
            continue
        print(f"\n📨 投稿 {post_id[:10]}... : {len(comments)}件のコメント（{page_count}ページ取得）")
        # コメントを優先度順にソート: 星座言及 > 文章 > 絵文字のみ
        def _comment_priority(c):
            txt = c.get("text", "")
            zodiac_names_short = ["牡羊", "牡牛", "双子", "蟹", "獅子", "乙女",
                                   "天秤", "蠍", "射手", "山羊", "水瓶", "魚"]
            if any(z in txt for z in zodiac_names_short):
                return 0  # 最優先
            if any(ch.isalpha() or '\u3040' <= ch <= '\u9fff' for ch in txt):
                return 1  # 文章コメント
            return 2  # 絵文字のみ
        comments = sorted(comments, key=_comment_priority)

        replied_users_this_post = set()  # この投稿で既に返信したユーザー

        # Threads API上で既に返信済みのコメントIDとユーザーを特定
        # （replied-comments.json リセット時の再返信防止）
        already_replied_on_threads = set()  # Threads上で既に返信が存在するコメントID
        my_replies_in_thread = [c for c in comments if c.get("username") == "yozora.uranai"]
        for my_reply in my_replies_in_thread:
            my_text = my_reply.get("text", "")
            # 返信テキスト冒頭の @ユーザー名 を抽出して返信済みユーザーを特定
            mention_match = re.match(r"@(\S+)", my_text)
            if mention_match:
                mentioned_user = mention_match.group(1)
                replied_users_this_post.add(mentioned_user)
                # そのユーザーのコメントIDを返信済みとしてマーク
                for c in comments:
                    if c.get("username") == mentioned_user:
                        already_replied_on_threads.add(c["id"])

        for comment in comments:
            if total_replied >= max_replies_per_run:
                break

            comment_id = comment["id"]
            comment_text = comment.get("text", "")
            comment_user = comment.get("username", "")

            # 自分のコメントはスキップ
            if comment_user == "yozora.uranai":
                replied_users_this_post.add(comment_user)
                continue

            # 既に返信済みならスキップ（JSON記録 OR Threads上に返信が存在）
            if comment_id in replied_ids or comment_id in already_replied_on_threads:
                replied_users_this_post.add(comment_user)
                if comment_id in already_replied_on_threads and comment_id not in replied_ids:
                    replied_ids.add(comment_id)  # JSONにも記録を復元
                continue

            # 同一ユーザーへの返信は1投稿につき1回まで（重複返信防止）
            if comment_user in replied_users_this_post:
                replied_ids.add(comment_id)
                print(f"  ⏭ @{comment_user}: 同一投稿内重複スキップ")
                continue

            # 空コメントはスキップ
            if not comment_text.strip():
                continue

            # コメントログに記録（返信前にエントリ作成、返信後にフラグ更新）
            if comment_id not in pending_log:
                pending_log[comment_id] = {
                    "comment_id": comment_id,
                    "comment_text": comment_text,
                    "comment_type": classify_comment_type(comment_text),
                    "commenter": comment_user,
                    "is_repeat": comment_user in known_commenters,
                    "logged_at": datetime.now(JST).isoformat(),
                    "post_id": post_id,
                    "post_hook": post_info["hook"],
                    "post_pattern": post_info["pattern_name"],
                    "replied": False,
                    "reply_text": "",
                }

            # 返信判定:
            # - 星座名を含むコメント → 必ず返信（コメント誘導型の約束）
            # - 文章コメント → 必ず返信
            # - 絵文字のみ → 70%の確率（人間は全レスしない）
            is_text_comment = any(
                c.isalpha() or ('\u3040' <= c <= '\u309f') or ('\u30a0' <= c <= '\u30ff') or ('\u4e00' <= c <= '\u9fff') for c in comment_text
            )
            has_zodiac = any(z in comment_text for z in ["牡羊座", "おひつじ", "牡牛座", "おうし", "双子座", "ふたご", "蟹座", "かに", "獅子座", "しし", "乙女座", "おとめ", "天秤座", "てんびん", "蠍座", "さそり", "射手座", "いて", "山羊座", "やぎ", "水瓶座", "みずがめ", "魚座", "うお"])
            if not is_text_comment and not has_zodiac and random.random() > 0.7:
                replied_ids.add(comment_id)  # スキップしたことは記録
                total_skipped += 1
                print(f"  ⏭ @{comment_user}: 「{comment_text[:10]}」（スキップ）")
                continue

            # 固定LINE誘導文を返信（Claude API不使用・コスト0）
            reply_text = generate_reply()

            # Threads APIで返信投稿（reply_to_idには元の投稿IDを使う。コメントIDではない）
            try:
                reply_id = threads_reply(reply_text, post_id, user_id, access_token)
                replied_ids.add(comment_id)
                replied_users_this_post.add(comment_user)
                recent_replies.append({"text": reply_text, "to_user": comment_user, "post_id": post_id})
                total_replied += 1
                if comment_id in pending_log:
                    pending_log[comment_id]["replied"] = True
                    pending_log[comment_id]["reply_text"] = reply_text
                print(f"  ✅ @{comment_user}: 「{comment_text[:20]}」→ 「{reply_text[:40]}」")
            except urllib.error.HTTPError as e:
                error_count = consecutive_errors.get(post_id, 0) + 1
                consecutive_errors[post_id] = error_count
                if e.code == 400 and error_count >= 3:
                    print(f"  [WARN] 投稿{post_id}で400エラーが{error_count}回連続。この投稿への返信をスキップします。")
                    break  # この投稿のコメントループを抜ける
                print(f"  [WARN] 返信投稿エラー ({e.code}): {e}")
                continue
            except Exception as e:
                print(f"  [WARN] 返信投稿エラー: {e}")
                continue
            else:
                # 成功したらこの投稿の連続エラーカウントをリセット
                consecutive_errors[post_id] = 0

    # コメントログを保存
    for entry in pending_log.values():
        append_comment_log(entry)
    if pending_log:
        print(f"  📝 コメントログ追記: {len(pending_log)}件")

    # 返信済みIDと直近返信テキストを保存
    replied["replied_ids"] = list(replied_ids)[-500:]
    replied["recent_reply_texts"] = recent_replies[-5:]  # 直近5件の返信文を保持
    replied["last_checked"] = datetime.now(JST).isoformat()
    save_json("state/replied-comments.json", replied)

    print(f"\n返信完了: {total_replied}件 / スキップ{total_skipped}件（累計{len(replied['replied_ids'])}件）")

    # ── セッション現状把握ファイル更新 ──
    try:
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            "update_session_context",
            pathlib.Path(__file__).parent / "update_session_context.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.main()
    except Exception as e:
        print(f"[auto_reply] session-context更新エラー（続行）: {e}")


if __name__ == "__main__":
    main()
