#!/usr/bin/env python3
"""
バズ投稿コレクター
複数ソース（X検索・Threads検索・手動入力・自アカウントベスト）から
スピ占い系のバズ投稿を収集し state/buzz-collection.json に保存
"""

import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAX_POSTS = 500  # 保持する最大件数


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


# =============================================
# 重複チェック
# =============================================
def _content_key(content):
    """重複判定用: content の先頭50文字"""
    return content[:50].strip() if content else ""


def _is_duplicate(content, existing_keys):
    """既存投稿との重複チェック"""
    key = _content_key(content)
    if not key:
        return True
    return key in existing_keys


def _build_existing_keys(posts):
    """既存投稿のコンテンツキーセットを構築"""
    return {_content_key(p.get("content", "")) for p in posts}


# =============================================
# ID生成
# =============================================
def _generate_id(platform, source, index):
    """ユニークなバズ投稿IDを生成"""
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    return f"buzz_{platform}_{source}_{ts}_{index:03d}"


# =============================================
# ソース1: X API Search
# =============================================
def collect_x_search(config, existing_keys):
    """X API v2 の検索エンドポイントでバズ投稿を収集"""
    plan = os.environ.get("X_API_PLAN", "free").lower()
    if plan not in ("basic", "pro", "payperuse", "pay-per-use"):
        log("INFO", f"X検索: API未対応（{plan} plan）。Basic/Pro/PayPerUseが必要。スキップ")
        return [], False

    bearer = os.environ.get("X_BEARER_TOKEN", "")
    if not bearer:
        log("WARN", "X検索: X_BEARER_TOKEN が未設定。スキップ")
        return [], False

    # 検索キーワード構築
    keywords = config.get("search_keywords", {})
    primary = keywords.get("primary", [])
    secondary = keywords.get("secondary", [])
    hashtags = keywords.get("hashtags", [])
    all_terms = primary + secondary + hashtags

    if not all_terms:
        log("WARN", "X検索: 検索キーワードが空。スキップ")
        return [], False

    # OR結合でクエリ構築（URLエンコード前）
    query_terms = " OR ".join(all_terms)
    query = f"({query_terms}) lang:ja -is:retweet -is:reply"

    threshold = config.get("buzz_threshold", {}).get("x", {})
    min_likes = threshold.get("min_likes", 100)
    min_retweets = threshold.get("min_retweets", 20)
    min_impressions = threshold.get("min_impressions", 5000)

    params = urllib.parse.urlencode({
        "query": query,
        "max_results": 50,
        "tweet.fields": "public_metrics,created_at,author_id,lang",
    })
    url = f"https://api.twitter.com/2/tweets/search/recent?{params}"

    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {bearer}"},
        method="GET",
    )

    posts = []
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        tweets = result.get("data", [])
        log("INFO", f"X検索: {len(tweets)}件取得")

        for i, tweet in enumerate(tweets):
            metrics = tweet.get("public_metrics", {})
            likes = metrics.get("like_count", 0)
            retweets = metrics.get("retweet_count", 0)
            impressions = metrics.get("impression_count", 0)
            replies = metrics.get("reply_count", 0)

            # バズ閾値フィルタ
            if likes < min_likes or retweets < min_retweets or impressions < min_impressions:
                continue

            content = tweet.get("text", "")
            if _is_duplicate(content, existing_keys):
                continue

            eng_rate = 0
            if impressions > 0:
                eng_rate = round((likes + retweets + replies) / impressions * 100, 2)

            post = {
                "id": _generate_id("x", "search", i),
                "platform": "x",
                "source": "x_search",
                "username": f"@{tweet.get('author_id', 'unknown')}",
                "content": content,
                "metrics": {
                    "likes": likes,
                    "retweets": retweets,
                    "replies": replies,
                    "views": impressions,
                    "engagement_rate": eng_rate,
                },
                "collected_at": datetime.now(JST).isoformat(),
                "analyzed": False,
            }
            posts.append(post)
            existing_keys.add(_content_key(content))

        log("INFO", f"X検索: バズ閾値通過 {len(posts)}件")
        return posts, True

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        log("ERROR", f"X検索APIエラー (HTTP {e.code}): {error_body[:200]}")
        return [], False
    except Exception as e:
        log("ERROR", f"X検索エラー: {e}")
        return [], False


# =============================================
# ソース2: Threads API Keyword Search
# =============================================
def collect_threads_search(config, existing_keys):
    """Threads API のキーワード検索でバズ投稿を収集"""
    api_level = os.environ.get("THREADS_API_LEVEL", "basic").lower()
    if api_level != "advanced":
        log("INFO", f"Threads検索: Advanced Access未対応（{api_level}）。スキップ")
        return [], False

    access_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not access_token:
        log("WARN", "Threads検索: THREADS_ACCESS_TOKEN が未設定。スキップ")
        return [], False

    keywords = config.get("search_keywords", {})
    primary = keywords.get("primary", [])
    secondary = keywords.get("secondary", [])
    all_terms = primary + secondary

    if not all_terms:
        log("WARN", "Threads検索: 検索キーワードが空。スキップ")
        return [], False

    threshold = config.get("buzz_threshold", {}).get("threads", {})
    min_likes = threshold.get("min_likes", 50)
    min_replies = threshold.get("min_replies", 10)
    min_views = threshold.get("min_views", 1000)

    posts = []
    for term in all_terms:
        try:
            params = urllib.parse.urlencode({
                "q": term,
                "search_type": "TOP",
                "fields": "id,text,username,timestamp,like_count,reply_count,views",
                "access_token": access_token,
            })
            url = f"https://graph.threads.net/v1.0/keyword_search?{params}"
            req = urllib.request.Request(url, method="GET")

            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            items = result.get("data", [])
            log("INFO", f"Threads検索 '{term}': {len(items)}件取得")

            for i, item in enumerate(items):
                likes = item.get("like_count", 0)
                replies = item.get("reply_count", 0)
                views = item.get("views", 0)

                # バズ閾値フィルタ
                if likes < min_likes or replies < min_replies or views < min_views:
                    continue

                content = item.get("text", "")
                if _is_duplicate(content, existing_keys):
                    continue

                eng_rate = 0
                if views > 0:
                    eng_rate = round((likes + replies) / views * 100, 2)

                post = {
                    "id": _generate_id("threads", "search", len(posts) + i),
                    "platform": "threads",
                    "source": "threads_search",
                    "username": f"@{item.get('username', 'unknown')}",
                    "content": content,
                    "metrics": {
                        "likes": likes,
                        "retweets": 0,
                        "replies": replies,
                        "views": views,
                        "engagement_rate": eng_rate,
                    },
                    "collected_at": datetime.now(JST).isoformat(),
                    "analyzed": False,
                }
                posts.append(post)
                existing_keys.add(_content_key(content))

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            log("ERROR", f"Threads検索APIエラー '{term}' (HTTP {e.code}): {error_body[:200]}")
            continue
        except Exception as e:
            log("ERROR", f"Threads検索エラー '{term}': {e}")
            continue

    log("INFO", f"Threads検索: バズ閾値通過 合計{len(posts)}件")
    return posts, True


# =============================================
# ソース3: Manual Input
# =============================================
def collect_manual_input(existing_keys):
    """state/buzz-manual-input.json からユーザー手動追加の投稿を取り込み"""
    manual = load_json("state/buzz-manual-input.json")
    manual_posts = manual.get("posts", [])

    if not manual_posts:
        log("INFO", "手動入力: 投稿なし")
        return []

    posts = []
    updated = False

    for i, item in enumerate(manual_posts):
        if item.get("processed", False):
            continue

        content = item.get("content", "")
        if _is_duplicate(content, existing_keys):
            item["processed"] = True
            updated = True
            continue

        platform = item.get("platform", "unknown")
        likes = item.get("likes", 0)
        replies = item.get("replies", 0)
        views = item.get("views", 0)

        eng_rate = 0
        if views > 0:
            eng_rate = round((likes + replies) / views * 100, 2)

        post = {
            "id": _generate_id(platform, "manual", i),
            "platform": platform,
            "source": "manual",
            "username": item.get("username", "unknown"),
            "content": content,
            "metrics": {
                "likes": likes,
                "retweets": item.get("retweets", 0),
                "replies": replies,
                "views": views,
                "engagement_rate": eng_rate,
            },
            "url": item.get("url", ""),
            "collected_at": datetime.now(JST).isoformat(),
            "analyzed": False,
        }
        posts.append(post)
        existing_keys.add(_content_key(content))

        item["processed"] = True
        updated = True

    if updated:
        save_json("state/buzz-manual-input.json", manual)

    log("INFO", f"手動入力: {len(posts)}件取り込み")
    return posts


# =============================================
# ソース4: 自アカウントのベスト投稿
# =============================================
def collect_own_best(existing_keys):
    """winning-patterns.json の best_posts を取り込み"""
    patterns = load_json("state/winning-patterns.json")
    best_posts = patterns.get("best_posts", [])

    if not best_posts:
        log("INFO", "自アカウントベスト: データなし")
        return []

    posts = []
    for i, bp in enumerate(best_posts):
        content = bp.get("content_preview", "")
        if _is_duplicate(content, existing_keys):
            continue

        views = bp.get("views", 0)
        likes = bp.get("likes", 0)
        replies = bp.get("replies", 0)
        eng_rate = bp.get("eng_rate", 0)

        post = {
            "id": _generate_id("own", "best", i),
            "platform": "threads",
            "source": "own_best",
            "username": "self",
            "content": content,
            "metrics": {
                "likes": likes,
                "retweets": 0,
                "replies": replies,
                "views": views,
                "engagement_rate": eng_rate,
            },
            "original_id": bp.get("id", ""),
            "pattern": bp.get("pattern", ""),
            "collected_at": datetime.now(JST).isoformat(),
            "analyzed": False,
        }
        posts.append(post)
        existing_keys.add(_content_key(content))

    log("INFO", f"自アカウントベスト: {len(posts)}件取り込み")
    return posts


# =============================================
# メイン処理
# =============================================
def collect_all():
    """全ソースからバズ投稿を収集し buzz-collection.json に保存"""
    config = load_json("config/monitor-accounts.json")
    collection = load_json("state/buzz-collection.json")

    if "posts" not in collection:
        collection["posts"] = []
    if "sources" not in collection:
        collection["sources"] = {}

    existing_keys = _build_existing_keys(collection["posts"])
    now = datetime.now(JST)

    # --- ソース1: X検索 ---
    x_posts, x_enabled = collect_x_search(config, existing_keys)
    collection["sources"]["x_search"] = {
        "enabled": x_enabled,
        "last_count": len(x_posts),
    }

    # --- ソース2: Threads検索 ---
    threads_posts, threads_enabled = collect_threads_search(config, existing_keys)
    collection["sources"]["threads_search"] = {
        "enabled": threads_enabled,
        "last_count": len(threads_posts),
    }

    # --- ソース3: 手動入力（常に利用可能） ---
    manual_posts = collect_manual_input(existing_keys)
    collection["sources"]["manual"] = {
        "last_count": len(manual_posts),
    }

    # --- ソース4: 自アカウントベスト（常に利用可能） ---
    own_posts = collect_own_best(existing_keys)
    collection["sources"]["own_best"] = {
        "last_count": len(own_posts),
    }

    # 全投稿をマージ
    new_posts = x_posts + threads_posts + manual_posts + own_posts
    collection["posts"] = new_posts + collection["posts"]

    # 最新MAX_POSTS件に制限（古いものから削除）
    if len(collection["posts"]) > MAX_POSTS:
        collection["posts"] = collection["posts"][:MAX_POSTS]

    collection["last_collected"] = now.isoformat()

    save_json("state/buzz-collection.json", collection)

    total_new = len(new_posts)
    total_stored = len(collection["posts"])
    log("INFO", f"収集完了: 新規{total_new}件, 保存済み合計{total_stored}件")
    log("INFO", f"  X検索: {len(x_posts)}件, Threads検索: {len(threads_posts)}件, "
                 f"手動: {len(manual_posts)}件, 自ベスト: {len(own_posts)}件")


if __name__ == "__main__":
    load_env()
    log("INFO", "バズ投稿コレクター開始")
    try:
        collect_all()
    except Exception as e:
        log("ERROR", f"バズ投稿コレクターエラー: {e}")
    log("INFO", "バズ投稿コレクター完了")
