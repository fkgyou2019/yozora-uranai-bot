#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
メトリクス収集: 投稿24h以上経過した投稿のviews/likes/replies/repostsを取得
state/performance-data.json に蓄積
"""

import json
import os
import sys
import urllib.request
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


def fetch_insights(post_id, access_token):
    """1投稿のインサイトを取得"""
    url = (
        f"https://graph.threads.net/v1.0/{post_id}/insights"
        f"?metric=views,likes,replies,reposts,quotes"
        f"&access_token={access_token}"
    )
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        metrics = {}
        for m in data.get("data", []):
            metrics[m["name"]] = m.get("values", [{}])[0].get("value", 0)
        return metrics
    except Exception as e:
        print(f"  [WARN] {post_id}: {e}")
        return None


def main():
    access_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not access_token:
        # api-keys.envから読む
        env_path = os.path.join(PROJECT_DIR, "config", "api-keys.env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("THREADS_ACCESS_TOKEN="):
                        access_token = line.split("=", 1)[1].strip()

    if not access_token:
        print("ERROR: THREADS_ACCESS_TOKEN が未設定")
        sys.exit(1)

    history = load_json("state/post-history.json")
    perf = load_json("state/performance-data.json")
    if not perf:
        perf = {"posts": [], "last_updated": None}

    # 既に収集済みのpost IDリスト
    collected_ids = {p["id"] for p in perf.get("posts", [])}

    now = datetime.now(JST)
    posts = history.get("posts", [])
    new_count = 0

    for post in posts:
        pid = post.get("id", "")
        platform_id = post.get("platform_post_id", "")

        # 既に収集済みならスキップ
        if pid in collected_ids:
            continue

        # platform_post_idがなければスキップ
        if not platform_id:
            continue

        # 投稿から24h以上経過しているかチェック
        posted_at = post.get("posted_at", "")
        if not posted_at:
            continue
        try:
            post_time = datetime.fromisoformat(posted_at)
            if post_time.tzinfo is None:
                post_time = post_time.replace(tzinfo=JST)
            elapsed_hours = (now - post_time).total_seconds() / 3600
            if elapsed_hours < 24:
                continue
        except Exception:
            continue

        # メトリクス取得
        metrics = fetch_insights(platform_id, access_token)
        if metrics is None:
            continue

        views = metrics.get("views", 0)
        likes = metrics.get("likes", 0)
        replies = metrics.get("replies", 0)
        reposts = metrics.get("reposts", 0)

        eng_rate = ((likes + replies + reposts) / views * 100) if views > 0 else 0

        # テキスト分析
        content = post.get("content", "")
        lines = content.split("\n")
        first_line = lines[0] if lines else ""

        perf_entry = {
            "id": pid,
            "platform_post_id": platform_id,
            "posted_at": posted_at,
            "pattern_name": post.get("pattern_name", ""),
            "category": post.get("category", ""),
            "content_preview": content[:50],
            "first_line": first_line[:30],
            "char_count": len(content),
            "metrics": {
                "views": views,
                "likes": likes,
                "replies": replies,
                "reposts": reposts,
                "engagement_rate": round(eng_rate, 2),
            },
            "features": {
                "has_ranking": "🥇" in content or "第1位" in content or "1位" in content,
                "has_number_hook": any(c.isdigit() for c in first_line[:15]),
                "has_question": "？" in content,
                "has_cta_emoji": any(w in content for w in ["を置", "をコメント", "で受け取"]),
                "has_fear_hook": any(w in first_line for w in ["失う", "注意", "危険", "逆転", "急に"]),
                "hook_length": len(first_line),
            },
            "collected_at": now.isoformat(),
        }

        perf["posts"].append(perf_entry)
        collected_ids.add(pid)
        new_count += 1
        print(f"  ✅ {pid}: 👁{views} ❤{likes} 💬{replies} eng={eng_rate:.1f}%")

    perf["last_updated"] = now.isoformat()
    save_json("state/performance-data.json", perf)
    print(f"\nメトリクス収集完了: 新規{new_count}件 / 累計{len(perf['posts'])}件")


if __name__ == "__main__":
    main()
