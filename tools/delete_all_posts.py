#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Threads 全投稿削除ツール
アカウントリニューアル（よぞら. → ルナ姉）時の一括削除用

【使い方】
  python tools/delete_all_posts.py

【動作】
  ① 全投稿IDを取得して件数を表示
  ② 「DELETE」と入力で削除実行
  ③ それ以外は中止
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_credentials():
    access_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    user_id      = os.environ.get("THREADS_USER_ID", "")

    if not access_token or not user_id:
        env_path = os.path.join(PROJECT_DIR, "config", "api-keys.env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k == "THREADS_ACCESS_TOKEN" and not access_token:
                        access_token = v
                    elif k == "THREADS_USER_ID" and not user_id:
                        user_id = v

    if not access_token or not user_id:
        print("ERROR: THREADS_ACCESS_TOKEN / THREADS_USER_ID が未設定")
        sys.exit(1)

    return access_token, user_id


def fetch_all_posts(user_id: str, access_token: str) -> list[dict]:
    """全投稿をページネーションで取得"""
    posts = []
    url = (
        f"https://graph.threads.net/v1.0/{user_id}/threads"
        f"?fields=id,text,timestamp&limit=100&access_token={access_token}"
    )
    page = 0

    while url:
        page += 1
        print(f"  取得中... {page}ページ目（累計{len(posts)}件）", end="\r")
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"\nERROR: 投稿取得失敗: {e}")
            break

        items = data.get("data", [])
        posts.extend(items)
        url = data.get("paging", {}).get("next")
        time.sleep(0.3)

    print()
    return posts


def delete_post(post_id: str, access_token: str) -> bool:
    """1件削除。成功でTrue"""
    url = f"https://graph.threads.net/v1.0/{post_id}?access_token={access_token}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("success", False)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        print(f"  [ERROR] {post_id}: HTTP {e.code} {body[:80]}")
        return False
    except Exception as e:
        print(f"  [ERROR] {post_id}: {e}")
        return False


def main():
    print("=" * 50)
    print("  Threads 全投稿削除ツール")
    print("  よぞら. → ルナ姉 アカウントリニューアル用")
    print("=" * 50)
    print()

    access_token, user_id = load_credentials()
    print(f"アカウント USER_ID: {user_id}")
    print()

    # ① 全投稿取得
    print("▼ 全投稿を取得しています...")
    posts = fetch_all_posts(user_id, access_token)

    if not posts:
        print("投稿が見つかりませんでした。終了します。")
        return

    print(f"✅ 取得完了: 全 {len(posts)} 件")
    print()

    # ② 直近5件をプレビュー表示
    print("── 直近5件のプレビュー ──")
    for p in posts[:5]:
        ts  = p.get("timestamp", "")[:10]
        txt = p.get("text", "（テキストなし）")[:40].replace("\n", " ")
        print(f"  [{ts}] {txt}")
    if len(posts) > 5:
        print(f"  ... 他 {len(posts) - 5} 件")
    print()

    # ③ 削除確認
    print(f"⚠️  {len(posts)} 件を全て削除します。この操作は取り消せません。")
    print()
    confirm = input('削除を実行する場合は「DELETE」と入力してください: ').strip()

    if confirm != "DELETE":
        print("中止しました。")
        return

    # ④ 一括削除
    print()
    print("▼ 削除を開始します...")
    success = 0
    failed  = 0

    for i, post in enumerate(posts, 1):
        post_id = post["id"]
        ts      = post.get("timestamp", "")[:10]
        txt     = post.get("text", "")[:30].replace("\n", " ")

        ok = delete_post(post_id, access_token)
        if ok:
            success += 1
            print(f"  ✅ [{i:3}/{len(posts)}] {ts} {txt}")
        else:
            failed += 1
            print(f"  ❌ [{i:3}/{len(posts)}] {ts} {txt} → 削除失敗")

        time.sleep(0.5)   # レート制限対策

    print()
    print("=" * 50)
    print(f"  削除完了: 成功 {success} 件 / 失敗 {failed} 件")
    print("=" * 50)

    if failed > 0:
        print(f"\n⚠️ {failed} 件削除できませんでした。再度スクリプトを実行してください。")


if __name__ == "__main__":
    main()
