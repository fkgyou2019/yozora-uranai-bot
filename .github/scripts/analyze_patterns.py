#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
パターン分析: performance-data.json から勝ちパターンを抽出
state/winning-patterns.json に出力 → generate_posts.py が参照

【設計方針】
winning-patterns.json は2層構造:
  - permanent_rules: 人間が書いた固定ルール（上書き禁止）
  - auto_analysis:   このスクリプトが毎回上書きする自動集計結果

generate_posts.py の build_learning_block() は両方を参照する。
"""

import json
import os
import re
from datetime import datetime, timezone, timedelta
from collections import defaultdict

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


# =====================================================================
# パターン名が空("")の投稿をコンテンツから推定分類する
# =====================================================================
CONTENT_PATTERN_RULES = [
    # (正規表現, 推定パターン名)
    (r"(気をつけた方がいい|要注意|注意が必要|気をつけて)", "注意喚起+限定型"),
    (r"(たった[1-9２-９一二三四五六七八九十]+つだけ|12星座中.*[1-9]つ)", "数字+限定型"),
    (r"(TOP3|TOP5|ランキング|1位|🥇)", "ランキング型"),
    (r"(モヤモヤ|ざわざわ|していませんか|していません？)", "共感×質問型"),
    (r"(水星逆行|木星|土星|満月|新月|天体|星座の動き)", "天文イベント型"),
    (r"(明日.*届け|来週.*お伝え|シリーズ|予告)", "予告型"),
    (r"(正直に言います|実は.*占い師|ぶっちゃけ)", "告白・暴露型"),
    (r"(カードを.*選んで|引かれるカード|🌕|🌹)", "カード選択型"),
    (r"(木星の優しい光|土星が.*微笑|春の陽射し|応援メッセージ)", "励まし型【禁止】"),
    (r"(来週.*仕事|仕事で.*大きな話|昇進|転機)", "注意喚起+限定型"),
]

def infer_pattern(post):
    """pattern_nameが空の投稿をコンテンツから推定する"""
    name = post.get("pattern_name", "").strip()
    if name:
        return name
    content = post.get("content_preview", "") + " " + post.get("first_line", "")
    for pattern, label in CONTENT_PATTERN_RULES:
        if re.search(pattern, content):
            return label + "（推定）"
    return "その他（推定）"


# =====================================================================
# 時間帯ラベル
# =====================================================================
def hour_to_slot(hour):
    if 6 <= hour <= 9:   return "朝（6〜9時台）"
    if 10 <= hour <= 12: return "午前（10〜12時台）"
    if 13 <= hour <= 15: return "午後（13〜15時台）"
    if 16 <= hour <= 18: return "夕方（16〜18時台）"
    if 19 <= hour <= 21: return "夜（19〜21時台）"
    return "深夜（22〜0時台）"


def main():
    perf = load_json("state/performance-data.json")
    posts = perf.get("posts", [])

    # === 既存の winning-patterns.json を読み込み、permanent_rules を保持 ===
    existing = load_json("state/winning-patterns.json")
    permanent_rules = existing.get("permanent_rules", {})

    if len(posts) < 3:
        print(f"データ不足: {len(posts)}件（最低3件必要）")
        result = dict(existing)
        result["auto_analysis"] = {
            "data_count": len(posts),
            "last_analyzed": datetime.now(JST).isoformat(),
            "confidence": "low",
            "note": "データ不足のため自動分析スキップ",
        }
        save_json("state/winning-patterns.json", result)
        return

    print(f"分析対象: {len(posts)}件")

    # =====================================================================
    # 1. パターン推定（空欄を補完）
    # =====================================================================
    for p in posts:
        p["_inferred_pattern"] = infer_pattern(p)

    # =====================================================================
    # 2. パターン別集計
    # =====================================================================
    pattern_stats = defaultdict(lambda: {
        "count": 0, "total_eng": 0, "total_views": 0,
        "total_likes": 0, "total_replies": 0
    })
    feature_stats = defaultdict(lambda: {"count": 0, "total_eng": 0})
    best_posts = []

    for p in posts:
        eng    = p.get("metrics", {}).get("engagement_rate", 0)
        views  = p.get("metrics", {}).get("views", 0)
        likes  = p.get("metrics", {}).get("likes", 0)
        replies = p.get("metrics", {}).get("replies", 0)
        pattern = p["_inferred_pattern"]
        features = p.get("features", {})

        ps = pattern_stats[pattern]
        ps["count"]        += 1
        ps["total_eng"]    += eng
        ps["total_views"]  += views
        ps["total_likes"]  += likes
        ps["total_replies"] += replies

        for feat, val in features.items():
            if val and feat != "hook_length":
                feature_stats[feat]["count"]     += 1
                feature_stats[feat]["total_eng"] += eng

        best_posts.append({
            "id":              p["id"],
            "pattern":         pattern,
            "eng_rate":        eng,
            "views":           views,
            "likes":           likes,
            "replies":         replies,
            "first_line":      p.get("first_line", ""),
            "content_preview": p.get("content_preview", ""),
        })

    best_posts.sort(key=lambda x: x["eng_rate"], reverse=True)

    # =====================================================================
    # 3. パターン別ランキング
    # =====================================================================
    pattern_ranking = []
    for pat, s in pattern_stats.items():
        n = s["count"]
        pattern_ranking.append({
            "pattern":        pat,
            "count":          n,
            "avg_engagement": round(s["total_eng"] / n, 2) if n else 0,
            "avg_views":      round(s["total_views"] / n, 0) if n else 0,
            "total_likes":    s["total_likes"],
            "total_replies":  s["total_replies"],
        })
    pattern_ranking.sort(key=lambda x: x["avg_engagement"], reverse=True)

    # 特徴別
    feature_ranking = []
    for feat, s in feature_stats.items():
        n = s["count"]
        feature_ranking.append({
            "feature":        feat,
            "count":          n,
            "avg_engagement": round(s["total_eng"] / n, 2) if n else 0,
        })
    feature_ranking.sort(key=lambda x: x["avg_engagement"], reverse=True)

    # top_patterns（上位5件。重みを配分）
    total_eng = sum(p["avg_engagement"] for p in pattern_ranking) or 1
    top_patterns = []
    for pr in pattern_ranking[:5]:
        weight = max(5, int(pr["avg_engagement"] / total_eng * 100))
        top_patterns.append({
            "pattern":        pr["pattern"],
            "weight":         weight,
            "avg_engagement": pr["avg_engagement"],
            "avg_views":      pr["avg_views"],
            "sample_count":   pr["count"],
            "reason":         f"eng率{pr['avg_engagement']:.1f}%・閲覧{pr['avg_views']:.0f}（{pr['count']}件）",
        })

    # =====================================================================
    # 4. 時間帯別集計
    # =====================================================================
    hourly_stats = defaultdict(lambda: {"count": 0, "total_eng": 0, "total_views": 0})
    for p in posts:
        posted_at = p.get("posted_at", "")
        if not posted_at:
            continue
        try:
            dt = datetime.fromisoformat(posted_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            hour = dt.astimezone(JST).hour
            hourly_stats[hour]["count"]      += 1
            hourly_stats[hour]["total_eng"]  += p.get("metrics", {}).get("engagement_rate", 0)
            hourly_stats[hour]["total_views"] += p.get("metrics", {}).get("views", 0)
        except Exception:
            continue

    hourly_data = {}
    for h, s in sorted(hourly_stats.items()):
        n = s["count"]
        if n > 0:
            hourly_data[str(h)] = {
                "slot":      hour_to_slot(h),
                "avg_eng":   round(s["total_eng"] / n, 2),
                "avg_views": round(s["total_views"] / n, 0),
                "count":     n,
            }

    # =====================================================================
    # 5. 時間帯×パターン 交差分析
    # =====================================================================
    cross_stats = defaultdict(lambda: defaultdict(lambda: {
        "count": 0, "total_eng": 0, "total_views": 0
    }))
    for p in posts:
        posted_at = p.get("posted_at", "")
        if not posted_at:
            continue
        try:
            dt = datetime.fromisoformat(posted_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            slot = hour_to_slot(dt.astimezone(JST).hour)
            pat  = p["_inferred_pattern"]
            cross_stats[slot][pat]["count"]      += 1
            cross_stats[slot][pat]["total_eng"]  += p.get("metrics", {}).get("engagement_rate", 0)
            cross_stats[slot][pat]["total_views"] += p.get("metrics", {}).get("views", 0)
        except Exception:
            continue

    # 各時間帯のベストパターン
    slot_best_pattern = {}
    for slot, pats in cross_stats.items():
        best_pat = max(
            pats.items(),
            key=lambda x: x[1]["total_eng"] / x[1]["count"] if x[1]["count"] else 0
        )
        n = best_pat[1]["count"]
        slot_best_pattern[slot] = {
            "best_pattern": best_pat[0],
            "avg_eng":      round(best_pat[1]["total_eng"] / n, 2) if n else 0,
            "avg_views":    round(best_pat[1]["total_views"] / n, 0) if n else 0,
            "sample_count": n,
            "note":         "※N=1は参考値" if n == 1 else "",
        }

    # =====================================================================
    # 6. 避けるべきパターン（自動検出 + permanent_rulesのものを保持）
    # =====================================================================
    auto_avoid = []
    for pr in pattern_ranking:
        if pr["avg_engagement"] < 5.0 and pr["count"] >= 2:
            auto_avoid.append(
                f"【avg_views:{pr['avg_views']:.0f}・ER:{pr['avg_engagement']:.1f}%・{pr['count']}件】{pr['pattern']}"
            )

    # permanent_rules の avoid_patterns と統合（重複なし）
    fixed_avoid = permanent_rules.get("avoid_patterns", [])

    # =====================================================================
    # 7. インサイト生成
    # =====================================================================
    insights = []
    if pattern_ranking:
        bp = pattern_ranking[0]
        insights.append(
            f"最強パターン: {bp['pattern']}（eng率{bp['avg_engagement']:.1f}%・閲覧{bp['avg_views']:.0f}・{bp['count']}件）"
        )
    for fr in feature_ranking[:3]:
        label = {
            "has_ranking":    "ランキング形式",
            "has_number_hook":"数字フック",
            "has_question":   "質問文",
            "has_cta_emoji":  "絵文字CTA",
            "has_fear_hook":  "恐怖・焦りフック",
        }.get(fr["feature"], fr["feature"])
        insights.append(f"{label}あり → avg eng {fr['avg_engagement']:.1f}%")

    # 時間帯ベストを追記
    for slot, info in slot_best_pattern.items():
        if info["sample_count"] >= 2:
            insights.append(
                f"{slot}: 最強パターン={info['best_pattern']}（eng{info['avg_eng']:.1f}%・閲覧{info['avg_views']:.0f}）"
            )

    if auto_avoid:
        insights.append(f"自動検出・低パフォーマンスパターン: {len(auto_avoid)}件")

    # optimal hours（閲覧数ベース、2件以上優先）
    sorted_hours = sorted(
        [(int(h), v) for h, v in hourly_data.items()],
        key=lambda x: (x[1]["count"] >= 2, x[1]["avg_views"]),
        reverse=True,
    )
    optimal_hours = [h for h, _ in sorted_hours[:3]] if sorted_hours else []

    confidence = "low" if len(posts) < 10 else ("medium" if len(posts) < 30 else "high")

    # =====================================================================
    # 8. 保存 — permanent_rules は一切触らず、auto_analysis だけ上書き
    # =====================================================================
    result = dict(existing)  # 既存データをベースにする
    result["auto_analysis"] = {
        "data_count":       len(posts),
        "last_analyzed":    datetime.now(JST).isoformat(),
        "confidence":       confidence,
        "top_patterns":     top_patterns,
        "pattern_ranking":  pattern_ranking,
        "feature_ranking":  feature_ranking,
        "top_hook_styles":  [{"style": b["pattern"], "example": b["first_line"], "avg_eng": b["eng_rate"]} for b in best_posts[:5]],
        "best_posts":       best_posts[:5],
        "auto_avoid_patterns": auto_avoid,
        "hourly_data":      hourly_data,
        "slot_best_pattern":slot_best_pattern,
        "optimal_hours":    optimal_hours,
        "insights":         insights,
    }
    # 後方互換: トップレベルにも主要フィールドを残す（generate_posts.pyが参照）
    result["data_count"]      = len(posts)
    result["last_analyzed"]   = result["auto_analysis"]["last_analyzed"]
    result["confidence"]      = confidence
    result["top_patterns"]    = top_patterns
    result["pattern_ranking"] = pattern_ranking
    result["feature_ranking"] = feature_ranking
    result["best_posts"]      = best_posts[:5]
    result["insights"]        = insights
    result["hourly_data"]     = hourly_data
    result["optimal_hours"]   = optimal_hours
    # avoid_patterns = 固定ルール + 自動検出（重複除去）
    combined_avoid = list(fixed_avoid)
    for a in auto_avoid:
        if a not in combined_avoid:
            combined_avoid.append(a)
    result["avoid_patterns"]  = combined_avoid

    save_json("state/winning-patterns.json", result)

    print("\n=== 分析結果 ===")
    for ins in insights:
        print(f"  💡 {ins}")
    print(f"\n信頼度: {confidence}（{len(posts)}件）")
    if optimal_hours:
        print(f"最適投稿時間帯（閲覧数基準）: {', '.join(str(h)+'時' for h in optimal_hours)}")
    print(f"\n時間帯×パターン 交差分析:")
    for slot, info in slot_best_pattern.items():
        note = info.get("note", "")
        print(f"  {slot}: {info['best_pattern']} (eng{info['avg_eng']:.1f}% {note})")
    print("\nstate/winning-patterns.json を更新しました（permanent_rules は保持）")


if __name__ == "__main__":
    main()
