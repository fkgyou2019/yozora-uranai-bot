#!/usr/bin/env python3
"""
エージェント4: ポスター
投稿キューからThreads API / X APIで投稿を実行
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import hmac
import hashlib
import base64
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_env_file(path):
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    return True


def load_env():
    primary = os.path.join(PROJECT_DIR, "config", "api-keys.env")
    fallback = os.path.join(PROJECT_DIR, ".env")
    if not _read_env_file(primary) and not _read_env_file(fallback):
        print("[ERROR] config/api-keys.env も .env も見つかりません")
        sys.exit(1)


def load_json(path):
    full = os.path.join(PROJECT_DIR, path)
    if os.path.exists(full):
        with open(full, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path, data):
    full = os.path.join(PROJECT_DIR, path)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def log(level, msg):
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {msg}")


def check_kill_switch():
    status = load_json("state/system-status.json")
    if status.get("kill_switch", False):
        log("INFO", "KILL_SWITCH がONのため停止")
        sys.exit(0)


def update_agent_status(agent, status_str):
    data = load_json("state/system-status.json")
    data["agents"][agent]["status"] = status_str
    data["agents"][agent]["last_run"] = datetime.now(JST).isoformat()
    if status_str == "error":
        data["agents"][agent]["error_count"] = data["agents"][agent].get("error_count", 0) + 1
        data["consecutive_errors"] = data.get("consecutive_errors", 0) + 1
    else:
        data["consecutive_errors"] = 0
    save_json("state/system-status.json", data)


def check_posting_interval(history, safety):
    """最低投稿間隔のチェック（safety.jsonの設定値を使用）
    ※ is_repost=True の再投稿（ヘルスチェック補填）はスキップして、
       通常スケジュール投稿の間隔計算に影響させない。
    """
    min_interval = safety.get("posting_safety", {}).get("min_interval_seconds", 7200)
    posts = history.get("posts", [])
    if not posts:
        return True
    # is_repost=True の補填投稿は間隔計算の基準にしない
    regular_posts = [p for p in posts if not p.get("is_repost")]
    if not regular_posts:
        return True
    last = regular_posts[-1]
    last_time = datetime.fromisoformat(last.get("posted_at", "2000-01-01T00:00:00"))
    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=JST)
    elapsed = (datetime.now(JST) - last_time).total_seconds()
    if elapsed < min_interval:
        remaining = int(min_interval - elapsed)
        log("INFO", f"投稿間隔{min_interval}秒未満のためスキップ（残り{remaining}秒）")
        return False
    return True


def check_time_contradiction(content, current_hour):
    """投稿内容と現在時刻の矛盾をチェック。矛盾があれば理由を返す"""
    import re
    # 【再発防止】「M/D、」形式の日付フックが今日の日付と一致しない場合は除外
    # 例: 4/12生成の「4/12、急に動く星座。」が4/13に投稿されるのを防ぐ
    m_date = re.search(r'(\d{1,2})/(\d{1,2})[、,]', content)
    if m_date:
        post_month, post_day = int(m_date.group(1)), int(m_date.group(2))
        today_now = datetime.now(JST)
        if post_month != today_now.month or post_day != today_now.day:
            return f"日付フック「{m_date.group(1)}/{m_date.group(2)}、」が今日({today_now.month}/{today_now.day})と不一致 → 自動除外"
    # 「今夜○時までに」が○時以降
    m = re.search(r'今夜(\d+)時まで', content)
    if m and current_hour >= int(m.group(1)):
        return f"「今夜{m.group(1)}時まで」だが現在{current_hour}時"
    # 「今朝」が15時以降
    if '今朝' in content and current_hour >= 15:
        return f"「今朝」だが現在{current_hour}時"
    # 「午前中に」が13時以降
    if '午前中に' in content and current_hour >= 13:
        return f"「午前中に」だが現在{current_hour}時"
    # 「朝一で」が11時以降
    if '朝一で' in content and current_hour >= 11:
        return f"「朝一で」だが現在{current_hour}時"
    # 「今夜中に」が朝（6-12時）
    if '今夜中に' in content and 6 <= current_hour <= 12:
        return f"「今夜中に」だが現在{current_hour}時（朝）"
    return None


def check_banned_hours(safety):
    """深夜投稿禁止時間帯のチェック"""
    banned = safety.get("posting_safety", {}).get("banned_hours", [])
    current_hour = datetime.now(JST).hour
    if current_hour in banned:
        log("INFO", f"投稿禁止時間帯（{current_hour}時）のためスキップ")
        return False
    return True


def check_daily_limit(status, safety):
    """1日の投稿上限チェック（safety.jsonの設定値を使用）"""
    max_posts = safety.get("posting_safety", {}).get("max_posts_per_day", 8)
    today = datetime.now(JST).strftime("%Y-%m-%d")
    if status.get("daily_post_date") != today:
        status["daily_post_count"] = 0
        status["daily_post_date"] = today
    current = status.get("daily_post_count", 0)
    if current >= max_posts:
        log("INFO", f"1日の投稿上限（{max_posts}件）に到達")
        return False
    return True


# =============================================
# Threads API
# =============================================
def threads_post_text(text, user_id, access_token, max_retries=2):
    """Threads APIでテキスト投稿（auto_publish_text で1コール完了）"""
    url = f"https://graph.threads.net/v1.0/{user_id}/threads"
    params = {
        "media_type": "TEXT",
        "text": text,
        "auto_publish_text": "true",
        "access_token": access_token,
    }
    data = urllib.parse.urlencode(params).encode("utf-8")

    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return result.get("id")
        except (urllib.error.URLError, OSError) as e:
            log("WARN", f"Threads API リトライ {attempt+1}/{max_retries+1}: {e}")
            if attempt < max_retries:
                time.sleep(5)
            else:
                raise


def threads_reply(text, reply_to_id, user_id, access_token, max_retries=2):
    """Threads APIで自コメント（reply_to_id + auto_publish_text で1コール完了）"""
    url = f"https://graph.threads.net/v1.0/{user_id}/threads"
    params = {
        "media_type": "TEXT",
        "text": text,
        "reply_to_id": reply_to_id,
        "auto_publish_text": "true",
        "access_token": access_token,
    }
    data = urllib.parse.urlencode(params).encode("utf-8")

    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return result.get("id")
        except (urllib.error.URLError, OSError) as e:
            log("WARN", f"Threads Reply リトライ {attempt+1}/{max_retries+1}: {e}")
            if attempt < max_retries:
                time.sleep(5)
            else:
                raise


# =============================================
# X (Twitter) API v2
# =============================================
def x_create_oauth_header(method, url, params, consumer_key, consumer_secret,
                          access_token, access_secret):
    """OAuth 1.0a 署名ヘッダーを生成"""
    import uuid
    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }
    all_params = {**oauth_params, **params}
    sorted_params = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(all_params.items())
    )
    base_string = f"{method}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(sorted_params, safe='')}"
    signing_key = f"{urllib.parse.quote(consumer_secret, safe='')}&{urllib.parse.quote(access_secret, safe='')}"
    signature = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()
    oauth_params["oauth_signature"] = signature
    auth_header = "OAuth " + ", ".join(
        f'{k}="{urllib.parse.quote(v, safe="")}"'
        for k, v in sorted(oauth_params.items())
    )
    return auth_header


def x_post_tweet(text):
    """X API v2 でツイート投稿"""
    url = "https://api.twitter.com/2/tweets"
    consumer_key = os.environ.get("X_API_KEY", "")
    consumer_secret = os.environ.get("X_API_SECRET", "")
    access_token = os.environ.get("X_ACCESS_TOKEN", "")
    access_secret = os.environ.get("X_ACCESS_TOKEN_SECRET", "")

    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    auth_header = x_create_oauth_header(
        "POST", url, {}, consumer_key, consumer_secret, access_token, access_secret
    )
    req = urllib.request.Request(
        url, data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": auth_header,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result.get("data", {}).get("id")


# =============================================
# メイン処理
# =============================================
def post_one():
    """キューから1件取り出して投稿"""
    try:
        _post_one_inner()
    except SystemExit:
        raise  # kill_switch等のsys.exit()は再送出
    except Exception as e:
        log("ERROR", f"post_one() で予期しない例外が発生: {e}")
        import traceback
        traceback.print_exc()
        try:
            update_agent_status("poster", "error")
        except Exception:
            pass


def _post_one_inner():
    """post_one() の内部実装"""
    check_kill_switch()

    queue = load_json("state/post-queue.json")
    history = load_json("state/post-history.json")
    status = load_json("state/system-status.json")
    safety = load_json("config/safety.json")

    # errorステータスの投稿をキューからクリーンアップ
    error_posts = [p for p in queue.get("queue", []) if p.get("status") == "error"]
    if error_posts:
        log("INFO", f"errorステータスの投稿を{len(error_posts)}件キューから除去")
        for ep in error_posts:
            log("INFO", f"  除去: id={ep.get('id', '?')} error={ep.get('error', '不明')}")
        queue["queue"] = [p for p in queue.get("queue", []) if p.get("status") != "error"]
        save_json("state/post-queue.json", queue)

    # postingステータスのまま30分以上滞留した投稿をクリーンアップ
    now_cleanup = datetime.now(JST)
    stale_posting = []
    for p in queue.get("queue", []):
        if p.get("status") == "posting":
            posted_at = p.get("posting_started_at") or p.get("posted_at")
            if posted_at:
                try:
                    started = datetime.fromisoformat(posted_at)
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=JST)
                    if (now_cleanup - started).total_seconds() > 1800:
                        stale_posting.append(p)
                except (ValueError, TypeError):
                    stale_posting.append(p)
            else:
                # タイムスタンプなしのpostingは滞留とみなす
                stale_posting.append(p)
    if stale_posting:
        log("WARN", f"postingステータスで30分以上滞留した投稿を{len(stale_posting)}件errorに変更")
        for sp in stale_posting:
            log("WARN", f"  滞留: id={sp.get('id', '?')}")
            sp["status"] = "error"
            sp["error"] = "posting状態で30分以上滞留（タイムアウト）"
        save_json("state/post-queue.json", queue)

    # 安全チェック（3段階）
    if not check_banned_hours(safety):
        return
    # FORCE_POST=1 の場合は間隔チェックをスキップ（ヘルスチェック後の再投稿用）
    force_post = os.environ.get("FORCE_POST", "0") == "1"
    if not force_post and not check_posting_interval(history, safety):
        return
    if not check_daily_limit(status, safety):
        return

    # ========================================
    # Googleスプレッドシート承認チェック
    # GOOGLE_SHEETS_CREDENTIALS が設定されている場合のみ有効
    # 未設定の場合は従来通り投稿許可（フォールバック）
    # ========================================
    sheets_enabled = bool(os.environ.get("GOOGLE_SHEETS_CREDENTIALS"))
    sheets_approved_slot = None  # 承認済みスロット番号（Sheets有効時）

    if sheets_enabled:
        try:
            _agent_dir = os.path.join(PROJECT_DIR, "agents")
            if _agent_dir not in sys.path:
                sys.path.insert(0, _agent_dir)
            from shared.sheets_client import is_slot_approved, get_current_slot_num, get_approved_content
            current_slot = get_current_slot_num()
            if current_slot is not None:
                if is_slot_approved(current_slot):
                    sheets_approved_slot = current_slot
                    log("INFO", f"[Sheets] スロット{current_slot} ✅ 承認済み → 投稿許可")
                    # Sheetsに再生成後の最新コンテンツがあれば取得
                    sheets_content = get_approved_content(current_slot)
                else:
                    log("INFO", f"[Sheets] スロット{current_slot} ⏳ 未承認 → 本日スキップ")
                    return
            else:
                log("INFO", f"[Sheets] 現在時刻({datetime.now(JST).strftime('%H:%M')})はスロット外 → Sheetsチェックスキップ")
                sheets_content = None
        except Exception as e:
            log("WARN", f"[Sheets] 承認チェックエラー（フォールバック投稿許可）: {e}")
            sheets_content = None
    else:
        sheets_content = None

    # キューから次の投稿を取得（時間矛盾チェック付き）
    pending = [p for p in queue.get("queue", []) if p.get("status") == "queued"]
    if not pending:
        log("INFO", "投稿キューが空です")
        return

    now = datetime.now(JST)
    current_hour = now.hour

    # 禁止パターン（実績データ確認済み・投稿直前最終チェック）
    BANNED_PATTERN_REGEXES = [
        (r"(木星の優しい光|土星が.*微笑|春の陽射しが心強|応援メッセージ)", "励まし型"),
        (r"^(ここ数日.*モヤモヤ|なんか.*モヤモヤ|最近.*モヤモヤ)", "共感型フック"),
        (r"^(今日も頑張|自分を信じ|あなたは大丈夫)", "抽象共感型"),
    ]

    import re as _re

    post = None
    for candidate in pending:
        content = candidate.get("content", "")
        skip_reason = check_time_contradiction(content, current_hour)
        if skip_reason:
            log("INFO", f"時間矛盾で除外: {skip_reason} | {content[:25]}...")
            candidate["status"] = "skipped_time"
            continue
        # 禁止パターンチェック
        banned_reason = None
        for pat, label in BANNED_PATTERN_REGEXES:
            if _re.search(pat, content[:80]):
                banned_reason = label
                break
        if banned_reason:
            log("INFO", f"禁止パターンで除外: {banned_reason} | {content[:30]}...")
            candidate["status"] = "skipped_banned"
            continue
        post = candidate
        break

    # Sheetsで再生成された内容があれば、キューの内容を上書き（再作成機能の反映）
    if post is not None and sheets_enabled and sheets_content:
        if sheets_content.strip() != post.get("content", "").strip():
            log("INFO", f"[Sheets] 再生成コンテンツをキューに反映: {sheets_content[:30]}...")
            post["content"] = sheets_content

    if not post:
        log("INFO", "時間矛盾により投稿可能な投稿なし")
        save_json("state/post-queue.json", queue)
        return
    post["status"] = "posting"
    post["posting_started_at"] = datetime.now(JST).isoformat()
    save_json("state/post-queue.json", queue)

    # エージェントステータスをrunningに（status dictに直接書き込み、後で一括save）
    if "agents" in status and "poster" in status["agents"]:
        status["agents"]["poster"]["status"] = "running"
        status["agents"]["poster"]["last_run"] = datetime.now(JST).isoformat()
    platform = post.get("platform", "threads")

    # マルチアカウント: account_id からアカウント情報を解決
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    from agents.account_manager import (
        get_account, get_credentials, select_next_account,
        record_post as am_record_post, record_error as am_record_error,
        check_account_daily_limit, check_account_interval,
    )

    account_id = post.get("account_id")
    account = None
    if account_id:
        account = get_account(account_id)
        if account and not check_account_daily_limit(account_id, account):
            log("INFO", f"アカウント {account_id} の日次上限に到達。スキップ")
            post["status"] = "queued"  # 戻す
            save_json("state/post-queue.json", queue)
            return
        if account and not check_account_interval(account_id, account):
            log("INFO", f"アカウント {account_id} の投稿間隔未達。スキップ")
            post["status"] = "queued"
            save_json("state/post-queue.json", queue)
            return
    else:
        # account_id 未指定 → 自動選択（後方互換: 環境変数フォールバック）
        account = select_next_account(platform)
        if account:
            account_id = account["id"]
            log("INFO", f"アカウント自動選択: {account_id}")

    try:
        post_id = None

        if platform == "threads":
            # マルチアカウント: accounts.json から認証情報を取得
            if account:
                creds = get_credentials(account)
                user_id = creds.get("user_id", "")
                access_token = creds.get("access_token", "")
            else:
                # 後方互換: 環境変数から取得
                user_id = os.environ.get("THREADS_USER_ID", "")
                access_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
            if not user_id or not access_token:
                raise ValueError("THREADS_USER_ID / THREADS_ACCESS_TOKEN が未設定")

            content = post.get("content", "")
            hashtag = post.get("hashtag", "")
            if hashtag:
                content = f"{content}\n\n{hashtag}"

            post_id = threads_post_text(content, user_id, access_token)
            log("INFO", f"Threads投稿完了: {post_id} (account={account_id or 'default'})")

            # アフィリエイト投稿の場合、コメントでPRリンク
            if post.get("is_affiliate") and post.get("affiliate_comment"):
                time.sleep(5)
                reply_id = threads_reply(
                    post["affiliate_comment"], post_id, user_id, access_token
                )
                log("INFO", f"PRコメント投稿完了: {reply_id}")

        elif platform == "x":
            content = post.get("content", "")
            hashtag = post.get("hashtag", "")
            if hashtag:
                content = f"{content}\n\n{hashtag}"
            if len(content) > 280:
                log("WARN", f"X投稿が280文字超過({len(content)}文字)。トリミングします。")
                content = content[:277] + "..."

            # マルチアカウント: accounts.json から認証情報を取得
            if account:
                creds = get_credentials(account)
                consumer_key = os.environ.get("X_API_KEY", "")
                consumer_secret = os.environ.get("X_API_SECRET", "")
                acc_token = creds.get("access_token", "")
                acc_secret = creds.get("access_token_secret", "")
                if acc_token and acc_secret:
                    url = "https://api.twitter.com/2/tweets"
                    body = json.dumps({"text": content}, ensure_ascii=False).encode("utf-8")
                    auth_header = x_create_oauth_header(
                        "POST", url, {}, consumer_key, consumer_secret, acc_token, acc_secret
                    )
                    req = urllib.request.Request(
                        url, data=body,
                        headers={"Content-Type": "application/json", "Authorization": auth_header},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        result = json.loads(resp.read().decode("utf-8"))
                    post_id = result.get("data", {}).get("id")
                else:
                    post_id = x_post_tweet(content)
            else:
                post_id = x_post_tweet(content)

            log("INFO", f"X投稿完了: {post_id} ({len(content)}文字, account={account_id or 'default'})")

            # リポスト待ちキューに追加（別ワークフローが処理）
            if post_id:
                try:
                    repost_config = load_json("config/repost.json")
                    if repost_config.get("enabled", False):
                        source_account = post.get("repost_source_account", account_id or "account_1")
                        pending_repost = load_json("state/pending-reposts.json")
                        if "pending" not in pending_repost:
                            pending_repost["pending"] = []
                        pending_repost["pending"].append({
                            "tweet_id": str(post_id),
                            "source_account": source_account,
                            "posted_at": datetime.now(JST).isoformat(),
                        })
                        save_json("state/pending-reposts.json", pending_repost)
                        log("INFO", f"リポスト待ちキュー追加: tweet={post_id}, source={source_account}")
                except Exception as e:
                    log("WARN", f"リポストキュー追加失敗（投稿自体は成功）: {e}")

        # 成功 → 履歴に追加
        post["status"] = "posted"
        post["posted_at"] = datetime.now(JST).isoformat()
        post["platform_post_id"] = post_id
        if account_id:
            post["account_id"] = account_id
        # FORCE_POST=1（ヘルスチェック補填）はis_repostフラグで通常間隔チェックから除外
        if os.environ.get("FORCE_POST", "0") == "1":
            post["is_repost"] = True

        if "posts" not in history:
            history["posts"] = []
        # ID重複チェック: 同IDが既にhistoryにある場合はIDに _dup を付与して区別
        existing_ids = {p.get("id") for p in history["posts"]}
        if post.get("id") in existing_ids:
            import uuid as _uuid
            post["id"] = f"{post['id']}_{_uuid.uuid4().hex[:6]}"
            log("WARN", f"ID重複検出: 新IDで記録 → {post['id']}")
        history["posts"].append(post)
        save_json("state/post-history.json", history)

        # キューから削除
        queue["queue"] = [p for p in queue["queue"] if p.get("id") != post.get("id")]
        save_json("state/post-queue.json", queue)

        # 日次カウント更新 + エージェントステータス更新（二重save防止のため統合）
        status["daily_post_count"] = status.get("daily_post_count", 0) + 1
        status["consecutive_errors"] = 0
        if "agents" in status and "poster" in status["agents"]:
            status["agents"]["poster"]["status"] = "idle"
            status["agents"]["poster"]["last_run"] = datetime.now(JST).isoformat()
            status["agents"]["poster"]["error_count"] = 0
        save_json("state/system-status.json", status)

        # マルチアカウント: アカウント別ステータス記録
        if account_id:
            am_record_post(account_id)

    except Exception as e:
        log("ERROR", f"投稿失敗: {e}")
        post["status"] = "error"
        post["error"] = str(e)
        save_json("state/post-queue.json", queue)
        update_agent_status("poster", "error")
        # マルチアカウント: アカウント別エラー記録
        if account_id:
            am_record_error(account_id, str(e))


if __name__ == "__main__":
    import threading

    def watchdog(timeout_sec=90):
        """グローバルタイムアウト: poster.pyが指定秒数でハングしたら強制終了"""
        time.sleep(timeout_sec)
        log("ERROR", f"グローバルタイムアウト({timeout_sec}秒)で強制終了")
        os._exit(1)

    # ウォッチドッグ起動（デーモンスレッドなので正常終了時は自動消滅）
    wd = threading.Thread(target=watchdog, args=(90,), daemon=True)
    wd.start()

    load_env()
    log("INFO", "ポスターエージェント開始")
    post_one()
    log("INFO", "ポスターエージェント完了")
