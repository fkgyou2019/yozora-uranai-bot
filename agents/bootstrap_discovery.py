#!/usr/bin/env python3
"""
Phase2: ハッシュタグ巡回で競合アカウントを自動発掘
Threads検索ページからハッシュタグ経由で占い系アカウントを収集し、
フォロワー数・投稿頻度でフィルタして monitor-accounts.json と competitor_seed.json に追加

使い方:
  python agents/bootstrap_discovery.py             # 自動発掘（フィルタ付き）
  python agents/bootstrap_discovery.py --dry-run   # 変更を保存しない（確認用）
  python agents/bootstrap_discovery.py --min 500   # 最低フォロワー数を変更（デフォルト1000）
"""

import json
import os
import re
import sys
import time
import asyncio
import urllib.parse
from datetime import datetime, timezone, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 検索するハッシュタグ（カテゴリ推定のヒント付き）
SEARCH_QUERIES = [
    {"query": "占い", "hint_category": "general"},
    {"query": "タロット占い", "hint_category": "tarot"},
    {"query": "星座占い", "hint_category": "astrology"},
    {"query": "占星術", "hint_category": "astrology"},
    {"query": "スピリチュアル", "hint_category": "spiritual"},
    {"query": "AI占い", "hint_category": "ai_art_fortune"},
    {"query": "数秘術", "hint_category": "palm_numerology"},
    {"query": "恋愛占い", "hint_category": "love_specialized"},
    {"query": "手相占い", "hint_category": "palm_numerology"},
    {"query": "今日の運勢", "hint_category": "astrology"},
    {"query": "開運", "hint_category": "spiritual"},
    {"query": "霊視 占い", "hint_category": "spiritual"},
]

DEFAULT_MIN_FOLLOWERS = 1000
TARGET_NEW_ACCOUNTS = 22
INTER_SEARCH_DELAY_SEC = 3
PAGE_TIMEOUT_MS = 30000


def load_json(path, default=None):
    full = os.path.join(PROJECT_DIR, path)
    if os.path.exists(full):
        with open(full, "r", encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else {}


def save_json(path, data):
    full = os.path.join(PROJECT_DIR, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def log(level, msg):
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {msg}")


def _parse_follower_count(body_text):
    """フォロワー数をテキストからパース"""
    m = re.search(r"フォロワー\s*([\d.]+)\s*万\s*人", body_text)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r"フォロワー\s*([\d,]+)\s*人", body_text)
    if m:
        return int(m.group(1).replace(",", ""))
    m = re.search(r"([\d.]+)\s*([KMkm])\s*followers", body_text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        unit = m.group(2).upper()
        return int(val * (1000 if unit == "K" else 1000000))
    m = re.search(r"([\d,]+)\s*followers", body_text, re.IGNORECASE)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def _guess_category(handle, bio_text, search_hint):
    """プロフィールテキストからカテゴリを推定"""
    text = (bio_text or "").lower()
    if any(kw in text for kw in ["タロット", "tarot"]):
        return "tarot"
    if any(kw in text for kw in ["占星術", "星読み", "星座", "horoscope", "astrology"]):
        return "astrology"
    if any(kw in text for kw in ["スピリチュアル", "霊視", "霊感", "チャネリング"]):
        return "spiritual"
    if any(kw in text for kw in ["ai占い", "ai×占い", "ai ", "ＡＩ"]):
        return "ai_art_fortune"
    if any(kw in text for kw in ["恋愛", "復縁", "片想い", "結婚"]):
        return "love_specialized"
    if any(kw in text for kw in ["手相", "数秘", "姓名", "九星", "マヤ暦", "四柱推命", "算命"]):
        return "palm_numerology"
    if any(kw in text for kw in ["ゲッターズ", "公式", "編集部", "メディア"]):
        return "major_brand"
    # 検索クエリのヒントにフォールバック
    if search_hint and search_hint != "general":
        return search_hint
    return "general"


async def _search_threads(page, query):
    """Threads検索ページで投稿を検索し、出現したアカウントハンドルを返す"""
    encoded = urllib.parse.quote(query)
    url = f"https://www.threads.com/search?q={encoded}&serp_type=default"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        await page.wait_for_timeout(4000)

        # プロフィールリンクからハンドルを抽出
        handles = await page.evaluate("""
            () => {
                const links = document.querySelectorAll('a[href^="/@"]');
                const seen = new Set();
                const results = [];
                for (const link of links) {
                    const href = link.getAttribute('href') || '';
                    const match = href.match(/^\\/@([^/]+)$/);
                    if (match && !seen.has(match[1])) {
                        seen.add(match[1]);
                        results.push(match[1]);
                    }
                }
                return results;
            }
        """)
        return handles
    except Exception as e:
        log("WARN", f"検索 '{query}' 失敗: {e}")
        return []


async def _get_account_info(page, handle):
    """アカウントのプロフィールページを訪問し、フォロワー数とbioを取得"""
    url = f"https://www.threads.com/@{handle}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        await page.wait_for_timeout(3000)

        body_text = await page.locator("body").first.inner_text(timeout=5000)
        follower_count = _parse_follower_count(body_text)

        # bio はフォロワー行の前のテキスト（プロフィール説明）
        # 簡易: body全体の最初の500文字をbioとして扱う
        bio = body_text[:500] if body_text else ""

        # 投稿数の推定（data-pressable-container の数）
        post_count = await page.locator("[data-pressable-container]").count()

        return {
            "handle": handle,
            "follower_count": follower_count,
            "bio": bio,
            "visible_post_count": post_count,
        }
    except Exception as e:
        log("WARN", f"@{handle} プロフィール取得失敗: {e}")
        return None


async def discover_accounts(min_followers, dry_run=False):
    """メインの発掘ロジック"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("ERROR", "playwright 未インストール")
        return

    # 既存アカウントの読み込み
    monitor = load_json("config/monitor-accounts.json")
    existing_handles = {a.get("username", "") for a in monitor.get("threads_accounts", [])}
    log("INFO", f"既存アカウント: {len(existing_handles)}件")

    # 候補アカウントの収集
    candidates = {}  # handle -> {search_hint, ...}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 3000},
            locale="ja-JP",
        )
        page = await context.new_page()

        # Phase 1: ハッシュタグ検索で候補ハンドルを収集
        log("INFO", f"=== Phase 1: ハッシュタグ検索 ({len(SEARCH_QUERIES)}クエリ) ===")
        for sq in SEARCH_QUERIES:
            query = sq["query"]
            hint = sq["hint_category"]
            log("INFO", f"検索中: '{query}'")
            handles = await _search_threads(page, query)
            for h in handles:
                if h not in existing_handles and h not in candidates:
                    candidates[h] = {"search_hint": hint, "found_in": [query]}
                elif h in candidates:
                    candidates[h]["found_in"].append(query)
            log("INFO", f"  → {len(handles)}件検出 (新規候補累計: {len(candidates)}件)")
            await asyncio.sleep(INTER_SEARCH_DELAY_SEC)

        log("INFO", f"Phase 1 完了: 候補 {len(candidates)}件")

        # Phase 2: 各候補のプロフィールを確認しフィルタ
        log("INFO", f"=== Phase 2: プロフィール確認 & フィルタ (最低{min_followers}フォロワー) ===")
        qualified = []
        checked = 0
        for handle, info in candidates.items():
            if len(qualified) >= TARGET_NEW_ACCOUNTS:
                log("INFO", f"目標{TARGET_NEW_ACCOUNTS}件に到達。残りスキップ")
                break

            checked += 1
            log("INFO", f"[{checked}/{len(candidates)}] @{handle} 確認中...")
            account_info = await _get_account_info(page, handle)
            if not account_info:
                continue

            fc = account_info.get("follower_count") or 0
            post_count = account_info.get("visible_post_count", 0)
            bio = account_info.get("bio", "")

            # フィルタ: フォロワー数
            if fc < min_followers:
                log("INFO", f"  → SKIP: follower={fc} (最低{min_followers})")
                continue

            # フィルタ: 投稿が少なすぎる（3件未満）
            if post_count < 3:
                log("INFO", f"  → SKIP: posts={post_count} (最低3)")
                continue

            category = _guess_category(handle, bio, info["search_hint"])
            log("INFO", f"  → PASS: follower={fc} posts={post_count} category={category}")

            qualified.append({
                "handle": handle,
                "follower_count": fc,
                "category": category,
                "bio_preview": bio[:150].replace("\n", " "),
                "found_in_queries": info["found_in"],
                "visible_post_count": post_count,
            })

            await asyncio.sleep(INTER_SEARCH_DELAY_SEC)

        await browser.close()

    log("INFO", f"Phase 2 完了: 合格 {len(qualified)}件 / 確認 {checked}件")

    if not qualified:
        log("WARN", "発掘アカウントが0件。min_followers を下げるか、検索クエリを追加してください")
        return

    if dry_run:
        log("INFO", "=== DRY RUN: 以下のアカウントが発掘されました（保存なし）===")
        for q in qualified:
            print(f"  @{q['handle']} follower={q['follower_count']} cat={q['category']} queries={q['found_in_queries']}")
        return

    # Phase 3: monitor-accounts.json と competitor_seed.json に追記
    log("INFO", "=== Phase 3: ファイル更新 ===")

    # monitor-accounts.json 更新
    for q in qualified:
        monitor["threads_accounts"].append({
            "username": q["handle"],
            "category": q["category"],
            "note": f"Phase2自動発掘: follower={q['follower_count']}, queries={','.join(q['found_in_queries'][:2])}",
        })
    save_json("config/monitor-accounts.json", monitor)
    log("INFO", f"monitor-accounts.json 更新: {len(monitor['threads_accounts'])}件")

    # competitor_seed.json 更新
    seed = load_json("research/structured/competitor_seed.json")
    next_id = len(seed.get("accounts", [])) + 1
    for q in qualified:
        seed["accounts"].append({
            "id": f"seed_{next_id:03d}",
            "handle": q["handle"],
            "display_name": "",
            "url": f"https://www.threads.com/@{q['handle']}",
            "category": q["category"],
            "traits": f"Phase2自動発掘 ({','.join(q['found_in_queries'][:2])})",
            "follower_count": q["follower_count"],
            "post_frequency": None,
            "verified": True,
            "notes": q["bio_preview"][:100],
        })
        next_id += 1

    seed["total_seed_count"] = len(seed["accounts"])
    seed["last_updated"] = datetime.now(JST).strftime("%Y-%m-%d")

    # カテゴリ分布を再計算
    dist = {}
    for acc in seed["accounts"]:
        cat = acc.get("category", "other")
        dist[cat] = dist.get(cat, 0) + 1
    seed["category_distribution"] = dist

    save_json("research/structured/competitor_seed.json", seed)
    log("INFO", f"competitor_seed.json 更新: {seed['total_seed_count']}件")

    # サマリ出力
    print("\n=== 発掘結果サマリ ===")
    print(f"新規追加: {len(qualified)}件")
    print(f"合計監視対象: {len(monitor['threads_accounts'])}件")
    print(f"\nカテゴリ分布:")
    for cat, count in sorted(dist.items()):
        print(f"  {cat}: {count}")
    print(f"\n新規アカウント:")
    for q in qualified:
        print(f"  @{q['handle']} (follower={q['follower_count']}, {q['category']})")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Phase2: 占いアカウント自動発掘")
    parser.add_argument("--dry-run", action="store_true", help="変更を保存しない")
    parser.add_argument("--min", type=int, default=DEFAULT_MIN_FOLLOWERS, help=f"最低フォロワー数 (default: {DEFAULT_MIN_FOLLOWERS})")
    args = parser.parse_args()

    log("INFO", f"Phase2 自動発掘開始 (min_followers={args.min}, dry_run={args.dry_run})")
    try:
        asyncio.run(discover_accounts(min_followers=args.min, dry_run=args.dry_run))
    except Exception as e:
        log("ERROR", f"実行エラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    log("INFO", "Phase2 自動発掘完了")
