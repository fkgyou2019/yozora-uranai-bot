#!/usr/bin/env python3
"""
アカウントマネージャー: 複数アカウントの管理・選択ロジック
config/accounts.json を読み書きし、アカウント別のステータスを管理する。
"""

import json
import os
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def get_accounts(platform="x"):
    """指定プラットフォームの有効なアカウント一覧を返す"""
    accounts_data = load_json("config/accounts.json")
    key = "x_accounts" if platform == "x" else "threads_accounts"
    return [a for a in accounts_data.get(key, []) if a.get("enabled", True)]


def get_account(account_id):
    """IDでアカウントを取得（プラットフォーム問わず）"""
    accounts_data = load_json("config/accounts.json")
    for key in ["x_accounts", "threads_accounts"]:
        for acc in accounts_data.get(key, []):
            if acc["id"] == account_id:
                return acc
    return None


def get_account_status(account_id):
    """アカウント別のステータスを取得"""
    status = load_json("state/system-status.json")
    account_statuses = status.get("account_statuses", {})
    today = datetime.now(JST).strftime("%Y-%m-%d")
    acc_status = account_statuses.get(account_id, {})
    # 日付リセット
    if acc_status.get("daily_post_date") != today:
        acc_status["daily_post_count"] = 0
        acc_status["daily_post_date"] = today
    return acc_status


def update_account_status(account_id, updates):
    """アカウント別ステータスを更新"""
    status = load_json("state/system-status.json")
    if "account_statuses" not in status:
        status["account_statuses"] = {}
    if account_id not in status["account_statuses"]:
        status["account_statuses"][account_id] = {}
    status["account_statuses"][account_id].update(updates)
    save_json("state/system-status.json", status)


def check_account_daily_limit(account_id, account_config):
    """アカウント別の日次投稿上限チェック"""
    acc_status = get_account_status(account_id)
    max_posts = account_config.get("limits", {}).get("max_posts_per_day", 5)
    current = acc_status.get("daily_post_count", 0)
    return current < max_posts


def check_account_interval(account_id, account_config):
    """アカウント別の最低投稿間隔チェック"""
    acc_status = get_account_status(account_id)
    min_interval = account_config.get("limits", {}).get("min_interval_seconds", 1200)
    last_posted = acc_status.get("last_posted_at")
    if not last_posted:
        return True
    last_time = datetime.fromisoformat(last_posted)
    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=JST)
    elapsed = (datetime.now(JST) - last_time).total_seconds()
    return elapsed >= min_interval


def select_next_account(platform, exclude_ids=None):
    """
    ラウンドロビン＋クールダウンで次に投稿すべきアカウントを選択。
    - 日次上限に達していないアカウント
    - 最低投稿間隔を満たしているアカウント
    - 最も投稿数が少ないアカウントを優先
    """
    accounts = get_accounts(platform)
    if not accounts:
        return None

    exclude = set(exclude_ids or [])
    candidates = []

    for acc in accounts:
        aid = acc["id"]
        if aid in exclude:
            continue
        if not check_account_daily_limit(aid, acc):
            continue
        if not check_account_interval(aid, acc):
            continue
        acc_status = get_account_status(aid)
        candidates.append((acc, acc_status.get("daily_post_count", 0)))

    if not candidates:
        return None

    # 投稿数が最も少ないアカウントを選択（均等分散）
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]


def record_post(account_id):
    """投稿成功時にアカウントステータスを更新"""
    now = datetime.now(JST)
    acc_status = get_account_status(account_id)
    update_account_status(account_id, {
        "daily_post_count": acc_status.get("daily_post_count", 0) + 1,
        "daily_post_date": now.strftime("%Y-%m-%d"),
        "last_posted_at": now.isoformat(),
        "consecutive_errors": 0,
    })


def record_error(account_id, error_msg):
    """投稿失敗時にアカウントステータスを更新"""
    acc_status = get_account_status(account_id)
    update_account_status(account_id, {
        "consecutive_errors": acc_status.get("consecutive_errors", 0) + 1,
        "last_error": error_msg,
        "last_error_at": datetime.now(JST).isoformat(),
    })


def get_credentials(account, platform=None):
    """
    アカウント設定から認証情報を解決する。
    環境変数参照（ENV:XXX）はそのまま環境変数から取得。
    直書きの値はそのまま返す。
    """
    auth = account.get("auth", {})
    resolved = {}
    for key, value in auth.items():
        if isinstance(value, str) and value.startswith("ENV:"):
            env_key = value[4:]
            resolved[key] = os.environ.get(env_key, "")
        else:
            resolved[key] = value
    return resolved
