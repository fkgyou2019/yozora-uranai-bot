#!/usr/bin/env python3
"""
アカウント時系列コレクター（案G）
config/monitor-accounts.json の threads_accounts に登録された競合占いアカウントを
定期巡回し、最新投稿のmetricsを時系列追跡する。

優先戦略:
  1. Threads Graph API（Advanced Access）があれば使用
  2. なければ Playwright スクレイピングにフォールバック

出力: state/account-timeline.json
   - accounts: { handle: { follower_count, last_fetched, posts: [...] } }
   - posts: 各投稿は { post_id, text, posted_at, fetched_at, metrics, snapshots, classification }

使い方:
  python agents/account_timeline_collector.py              # 全アカウント巡回
  python agents/account_timeline_collector.py @shihoriiinu # 1アカウントのみ（テスト用）
"""

import json
import os
import re
import sys
import time

# Windows cp932 対策: stdout/stderrをUTF-8に
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import asyncio
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAX_POSTS_PER_ACCOUNT = 20       # 1アカウントあたり最新何投稿を追跡するか
INTER_ACCOUNT_DELAY_SEC = 3      # アカウント間の待機時間（BAN回避）
PAGE_TIMEOUT_MS = 30000          # Playwrightページロードタイムアウト

# ---------------------------------------------
# 共通ユーティリティ（buzz_collector.py の書き方に合わせる）
# ---------------------------------------------

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
    # 環境変数が無くても動かすのでエラーにはしない
    _read_env_file(primary) or _read_env_file(fallback)


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


# ---------------------------------------------
# 分類ユーティリティ（最小実装・今後拡張）
# ---------------------------------------------

HOOK_CATEGORY_PATTERNS = [
    ("注目緊急系", [r"^【", r"注意", r"緊急", r"速報", r"今すぐ", r"やばい"]),
    ("感情共感系", [r"わかる", r"つらい", r"泣", r"救われ", r"疲れ", r"心が"]),
    ("期待好奇心系", [r"実は", r"意外", r"知らない", r"秘密", r"本当の", r"驚き"]),
    ("数字具体性系", [r"^\d", r"\d+位", r"\d+個", r"\d+つ", r"\d+%", r"ランキング"]),
    ("断言逆張り系", [r"絶対", r"必ず", r"やめ", r"間違い", r"逆に", r"ダメ"]),
    ("疑問問いかけ系", [r"[?？]$", r"^なぜ", r"^どう", r"^知って", r"かも[?？]"]),
    ("運命宿命系", [r"運命", r"宿命", r"前世", r"ソウル", r"導かれ", r"星の"]),
    ("開運日時事系", [r"満月", r"新月", r"\d+月\d+日", r"今日", r"今週", r"今月"]),
]


def classify_hook(first_line):
    """1行目を hooks.json のカテゴリに分類（簡易版）"""
    if not first_line:
        return None
    for category, patterns in HOOK_CATEGORY_PATTERNS:
        for pat in patterns:
            if re.search(pat, first_line):
                return category
    return "その他"


def classify_structure(text):
    """本文を buzz_structures.json の A〜E に簡易分類"""
    if not text:
        return None
    # E: 短文全星座ツリー型
    if "牡羊" in text and "魚" in text and text.count(":") >= 8:
        return "structure_E"
    # C: ランキング断言型
    if re.search(r"\d+位", text) and ("ランキング" in text or "TOP" in text or "最強" in text):
        return "structure_C"
    # D: 業界暴露・逆張り型
    if re.search(r"(ヤラない|やめ|逆に|言いたい)", text) and "・" in text:
        return "structure_D"
    # A: 否定→肯定→具体アクション型
    if re.search(r"(でも|しかし|実は)", text) and re.search(r"(みて|しましょう|してください)", text):
        return "structure_A"
    # B: 謎のメタファー型（比喩語を含む）
    if re.search(r"(ような|モード|ように)", text):
        return "structure_B"
    return None


def count_emoji(text):
    """絵文字の大雑把なカウント（Unicode範囲）"""
    if not text:
        return 0
    emoji_ranges = (
        (0x1F300, 0x1F9FF),  # Misc Symbols & Pictographs, Emoticons, etc.
        (0x2600, 0x27BF),    # Misc Symbols, Dingbats
    )
    n = 0
    for ch in text:
        cp = ord(ch)
        for lo, hi in emoji_ranges:
            if lo <= cp <= hi:
                n += 1
                break
    return n


def extract_hashtags(text):
    return re.findall(r"#[^\s#]+", text or "")


def _parse_follower_count(body_text):
    """フォロワー数をテキストからパース。万単位・K表記・カンマ区切りに対応"""
    # パターン1: 「フォロワー1.2万人」「フォロワー12万人」
    m = re.search(r"フォロワー\s*([\d.]+)\s*万\s*人", body_text)
    if m:
        return int(float(m.group(1)) * 10000)
    # パターン2: 「フォロワー1,234人」「フォロワー149人」
    m = re.search(r"フォロワー\s*([\d,]+)\s*人", body_text)
    if m:
        return int(m.group(1).replace(",", ""))
    # パターン3: 英語「12K followers」「1.2M followers」
    m = re.search(r"([\d.]+)\s*([KMkm])\s*followers", body_text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        unit = m.group(2).upper()
        return int(val * (1000 if unit == "K" else 1000000))
    # パターン4: 英語「1,234 followers」
    m = re.search(r"([\d,]+)\s*followers", body_text, re.IGNORECASE)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


# ---------------------------------------------
# データ変換・擬似ER計算
# ---------------------------------------------

def build_post_record(handle, post_raw, follower_count, fetched_at_iso):
    """スクレイピング/API結果を統一フォーマットに整形"""
    text = post_raw.get("text", "") or ""
    first_line = text.split("\n", 1)[0].strip() if text else ""
    likes = int(post_raw.get("likes", 0) or 0)
    replies = int(post_raw.get("replies", 0) or 0)
    reposts = int(post_raw.get("reposts", 0) or 0)
    views = post_raw.get("views")  # None or int

    # 擬似ER = (likes + replies*3 + reposts*5) / follower_count
    pseudo_er = None
    if follower_count and follower_count > 0:
        pseudo_er = round((likes + replies * 3 + reposts * 5) / follower_count, 6)

    return {
        "post_id": post_raw.get("post_id", ""),
        "account_handle": handle,
        "url": post_raw.get("url", ""),
        "posted_at": post_raw.get("posted_at", ""),
        "fetched_at": fetched_at_iso,
        "text": text,
        "text_length": len(text),
        "first_line": first_line,
        "newline_count": text.count("\n"),
        "emoji_count": count_emoji(text),
        "hashtags": extract_hashtags(text),
        "has_external_link": bool(re.search(r"https?://", text)),
        "media_type": post_raw.get("media_type", "text"),
        "is_thread_root": post_raw.get("is_thread_root", True),
        "metrics": {
            "likes": likes,
            "replies": replies,
            "reposts": reposts,
            "views": views,
        },
        "account_snapshot": {
            "follower_count": follower_count,
        },
        "pseudo_er": pseudo_er,
        "classification": {
            "hook_category": classify_hook(first_line),
            "structure": classify_structure(text),
        },
    }


# ---------------------------------------------
# 戦略1: Threads Graph API（Advanced Access）
# ---------------------------------------------

def fetch_via_threads_api(handle):
    """Threads Graph API で取得を試みる。未対応なら None を返す"""
    api_level = os.environ.get("THREADS_API_LEVEL", "basic").lower()
    if api_level != "advanced":
        return None
    access_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not access_token:
        return None

    # 注: Threads Graph API の「他ユーザーのタイムライン取得」は現状制限があり、
    # 自分のアカウントしか /{user_id}/threads 取得できない仕様。
    # 他ユーザーの場合は keyword_search 経由で投稿を取るしかない。
    # ここは将来拡張ポイントとして関数だけ用意し、現時点では None を返す。
    log("INFO", f"[{handle}] Threads Graph API は他ユーザータイムライン非対応。Playwright使用")
    return None


# ---------------------------------------------
# 戦略2: Playwright スクレイピング
# ---------------------------------------------

async def _scrape_one_account_async(handle, max_posts=MAX_POSTS_PER_ACCOUNT):
    """1アカウントのプロフィールページを Playwright で取得"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("ERROR", "playwright が未インストール: pip install playwright && playwright install chromium")
        return None

    url = f"https://www.threads.com/@{handle}"
    result = {"handle": handle, "url": url, "follower_count": None, "posts": []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 2000},
            locale="ja-JP",
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            # Threads は JS レンダリングなのでコンテンツが出るまで少し待つ
            await page.wait_for_timeout(3500)

            # フォロワー数を取得（body全体から「フォロワーN人」を探す）
            try:
                body_text = await page.locator("body").first.inner_text(timeout=5000)
                fc = _parse_follower_count(body_text)
                if fc:
                    result["follower_count"] = fc
            except Exception:
                pass

            # 全投稿を1回の evaluate で一括抽出（DOM往復を減らす）
            raw_posts = await page.evaluate("""
                (args) => {
                    const maxPosts = args[0];
                    const handleName = args[1];
                    const containers = document.querySelectorAll('[data-pressable-container]');
                    const results = [];
                    for (let i = 0; i < Math.min(containers.length, maxPosts); i++) {
                        const c = containers[i];
                        const post = {};

                        // 1. 投稿リンク → post_id, url
                        const link = c.querySelector('a[href*="/post/"]');
                        post.url = link ? link.getAttribute('href') : '';
                        const m = (post.url || '').match(/\\/post\\/([^\\/?#]+)/);
                        post.post_id = m ? m[1] : '';

                        // 2. 投稿日時 → time[datetime]
                        const timeEl = c.querySelector('time[datetime]');
                        post.posted_at = timeEl ? timeEl.getAttribute('datetime') : '';

                        // 3. メトリクス → role="button" のテキストから数字抽出
                        const buttons = c.querySelectorAll('[role="button"]');
                        post.likes = 0;
                        post.replies = 0;
                        post.reposts = 0;
                        for (const btn of buttons) {
                            const t = (btn.textContent || '').trim();
                            // 「いいね！」「いいね！」3 → likes
                            if (t.startsWith('「いいね！」')) {
                                const n = t.replace('「いいね！」', '').trim();
                                post.likes = n ? parseInt(n.replace(/,/g, ''), 10) || 0 : 0;
                            }
                            // コメントする / コメントする5 → replies
                            else if (t.startsWith('コメントする')) {
                                const n = t.replace('コメントする', '').trim();
                                post.replies = n ? parseInt(n.replace(/,/g, ''), 10) || 0 : 0;
                            }
                            // 再投稿 / 再投稿2 → reposts（ただしナビタブの「再投稿」は除外）
                            else if (t.startsWith('再投稿') && !btn.closest('a[role="link"]')) {
                                const n = t.replace('再投稿', '').trim();
                                post.reposts = n ? parseInt(n.replace(/,/g, ''), 10) || 0 : 0;
                            }
                        }

                        // 4. 本文テキスト（ボタンテキスト等を除外した純粋な投稿内容）
                        // ユーザー名 + 相対時間 + 本文 + ボタンテキスト が inner_text に混在する
                        // 戦略: テキストノードを収集し、ボタン/ヘッダーを除外
                        const fullText = (c.innerText || '').trim();
                        // ユーザー名行（最初の行）と相対時間行（2行目）を除去
                        const lines = fullText.split('\\n');
                        // 末尾のボタンテキスト行を除去
                        const btnTexts = ['「いいね！」', 'コメントする', '再投稿', 'シェアする', 'もっと見る'];
                        const contentLines = [];
                        let headerSkipped = false;
                        for (const line of lines) {
                            const trimmed = line.trim();
                            if (!trimmed) continue;
                            // 最初のユーザー名行をスキップ
                            if (!headerSkipped && trimmed === handleName) { headerSkipped = true; continue; }
                            // 相対時間行（「N時間」「N日」等）をスキップ
                            if (/^\\d+[秒分時日週ヶ月年]/.test(trimmed) && trimmed.length <= 10) continue;
                            // ボタンテキスト行をスキップ
                            if (btnTexts.some(bt => trimmed.startsWith(bt))) continue;
                            // 純粋な数字行（ボタンの数値）をスキップ
                            if (/^\\d+$/.test(trimmed)) continue;
                            contentLines.push(trimmed);
                        }
                        post.text = contentLines.join('\\n');

                        // 5. メディアタイプ推定
                        const hasImg = c.querySelector('img:not([alt*="プロフィール"])') !== null;
                        const hasVideo = c.querySelector('video') !== null;
                        post.media_type = hasVideo ? 'video' : (hasImg ? 'image' : 'text');

                        if (post.text.length >= 3) {
                            results.push(post);
                        }
                    }
                    return results;
                }
            """, [max_posts, handle])

            for raw in raw_posts:
                raw["views"] = None
                raw["is_thread_root"] = True
                if raw.get("url") and not raw["url"].startswith("http"):
                    raw["url"] = f"https://www.threads.com{raw['url']}"
                if not raw.get("post_id"):
                    raw["post_id"] = f"{handle}_idx{len(result['posts'])}"
                result["posts"].append(raw)

        except Exception as e:
            log("ERROR", f"[{handle}] ページ取得失敗: {e}")
        finally:
            await browser.close()

    return result


def fetch_via_playwright(handle):
    """同期ラッパ"""
    try:
        return asyncio.run(_scrape_one_account_async(handle))
    except Exception as e:
        log("ERROR", f"[{handle}] Playwright実行エラー: {e}")
        return None


# ---------------------------------------------
# メイン処理
# ---------------------------------------------

def collect_one_account(handle):
    """1アカウントを取得→整形して返す"""
    now_iso = datetime.now(JST).isoformat()

    # 戦略1: API
    raw = fetch_via_threads_api(handle)
    # 戦略2: Playwright
    if raw is None:
        raw = fetch_via_playwright(handle)
    if raw is None:
        log("WARN", f"[{handle}] 取得失敗")
        return None

    follower_count = raw.get("follower_count")
    posts = [
        build_post_record(handle, p, follower_count, now_iso)
        for p in raw.get("posts", [])
    ]
    return {
        "handle": handle,
        "url": raw.get("url", f"https://www.threads.com/@{handle}"),
        "follower_count": follower_count,
        "last_fetched": now_iso,
        "posts": posts,
    }


def merge_into_timeline(timeline, account_result):
    """既存timelineに新規データをマージ（重複排除・時系列保持）"""
    handle = account_result["handle"]
    if "accounts" not in timeline:
        timeline["accounts"] = {}

    acc = timeline["accounts"].setdefault(handle, {"posts": []})
    acc["url"] = account_result["url"]
    acc["last_fetched"] = account_result["last_fetched"]
    if account_result.get("follower_count"):
        acc["follower_count"] = account_result["follower_count"]

    # 既存 post_id セット
    existing_by_id = {p["post_id"]: p for p in acc.get("posts", [])}

    for new_post in account_result["posts"]:
        pid = new_post["post_id"]
        if pid in existing_by_id:
            # 既存投稿のsnapshot（時点値）を追記
            existing = existing_by_id[pid]
            snapshots = existing.setdefault("snapshots", [])
            snapshots.append({
                "fetched_at": new_post["fetched_at"],
                "metrics": new_post["metrics"],
                "pseudo_er": new_post["pseudo_er"],
            })
            # 最新メトリクスで上書き
            existing["metrics"] = new_post["metrics"]
            existing["pseudo_er"] = new_post["pseudo_er"]
        else:
            new_post["snapshots"] = [{
                "fetched_at": new_post["fetched_at"],
                "metrics": new_post["metrics"],
                "pseudo_er": new_post["pseudo_er"],
            }]
            acc["posts"].append(new_post)

    # 最新順に並べ、保持上限
    acc["posts"] = acc["posts"][-200:]


def collect_all(target_handle=None):
    """全アカウントまたは指定1アカウントを巡回"""
    config = load_json("config/monitor-accounts.json")
    accounts = config.get("threads_accounts", [])
    if not accounts:
        log("ERROR", "threads_accounts が空")
        return

    if target_handle:
        target = target_handle.lstrip("@")
        accounts = [a for a in accounts if a.get("username") == target]
        if not accounts:
            log("ERROR", f"指定ハンドル '{target_handle}' が monitor-accounts.json に存在しない")
            return

    timeline = load_json("state/account-timeline.json", default={"accounts": {}})

    success = 0
    for i, acc_cfg in enumerate(accounts):
        handle = acc_cfg.get("username", "").lstrip("@")
        if not handle:
            continue
        log("INFO", f"[{i+1}/{len(accounts)}] @{handle} 取得開始")
        result = collect_one_account(handle)
        if result:
            merge_into_timeline(timeline, result)
            success += 1
            log("INFO", f"[{i+1}/{len(accounts)}] @{handle} OK: "
                         f"follower={result['follower_count']} posts={len(result['posts'])}")
        if i < len(accounts) - 1:
            time.sleep(INTER_ACCOUNT_DELAY_SEC)

    timeline["last_run"] = datetime.now(JST).isoformat()
    timeline["total_accounts"] = len(config.get("threads_accounts", []))
    save_json("state/account-timeline.json", timeline)
    log("INFO", f"完了: {success}/{len(accounts)} アカウント成功")


if __name__ == "__main__":
    load_env()
    log("INFO", "アカウント時系列コレクター開始")
    target = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        collect_all(target_handle=target)
    except Exception as e:
        log("ERROR", f"実行エラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    log("INFO", "アカウント時系列コレクター完了")
