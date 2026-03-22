#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Threadsアクセストークン自動リフレッシュ
60日間有効のトークンを期限切れ前に更新し、GitHub Secretsを更新する。
毎週実行（期限30日前から更新試行）。
"""

import json
import os
import sys
import subprocess
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


def refresh_token(current_token):
    """Threads APIでトークンをリフレッシュ"""
    url = (
        f"https://graph.threads.net/refresh_access_token"
        f"?grant_type=th_refresh_token"
        f"&access_token={current_token}"
    )
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("access_token"), data.get("expires_in", 0)


def update_github_secret(secret_name, secret_value):
    """gh CLIでGitHub Secretsを更新"""
    result = subprocess.run(
        ["gh", "secret", "set", secret_name, "--body", secret_value],
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode == 0


def main():
    current_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not current_token:
        print("ERROR: THREADS_ACCESS_TOKEN が未設定")
        sys.exit(1)

    # トークン状態を確認
    token_state = load_json("state/token-state.json")
    last_refresh = token_state.get("last_refresh", "")
    days_since_refresh = 999

    if last_refresh:
        try:
            last_dt = datetime.fromisoformat(last_refresh)
            days_since_refresh = (datetime.now(JST) - last_dt).days
        except Exception:
            pass

    print(f"前回リフレッシュからの経過日数: {days_since_refresh}日")

    # 30日未満なら不要
    if days_since_refresh < 30:
        print(f"リフレッシュ不要（次回まであと{30 - days_since_refresh}日）")
        return

    # リフレッシュ実行
    print("トークンリフレッシュを実行...")
    try:
        new_token, expires_in = refresh_token(current_token)
        expires_days = expires_in // 86400
        print(f"新トークン取得成功: {expires_days}日間有効")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"リフレッシュ失敗 HTTP {e.code}: {body}")
        sys.exit(1)
    except Exception as e:
        print(f"リフレッシュ失敗: {e}")
        sys.exit(1)

    # GitHub Secretsを更新
    print("GitHub Secretsを更新...")
    if update_github_secret("THREADS_ACCESS_TOKEN", new_token):
        print("GitHub Secrets更新成功")
    else:
        print("WARNING: GitHub Secrets更新失敗（ghコマンド不可 → 手動更新が必要）")

    # 状態を保存
    token_state["last_refresh"] = datetime.now(JST).isoformat()
    token_state["expires_in"] = expires_in
    token_state["expires_at"] = (datetime.now(JST) + timedelta(seconds=expires_in)).isoformat()
    token_state["refresh_count"] = token_state.get("refresh_count", 0) + 1
    save_json("state/token-state.json", token_state)

    print(f"次回期限: {token_state['expires_at']}")


if __name__ == "__main__":
    main()
