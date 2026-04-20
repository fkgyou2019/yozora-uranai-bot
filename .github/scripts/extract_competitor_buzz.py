#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
競合アカウント時系列データからバズ投稿を抽出・構造分析し、
ライターが参照できる形式で state/competitor-buzz-references.json に保存する。

account-timeline.yml の collect ステップ後に自動実行される。
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

JST = timezone(timedelta(hours=9))
# .github/scripts/ → .github/ → project root
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TIMELINE_PATH = os.path.join(PROJECT_DIR, "state", "account-timeline.json")
OUTPUT_PATH   = os.path.join(PROJECT_DIR, "state", "competitor-buzz-references.json")

# バズ判定閾値
MIN_PSEUDO_ER   = 0.05   # pseudo_er >= 0.05 をバズとみなす
MIN_LIKES       = 5      # または likes >= 5
TOP_N           = 30     # カテゴリ横断トップ N 件
TOP_PER_CAT     = 8      # カテゴリ別トップ N 件

# フックパターン分類ルール（優先順位順）
HOOK_PATTERNS = [
    ("いいね強要型",      r"(い.?い.?ね|いいね|ハート|🔮|⛩️).{0,10}(運気|運|幸運|強運|爆上)"),
    ("スルー恐怖型",      r"(無視|素通り|飛ばし|スルー|見ちゃ).{0,15}(ダメ|損|呪|失|終|最悪|注意)"),
    ("強運断言型",        r"^(あなた.{0,8}(超強運|強運|最強|幸運)|嘘は言|正直に)"),
    ("ランキング型",      r"(第[1１]位|1位|一位|TOP\s*1|No\.1)"),
    ("限定星座型",        r"(たった[1-9１-９]つ|[1-9１-９]星座(だけ|のみ)|[1-9]つの星座)"),
    ("警告・危険型",      r"(危険|警告|注意|やめて|気をつけ|ヤバい|やばい).{0,10}(星座|生まれ|月)"),
    ("共感・場面描写型",  r"^(もし|あなたが|こんな|そんな|「|【|…)"),
    ("天体根拠型",        r"(月|水星|火星|木星|金星|太陽|満月|新月|逆行|天体|星座).{0,15}(動き|入り|影響|エネルギー)"),
    ("臨時収入型",        r"(臨時収入|入ってきます|収入|金運|金が|お金)"),
    ("質問フック型",      r"(知りたく|気になる|どっち|どれ|何番|わかる\?|ですか？$)"),
    ("その他",            r".+"),
]


def classify_hook(first_line: str) -> str:
    for name, pattern in HOOK_PATTERNS:
        if re.search(pattern, first_line):
            return name
    return "その他"


def classify_structure(text: str) -> str:
    """バズ構造の簡易判定"""
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines:
        return "不明"
    first = lines[0]

    if re.search(r"(第[1-9]位|1位|ランキング|TOP)", text):
        return "B_ランキング型"
    if re.search(r"(無視|素通り|飛ばし).{0,15}(損|呪|失|ダメ|注意)", text):
        return "D_恐怖回避型"
    if re.search(r"(いいね|ハート).{0,10}(置|押|運気)", text):
        return "E_エンゲージ強要型"
    if re.search(r"(たった[1-9]|[1-9]星座だけ|[1-9]つだけ)", text):
        return "A_限定希少型"
    if re.search(r"(88万|臨時収入|収入が入)", text):
        return "C_予言・断言型"
    if len(lines) >= 3 and re.search(r"(🌸|🌙|✨|⭐|🔮)", text[:50]):
        return "H_スピ共感型"
    if re.search(r"(もし|あなたが|こんな気持ち|そんな時)", first):
        return "H_スピ共感型"
    if re.search(r"(月|天体|星の|占星術)", first):
        return "G_天体根拠型"
    return "F_その他"


def extract_first_line(text: str) -> str:
    """有効な1行目を抽出（日付行・ピン留め行をスキップ）"""
    skip_patterns = re.compile(
        r"^(ピン留め済み|ピン留め|20\d\d[/\-]\d+[/\-]\d+|\d+件のリプライ|リポスト済み)$"
    )
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and len(stripped) > 3 and not skip_patterns.match(stripped):
            # 数字だけの行（いいね数など）はスキップ
            if re.match(r"^[\d,]+$", stripped):
                continue
            return stripped[:60]
    return text[:60]


def load_timeline() -> dict:
    if not os.path.exists(TIMELINE_PATH):
        print(f"[WARN] {TIMELINE_PATH} が存在しません", file=sys.stderr)
        return {}
    with open(TIMELINE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    data = load_timeline()
    accounts_raw = data.get("accounts", {})

    # --- 全投稿をフラット化 ---
    all_posts = []
    for handle, acc_data in accounts_raw.items():
        category = acc_data.get("category", "other")
        follower_count = 0
        for post in acc_data.get("posts", []):
            metrics = post.get("metrics", {})
            likes    = metrics.get("likes", 0) or 0
            replies  = metrics.get("replies", 0) or 0
            reposts  = metrics.get("reposts", 0) or 0
            pseudo_er = post.get("pseudo_er", 0) or 0
            snap = post.get("account_snapshot") or {}
            fc = snap.get("follower_count", 0) or 0
            if fc:
                follower_count = fc

            text = post.get("text", "") or ""
            if not text.strip():
                continue

            all_posts.append({
                "handle":       handle,
                "category":     category,
                "url":          post.get("url", ""),
                "posted_at":    post.get("posted_at", ""),
                "text":         text,
                "first_line":   extract_first_line(text),
                "likes":        likes,
                "replies":      replies,
                "reposts":      reposts,
                "pseudo_er":    pseudo_er,
                "follower_count": fc or follower_count,
            })

    print(f"[INFO] 全投稿: {len(all_posts)}件")

    # --- バズ投稿フィルタ ---
    buzz_posts = [
        p for p in all_posts
        if p["pseudo_er"] >= MIN_PSEUDO_ER or p["likes"] >= MIN_LIKES
    ]
    buzz_posts.sort(key=lambda x: x["pseudo_er"], reverse=True)
    print(f"[INFO] バズ投稿: {len(buzz_posts)}件 (ER>={MIN_PSEUDO_ER} or likes>={MIN_LIKES})")

    # --- フック分類 ---
    for p in buzz_posts:
        p["hook_pattern"] = classify_hook(p["first_line"])
        p["structure"]    = classify_structure(p["text"])

    # --- トップ N 件（全体） ---
    top_overall = buzz_posts[:TOP_N]

    # --- カテゴリ別トップ ---
    by_category = defaultdict(list)
    for p in buzz_posts:
        by_category[p["category"]].append(p)

    top_by_category = {}
    for cat, posts in by_category.items():
        top_by_category[cat] = posts[:TOP_PER_CAT]

    # --- フックパターン別集計 ---
    hook_counts = defaultdict(lambda: {"count": 0, "avg_er": 0.0, "examples": []})
    for p in buzz_posts:
        hp = p["hook_pattern"]
        hook_counts[hp]["count"] += 1
        hook_counts[hp]["avg_er"] += p["pseudo_er"]
        if len(hook_counts[hp]["examples"]) < 3:
            hook_counts[hp]["examples"].append({
                "handle":    p["handle"],
                "first_line": p["first_line"],
                "likes":     p["likes"],
                "pseudo_er": round(p["pseudo_er"], 4),
            })

    hook_summary = {}
    for hp, v in hook_counts.items():
        cnt = v["count"]
        hook_summary[hp] = {
            "count":    cnt,
            "avg_er":   round(v["avg_er"] / cnt, 4) if cnt else 0,
            "examples": v["examples"],
        }
    # avg_er 降順でソート
    hook_summary = dict(
        sorted(hook_summary.items(), key=lambda x: x[1]["avg_er"], reverse=True)
    )

    # --- 構造別集計 ---
    structure_counts = defaultdict(lambda: {"count": 0, "avg_er": 0.0, "examples": []})
    for p in buzz_posts:
        st = p["structure"]
        structure_counts[st]["count"] += 1
        structure_counts[st]["avg_er"] += p["pseudo_er"]
        if len(structure_counts[st]["examples"]) < 2:
            structure_counts[st]["examples"].append({
                "handle":    p["handle"],
                "text_preview": p["text"][:120].replace("\n", " "),
                "likes":     p["likes"],
                "pseudo_er": round(p["pseudo_er"], 4),
            })

    structure_summary = {}
    for st, v in structure_counts.items():
        cnt = v["count"]
        structure_summary[st] = {
            "count":    cnt,
            "avg_er":   round(v["avg_er"] / cnt, 4) if cnt else 0,
            "examples": v["examples"],
        }
    structure_summary = dict(
        sorted(structure_summary.items(), key=lambda x: x[1]["avg_er"], reverse=True)
    )

    # --- 出力用：投稿は必要なフィールドのみに絞る ---
    def slim(p):
        return {
            "handle":     p["handle"],
            "category":   p["category"],
            "first_line": p["first_line"],
            "text":       p["text"][:300],
            "likes":      p["likes"],
            "replies":    p["replies"],
            "pseudo_er":  round(p["pseudo_er"], 4),
            "hook_pattern": p["hook_pattern"],
            "structure":    p["structure"],
        }

    output = {
        "generated_at":       datetime.now(JST).isoformat(),
        "source_accounts":    len(accounts_raw),
        "total_posts":        len(all_posts),
        "buzz_posts_count":   len(buzz_posts),
        "filters": {
            "min_pseudo_er": MIN_PSEUDO_ER,
            "min_likes":     MIN_LIKES,
        },
        "hook_pattern_summary":    hook_summary,
        "structure_summary":       structure_summary,
        "top_overall": [slim(p) for p in top_overall],
        "top_by_category": {
            cat: [slim(p) for p in posts]
            for cat, posts in top_by_category.items()
        },
        "writer_guidance": _build_writer_guidance(hook_summary, structure_summary, top_overall),
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # ── トレンド用スナップショット保存 ──
    trend_path = os.path.join(PROJECT_DIR, "state", "competitor-buzz-trend.json")
    trend_data = {"snapshots": []}
    if os.path.exists(trend_path):
        with open(trend_path, "r", encoding="utf-8") as f:
            trend_data = json.load(f)

    today_str = datetime.now(JST).strftime("%Y-%m-%d")
    # 同日のスナップショットは上書き
    snapshots = [s for s in trend_data.get("snapshots", []) if s.get("date") != today_str]
    snapshots.append({
        "date": today_str,
        "hook_pattern_summary": {
            k: {"count": v["count"], "avg_er": v["avg_er"]}
            for k, v in hook_summary.items()
        },
    })
    snapshots = snapshots[-30:]  # 直近30日分のみ保持
    trend_data["snapshots"] = snapshots
    with open(trend_path, "w", encoding="utf-8") as f:
        json.dump(trend_data, f, ensure_ascii=False, indent=2)
    print(f"[INFO] トレンドスナップショット保存: {today_str}（累計{len(snapshots)}日分）")

    print(f"[INFO] 保存完了: {OUTPUT_PATH}")
    print(f"[INFO] バズ投稿トップ3:")
    for i, p in enumerate(top_overall[:3], 1):
        print(f"  {i}. ER={p['pseudo_er']:.4f} @{p['handle']} [{p['hook_pattern']}]")
        print(f"     {p['first_line'][:60]}")
    print(f"[INFO] フックパターン上位3:")
    for hp, v in list(hook_summary.items())[:3]:
        print(f"  {hp}: avg_er={v['avg_er']:.4f} ({v['count']}件)")


def _build_writer_guidance(hook_summary, structure_summary, top_posts) -> dict:
    """ライターへの示唆サマリを生成"""
    top_hooks = list(hook_summary.keys())[:5]
    top_structures = list(structure_summary.keys())[:3]

    # ER > 0.1 のバズ投稿からフック文例を抽出
    strong_examples = [
        p["first_line"] for p in top_posts
        if p["pseudo_er"] >= 0.1
    ][:10]

    # カテゴリ横断の最高ER投稿から本文例を抽出
    text_examples = [
        {
            "hook_pattern": p["hook_pattern"],
            "first_line":   p["first_line"],
            "text_excerpt": p["text"][:200].replace("\n", " "),
            "pseudo_er":    round(p["pseudo_er"], 4),
        }
        for p in top_posts[:5]
    ]

    top_hook_label = top_hooks[0] if top_hooks else "不明"
    top_hook_er    = hook_summary.get(top_hook_label, {}).get("avg_er", 0)
    top_struct_label = top_structures[0] if top_structures else "不明"

    return {
        "top_hook_patterns_by_er": top_hooks,
        "top_structures_by_er":    top_structures,
        "strong_first_line_examples": strong_examples,
        "top_post_examples":       text_examples,
        "insight": (
            f"競合{len(top_posts)}件分析結果: "
            f"最高ERフックは「{top_hook_label}」(avg_er={top_hook_er:.4f})。"
            f"最多バズ構造は「{top_struct_label}」。"
            f"いいね強要・スルー恐怖・ランキング型が特に高拡散。"
            f"ただしNG表現（断定・祈祷等）に抵触しないよう要注意。"
        ),
    }


if __name__ == "__main__":
    main()
