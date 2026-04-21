#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
競合バズ投稿分析
monitor-accounts.json の threads_accounts を巡回し、
直近投稿（いいね・コメント数含む）を収集 → 高エンゲ投稿を Claude で構造分析
→ state/competitor-buzz-references.json に保存。

generate_posts.py が次回生成時にこのデータをプロンプトに注入する。

【実行タイミング】
  competitor-buzz-analysis.yml が毎日 JST 03:00 に実行

【バズ判定閾値】
  いいね >= 50  OR  コメント（返信）>= 20
"""

import asyncio
import json
import os
import re
import sys
import time
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
PAGE_WAIT_MS          = 3500    # JS レンダリング待機（ms）
PAGE_TIMEOUT_MS       = 25000   # ページロードタイムアウト（ms）
INTER_ACCOUNT_WAIT    = 1.5     # アカウント間待機（秒）
MAX_POSTS_PER_ACCOUNT = 5       # 1 アカウントから取得する最大投稿数
BUZZ_MIN_LIKES        = 50      # バズ判定: いいね閾値
BUZZ_MIN_COMMENTS     = 20      # バズ判定: コメント（返信）閾値
MAX_ANALYZE           = 15      # Claude に送る最大バズ投稿数
INSIGHTS_KEEP         = 40      # state ファイルに保持する最大バズ投稿数


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


def _save(rel_path: str, data):
    full = os.path.join(PROJECT_DIR, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Playwright: 複数投稿＋エンゲージメント数スクレイプ ────────────
_SCRAPE_JS = """
() => {
    function parseCount(s) {
        if (!s) return 0;
        s = s.replace(/,/g, '').trim();
        const hasWan = s.includes('万');
        const hasK   = /[kK千]/.test(s);
        const num    = parseFloat(s.replace(/[万kK千]/g, ''));
        if (hasWan) return Math.round(num * 10000);
        if (hasK)   return Math.round(num * 1000);
        return Math.round(num) || 0;
    }

    const containers = document.querySelectorAll('[data-pressable-container]');
    const posts = [];

    for (const c of Array.from(containers).slice(0, 8)) {
        // post_id & URL
        const link = c.querySelector('a[href*="/post/"]');
        const href = link ? link.getAttribute('href') : '';
        const m = (href || '').match(/\\/post\\/([^\\/?#]+)/);
        const post_id = m ? m[1] : '';
        if (!post_id) continue;

        // posted_at
        const timeEl = c.querySelector('time[datetime]');
        const posted_at = timeEl ? timeEl.getAttribute('datetime') : '';

        // 投稿テキスト（最大500文字）
        let text = '';
        for (const sp of c.querySelectorAll('span')) {
            const t = (sp.textContent || '').trim();
            if (t.length > 20 && !/^(いいね|コメント|返信|再投稿|シェア|保存)/.test(t)) {
                text = t.substring(0, 500);
                break;
            }
        }

        // エンゲージメント数（innerText全体から正規表現）
        const fullText = c.innerText || '';
        let likes = 0, comments = 0;

        const likePatterns = [
            /([\d,]+(?:\\.\\d+)?万?[kK千]?)\\s*件のいいね/,
            /いいね\\s*([\d,]+(?:\\.\\d+)?万?[kK千]?)/,
            /([\d,.]+[万kK千]?)\\s*[Ll]ike/,
        ];
        for (const pat of likePatterns) {
            const lm = fullText.match(pat);
            if (lm && lm[1]) { likes = parseCount(lm[1]); break; }
        }

        const cmtPatterns = [
            /([\d,]+(?:\\.\\d+)?万?[kK千]?)\\s*件の返信/,
            /返信\\s*([\d,]+(?:\\.\\d+)?万?[kK千]?)/,
            /([\d,.]+[万kK千]?)\\s*[Rr]epl/,
        ];
        for (const pat of cmtPatterns) {
            const cm = fullText.match(pat);
            if (cm && cm[1]) { comments = parseCount(cm[1]); break; }
        }

        posts.push({
            post_id,
            posted_at,
            text,
            likes,
            comments,
            url: href ? 'https://www.threads.com' + href : ''
        });
    }
    return posts;
}
"""


async def _scrape_account_posts(page, handle: str) -> list[dict]:
    """1アカウントの直近投稿リストを返す（エンゲージメント数込み）"""
    url = f"https://www.threads.com/@{handle}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        await page.wait_for_timeout(PAGE_WAIT_MS)
        results = await page.evaluate(_SCRAPE_JS)
        if not results:
            log("SKIP", f"@{handle}: 投稿取得失敗（DOM未レンダリング？）")
            return []
        posts = []
        for r in (results or [])[:MAX_POSTS_PER_ACCOUNT]:
            if r.get("post_id"):
                posts.append({
                    "handle":     handle,
                    "post_id":    r["post_id"],
                    "posted_at":  r.get("posted_at", ""),
                    "text":       r.get("text", ""),
                    "likes":      r.get("likes", 0),
                    "comments":   r.get("comments", 0),
                    "url":        r.get("url", f"https://www.threads.com/@{handle}"),
                    "scraped_at": datetime.now(JST).isoformat(),
                })
        return posts
    except Exception as e:
        log("WARN", f"@{handle} スクレイプエラー: {type(e).__name__}: {e}")
        return []


async def scrape_all_accounts(handles: list[str]) -> list[dict]:
    """全アカウントを巡回し、全投稿リストを返す"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("ERROR", "playwright 未インストール")
        return []

    all_posts = []

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

        for i, handle in enumerate(handles):
            log("SCAN", f"[{i+1}/{len(handles)}] @{handle}")
            posts = await _scrape_account_posts(page, handle)
            for p_item in posts:
                is_buzz = (
                    p_item["likes"]    >= BUZZ_MIN_LIKES or
                    p_item["comments"] >= BUZZ_MIN_COMMENTS
                )
                log(
                    "BUZZ" if is_buzz else "  --",
                    f"  @{handle} {p_item['post_id'][:8]} "
                    f"❤️{p_item['likes']} 💬{p_item['comments']} "
                    f"{'★バズ' if is_buzz else ''}"
                )
            all_posts.extend(posts)
            await asyncio.sleep(INTER_ACCOUNT_WAIT)

        await browser.close()

    return all_posts


# ── Claude でバズ投稿を構造分析 ───────────────────────────────────
def analyze_buzz_posts(buzz_posts: list[dict], api_key: str) -> list[dict]:
    """
    バズ投稿リストを Claude Haiku に渡し、構造分析を付与して返す。
    最大 MAX_ANALYZE 件をまとめてバッチ分析する。
    """
    targets = buzz_posts[:MAX_ANALYZE]
    if not targets:
        return []

    # 投稿リストをプロンプト用に整形
    post_block = ""
    for i, p in enumerate(targets, 1):
        post_block += (
            f"\n【投稿{i}】@{p['handle']} "
            f"❤️{p['likes']} 💬{p['comments']}\n"
            f"{p['text'][:300]}\n"
        )

    prompt = f"""占いSNS（Threads）のバズ投稿を分析してください。
各投稿について以下をJSON配列で返してください。

{post_block}

各投稿について分析してください:
- index: 投稿番号（1〜{len(targets)}）
- format_type: 投稿形式（例:「TOP5ランキング型」「スルー恐怖型」「限定型」「問いかけ型」等）
- hook: 1行目のフックテキスト（抜粋）
- cta_type: CTA形式（例:「絵文字置く誘導型」「いいね強要型」「コメント誘導型」等）
- key_elements: バズ要因となった特徴（3つ以内、配列）
- why_buzzes: 高エンゲージメントの理由（50文字以内）

JSON配列のみ出力。説明不要。
[{{"index":1,"format_type":"...","hook":"...","cta_type":"...","key_elements":["..."],"why_buzzes":"..."}}]"""

    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 2000,
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
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        text = result["content"][0]["text"]
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            log("WARN", "Claude分析: JSON抽出失敗")
            return targets
        analyses = json.loads(m.group(0))
        # 分析結果を対応する投稿にマージ
        for a in analyses:
            idx = a.get("index", 0) - 1
            if 0 <= idx < len(targets):
                targets[idx]["analysis"] = {
                    "format_type":   a.get("format_type", ""),
                    "hook":          a.get("hook", ""),
                    "cta_type":      a.get("cta_type", ""),
                    "key_elements":  a.get("key_elements", []),
                    "why_buzzes":    a.get("why_buzzes", ""),
                }
        return targets
    except Exception as e:
        log("WARN", f"Claude分析エラー: {e}")
        return targets


# ── insights JSON を build_competitor_buzz_block 向けに整形 ────────
def build_insights(buzz_posts_analyzed: list[dict], prev_data: dict) -> dict:
    """
    分析済みバズ投稿 → generate_posts.py の build_competitor_buzz_block が
    読み込める形式（competitor-buzz-references.json）に変換する。
    """
    # 既存データとマージ（重複除去・件数上限）
    existing = prev_data.get("top_buzz_posts", [])
    existing_ids = {p["post_id"] for p in existing}
    merged = [p for p in buzz_posts_analyzed if p["post_id"] not in existing_ids]
    merged = (merged + existing)[:INSIGHTS_KEEP]

    # フック1行目の例文（いいね数降順 上位10件）
    sorted_by_likes = sorted(merged, key=lambda x: x.get("likes", 0), reverse=True)
    strong_hooks = []
    for p in sorted_by_likes[:10]:
        hook = p.get("analysis", {}).get("hook") or p.get("text", "").split("\n")[0][:40]
        if hook and hook not in strong_hooks:
            strong_hooks.append(hook)

    # フックパターン集計
    hook_agg: dict[str, dict] = {}
    for p in merged:
        fmt = p.get("analysis", {}).get("format_type", "不明")
        total = p.get("likes", 0) + p.get("comments", 0) * 3  # コメントを3倍重み
        if fmt not in hook_agg:
            hook_agg[fmt] = {"total_score": 0, "count": 0}
        hook_agg[fmt]["total_score"] += total
        hook_agg[fmt]["count"] += 1

    hook_pattern_summary = {
        fmt: {
            "avg_er": round(v["total_score"] / v["count"] / 1000, 4),
            "count":  v["count"],
        }
        for fmt, v in sorted(hook_agg.items(), key=lambda x: -x[1]["total_score"] / max(x[1]["count"], 1))
    }

    # CTA形式集計
    cta_agg: dict[str, dict] = {}
    for p in merged:
        cta = p.get("analysis", {}).get("cta_type", "不明")
        total = p.get("likes", 0) + p.get("comments", 0) * 3
        if cta not in cta_agg:
            cta_agg[cta] = {"total_score": 0, "count": 0}
        cta_agg[cta]["total_score"] += total
        cta_agg[cta]["count"] += 1

    structure_summary = {
        cta: {
            "avg_er": round(v["total_score"] / v["count"] / 1000, 4),
            "count":  v["count"],
        }
        for cta, v in sorted(cta_agg.items(), key=lambda x: -x[1]["total_score"] / max(x[1]["count"], 1))
    }

    # バズ投稿の具体例テキスト（上位5件）
    buzz_examples = []
    for p in sorted_by_likes[:5]:
        text = p.get("text", "")[:200]
        handle = p.get("handle", "")
        likes = p.get("likes", 0)
        comments = p.get("comments", 0)
        why = p.get("analysis", {}).get("why_buzzes", "")
        buzz_examples.append(
            f"@{handle} ❤️{likes} 💬{comments}\n{text}\n→ {why}"
        )

    return {
        "generated_at":       datetime.now(JST).isoformat(),
        "buzz_posts_count":   len(merged),
        "top_buzz_posts":     merged,
        "writer_guidance": {
            "strong_first_line_examples": strong_hooks,
            "buzz_post_examples":         buzz_examples,
        },
        "hook_pattern_summary":  hook_pattern_summary,
        "structure_summary":     structure_summary,
    }


# ── メイン ────────────────────────────────────────────────────────
def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        env_path = os.path.join(PROJECT_DIR, "config", "api-keys.env")
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8"):
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()

    # 監視対象アカウント
    config = _load("config/monitor-accounts.json", {})
    handles = [
        acc.get("username", "").strip()
        for acc in config.get("threads_accounts", [])
        if acc.get("username", "").strip()
    ]
    if not handles:
        log("WARN", "monitor-accounts.json にアカウントが見つかりません")
        return

    log("START", f"競合バズ分析開始: {len(handles)}アカウント")

    # スクレイプ
    start = time.time()
    all_posts = asyncio.run(scrape_all_accounts(handles))
    elapsed = time.time() - start
    log("INFO", f"スクレイプ完了: {len(all_posts)}件取得 ({elapsed:.1f}秒)")

    # バズ投稿フィルタリング
    buzz_posts = [
        p for p in all_posts
        if p["likes"] >= BUZZ_MIN_LIKES or p["comments"] >= BUZZ_MIN_COMMENTS
    ]
    log("INFO", f"バズ投稿: {len(buzz_posts)}件（いいね{BUZZ_MIN_LIKES}+ or コメント{BUZZ_MIN_COMMENTS}+）")

    if not buzz_posts:
        log("INFO", "バズ投稿なし → state は更新しません")
        return

    # いいね+コメント*3 で降順ソート
    buzz_posts.sort(key=lambda x: x["likes"] + x["comments"] * 3, reverse=True)

    # Claude 分析
    if api_key:
        log("ANALYZE", f"Claude Haiku で上位{min(len(buzz_posts), MAX_ANALYZE)}件を構造分析中...")
        buzz_posts = analyze_buzz_posts(buzz_posts, api_key)
        log("ANALYZE", "分析完了")
    else:
        log("WARN", "ANTHROPIC_API_KEY なし → 構造分析スキップ")

    # 既存 state と合わせて insights を構築・保存
    prev_data = _load("state/competitor-buzz-references.json", {})
    insights = build_insights(buzz_posts, prev_data)
    _save("state/competitor-buzz-references.json", insights)

    log("SAVE", (
        f"competitor-buzz-references.json 保存完了: "
        f"累計{insights['buzz_posts_count']}件 / "
        f"フックパターン{len(insights['hook_pattern_summary'])}種"
    ))

    # サマリー出力
    log("RESULT", "=== バズ投稿 TOP5 ===")
    for i, p in enumerate(buzz_posts[:5], 1):
        fmt = p.get("analysis", {}).get("format_type", "?")
        log("RESULT", f"  {i}. @{p['handle']} ❤️{p['likes']} 💬{p['comments']} [{fmt}] {p['text'][:40]}")

    log("DONE", "完了")


if __name__ == "__main__":
    main()
