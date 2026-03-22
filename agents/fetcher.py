#!/usr/bin/env python3
"""
エージェント5: フェッチャー
投稿24時間後にThreads API / X APIからメトリクスを取得
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env():
    env_file = os.path.join(PROJECT_DIR, "config", "api-keys.env")
    if not os.path.exists(env_file):
        print("[ERROR] config/api-keys.env が見つかりません")
        sys.exit(1)
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()


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


def update_agent_status(agent, status_str):
    data = load_json("state/system-status.json")
    data["agents"][agent]["status"] = status_str
    data["agents"][agent]["last_run"] = datetime.now(JST).isoformat()
    if status_str == "error":
        data["agents"][agent]["error_count"] = data["agents"][agent].get("error_count", 0) + 1
    save_json("state/system-status.json", data)


# =============================================
# Threads API メトリクス取得
# =============================================
def fetch_threads_metrics(post_id, access_token):
    """Threads APIから投稿メトリクスを取得"""
    url = (
        f"https://graph.threads.net/v1.0/{post_id}"
        f"?fields=views,likes,replies,reposts,quotes"
        f"&access_token={access_token}"
    )
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return {
            "views": result.get("views", 0),
            "likes": result.get("likes", 0),
            "replies": result.get("replies", 0),
            "reposts": result.get("reposts", 0),
            "quotes": result.get("quotes", 0),
        }
    except Exception as e:
        log("ERROR", f"Threadsメトリクス取得失敗 (post_id={post_id}): {e}")
        return None


# =============================================
# X API メトリクス取得
# =============================================
def fetch_x_metrics(tweet_id):
    """X API v2 からツイートメトリクスを取得"""
    bearer = os.environ.get("X_BEARER_TOKEN", "")
    if not bearer:
        log("ERROR", "X_BEARER_TOKEN が未設定")
        return None

    url = (
        f"https://api.twitter.com/2/tweets/{tweet_id}"
        f"?tweet.fields=public_metrics"
    )
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {bearer}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        metrics = result.get("data", {}).get("public_metrics", {})
        return {
            "views": metrics.get("impression_count", 0),
            "likes": metrics.get("like_count", 0),
            "replies": metrics.get("reply_count", 0),
            "reposts": metrics.get("retweet_count", 0),
            "quotes": metrics.get("quote_count", 0),
        }
    except Exception as e:
        log("ERROR", f"Xメトリクス取得失敗 (tweet_id={tweet_id}): {e}")
        return None


# =============================================
# メイン処理
# =============================================
def fetch_all():
    """投稿24時間以上経過した投稿のメトリクスを取得"""
    history = load_json("state/post-history.json")
    metrics_data = load_json("state/metrics.json")

    if "posts" not in metrics_data:
        metrics_data["posts"] = []

    access_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    now = datetime.now(JST)
    fetched_count = 0
    already_fetched_ids = {p.get("post_id") for p in metrics_data.get("posts", [])}

    for post in history.get("posts", []):
        post_id = post.get("platform_post_id")
        if not post_id or post_id in already_fetched_ids:
            continue

        posted_at = post.get("posted_at", "")
        if not posted_at:
            continue

        posted_time = datetime.fromisoformat(posted_at)
        if posted_time.tzinfo is None:
            posted_time = posted_time.replace(tzinfo=JST)

        elapsed_hours = (now - posted_time).total_seconds() / 3600
        if elapsed_hours < 24:
            continue

        platform = post.get("platform", "threads")
        metrics = None

        if platform == "threads" and access_token:
            metrics = fetch_threads_metrics(post_id, access_token)
        elif platform == "x":
            metrics = fetch_x_metrics(post_id)

        if metrics:
            entry = {
                "post_id": post_id,
                "internal_id": post.get("id", ""),
                "platform": platform,
                "fetched_at": now.isoformat(),
                "posted_at": posted_at,
                "category": post.get("category", ""),
                "pattern_name": post.get("pattern_name", ""),
                "is_affiliate": post.get("is_affiliate", False),
                **metrics,
            }
            # エンゲージメント率計算
            views = metrics.get("views", 0)
            if views > 0:
                engagement = metrics["likes"] + metrics["replies"] + metrics["reposts"]
                entry["engagement_rate"] = round(engagement / views * 100, 2)
            else:
                entry["engagement_rate"] = 0

            metrics_data["posts"].append(entry)
            fetched_count += 1
            log("INFO", f"メトリクス取得: {post_id} (views={metrics['views']}, likes={metrics['likes']})")

    # サマリー更新
    all_posts = metrics_data.get("posts", [])
    if all_posts:
        metrics_data["summary"] = {
            "total_posts": len(all_posts),
            "total_views": sum(p.get("views", 0) for p in all_posts),
            "total_likes": sum(p.get("likes", 0) for p in all_posts),
            "total_replies": sum(p.get("replies", 0) for p in all_posts),
            "total_reposts": sum(p.get("reposts", 0) for p in all_posts),
            "avg_engagement_rate": round(
                sum(p.get("engagement_rate", 0) for p in all_posts) / len(all_posts), 2
            ),
        }

    metrics_data["last_updated"] = now.isoformat()
    save_json("state/metrics.json", metrics_data)
    log("INFO", f"メトリクス取得完了: {fetched_count}件")


if __name__ == "__main__":
    load_env()
    log("INFO", "フェッチャーエージェント開始")
    update_agent_status("fetcher", "running")
    try:
        fetch_all()
        update_agent_status("fetcher", "idle")
    except Exception as e:
        log("ERROR", f"フェッチャーエラー: {e}")
        update_agent_status("fetcher", "error")
    log("INFO", "フェッチャーエージェント完了")
