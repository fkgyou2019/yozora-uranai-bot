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
            text = r.get("text", "") if isinstance(r, dict) else str(r)
            recent_block += f"・{text[:40]}\n"

    # 星座名コメントの検出（コメント誘導型の投稿への返信）
    zodiac_hint = ""
    zodiac_names = ["牡羊座", "おひつじ座", "牡牛座", "おうし座", "双子座", "ふたご座",
                    "蟹座", "かに座", "獅子座", "しし座", "乙女座", "おとめ座",
                    "天秤座", "てんびん座", "蠍座", "さそり座", "射手座", "いて座",
                    "山羊座", "やぎ座", "水瓶座", "みずがめ座", "魚座", "うお座"]
    found_zodiac = None
    for z in zodiac_names:
        if z in comment_text:
            found_zodiac = z
            break
    if found_zodiac:
        zodiac_hint = f"\n※ この方は「{found_zodiac}」と星座を教えてくれました。元の投稿で約束した通り、この星座に個別のアドバイスを3行程度で具体的に伝えてください。スピリチュアルな表現を入れつつも、実際に行動できる具体的なアドバイスを含めること。これは返信の中で最も重要な要素です。"

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
{comment_text}{zodiac_hint}{emoji_hint}

【{time_context}】

{recent_block}
【返信ルール】
1. 冒頭に「@{commenter_name} さん\\n\\n」から始めること（必須。さんの後に必ず改行＋空行）
2. {'星座コメントの場合は3行以内で個別アドバイスを含める' if found_zodiac else '1-2行の短い返信（40-60文字が理想）'}。名前行は行数に含めない
3. 返信全体は3行以内に収めること（短く簡潔に）
4. 1行は20文字以内にすること（超えたら改行する）
5. 絵文字は1個まで（🌙✨🔮⭐のいずれか）

【最重要：人間味のあるコミュニケーションをしろ】
あなたはBotではない。友人や知人に返信するように、自然な会話をしろ。
以下のパターンをランダムに使い分けること：

A.「感謝+共感」型:
  - 「返信ありがとう！嬉しいです🌙」
  - 「コメントありがとうね✨」
  - 「いつもありがとう！励みになります」

B.「相手に触れる」型:
  - 「○○さん、いつもコメントくれて嬉しい🌙」
  - 「○○さんの直感、当たってますよ」
  - 「○○さんも同じこと感じてたんですね」

C.「会話を広げる」型:
  - 「○○座なんですね！来週楽しみにしてて✨」
  - 「気になりますよね、また詳しく書きますね」
  - 「それ分かります！私もそう思います🌙」

【禁止（Bot感が出るため絶対NG）】
- 毎回「ありがとうございます」で始める
- 「受け取ってくださり」
- 「良い流れが届きますように」
- 「素敵なタイミングですね」
- 「星が微笑んでますよ」
- 同じ定型文の繰り返し
   - 「その想い、きっと届きますよ」
9. 相手のコメントに文章がある場合は、その内容に具体的に触れる
10. 相手の星座が分かれば星座に触れる
11. 直近の返信と絶対に同じ言い回しを使わない
12. 【改行ルール（重要）】
   - @usernameさん の後は必ず改行すること
   - 1行は最大20文字。超えたら改行する
   - 名前の後は必ず空行を入れる
   - 2文以上の返信は文と文の間に改行を入れる
   - スマホで読みやすいように詰め込まない

返信文のみを出力。「@{commenter_name} さん\\n\\n本文」の形式で。余計な説明不要。"""

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

            # Claude APIで返信生成（類似性チェック付き。合格するまで最大3回再生成）
            reply_text = None
            for attempt in range(3):
                try:
                    candidate = generate_reply(
                        comment_text, post_text,
                        comment_user, recent_replies, api_key
                    )
                except Exception as e:
                    print(f"  [WARN] 返信生成エラー (attempt {attempt+1}): {e}")
                    break

                # 類似性チェック: 同じ投稿への直近返信と90%以上類似なら棄却
                from difflib import SequenceMatcher
                is_similar = False
                same_post_replies = [
                    r for r in recent_replies
                    if (r.get("post_id") if isinstance(r, dict) else None) == post_id
                ][-5:]
                for prev in same_post_replies:
                    prev_text = prev.get("text", "") if isinstance(prev, dict) else str(prev)
                    ratio = SequenceMatcher(None, candidate, prev_text).ratio()
                    if ratio >= 0.90:
                        print(f"  ⚠ 類似度{ratio:.0%}で棄却 (attempt {attempt+1})")
                        is_similar = True
                        break

                if not is_similar:
                    reply_text = candidate
                    if attempt > 0:
                        print(f"  ✅ {attempt+1}回目で合格")
                    break

            if reply_text is None:
                print(f"  [SKIP] @{comment_user}: 返信生成失敗（3回試行）→ 次回に持ち越し")
                # replied_ids に追加しない → 次回の実行で再試行される
                continue

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
