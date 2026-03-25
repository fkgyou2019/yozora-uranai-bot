#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
返信品質チェッカー: 自動返信の品質を検査し、問題のある返信を検知・削除
GitHub Actionsで定期実行
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

MY_USERNAME = "yozora.uranai"

# --- ペルソナ違反NG表現 ---
PERSONA_NG_WORDS = [
    "あんた", "お前", "黙って", "しなさい", "してやる", "褒めてあげる",
    "ですわ", "ですの", "かしら",
]

# --- ユーティリティ ---


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


def get_env(name):
    """環境変数から取得、なければ api-keys.env からフォールバック"""
    val = os.environ.get(name)
    if val:
        return val
    env_file = os.path.join(PROJECT_DIR, "config", "api-keys.env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == name:
                    return v.strip()
    return None


def threads_get(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def threads_delete(media_id, access_token):
    """Threads APIで投稿（返信）を削除"""
    url = f"https://graph.threads.net/v1.0/{media_id}?access_token={access_token}"
    req = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --- メイン処理 ---


def fetch_my_posts(user_id, access_token, limit=10):
    """自分の最新投稿を取得"""
    url = (
        f"https://graph.threads.net/v1.0/{user_id}/threads"
        f"?fields=id,text,timestamp&limit={limit}"
        f"&access_token={access_token}"
    )
    result = threads_get(url)
    return result.get("data", [])


def fetch_replies(media_id, access_token):
    """投稿のコメント（返信）を取得"""
    url = (
        f"https://graph.threads.net/v1.0/{media_id}/replies"
        f"?fields=id,text,username,timestamp"
        f"&access_token={access_token}"
    )
    result = threads_get(url)
    return result.get("data", [])


def check_persona_violation(text):
    """ペルソナ違反チェック: NG表現を検出"""
    violations = []
    for ng in PERSONA_NG_WORDS:
        if ng in text:
            violations.append(ng)
    return violations


def check_duplicate_replies(my_replies):
    """同一投稿内で同じユーザーへの重複返信を検出"""
    user_replies = {}
    for reply in my_replies:
        text = reply.get("text", "")
        # @usernameさん のパターンから返信先ユーザーを抽出
        match = re.match(r"@(\S+?)さん", text)
        if match:
            target_user = match.group(1)
        else:
            match = re.match(r"@(\S+)", text)
            target_user = match.group(1) if match else "_unknown_"

        if target_user not in user_replies:
            user_replies[target_user] = []
        user_replies[target_user].append(reply)

    duplicates = {}
    for user, replies in user_replies.items():
        if len(replies) >= 2:
            # 最初の1件を残し、残りを削除対象に
            duplicates[user] = {
                "keep": replies[0],
                "delete": replies[1:],
                "total": len(replies),
            }
    return duplicates


def check_reply_format(text):
    """返信フォーマットチェック"""
    warnings = []
    lines = text.split("\n")

    # @usernameさん の後に改行がない
    if re.match(r"@\S+さん\s*\S", lines[0]) and "\n" not in text[:text.find("さん") + 2 + 1] if "さん" in text else False:
        first_line = lines[0]
        if re.match(r"@\S+さん.+", first_line) and not re.match(r"@\S+さん\s*$", first_line):
            warnings.append("@usernameさん の後に改行なし")

    # 4行以上
    non_empty_lines = [l for l in lines if l.strip()]
    if len(non_empty_lines) >= 4:
        warnings.append(f"返信が{len(non_empty_lines)}行（4行以上）")

    # 1行25文字以上
    for i, line in enumerate(lines):
        if len(line) >= 25:
            warnings.append(f"行{i+1}が{len(line)}文字（25文字以上）")
            break  # 1件だけ報告

    return warnings


def check_bot_pattern(all_my_replies):
    """Bot感チェック: 直近の返信パターンを検査"""
    delete_ids = []
    warnings = []

    if len(all_my_replies) >= 5:
        recent_5 = all_my_replies[:5]
        first_lines = []
        for r in recent_5:
            text = r.get("text", "")
            # @username部分を除いた本文の先頭を取得
            body = re.sub(r"^@\S+さん\s*\n?", "", text).strip()
            first_line = body.split("\n")[0] if body else ""
            first_lines.append(first_line)

        # 直近5件が全て同じ文で始まる
        if len(set(first_lines)) == 1 and first_lines[0]:
            for r in recent_5:
                delete_ids.append(r["id"])

    # 「ありがとうございます」で始まる返信が3件連続
    if len(all_my_replies) >= 3:
        arigatou_streak = 0
        for r in all_my_replies:
            text = r.get("text", "")
            body = re.sub(r"^@\S+さん\s*\n?", "", text).strip()
            if body.startswith("ありがとうございます"):
                arigatou_streak += 1
                if arigatou_streak >= 3:
                    warnings.append("「ありがとうございます」で始まる返信が3件連続")
                    break
            else:
                arigatou_streak = 0

    return delete_ids, warnings


def main():
    access_token = get_env("THREADS_ACCESS_TOKEN")
    user_id = get_env("THREADS_USER_ID")

    if not access_token or not user_id:
        print("ERROR: THREADS_ACCESS_TOKEN / THREADS_USER_ID が未設定")
        sys.exit(1)

    now = datetime.now(JST)
    print(f"返信チェック開始: {now.strftime('%Y-%m-%d %H:%M')} JST")

    # 最新投稿を取得
    try:
        posts = fetch_my_posts(user_id, access_token, limit=10)
    except Exception as e:
        print(f"ERROR: 投稿取得失敗: {e}")
        sys.exit(1)

    total_replies_checked = 0
    total_deleted = 0
    total_warnings = 0
    all_my_replies_global = []
    log_entries = []

    for post in posts:
        post_id = post.get("id", "unknown")
        post_time = post.get("timestamp", "")
        # タイムスタンプからJST時刻を表示
        try:
            dt = datetime.fromisoformat(post_time.replace("Z", "+00:00")).astimezone(JST)
            time_str = dt.strftime("%H:%M")
        except Exception:
            time_str = "??:??"

        print(f"\n--- 投稿ID {post_id} ({time_str}) ---")

        # コメント取得
        try:
            replies = fetch_replies(post_id, access_token)
        except Exception as e:
            print(f"  ERROR: コメント取得失敗: {e}")
            continue

        # 自分の返信を抽出
        my_replies = [r for r in replies if r.get("username") == MY_USERNAME]
        total_replies_checked += len(my_replies)
        all_my_replies_global.extend(my_replies)

        if not my_replies:
            print(f"  (自分の返信なし)")
            continue

        post_deleted = 0
        post_warnings = 0
        post_log = {"post_id": post_id, "time": time_str, "issues": []}

        # --- a. ペルソナ違反チェック ---
        for reply in my_replies[:]:
            text = reply.get("text", "")
            violations = check_persona_violation(text)
            if violations:
                reply_id = reply["id"]
                ng_word = violations[0]
                print(f"  NG: ペルソナ違反: 「{ng_word}」を含む返信を削除 (ID: {reply_id})")
                try:
                    threads_delete(reply_id, access_token)
                    post_deleted += 1
                    post_log["issues"].append({
                        "type": "persona_violation",
                        "reply_id": reply_id,
                        "ng_word": ng_word,
                        "action": "deleted",
                    })
                except Exception as e:
                    print(f"    削除失敗: {e}")
                    post_log["issues"].append({
                        "type": "persona_violation",
                        "reply_id": reply_id,
                        "ng_word": ng_word,
                        "action": "delete_failed",
                        "error": str(e),
                    })

        # --- b. 重複返信チェック ---
        duplicates = check_duplicate_replies(my_replies)
        for target_user, info in duplicates.items():
            print(f"  WARNING: @{target_user} への重複返信検知（{info['total']}件→1件に削減）")
            for dup_reply in info["delete"]:
                dup_id = dup_reply["id"]
                try:
                    threads_delete(dup_id, access_token)
                    post_deleted += 1
                    post_log["issues"].append({
                        "type": "duplicate_reply",
                        "target_user": target_user,
                        "reply_id": dup_id,
                        "action": "deleted",
                    })
                except Exception as e:
                    print(f"    削除失敗: {e}")
                    post_log["issues"].append({
                        "type": "duplicate_reply",
                        "target_user": target_user,
                        "reply_id": dup_id,
                        "action": "delete_failed",
                        "error": str(e),
                    })

        # --- c. フォーマットチェック ---
        for reply in my_replies:
            text = reply.get("text", "")
            fmt_warnings = check_reply_format(text)
            for w in fmt_warnings:
                print(f"  WARNING: フォーマット: {w} (ID: {reply['id']})")
                post_warnings += 1
                post_log["issues"].append({
                    "type": "format_warning",
                    "reply_id": reply["id"],
                    "detail": w,
                })

        if post_deleted == 0 and post_warnings == 0 and not duplicates:
            print(f"  OK: 返信{len(my_replies)}件: 問題なし")

        total_deleted += post_deleted
        total_warnings += post_warnings
        if post_log["issues"]:
            log_entries.append(post_log)

    # --- d. Bot感チェック（全投稿横断） ---
    # タイムスタンプ降順でソート
    all_my_replies_global.sort(
        key=lambda r: r.get("timestamp", ""), reverse=True
    )
    bot_delete_ids, bot_warnings = check_bot_pattern(all_my_replies_global)

    if bot_delete_ids:
        print(f"\n  NG: Bot感検知: 直近5件が同一パターン → {len(bot_delete_ids)}件削除")
        for rid in bot_delete_ids:
            try:
                threads_delete(rid, access_token)
                total_deleted += 1
                log_entries.append({
                    "post_id": "global",
                    "issues": [{
                        "type": "bot_pattern",
                        "reply_id": rid,
                        "action": "deleted",
                    }],
                })
            except Exception as e:
                print(f"    削除失敗 ({rid}): {e}")

    for bw in bot_warnings:
        print(f"\n  WARNING: Bot感: {bw}")
        total_warnings += 1

    # --- 結果サマリー ---
    print(f"\n投稿{len(posts)}件、返信{total_replies_checked}件を検査")
    print(f"\n=== 結果 ===")
    print(f"検査: {total_replies_checked}件")
    print(f"削除: {total_deleted}件")
    print(f"警告: {total_warnings}件")

    # --- ログ保存 ---
    log_data = {
        "checked_at": now.isoformat(),
        "posts_checked": len(posts),
        "replies_checked": total_replies_checked,
        "deleted": total_deleted,
        "warnings": total_warnings,
        "details": log_entries,
    }
    save_json("state/reply-check-log.json", log_data)
    print(f"\nログ保存: state/reply-check-log.json")


if __name__ == "__main__":
    main()
