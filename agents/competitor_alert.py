#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
競合アカウント新投稿アラート（軽量版）
config/monitor-accounts.json の threads_accounts を巡回し、
新規投稿を検知したら ntfy.sh でスマホにpush通知を送る。

【セットアップ】
1. スマホに ntfy アプリをインストール（iOS/Android 無料）
2. アプリで「Subscribe to topic」→ 下記 NTFY_TOPIC を入力
3. GitHub Secrets に NTFY_TOPIC を追加（例: yozora-uranai-20260421）

【通知タイミング】
  competitor-alert.yml が毎時0分・30分に実行（JST 07:00〜22:00）

【出力】
  state/alert-last-seen.json - アカウントごとの最終確認投稿ID
"""

import asyncio
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 設定 ──────────────────────────────────────────────────────────
# スクレイプ設定
PAGE_WAIT_MS       = 3000   # JS レンダリング待機（ms）
PAGE_TIMEOUT_MS    = 25000  # ページロードタイムアウト（ms）
INTER_ACCOUNT_WAIT = 1.5    # アカウント間待機（秒）

# ntfy.sh 設定
NTFY_BASE_URL = "https://ntfy.sh"
DEFAULT_TOPIC = "yozora-uranai-alert"   # GitHub Secret NTFY_TOPIC を推奨


def log(level: str, msg: str):
    now = datetime.now(JST).strftime("%H:%M:%S")
    print(f"[{now}][{level}] {msg}", flush=True)


# ── ファイル操作 ──────────────────────────────────────────────────
def _load(rel_path: str, default=None):
    full = os.path.join(PROJECT_DIR, rel_path)
    if os.path.exists(full):
        try:
            with open(full, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            pass
    return default if default is not None else {}


def _save(rel_path: str, data: dict):
    full = os.path.join(PROJECT_DIR, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── アカウントリスト読み込み ─────────────────────────────────────
def load_alert_targets() -> list[str]:
    """monitor-accounts.json から監視対象ハンドルを返す"""
    config = _load("config/monitor-accounts.json", {})
    handles = []
    for acc in config.get("threads_accounts", []):
        username = acc.get("username", "").strip()
        if username:
            handles.append(username)
    return handles


# ── ntfy.sh 通知 ─────────────────────────────────────────────────
def send_push(topic: str, title: str, body: str, priority: str = "default",
              url: str = "", actions: list | None = None):
    """ntfy.sh に push 通知を送る（JSON API 使用で日本語タイトル対応）"""
    priority_map = {"high": 4, "default": 3, "low": 2}
    payload = {
        "topic":    topic,
        "title":    title,
        "message":  body,
        "priority": priority_map.get(priority, 3),
        "tags":     ["bell"],
    }
    if url:
        payload["click"] = url
    if actions:
        payload["actions"] = actions

    try:
        req = urllib.request.Request(
            NTFY_BASE_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.getcode()
        if status == 200:
            log("NOTIFY", f"push sent → [{title}]")
        else:
            log("WARN", f"ntfy 応答: HTTP {status}")
    except Exception as e:
        log("WARN", f"ntfy 送信エラー: {e}")


# ── Playwright 軽量チェック ──────────────────────────────────────
async def _scrape_latest_post(page, handle: str) -> dict | None:
    """
    1アカウントのプロフィールページを開き、
    最新投稿の post_id, posted_at, text 冒頭, フォロワー数だけ返す。
    失敗時は None。
    """
    url = f"https://www.threads.com/@{handle}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        await page.wait_for_timeout(PAGE_WAIT_MS)

        # フォロワー数（body全体テキストから）
        follower_count = 0
        try:
            body_text = await page.locator("body").first.inner_text(timeout=5000)
            # 「X人のフォロワー」「フォロワー X 人」「フォロワー数 X」いずれかのパターン
            import re
            patterns = [
                r"([\d,]+)\s*人のフォロワー",
                r"フォロワー\s*([\d,]+)\s*人",
                r"([\d,.]+[万k]?)\s*フォロワー",
                r"Followers\s*([\d,]+)",
            ]
            for pat in patterns:
                m = re.search(pat, body_text)
                if m:
                    raw = m.group(1).replace(",", "").replace(".", "")
                    if "万" in m.group(0):
                        follower_count = int(float(raw) * 10000)
                    elif "k" in m.group(0).lower():
                        follower_count = int(float(raw) * 1000)
                    else:
                        follower_count = int(raw)
                    break
        except Exception:
            pass

        # 最新投稿のみ取得（containers[0] のみ）
        result = await page.evaluate("""
            () => {
                const containers = document.querySelectorAll('[data-pressable-container]');
                if (!containers.length) return null;
                const c = containers[0];

                // post_id / url
                const link = c.querySelector('a[href*="/post/"]');
                const href = link ? link.getAttribute('href') : '';
                const m = (href || '').match(/\\/post\\/([^\\/?#]+)/);
                const post_id = m ? m[1] : '';

                // posted_at
                const timeEl = c.querySelector('time[datetime]');
                const posted_at = timeEl ? timeEl.getAttribute('datetime') : '';

                // テキスト冒頭（最大100字）
                let text = '';
                const spanEls = c.querySelectorAll('span');
                for (const sp of spanEls) {
                    const t = (sp.textContent || '').trim();
                    if (t.length > 20 && !t.startsWith('いいね') && !t.startsWith('コメント') && !t.startsWith('再投稿')) {
                        text = t.substring(0, 100);
                        break;
                    }
                }

                // スレッドURL を絶対パスに
                const full_url = href ? 'https://www.threads.com' + href : '';

                return { post_id, posted_at, text, url: full_url };
            }
        """)

        if not result or not result.get("post_id"):
            log("SKIP", f"@{handle}: 投稿取得失敗（DOM未レンダリング？）")
            return None

        return {
            "handle":         handle,
            "post_id":        result["post_id"],
            "posted_at":      result.get("posted_at", ""),
            "text":           result.get("text", ""),
            "url":            result.get("url", f"https://www.threads.com/@{handle}"),
            "follower_count": follower_count,
        }

    except Exception as e:
        log("WARN", f"@{handle} スクレイプエラー: {e}")
        return None


async def run_alert_check(handles: list[str], last_seen: dict, topic: str) -> tuple[list[dict], dict]:
    """
    全アカウントを軽量チェックし、新投稿を検出して返す。
    last_seen を更新して返す。
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("ERROR", "playwright 未インストール: pip install playwright && python -m playwright install chromium")
        return [], last_seen

    new_posts = []
    now_str = datetime.now(JST).isoformat()

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
        # 全アカウントで1つのページを再利用（速度向上）
        page = await context.new_page()

        for i, handle in enumerate(handles):
            log("CHECK", f"[{i+1}/{len(handles)}] @{handle}")

            latest = await _scrape_latest_post(page, handle)

            if not latest:
                # スクレイプ失敗でも last_checked だけ更新
                if handle in last_seen.get("accounts", {}):
                    last_seen["accounts"][handle]["last_checked"] = now_str
                await asyncio.sleep(INTER_ACCOUNT_WAIT)
                continue

            prev = last_seen.get("accounts", {}).get(handle, {})
            prev_post_id = prev.get("last_post_id", "")

            is_new = (latest["post_id"] != "" and latest["post_id"] != prev_post_id)

            # last_seen 更新
            if "accounts" not in last_seen:
                last_seen["accounts"] = {}
            last_seen["accounts"][handle] = {
                "last_post_id":  latest["post_id"],
                "last_posted_at": latest["posted_at"],
                "last_checked":  now_str,
                "follower_count": latest.get("follower_count", 0),
            }

            if is_new and prev_post_id:
                # prev_post_id が空（初回実行）は通知しない（既存投稿でノイズになる）
                log("NEW", f"@{handle} 新投稿検知！ ({latest['post_id']})")
                new_posts.append(latest)
            elif not prev_post_id:
                log("INIT", f"@{handle} 初回記録: {latest['post_id']}")
            else:
                log("SAME", f"@{handle} 変化なし")

            await asyncio.sleep(INTER_ACCOUNT_WAIT)

        await browser.close()

    last_seen["last_run"] = now_str
    return new_posts, last_seen


# ── 月相 ─────────────────────────────────────────────────────────
def get_moon_phase(dt: datetime) -> str:
    known_new_moon = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    lunar_cycle = 29.53058867
    diff = (dt.astimezone(timezone.utc) - known_new_moon).total_seconds() / 86400
    phase_day = diff % lunar_cycle
    if phase_day < 1.5:   return "新月（新しいスタートのエネルギー）"
    elif phase_day < 7.4:  return "上弦の月（行動・積み上げのエネルギー）"
    elif phase_day < 8.9:  return "上弦の半月（バランスを取るエネルギー）"
    elif phase_day < 14.8: return "満月前（感情が高まり・直感が冴える時期）"
    elif phase_day < 16.3: return "満月（感情のピーク・気づきと解放のエネルギー）"
    elif phase_day < 22.1: return "下弦の月（手放し・内省のエネルギー）"
    elif phase_day < 23.6: return "下弦の半月（整理と見直しのエネルギー）"
    else:                  return "晦日（新月前夜・静寂と準備のエネルギー）"


# ── コメント生成（Claude Haiku）───────────────────────────────────
def generate_comment(post: dict, moon_phase: str, api_key: str) -> str | None:
    """新投稿に対するコメント案をその場で生成して返す"""
    prompt = f"""あなたは占いSNSアカウント「よぞら.」の運営者・月詠（つくよみ）です。
競合占いアカウント @{post['handle']} の投稿に自然なコメントを残します。

【相手の投稿内容】
{post.get('text', '')[:300]}

【今の月相】
{moon_phase}

【コメントのルール】
1. 25〜40文字程度
2. 占い・星読みの知識を1片だけ自然に含める
3. 宣伝・自アカウント名は完全禁止
4. 相手の投稿内容に具体的に触れる
5. 絵文字は0〜1個（🌙✨🔮⭐💫のいずれか）

コメント文のみ出力。説明不要。"""

    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["content"][0]["text"].strip()
    except Exception as e:
        log("WARN", f"コメント生成エラー: {e}")
        return None


# ── 通知メッセージ生成（1件分）────────────────────────────────────
def build_single_notification(post: dict, comment: str | None) -> dict:
    """
    1投稿分の ntfy JSON ペイロードを返す。
    - 本文 = コメント案のみ（長押しでコピーしやすい）
    - 投稿URLはアクションボタンに分離（タップで投稿を開く）
    """
    fc_str = f"{post['follower_count']:,}" if post.get('follower_count', 0) > 0 else "?"
    title = f"🔔 @{post['handle']} が投稿（{fc_str}F）"
    post_url = post.get("url", f"https://www.threads.com/@{post['handle']}")

    # 本文：コメント案 or シンプルな案内
    if comment:
        message = comment
    else:
        text = post.get("text", "")
        snippet = f"「{text[:40]}…」" if len(text) > 40 else (f"「{text}」" if text else "")
        message = f"コメントチャンス！\n{snippet}".strip()

    return {
        "title":    title,
        "message":  message,
        "priority": 4,
        "tags":     ["bell"],
        "actions": [
            {
                "action": "view",
                "label":  "投稿を開く",
                "url":    post_url,
                "clear":  False,
            }
        ],
    }


# ── メイン ────────────────────────────────────────────────────────
def main():
    ntfy_topic = os.environ.get("NTFY_TOPIC", DEFAULT_TOPIC).strip()
    if not ntfy_topic:
        ntfy_topic = DEFAULT_TOPIC

    # テストモード: DRY_RUN=true の場合はテスト通知だけ送って終了
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    if dry_run:
        log("TEST", f"テスト通知を送信中... (topic: {ntfy_topic})")
        send_push(
            ntfy_topic,
            "🔔 テスト通知",
            "ntfy の接続確認です。このメッセージが届いていれば設定完了！",
            priority="default",
        )
        log("TEST", "テスト通知送信完了")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        # ローカル実行時のフォールバック
        env_path = os.path.join(PROJECT_DIR, "config", "api-keys.env")
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8"):
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()

    log("START", f"競合アラートチェック開始 (ntfy topic: {ntfy_topic})")
    if api_key:
        log("INFO", "Claude API あり → コメント案を通知に含めます")
    else:
        log("INFO", "Claude API なし → コメント案なしで通知します")

    # 監視対象アカウント
    handles = load_alert_targets()
    if not handles:
        log("WARN", "monitor-accounts.json にアカウントが見つかりません")
        return
    log("INFO", f"監視対象: {len(handles)}件")

    # 前回の既知投稿IDをロード
    last_seen = _load("state/alert-last-seen.json", {"accounts": {}})

    # 非同期スクレイプ実行
    start = time.time()
    new_posts, updated_last_seen = asyncio.run(
        run_alert_check(handles, last_seen, ntfy_topic)
    )
    elapsed = time.time() - start
    log("INFO", f"スクレイプ完了: {elapsed:.1f}秒")

    # state 保存
    _save("state/alert-last-seen.json", updated_last_seen)
    log("INFO", "alert-last-seen.json 保存完了")

    # 通知（1件ずつ個別送信）
    if new_posts:
        log("NOTIFY", f"新投稿 {len(new_posts)}件 → push通知送信")
        moon_phase = get_moon_phase(datetime.now(JST))

        for post in new_posts:
            # コメント案生成（API キーあれば）
            comment = None
            if api_key and post.get("text"):
                log("GEN", f"@{post['handle']} のコメント案を生成中...")
                comment = generate_comment(post, moon_phase, api_key)
                if comment:
                    log("GEN", f"生成完了: {comment}")

            payload = build_single_notification(post, comment)
            send_push(
                ntfy_topic,
                payload["title"],
                payload["message"],
                priority="high",
                actions=payload.get("actions"),
            )
    else:
        log("INFO", "新投稿なし → 通知なし")

    log("DONE", "完了")


if __name__ == "__main__":
    main()
