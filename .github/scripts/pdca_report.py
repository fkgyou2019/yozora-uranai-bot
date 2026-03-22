#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDCA自動レポート: 毎日のパフォーマンスを分析し、改善提案を生成
state/pdca-reports/ に日次レポートを蓄積
state/winning-patterns.json を更新して generate_posts.py に反映
"""

import json
import os
import sys
import urllib.request
import urllib.error
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


def threads_get(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all_post_metrics(user_id, access_token):
    """全投稿のメトリクスを取得"""
    url = (
        f"https://graph.threads.net/v1.0/{user_id}/threads"
        f"?fields=id,text,timestamp&limit=25&access_token={access_token}"
    )
    try:
        data = threads_get(url)
    except Exception as e:
        print(f"投稿取得エラー: {e}")
        return []

    results = []
    for p in data.get("data", []):
        pid = p["id"]
        text = p.get("text", "")
        ts = p.get("timestamp", "")

        utc_time = datetime.fromisoformat(ts.replace("+0000", "+00:00"))
        jst_time = utc_time.astimezone(JST)

        insight_url = (
            f"https://graph.threads.net/v1.0/{pid}/insights"
            f"?metric=views,likes,replies,reposts,quotes&access_token={access_token}"
        )
        try:
            ins = threads_get(insight_url)
            metrics = {}
            for m in ins.get("data", []):
                metrics[m["name"]] = m.get("values", [{}])[0].get("value", 0)

            views = metrics.get("views", 0)
            likes = metrics.get("likes", 0)
            replies = metrics.get("replies", 0)
            reposts = metrics.get("reposts", 0)
            eng = ((likes + replies) / views * 100) if views > 0 else 0

            lines = text.split("\n")
            first_line = lines[0] if lines else ""
            non_empty = [l for l in lines if l.strip()]

            results.append({
                "id": pid,
                "text": text,
                "first_line": first_line[:30],
                "timestamp": jst_time.isoformat(),
                "hour": jst_time.hour,
                "weekday": jst_time.strftime("%a"),
                "views": views,
                "likes": likes,
                "replies": replies,
                "reposts": reposts,
                "eng_rate": round(eng, 2),
                "char_count": len(text),
                "line_count": len(lines),
                "avg_line_len": round(sum(len(l) for l in non_empty) / len(non_empty), 1) if non_empty else 0,
                "has_ranking": "🥇" in text or "第1位" in text or "TOP" in text,
                "has_cta": any(w in text for w in ["を置", "で受け取", "をコメント"]),
                "has_question": "？" in text,
                "has_number_hook": any(c.isdigit() for c in first_line[:15]),
                "has_follow_cta": "フォロー" in text,
                "has_hashtag": "#" in text,
            })
        except Exception:
            continue

    return results


def analyze_trends(results, prev_report):
    """トレンド分析: 前回レポートとの比較"""
    if not results:
        return {}

    total_views = sum(r["views"] for r in results)
    total_likes = sum(r["likes"] for r in results)
    total_replies = sum(r["replies"] for r in results)
    avg_eng = sum(r["eng_rate"] for r in results) / len(results)
    avg_views = total_views / len(results)

    # 前回との比較
    prev_avg_eng = prev_report.get("summary", {}).get("avg_eng_rate", 0)
    prev_avg_views = prev_report.get("summary", {}).get("avg_views", 0)
    eng_trend = "↑" if avg_eng > prev_avg_eng else ("↓" if avg_eng < prev_avg_eng else "→")
    views_trend = "↑" if avg_views > prev_avg_views else ("↓" if avg_views < prev_avg_views else "→")

    # 特徴別のエンゲージメント比較
    feature_analysis = {}
    for feature in ["has_ranking", "has_cta", "has_question", "has_number_hook", "has_follow_cta"]:
        with_feat = [r for r in results if r.get(feature)]
        without_feat = [r for r in results if not r.get(feature)]
        if with_feat and without_feat:
            avg_with = sum(r["eng_rate"] for r in with_feat) / len(with_feat)
            avg_without = sum(r["eng_rate"] for r in without_feat) / len(without_feat)
            feature_analysis[feature] = {
                "with_count": len(with_feat),
                "without_count": len(without_feat),
                "avg_eng_with": round(avg_with, 2),
                "avg_eng_without": round(avg_without, 2),
                "lift": round(avg_with - avg_without, 2),
            }

    # 時間帯別
    hourly = defaultdict(lambda: {"count": 0, "total_eng": 0, "total_views": 0})
    for r in results:
        h = hourly[r["hour"]]
        h["count"] += 1
        h["total_eng"] += r["eng_rate"]
        h["total_views"] += r["views"]

    best_hour = max(hourly.items(), key=lambda x: x[1]["total_eng"] / x[1]["count"] if x[1]["count"] > 0 else 0) if hourly else (0, {})
    worst_hour = min(hourly.items(), key=lambda x: x[1]["total_eng"] / x[1]["count"] if x[1]["count"] > 0 else 999) if hourly else (0, {})

    # ベスト・ワースト投稿
    sorted_by_eng = sorted(results, key=lambda x: x["eng_rate"], reverse=True)
    sorted_by_views = sorted(results, key=lambda x: x["views"], reverse=True)

    return {
        "summary": {
            "total_posts": len(results),
            "total_views": total_views,
            "total_likes": total_likes,
            "total_replies": total_replies,
            "avg_eng_rate": round(avg_eng, 2),
            "avg_views": round(avg_views, 0),
            "eng_trend": eng_trend,
            "views_trend": views_trend,
        },
        "feature_analysis": feature_analysis,
        "best_post": {
            "first_line": sorted_by_eng[0]["first_line"] if sorted_by_eng else "",
            "eng_rate": sorted_by_eng[0]["eng_rate"] if sorted_by_eng else 0,
            "views": sorted_by_eng[0]["views"] if sorted_by_eng else 0,
        },
        "worst_post": {
            "first_line": sorted_by_eng[-1]["first_line"] if sorted_by_eng else "",
            "eng_rate": sorted_by_eng[-1]["eng_rate"] if sorted_by_eng else 0,
            "views": sorted_by_eng[-1]["views"] if sorted_by_eng else 0,
        },
        "most_viewed": {
            "first_line": sorted_by_views[0]["first_line"] if sorted_by_views else "",
            "views": sorted_by_views[0]["views"] if sorted_by_views else 0,
        },
        "best_hour": best_hour[0] if hourly else None,
        "worst_hour": worst_hour[0] if hourly else None,
        "hourly_data": {str(k): {"avg_eng": round(v["total_eng"] / v["count"], 2), "avg_views": round(v["total_views"] / v["count"], 0), "count": v["count"]} for k, v in sorted(hourly.items())},
    }


def generate_improvements(analysis, results):
    """データに基づく改善提案を自動生成"""
    improvements = []

    feat = analysis.get("feature_analysis", {})

    # CTA効果
    cta = feat.get("has_cta", {})
    if cta and cta.get("lift", 0) > 3:
        improvements.append(f"CTAあり投稿のeng率が{cta['lift']:.1f}%高い。全投稿にCTAを入れる方針を継続。")
    elif cta and cta.get("lift", 0) < 0:
        improvements.append(f"CTAあり投稿のeng率が低い。CTAの文面を変更すべき。")

    # ランキング効果
    rank = feat.get("has_ranking", {})
    if rank and rank.get("lift", 0) > 3:
        improvements.append(f"ランキング型のeng率が{rank['lift']:.1f}%高い。配分を増やすべき。")

    # 質問効果
    q = feat.get("has_question", {})
    if q and q.get("lift", 0) > 2:
        improvements.append(f"質問あり投稿のeng率が{q['lift']:.1f}%高い。質問を増やすべき。")

    # フォロー導線効果
    follow = feat.get("has_follow_cta", {})
    if follow and follow.get("with_count", 0) >= 2:
        if follow.get("lift", 0) > 0:
            improvements.append(f"フォロー導線あり投稿のeng率が{follow['lift']:.1f}%高い。効果あり。")
        else:
            improvements.append(f"フォロー導線がeng率を下げている可能性。文面を改善すべき。")

    # 平均行長チェック
    avg_line_lens = [r["avg_line_len"] for r in results if r["avg_line_len"] > 0]
    if avg_line_lens:
        overall_avg = sum(avg_line_lens) / len(avg_line_lens)
        if overall_avg < 12:
            improvements.append(f"平均行長{overall_avg:.0f}文字。短すぎ。15-20文字を目指すべき。")
        elif overall_avg > 22:
            improvements.append(f"平均行長{overall_avg:.0f}文字。長すぎ。スマホで折り返しが発生している可能性。")

    # 投稿数
    summary = analysis.get("summary", {})
    if summary.get("total_posts", 0) < 8:
        improvements.append(f"投稿数{summary['total_posts']}件。1日8件を目標に。")

    if not improvements:
        improvements.append("現状のパフォーマンスは良好。現在の戦略を継続。")

    return improvements


def main():
    access_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    user_id = os.environ.get("THREADS_USER_ID", "")

    if not access_token or not user_id:
        env_path = os.path.join(PROJECT_DIR, "config", "api-keys.env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip()
                        if k == "THREADS_ACCESS_TOKEN" and not access_token:
                            access_token = v
                        elif k == "THREADS_USER_ID" and not user_id:
                            user_id = v

    if not access_token or not user_id:
        print("ERROR: THREADS_ACCESS_TOKEN / THREADS_USER_ID が未設定")
        sys.exit(1)

    now = datetime.now(JST)
    today_str = now.strftime("%Y-%m-%d")

    print(f"=== PDCA日次レポート {today_str} ===\n")

    # 全投稿メトリクス取得
    results = fetch_all_post_metrics(user_id, access_token)
    print(f"取得した投稿: {len(results)}件\n")

    # 前回のレポートを読み込み
    prev_report = load_json("state/pdca-latest.json")

    # トレンド分析
    analysis = analyze_trends(results, prev_report)

    # 改善提案
    improvements = generate_improvements(analysis, results)

    # レポート出力
    summary = analysis.get("summary", {})
    print(f"【サマリー】")
    print(f"  投稿数: {summary.get('total_posts', 0)}件")
    print(f"  合計閲覧: {summary.get('total_views', 0)}")
    print(f"  合計いいね: {summary.get('total_likes', 0)}")
    print(f"  合計コメント: {summary.get('total_replies', 0)}")
    print(f"  平均eng率: {summary.get('avg_eng_rate', 0):.1f}% {summary.get('eng_trend', '')}")
    print(f"  平均閲覧: {summary.get('avg_views', 0):.0f} {summary.get('views_trend', '')}")

    print(f"\n【ベスト投稿】")
    best = analysis.get("best_post", {})
    print(f"  {best.get('first_line', '')} | eng={best.get('eng_rate', 0):.1f}% | 👁{best.get('views', 0)}")

    print(f"\n【ワースト投稿】")
    worst = analysis.get("worst_post", {})
    print(f"  {worst.get('first_line', '')} | eng={worst.get('eng_rate', 0):.1f}% | 👁{worst.get('views', 0)}")

    print(f"\n【特徴別分析】")
    feat = analysis.get("feature_analysis", {})
    label_map = {
        "has_ranking": "ランキング型",
        "has_cta": "CTA（絵文字置く）",
        "has_question": "質問あり",
        "has_number_hook": "数字フック",
        "has_follow_cta": "フォロー導線",
    }
    for k, v in feat.items():
        label = label_map.get(k, k)
        print(f"  {label}: あり={v['avg_eng_with']:.1f}% ({v['with_count']}件) / なし={v['avg_eng_without']:.1f}% ({v['without_count']}件) → 差{v['lift']:+.1f}%")

    print(f"\n【時間帯別】")
    for hour, data in sorted(analysis.get("hourly_data", {}).items()):
        print(f"  {hour}時: eng={data['avg_eng']:.1f}% views={data['avg_views']:.0f} ({data['count']}件)")

    print(f"\n【改善提案】")
    for imp in improvements:
        print(f"  💡 {imp}")

    # レポートを保存
    report = {
        "date": today_str,
        "generated_at": now.isoformat(),
        "summary": summary,
        "feature_analysis": feat,
        "best_post": best,
        "worst_post": worst,
        "hourly_data": analysis.get("hourly_data", {}),
        "improvements": improvements,
        "post_count": len(results),
    }

    # 日次レポートを蓄積
    save_json(f"state/pdca-reports/{today_str}.json", report)
    # 最新レポート（次回比較用）
    save_json("state/pdca-latest.json", report)

    # 累積レポートインデックス更新
    index = load_json("state/pdca-reports/index.json")
    if not index:
        index = {"reports": []}
    if today_str not in index["reports"]:
        index["reports"].append(today_str)
    index["reports"] = index["reports"][-90:]  # 直近90日分保持
    save_json("state/pdca-reports/index.json", index)

    print(f"\nレポート保存: state/pdca-reports/{today_str}.json")


if __name__ == "__main__":
    main()
