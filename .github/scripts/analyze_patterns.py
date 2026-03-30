#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
パターン分析: performance-data.json から勝ちパターンを抽出
state/winning-patterns.json に出力 → generate_posts.py が参照
日を追うごとにデータが蓄積され、精度が上がる
"""

import json
import os
import sys
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


def main():
    perf = load_json("state/performance-data.json")
    posts = perf.get("posts", [])

    if len(posts) < 3:
        print(f"データ不足: {len(posts)}件（最低3件必要）。デフォルトパターンを使用。")
        # デフォルト（バズ投稿分析から導出した初期値）
        default = {
            "data_count": len(posts),
            "last_analyzed": datetime.now(JST).isoformat(),
            "confidence": "low",
            "top_patterns": [
                {"pattern": "ランキング型", "weight": 40, "reason": "初期値（市場分析から）"},
                {"pattern": "数字インパクト型", "weight": 25, "reason": "初期値"},
                {"pattern": "コメント誘導型", "weight": 20, "reason": "初期値"},
                {"pattern": "星座別アドバイス型", "weight": 15, "reason": "初期値"},
            ],
            "top_hook_styles": [
                {"style": "恐怖×期待型", "example": "○月に人生が変わる星座。", "avg_eng": 0},
                {"style": "限定型", "example": "12星座中、たった2つだけ。", "avg_eng": 0},
                {"style": "ランキング見出し型", "example": "【○○な星座TOP3】", "avg_eng": 0},
            ],
            "top_cta_styles": [
                {"style": "絵文字CTA", "example": "「🔮」を置いた方に届きます。", "avg_eng": 0},
            ],
            "avoid_patterns": [],
            "insights": [
                "データ蓄積中。10件以上でパターン分析が有効になります。",
            ],
        }
        save_json("state/winning-patterns.json", default)
        return

    # === 分析開始 ===
    print(f"分析対象: {len(posts)}件")

    # パターン別集計
    pattern_stats = defaultdict(lambda: {"count": 0, "total_eng": 0, "total_views": 0, "total_likes": 0, "total_replies": 0})
    feature_stats = defaultdict(lambda: {"count": 0, "total_eng": 0})

    best_posts = []

    for p in posts:
        eng = p.get("metrics", {}).get("engagement_rate", 0)
        views = p.get("metrics", {}).get("views", 0)
        likes = p.get("metrics", {}).get("likes", 0)
        replies = p.get("metrics", {}).get("replies", 0)
        pattern = p.get("pattern_name", "不明")
        features = p.get("features", {})

        # パターン別
        ps = pattern_stats[pattern]
        ps["count"] += 1
        ps["total_eng"] += eng
        ps["total_views"] += views
        ps["total_likes"] += likes
        ps["total_replies"] += replies

        # 特徴別
        for feat, val in features.items():
            if val and feat != "hook_length":
                fs = feature_stats[feat]
                fs["count"] += 1
                fs["total_eng"] += eng

        # ベスト投稿
        best_posts.append({
            "id": p["id"],
            "pattern": pattern,
            "eng_rate": eng,
            "views": views,
            "likes": likes,
            "replies": replies,
            "first_line": p.get("first_line", ""),
            "content_preview": p.get("content_preview", ""),
        })

    # ソート
    best_posts.sort(key=lambda x: x["eng_rate"], reverse=True)

    # パターン別の平均エンゲージメント率
    pattern_ranking = []
    for pat, stats in pattern_stats.items():
        avg_eng = stats["total_eng"] / stats["count"] if stats["count"] > 0 else 0
        avg_views = stats["total_views"] / stats["count"] if stats["count"] > 0 else 0
        pattern_ranking.append({
            "pattern": pat,
            "count": stats["count"],
            "avg_engagement": round(avg_eng, 2),
            "avg_views": round(avg_views, 0),
            "total_likes": stats["total_likes"],
            "total_replies": stats["total_replies"],
        })
    pattern_ranking.sort(key=lambda x: x["avg_engagement"], reverse=True)

    # 特徴別
    feature_ranking = []
    for feat, stats in feature_stats.items():
        avg_eng = stats["total_eng"] / stats["count"] if stats["count"] > 0 else 0
        feature_ranking.append({
            "feature": feat,
            "count": stats["count"],
            "avg_engagement": round(avg_eng, 2),
        })
    feature_ranking.sort(key=lambda x: x["avg_engagement"], reverse=True)

    # 重み配分計算（上位パターンに多く配分）
    total_eng = sum(p["avg_engagement"] for p in pattern_ranking) or 1
    top_patterns = []
    for pr in pattern_ranking[:5]:
        weight = int((pr["avg_engagement"] / total_eng) * 100)
        weight = max(5, weight)  # 最低5%
        top_patterns.append({
            "pattern": pr["pattern"],
            "weight": weight,
            "avg_engagement": pr["avg_engagement"],
            "sample_count": pr["count"],
            "reason": f"eng率{pr['avg_engagement']:.1f}% ({pr['count']}件)",
        })

    # フックスタイル分析（ベスト投稿から抽出）
    top_hook_styles = []
    for bp in best_posts[:5]:
        top_hook_styles.append({
            "style": bp["pattern"],
            "example": bp["first_line"],
            "avg_eng": bp["eng_rate"],
        })

    # 避けるべきパターン（エンゲージメント率が低い）
    avoid = [pr["pattern"] for pr in pattern_ranking if pr["avg_engagement"] < 2.0 and pr["count"] >= 2]

    # ===== 投稿時間帯分析 =====
    # performance-data.json の posted_at から時間帯別エンゲージメントを集計
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
            eng = p.get("metrics", {}).get("engagement_rate", 0)
            views = p.get("metrics", {}).get("views", 0)
            hourly_stats[hour]["count"] += 1
            hourly_stats[hour]["total_eng"] += eng
            hourly_stats[hour]["total_views"] += views
        except Exception:
            continue

    hourly_data = {}
    for h, s in sorted(hourly_stats.items()):
        n = s["count"]
        if n > 0:
            hourly_data[str(h)] = {
                "avg_eng": round(s["total_eng"] / n, 2),
                "avg_views": round(s["total_views"] / n, 0),
                "count": n,
            }

    # 上位3時間帯をoptimal_hoursとして抽出（2件以上データがある時間帯を優先）
    sorted_hours = sorted(
        [(int(h), v) for h, v in hourly_data.items()],
        key=lambda x: (x[1]["count"] >= 2, x[1]["avg_eng"]),
        reverse=True,
    )
    optimal_hours = [h for h, _ in sorted_hours[:3]] if sorted_hours else []

    # インサイト生成
    insights = []
    if pattern_ranking:
        best_pat = pattern_ranking[0]
        insights.append(f"最強パターン: {best_pat['pattern']}（eng率{best_pat['avg_engagement']:.1f}%）")
    if feature_ranking:
        for fr in feature_ranking[:3]:
            label = {
                "has_ranking": "ランキング形式",
                "has_number_hook": "数字フック",
                "has_question": "質問文",
                "has_cta_emoji": "絵文字CTA",
                "has_fear_hook": "恐怖・焦りフック",
            }.get(fr["feature"], fr["feature"])
            insights.append(f"{label}あり → avg eng {fr['avg_engagement']:.1f}%")
    if optimal_hours:
        insights.append(f"最適投稿時間帯: {', '.join(str(h)+'時' for h in optimal_hours)}")
    if avoid:
        insights.append(f"避けるべき: {', '.join(avoid)}")

    confidence = "low" if len(posts) < 10 else ("medium" if len(posts) < 30 else "high")

    result = {
        "data_count": len(posts),
        "last_analyzed": datetime.now(JST).isoformat(),
        "confidence": confidence,
        "top_patterns": top_patterns,
        "pattern_ranking": pattern_ranking,
        "feature_ranking": feature_ranking,
        "top_hook_styles": top_hook_styles,
        "top_cta_styles": [
            {"style": "絵文字CTA", "example": "「🔮」を置いた方に届きます。", "avg_eng": 0},
        ],
        "best_posts": best_posts[:5],
        "avoid_patterns": avoid,
        "insights": insights,
        "hourly_data": hourly_data,
        "optimal_hours": optimal_hours,
    }

    save_json("state/winning-patterns.json", result)

    print("\n=== 分析結果 ===")
    for ins in insights:
        print(f"  💡 {ins}")
    if hourly_data:
        print(f"\n時間帯別 (上位3時間帯): {', '.join(str(h)+'時' for h in optimal_hours)}")
    print(f"\n信頼度: {confidence}（{len(posts)}件）")
    print("state/winning-patterns.json を更新しました")


if __name__ == "__main__":
    main()
