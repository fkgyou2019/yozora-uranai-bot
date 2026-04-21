#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
コメント候補生成スクリプト
競合アカウントの直近投稿を読み、よぞら.用コメント案をClaudeで生成。
Google Sheets「コメント候補」シートに書き出す。

account-timeline.yml から呼び出される（毎日09:00/21:00 JST）
"""

import json
import os
import sys
import urllib.request
import urllib.parse
import math
from datetime import datetime, timezone, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 設定 ──────────────────────────────────────────────────
MIN_FOLLOWERS   = 1_000    # コメント対象の最小フォロワー数
MAX_FOLLOWERS   = 50_000   # コメント対象の最大フォロワー数（大手すぎると埋もれる）
MAX_POST_AGE_H  = 48       # 何時間以内の投稿を対象にするか
MAX_SUGGESTIONS = 20       # 1回の実行で生成するコメント候補数上限
SKIP_ALREADY_SUGGESTED = True  # 既に候補生成済みの投稿はスキップ


def _load(path, default=None):
    full = os.path.join(PROJECT_DIR, path)
    if os.path.exists(full):
        try:
            with open(full, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            pass
    return default if default is not None else {}


def _save(path, data):
    full = os.path.join(PROJECT_DIR, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 月相計算（簡易版）──────────────────────────────────────
def get_moon_phase(dt: datetime) -> str:
    """月相を文字列で返す（簡易計算）"""
    known_new_moon = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    lunar_cycle = 29.53058867
    diff = (dt.astimezone(timezone.utc) - known_new_moon).total_seconds() / 86400
    phase_day = diff % lunar_cycle
    if phase_day < 1.5:
        return "新月（新しいスタートのエネルギー）"
    elif phase_day < 7.4:
        return "上弦の月（行動・積み上げのエネルギー）"
    elif phase_day < 8.9:
        return "上弦の半月（バランスを取るエネルギー）"
    elif phase_day < 14.8:
        return "満月前（感情が高まり・直感が冴える時期）"
    elif phase_day < 16.3:
        return "満月（感情のピーク・気づきと解放のエネルギー）"
    elif phase_day < 22.1:
        return "下弦の月（手放し・内省のエネルギー）"
    elif phase_day < 23.6:
        return "下弦の半月（整理と見直しのエネルギー）"
    else:
        return "晦日（新月前夜・静寂と準備のエネルギー）"


def get_planet_context(dt: datetime) -> str:
    """2026年の主な天体配置を返す"""
    month = dt.month
    # 2026年の天体配置（主要惑星）
    context_lines = [
        "【2026年の主な天体配置】",
        "・木星：双子座（2025年5月〜2026年6月）→ 情報・コミュニケーション・学びが拡大",
        "・土星：牡羊座（2025年5月〜2028年）→ 自己確立・新しい挑戦への試練",
        "・冥王星：水瓶座（2024年11月〜）→ 社会変革・テクノロジー・集団意識の変容",
        "・天王星：双子座（2025年7月〜）→ 予期せぬ変化・自由・革新への衝動",
    ]
    if 4 <= month <= 5:
        context_lines.append("・太陽：牡牛座（4/20〜5/20）→ 安定・五感・じっくり育てるエネルギー")
    elif month == 3 or (month == 4 and dt.day < 20):
        context_lines.append("・太陽：牡羊座 → 新しいスタート・情熱・行動のエネルギー")
    return "\n".join(context_lines)


# ── 投稿収集 ──────────────────────────────────────────────
def collect_target_posts(timeline: dict) -> list[dict]:
    """対象アカウントの直近投稿を収集"""
    now = datetime.now(JST)
    cutoff = now - timedelta(hours=MAX_POST_AGE_H)
    posts = []

    for handle, data in timeline.get("accounts", {}).items():
        account_posts = data.get("posts", [])
        if not account_posts:
            continue

        # フォロワー数チェック（最新投稿の snapshot から）
        followers = account_posts[0].get("account_snapshot", {}).get("follower_count", 0)
        if not (MIN_FOLLOWERS <= followers <= MAX_FOLLOWERS):
            continue

        for post in account_posts:
            posted_at_str = post.get("posted_at", "")
            if not posted_at_str:
                continue
            try:
                posted_at = datetime.fromisoformat(posted_at_str.replace("Z", "+00:00"))
                if posted_at < cutoff.astimezone(timezone.utc):
                    continue
            except Exception:
                continue

            text = post.get("text", "").strip()
            if not text or len(text) < 10:
                continue

            posts.append({
                "post_id":    post["post_id"],
                "handle":     handle,
                "followers":  followers,
                "text":       text,
                "first_line": post.get("first_line", text.split("\n")[0])[:80],
                "url":        post.get("url", f"https://www.threads.com/@{handle}"),
                "posted_at":  posted_at_str,
                "likes":      post.get("metrics", {}).get("likes", 0) or 0,
                "pseudo_er":  post.get("pseudo_er", 0),
            })

    # フォロワー多い順にソート
    posts.sort(key=lambda x: -x["followers"])
    return posts


# ── コメント生成 ──────────────────────────────────────────
def generate_comment(post: dict, moon_phase: str, planet_ctx: str, api_key: str) -> str | None:
    """Claude APIで1件のコメント案を生成"""
    prompt = f"""あなたは占いSNSアカウント「よぞら.」の運営者・月詠（つくよみ）です。
競合占いアカウント @{post['handle']}（フォロワー{post['followers']:,}人）の投稿に
自然なコメントを残します。

【相手の投稿内容】
{post['text'][:300]}

【今の天体情報（コメントの参考に）】
月相: {moon_phase}
{planet_ctx}

【コメントのルール】
1. 25〜40文字程度（短すぎず、長すぎず）
2. 占い・星読み・スピリチュアルの知識を1片だけ自然に含める
3. 「フォローしてください」「プロフィール見て」等の宣伝は完全禁止
4. 相手の投稿内容に具体的に触れる（汎用コメントにしない）
5. 絵文字は0〜1個（🌙✨🔮⭐💫のいずれか）
6. 自分のアカウント名・宣伝ワード一切なし
7. 読んだ人が「この人詳しいな」と感じる内容

コメント文のみ出力。説明不要。"""

    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 150,
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
        print(f"  [WARN] コメント生成エラー (@{post['handle']}): {e}")
        return None


# ── Google Sheets 書き出し ────────────────────────────────
def export_to_sheets(suggestions: list[dict], spreadsheet_id: str, creds_json: str):
    try:
        import gspread
        gc = gspread.service_account_from_dict(json.loads(creds_json))
        ss = gc.open_by_key(spreadsheet_id)

        try:
            ws = ss.worksheet("コメント候補")
        except Exception:
            ws = ss.add_worksheet(title="コメント候補", rows=500, cols=10)

        now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
        rows = [[
            f"更新: {now_str}",
            "★ 使い方: コメント欄をコピー → Threadsで投稿URLを開いて貼り付け",
            "", "", "", "", ""
        ]]
        rows.append([
            "アカウント", "フォロワー", "投稿冒頭（50字）",
            "★提案コメント（コピー用）", "投稿URL", "投稿時刻", "使用済み□"
        ])
        for s in suggestions:
            posted_jst = ""
            try:
                dt = datetime.fromisoformat(s["posted_at"].replace("Z", "+00:00"))
                posted_jst = dt.astimezone(JST).strftime("%m/%d %H:%M")
            except Exception:
                pass
            rows.append([
                f"@{s['handle']}",
                f"{s['followers']:,}",
                s["first_line"][:50],
                s["comment"],
                s["url"],
                posted_jst,
                "□",
            ])

        ws.clear()
        ws.update(rows, value_input_option="RAW")

        # A列とD列を幅広に
        try:
            ws.columns_auto_resize(0, 6)
        except Exception:
            pass

        print(f"[COMMENT] Sheets「コメント候補」更新完了: {len(suggestions)}件")
    except Exception as e:
        print(f"[COMMENT] Sheets書き込みエラー（続行）: {e}")


# ── メイン ────────────────────────────────────────────────
def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_ID",
                                    "124lW4BIn11nMBxiStcquHBKWUGv362vUWBUCNpWDa9k")

    # ローカル環境用フォールバック
    if not api_key:
        env_path = os.path.join(PROJECT_DIR, "config", "api-keys.env")
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8"):
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()

    if not api_key:
        print("[COMMENT] ANTHROPIC_API_KEY 未設定 → スキップ")
        return

    now = datetime.now(JST)
    moon_phase = get_moon_phase(now)
    planet_ctx = get_planet_context(now)
    print(f"[COMMENT] {now.strftime('%Y-%m-%d %H:%M JST')} 開始")
    print(f"[COMMENT] 月相: {moon_phase}")

    # 投稿収集
    timeline = _load("state/account-timeline.json")
    posts = collect_target_posts(timeline)
    print(f"[COMMENT] 対象投稿: {len(posts)}件（直近{MAX_POST_AGE_H}h・フォロワー{MIN_FOLLOWERS:,}〜{MAX_FOLLOWERS:,}）")

    if not posts:
        print("[COMMENT] 対象投稿なし → スキップ")
        return

    # 既に候補生成済みの投稿IDをロード
    suggestion_state = _load("state/comment-suggestions.json", {"generated": [], "suggestions": []})
    already_generated = set(suggestion_state.get("generated", []))

    # 未処理の投稿に絞る
    new_posts = [p for p in posts if p["post_id"] not in already_generated]
    print(f"[COMMENT] 未処理: {len(new_posts)}件 / 上限{MAX_SUGGESTIONS}件まで生成")

    # コメント生成
    suggestions = []
    for post in new_posts[:MAX_SUGGESTIONS]:
        print(f"  → @{post['handle']} ({post['followers']:,}F): {post['first_line'][:30]}...")
        comment = generate_comment(post, moon_phase, planet_ctx, api_key)
        if not comment:
            continue
        suggestions.append({
            "post_id":    post["post_id"],
            "handle":     post["handle"],
            "followers":  post["followers"],
            "first_line": post["first_line"],
            "text":       post["text"][:200],
            "url":        post["url"],
            "posted_at":  post["posted_at"],
            "comment":    comment,
            "generated_at": now.isoformat(),
        })
        already_generated.add(post["post_id"])
        print(f"     コメント案: {comment}")

    print(f"[COMMENT] 生成完了: {len(suggestions)}件")

    # 保存（直近200件を保持）
    all_suggestions = suggestion_state.get("suggestions", []) + suggestions
    all_suggestions = all_suggestions[-200:]
    _save("state/comment-suggestions.json", {
        "generated":   list(already_generated)[-500:],
        "suggestions": all_suggestions,
        "last_run":    now.isoformat(),
    })

    # Sheets書き出し（直近20件を表示）
    display = sorted(all_suggestions, key=lambda x: x.get("posted_at", ""), reverse=True)[:20]
    if creds_json:
        export_to_sheets(display, spreadsheet_id, creds_json)
    else:
        print("[COMMENT] GOOGLE_SHEETS_CREDENTIALS 未設定 → Sheets書き込みスキップ")


if __name__ == "__main__":
    main()
