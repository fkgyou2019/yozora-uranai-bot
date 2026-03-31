#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
投稿成長トラッカー: 毎時スナップショット
投稿後72時間以内の全投稿のメトリクスを1時間ごとに記録し
伸び率・ピーク時間・パターン別成長曲線を蓄積する。

state/post-growth-tracker.json に保存。
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TRACKER_FILE = "state/post-growth-tracker.json"
TRACK_HOURS = 72  # 投稿後何時間まで追跡するか


def load_env():
    env_path = os.path.join(PROJECT_DIR, "config", "api-keys.env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() and v.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()


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


def fetch_metrics(post_id, access_token):
    """Threads APIからメトリクスを取得（24h制限なし）"""
    url = (
        f"https://graph.threads.net/v1.0/{post_id}/insights"
        f"?metric=views,likes,replies,reposts,quotes"
        f"&access_token={access_token}"
    )
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {item["name"]: item.get("values", [{}])[0].get("value", 0)
                for item in data.get("data", [])}
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return None  # 削除済み
        raise
    except Exception:
        return None


def count_auto_replies(post_id, replied_state):
    """replied-comments.jsonから自動返信数をカウント"""
    # replied_stateはreplied-comments.jsonの内容
    # recent_replies等の構造に応じて調整
    count = 0
    for entry in replied_state.get("recent_replies", []):
        if entry.get("post_id") == post_id:
            count += 1
    return count


def main():
    load_env()
    access_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not access_token:
        print("ERROR: THREADS_ACCESS_TOKEN 未設定")
        sys.exit(1)

    now = datetime.now(JST)
    history = load_json("state/post-history.json")
    tracker = load_json(TRACKER_FILE)
    if not tracker:
        tracker = {"snapshots": {}, "last_updated": None}

    replied_state = load_json("state/replied-comments.json")

    posts = history.get("posts", [])
    tracked = 0
    skipped = 0

    for post in posts:
        pid = post.get("id", "")
        platform_id = post.get("platform_post_id", "")
        posted_at_str = post.get("posted_at", "")

        if not platform_id or not posted_at_str:
            continue

        try:
            posted_at = datetime.fromisoformat(posted_at_str)
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=JST)
        except Exception:
            continue

        elapsed_hours = (now - posted_at).total_seconds() / 3600
        if elapsed_hours > TRACK_HOURS:
            continue  # 72時間超は追跡終了

        # このpostのトラッカーエントリを初期化
        if pid not in tracker["snapshots"]:
            content = post.get("content", "")
            first_line = content.split("\n")[0] if content else ""
            tracker["snapshots"][pid] = {
                "post_id": pid,
                "platform_post_id": platform_id,
                "posted_at": posted_at_str,
                "pattern_name": post.get("pattern_name", ""),
                "slot_hour": posted_at.astimezone(JST).hour,
                "day_of_week": posted_at.astimezone(JST).weekday(),  # 0=月曜
                "day_name": ["月", "火", "水", "木", "金", "土", "日"][posted_at.astimezone(JST).weekday()],
                "first_line": first_line[:40],
                "content_preview": content[:60],
                "hourly": [],
                "completed": False,
            }

        entry = tracker["snapshots"][pid]
        if entry.get("completed"):
            continue

        # 現在の経過時間（小数点以下切り捨て）
        elapsed_int = int(elapsed_hours)

        # 同じ時間帯のスナップショットが既にあればスキップ
        existing_hours = {s["h"] for s in entry.get("hourly", [])}
        if elapsed_int in existing_hours:
            skipped += 1
            continue

        # メトリクス取得
        metrics = fetch_metrics(platform_id, access_token)
        if metrics is None:
            print(f"  [SKIP] {pid}: API取得失敗（削除済みの可能性）")
            entry["completed"] = True
            continue

        v = metrics.get("views", 0)
        l = metrics.get("likes", 0)
        r = metrics.get("replies", 0)
        rt = metrics.get("reposts", 0)
        q = metrics.get("quotes", 0)

        # 前スナップショットとの差分（伸び率）
        hourly_list = entry.get("hourly", [])
        if hourly_list:
            prev = hourly_list[-1]
            prev_v = prev.get("views", 0)
            prev_h = prev.get("h", 0)
            h_diff = max(elapsed_int - prev_h, 1)
            velocity_views = round((v - prev_v) / h_diff, 1)
        else:
            velocity_views = round(v / max(elapsed_int, 1), 1)

        auto_replies = count_auto_replies(platform_id, replied_state)
        er = round((l + r + rt + q) / v * 100, 2) if v > 0 else 0

        snapshot = {
            "h": elapsed_int,
            "ts": now.isoformat(),
            "views": v,
            "likes": l,
            "replies": r,
            "reposts": rt,
            "auto_replies": auto_replies,
            "er": er,
            "velocity_views_per_hour": velocity_views,
        }
        entry["hourly"].append(snapshot)

        # ピーク速度を更新
        all_velocities = [s["velocity_views_per_hour"] for s in entry["hourly"]]
        peak_idx = all_velocities.index(max(all_velocities))
        entry["peak_velocity"] = max(all_velocities)
        entry["peak_velocity_hour"] = entry["hourly"][peak_idx]["h"]

        tracked += 1
        print(f"  [{elapsed_int}h] {entry['first_line'][:20]} | 閲:{v} いい:{l} リプ:{r} 速度:{velocity_views}/h")

        # 72時間到達で完了マーク
        if elapsed_hours >= TRACK_HOURS:
            entry["completed"] = True

    tracker["last_updated"] = now.isoformat()
    save_json(TRACKER_FILE, tracker)

    # CSV出力（Googleスプレッドシートにインポート可能）
    export_csv(tracker, now)

    print(f"\n成長トラッカー更新: {tracked}件スナップショット取得 / {skipped}件スキップ")
    print(f"追跡中: {sum(1 for e in tracker['snapshots'].values() if not e.get('completed'))}件")


def export_csv(tracker, now):
    """スプレッドシート用CSVを出力"""
    import csv
    import io

    rows = []
    for pid, entry in tracker["snapshots"].items():
        for snap in entry.get("hourly", []):
            rows.append({
                "post_id": pid,
                "pattern_name": entry.get("pattern_name", ""),
                "posted_at": entry.get("posted_at", ""),
                "slot_hour": entry.get("slot_hour", ""),
                "day_name": entry.get("day_name", ""),
                "first_line": entry.get("first_line", ""),
                "elapsed_hours": snap.get("h", ""),
                "views": snap.get("views", 0),
                "likes": snap.get("likes", 0),
                "replies": snap.get("replies", 0),
                "reposts": snap.get("reposts", 0),
                "auto_replies": snap.get("auto_replies", 0),
                "er_pct": snap.get("er", 0),
                "velocity_views_per_hour": snap.get("velocity_views_per_hour", 0),
                "snapshot_at": snap.get("ts", ""),
            })

    if not rows:
        return

    csv_dir = os.path.join(PROJECT_DIR, "state", "reports")
    os.makedirs(csv_dir, exist_ok=True)
    csv_path = os.path.join(csv_dir, f"growth-{now.strftime('%Y-%m')}.csv")

    # 既存を読んで重複除去してから書き直す
    existing_keys = set()
    existing_rows = []
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (row.get("post_id", ""), row.get("elapsed_hours", ""))
                if key not in existing_keys:
                    existing_keys.add(key)
                    existing_rows.append(row)

    fieldnames = ["post_id", "pattern_name", "posted_at", "slot_hour", "day_name",
                  "first_line", "elapsed_hours", "views", "likes", "replies", "reposts",
                  "auto_replies", "er_pct", "velocity_views_per_hour", "snapshot_at"]

    new_rows = []
    for row in rows:
        key = (str(row["post_id"]), str(row["elapsed_hours"]))
        if key not in existing_keys:
            existing_keys.add(key)
            new_rows.append(row)

    all_rows = existing_rows + new_rows
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    if new_rows:
        print(f"CSV更新: {csv_path} (+{len(new_rows)}行)")


if __name__ == "__main__":
    main()
