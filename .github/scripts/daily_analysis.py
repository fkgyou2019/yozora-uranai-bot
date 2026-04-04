#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日次分析レポート生成スクリプト
毎朝7:07 JSTに daily-report.yml から実行される。
前日（JST）に投稿された全投稿のメトリクスを取得・分析し、
Markdown レポートを state/reports/YYYY-MM-DD.md に保存する。
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXPERIMENT_STRUCTURES = {"G", "A", "B", "C", "F"}

# 時間帯定義
TIME_SLOTS = [
    ("朝（6〜9時台）",   list(range(6, 10))),
    ("午前（10〜12時台）", list(range(10, 13))),
    ("午後（13〜15時台）", list(range(13, 16))),
    ("夕方（16〜18時台）", list(range(16, 19))),
    ("夜（19〜21時台）",   list(range(19, 22))),
    ("深夜（22〜0時台）",  [22, 23, 0]),
]


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def load_env():
    """config/api-keys.env を読み込んで環境変数にセット"""
    env_path = os.path.join(PROJECT_DIR, "config", "api-keys.env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and v and k not in os.environ:
                os.environ[k] = v


def load_json(rel_path):
    full = os.path.join(PROJECT_DIR, rel_path)
    if os.path.exists(full):
        with open(full, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def safe_avg(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def is_experiment_post(post):
    pattern = post.get("pattern_name", "")
    return any(f"構造{s}" in pattern for s in EXPERIMENT_STRUCTURES)


# ---------------------------------------------------------------------------
# Threads API
# ---------------------------------------------------------------------------

def fetch_post_insights(post_id, access_token):
    """
    GET /{post_id}/insights?metric=views,likes,replies,reposts,quotes
    成功: {"views": N, "likes": N, "replies": N, "reposts": N, "quotes": N}
    削除済み（400）や取得失敗: None を返す
    """
    url = (
        f"https://graph.threads.net/v1.0/{post_id}/insights"
        f"?metric=views,likes,replies,reposts,quotes"
        f"&access_token={access_token}"
    )
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        metrics = {}
        for m in data.get("data", []):
            metrics[m["name"]] = m.get("values", [{}])[0].get("value", 0)
        return metrics
    except urllib.error.HTTPError as e:
        if e.code == 400:
            print(f"  [SKIP] {post_id}: HTTP 400 (削除済みの可能性)", flush=True)
        else:
            print(f"  [WARN] {post_id}: HTTP {e.code}", flush=True)
        return None
    except Exception as e:
        print(f"  [WARN] {post_id}: {e}", flush=True)
        return None


# ---------------------------------------------------------------------------
# 投稿フィルタリング・分類
# ---------------------------------------------------------------------------

def get_yesterday_posts(history):
    """post-history.json から前日 JST 投稿を取得"""
    yesterday = (datetime.now(JST) - timedelta(days=1)).date()
    result = []
    for post in history.get("posts", []):
        platform_post_id = post.get("platform_post_id", "")
        if not platform_post_id:
            continue
        posted_at_str = post.get("posted_at", "")
        if not posted_at_str:
            continue
        try:
            post_time = datetime.fromisoformat(posted_at_str)
            if post_time.tzinfo is None:
                post_time = post_time.replace(tzinfo=JST)
            post_time_jst = post_time.astimezone(JST)
            if post_time_jst.date() == yesterday:
                result.append(post)
        except Exception:
            continue
    return result


def classify_time_slot(posted_at_str):
    """投稿時刻（JST）を時間帯ラベルに分類"""
    try:
        post_time = datetime.fromisoformat(posted_at_str)
        if post_time.tzinfo is None:
            post_time = post_time.replace(tzinfo=JST)
        hour = post_time.astimezone(JST).hour
    except Exception:
        return "その他"

    for label, hours in TIME_SLOTS:
        if hour in hours:
            return label
    return "その他"


def get_first_line(content):
    """投稿本文から最初の非空行を取得"""
    if not content:
        return ""
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


# ---------------------------------------------------------------------------
# ER 計算
# ---------------------------------------------------------------------------

def calc_er(views, likes, replies, reposts):
    if views and views > 0:
        return (likes + replies + reposts) / views * 100
    return 0.0


# ---------------------------------------------------------------------------
# Markdown レポート生成
# ---------------------------------------------------------------------------

def build_report(yesterday_str, collected, deleted_count, now_str, prev_summary=None, hc_delete_reasons=None, experiment_rate=None):
    """
    collected: list of dict {
        post, metrics, time_slot, er
    }
    """
    lines = []

    # タイトル
    try:
        dt = datetime.strptime(yesterday_str, "%Y-%m-%d")
        date_jp = f"{dt.year}年{dt.month:02d}月{dt.day:02d}日"
    except Exception:
        date_jp = yesterday_str

    lines.append(f"# 📊 投稿分析レポート: {date_jp}（前日分）")
    lines.append(f"生成日時: {now_str} JST")
    lines.append("")

    # -------------------------------------------------------------------
    # 全体サマリー
    # -------------------------------------------------------------------
    lines.append("## 📈 全体サマリー")

    total_posts = len(collected)
    if total_posts > 0:
        total_views = sum(c["metrics"]["views"] for c in collected)
        avg_views = total_views / total_posts
        avg_likes = safe_avg([c["metrics"]["likes"] for c in collected])
        avg_er = safe_avg([c["er"] for c in collected])
    else:
        total_views = avg_views = avg_likes = avg_er = 0

    # 前日比較
    delta_views_str = ""
    delta_er_str = ""
    if prev_summary:
        prev_avg_views = prev_summary.get("avg_views", 0)
        prev_avg_er = prev_summary.get("avg_er", 0)
        if prev_avg_views > 0 and avg_views > 0:
            dv = avg_views - prev_avg_views
            sign_v = "+" if dv >= 0 else ""
            delta_views_str = f"（前日比: {sign_v}{dv:,.1f}）"
        if prev_avg_er > 0 and avg_er > 0:
            de = avg_er - prev_avg_er
            sign_e = "+" if de >= 0 else ""
            delta_er_str = f"（前日比: {sign_e}{de:.2f}%）"

    lines.append("| 指標 | 値 |")
    lines.append("|------|-----|")
    lines.append(f"| 投稿数 | {total_posts}件 |")
    lines.append(f"| 総閲覧数 | {total_views:,} |")
    lines.append(f"| 平均閲覧数 | {avg_views:,.1f} {delta_views_str} |")
    lines.append(f"| 平均いいね | {avg_likes:.1f} |")
    lines.append(f"| 平均ER% | {avg_er:.2f}% {delta_er_str} |")
    lines.append(f"| 削除済み投稿 | {deleted_count}件 |")
    if experiment_rate is not None:
        exp_icon = "✅" if experiment_rate >= 80 else "⚠️" if experiment_rate >= 50 else "❌"
        lines.append(f"| EXPERIMENT遵守率 | {exp_icon} {experiment_rate:.0f}% |")
    lines.append("")

    # -------------------------------------------------------------------
    # 時間帯別パフォーマンス
    # -------------------------------------------------------------------
    lines.append("## 🕐 時間帯別パフォーマンス")
    lines.append("| 時間帯 | 件数 | 平均閲覧数 | 平均いいね | 平均ER% | 勝利パターン |")
    lines.append("|--------|------|------------|------------|---------|------------|")

    slot_order = [label for label, _ in TIME_SLOTS] + ["その他"]
    slot_data = defaultdict(list)
    for c in collected:
        slot_data[c["time_slot"]].append(c)

    for slot_label in slot_order:
        items = slot_data.get(slot_label, [])
        if not items:
            continue
        s_views = safe_avg([i["metrics"]["views"] for i in items])
        s_likes = safe_avg([i["metrics"]["likes"] for i in items])
        s_er = safe_avg([i["er"] for i in items])
        # ER最高のパターン名
        best = max(items, key=lambda x: x["er"])
        winning = best["post"].get("pattern_name", "—")
        lines.append(
            f"| {slot_label} | {len(items)} | "
            f"{s_views:,.1f} | {s_likes:.1f} | {s_er:.2f}% | {winning} |"
        )
    lines.append("")

    # -------------------------------------------------------------------
    # TOP5 ランキング（閲覧数）
    # -------------------------------------------------------------------
    lines.append("## 🏆 個別投稿ランキング（閲覧数TOP5）")
    sorted_by_views = sorted(collected, key=lambda x: x["metrics"]["views"], reverse=True)
    for rank, c in enumerate(sorted_by_views[:5], 1):
        m = c["metrics"]
        pattern = c["post"].get("pattern_name", "不明")
        first_line = get_first_line(c["post"].get("content", ""))[:40]
        bot_note = " ※ボット返信含む可能性あり" if m["replies"] > 10 else ""
        lines.append(
            f"{rank}. [{c['time_slot']}] {pattern} / "
            f"閲覧数{m['views']:,} / いいね{m['likes']} / "
            f"ER {c['er']:.2f}%{bot_note}"
        )
        lines.append(f"   フック: 「{first_line}」")
    lines.append("")

    # -------------------------------------------------------------------
    # ワースト3（閲覧数）
    # -------------------------------------------------------------------
    lines.append("## 📉 低パフォーマンス投稿（閲覧数ワースト3）")
    sorted_by_views_asc = sorted(collected, key=lambda x: x["metrics"]["views"])
    worst_count = min(3, len(sorted_by_views_asc))
    for rank, c in enumerate(sorted_by_views_asc[:worst_count], 1):
        m = c["metrics"]
        pattern = c["post"].get("pattern_name", "不明")
        first_line = get_first_line(c["post"].get("content", ""))[:40]
        bot_note = " ※ボット返信含む可能性あり" if m["replies"] > 10 else ""
        lines.append(
            f"{rank}. [{c['time_slot']}] {pattern} / "
            f"閲覧数{m['views']:,} / いいね{m['likes']} / "
            f"ER {c['er']:.2f}%{bot_note}"
        )
        lines.append(f"   フック: 「{first_line}」")
    lines.append("")

    # -------------------------------------------------------------------
    # 改善提案
    # -------------------------------------------------------------------
    lines.append("## 💡 改善提案")

    if not collected:
        lines.append("- データなし（前日の投稿が見つかりませんでした）")
    else:
        # 最高閲覧時間帯
        slot_avg_views = {}
        for slot_label in slot_order:
            items = slot_data.get(slot_label, [])
            if items:
                slot_avg_views[slot_label] = safe_avg([i["metrics"]["views"] for i in items])
        if slot_avg_views:
            best_slot = max(slot_avg_views, key=slot_avg_views.get)
            lines.append(
                f"- 最高閲覧時間帯: **{best_slot}**（平均閲覧数{slot_avg_views[best_slot]:,.0f}）"
            )

        # 最高ER パターン
        pattern_ers = defaultdict(list)
        for c in collected:
            pname = c["post"].get("pattern_name", "不明")
            pattern_ers[pname].append(c["er"])
        if pattern_ers:
            best_pattern = max(pattern_ers, key=lambda k: safe_avg(pattern_ers[k]))
            best_pattern_er = safe_avg(pattern_ers[best_pattern])
            lines.append(
                f"- 最高ERパターン: **{best_pattern}**（平均ER {best_pattern_er:.2f}%）"
            )

        # 次回推奨（ER最高のスロット × パターン組み合わせ）
        slot_er = {}
        for slot_label in slot_order:
            items = slot_data.get(slot_label, [])
            if items:
                slot_er[slot_label] = safe_avg([i["er"] for i in items])
        if slot_er and pattern_ers:
            best_slot_er = max(slot_er, key=slot_er.get)
            lines.append(
                f"- 次回推奨: **{best_slot_er} × {best_pattern}**（高ER組み合わせ）"
            )

        # 削除が多い場合の警告
        if deleted_count > 0:
            lines.append(
                f"- 削除済み投稿が{deleted_count}件あります。コンテンツポリシーの見直しを検討してください。"
            )

        # replies 異常チェック
        high_reply_posts = [c for c in collected if c["metrics"]["replies"] > 10]
        if high_reply_posts:
            lines.append(
                f"- replies > 10 の投稿が{len(high_reply_posts)}件あります。"
                f"ボット返信の可能性を踏まえてER解釈にご注意ください。"
            )

    lines.append("")

    # -------------------------------------------------------------------
    # 削除理由内訳（HC logから）
    # -------------------------------------------------------------------
    if hc_delete_reasons:
        lines.append("## 🗑️ 削除済み投稿の内訳")
        reason_counts = defaultdict(int)
        for reason in hc_delete_reasons:
            reason_counts[reason] += 1
        for reason, cnt in sorted(reason_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- {reason}: {cnt}件")
        lines.append("")

    # -------------------------------------------------------------------
    # 成長トラッカー: パターン別成長曲線サマリー
    # -------------------------------------------------------------------
    tracker = load_json("state/post-growth-tracker.json")
    if tracker and tracker.get("snapshots"):
        lines.append("## 📈 成長トラッカー（直近72h以内の投稿）")

        # パターン別に速度・ピーク時間を集計
        pattern_stats = defaultdict(list)
        slot_stats = defaultdict(list)
        entries_summary = []

        for pid, entry in tracker["snapshots"].items():
            hourly = entry.get("hourly", [])
            if not hourly:
                continue
            peak_v = entry.get("peak_velocity", 0)
            peak_h = entry.get("peak_velocity_hour", "-")
            latest = hourly[-1]
            pname = entry.get("pattern_name", "不明") or "不明"
            slot_h = entry.get("slot_hour", "?")
            day = entry.get("day_name", "?")
            fl = entry.get("first_line", "")[:20]
            v_total = latest.get("views", 0)
            l_total = latest.get("likes", 0)

            pattern_stats[pname].append({"peak_v": peak_v, "peak_h": peak_h, "views": v_total})
            slot_stats[slot_h].append({"peak_v": peak_v, "views": v_total})

            entries_summary.append({
                "pname": pname, "slot_h": slot_h, "day": day,
                "fl": fl, "peak_v": peak_v, "peak_h": peak_h,
                "views": v_total, "likes": l_total,
                "completed": entry.get("completed", False),
            })

        if entries_summary:
            # ベロシティTOP3
            lines.append("### 🚀 ベロシティランキング（閲覧数/時間）")
            sorted_entries = sorted(entries_summary, key=lambda x: x["peak_v"], reverse=True)
            lines.append("| # | フック | パターン | 時 | 曜 | ピーク速度 | ピーク時間 | 現在閲覧 |")
            lines.append("|---|--------|----------|----|----|-----------|----------|---------|")
            for i, e in enumerate(sorted_entries[:5], 1):
                comp = "✅" if e["completed"] else "📊"
                lines.append(
                    f"| {i}{comp} | {e['fl']} | {e['pname']} | "
                    f"{e['slot_h']}時 | {e['day']} | "
                    f"{e['peak_v']:.1f}/h | +{e['peak_h']}h | {e['views']:,} |"
                )
            lines.append("")

            # パターン別平均ピーク速度
            if len(pattern_stats) > 1:
                lines.append("### 📊 パターン別平均ピーク速度")
                lines.append("| パターン | 件数 | 平均ピーク速度 | 平均閲覧 |")
                lines.append("|----------|------|--------------|---------|")
                for pname, stats in sorted(
                    pattern_stats.items(),
                    key=lambda kv: safe_avg([s["peak_v"] for s in kv[1]]),
                    reverse=True
                ):
                    avg_pv = safe_avg([s["peak_v"] for s in stats])
                    avg_v = safe_avg([s["views"] for s in stats])
                    lines.append(
                        f"| {pname} | {len(stats)} | {avg_pv:.1f}/h | {avg_v:,.0f} |"
                    )
                lines.append("")

            # 時間帯別平均ピーク速度
            if len(slot_stats) > 1:
                lines.append("### 🕐 投稿時間帯別平均ピーク速度")
                lines.append("| 投稿時刻 | 件数 | 平均ピーク速度 | 平均閲覧 |")
                lines.append("|---------|------|--------------|---------|")
                for sh, stats in sorted(
                    slot_stats.items(),
                    key=lambda kv: safe_avg([s["peak_v"] for s in kv[1]]),
                    reverse=True
                ):
                    avg_pv = safe_avg([s["peak_v"] for s in stats])
                    avg_v = safe_avg([s["views"] for s in stats])
                    lines.append(
                        f"| {sh}時台 | {len(stats)} | {avg_pv:.1f}/h | {avg_v:,.0f} |"
                    )
                lines.append("")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    load_env()

    access_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not access_token:
        print("ERROR: THREADS_ACCESS_TOKEN が未設定", flush=True)
        sys.exit(1)

    now = datetime.now(JST)
    yesterday = now - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    now_str = now.strftime("%Y-%m-%d %H:%M")

    print(f"=== 日次分析レポート生成 {yesterday_str} ===", flush=True)

    # post-history.json 読み込み
    history = load_json("state/post-history.json")

    # HC log読み込み（削除理由内訳用）
    hc_log = load_json("state/health-check-log.json")

    # 前日のJSON結果を読み込み（前日比較用）
    prev_date_str = (yesterday - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_json_path = os.path.join(PROJECT_DIR, "state", "reports", f"{prev_date_str}.json")
    prev_summary = None
    if os.path.exists(prev_json_path):
        with open(prev_json_path, "r", encoding="utf-8") as f:
            prev_data = json.load(f)
        prev_summary = prev_data.get("summary", None)
        print(f"前日JSON読み込み完了: {prev_date_str}", flush=True)
    else:
        print(f"前日JSONなし（初回 or 欠損）: {prev_date_str}", flush=True)

    # 前日投稿を取得
    yesterday_posts = get_yesterday_posts(history)
    print(f"前日の投稿: {len(yesterday_posts)}件", flush=True)

    if not yesterday_posts:
        print("前日の投稿が見つかりません。レポートは空データで生成します。", flush=True)

    # 各投稿のメトリクスをAPIで取得
    collected = []
    deleted_count = 0

    for post in yesterday_posts:
        platform_post_id = post.get("platform_post_id", "")
        post_id_label = post.get("id", platform_post_id)
        print(f"  取得中: {post_id_label} ({platform_post_id})", flush=True)

        metrics = fetch_post_insights(platform_post_id, access_token)
        if metrics is None:
            deleted_count += 1
            continue

        views = metrics.get("views", 0)
        likes = metrics.get("likes", 0)
        replies = metrics.get("replies", 0)
        reposts = metrics.get("reposts", 0)
        quotes = metrics.get("quotes", 0)

        er = calc_er(views, likes, replies, reposts)
        time_slot = classify_time_slot(post.get("posted_at", ""))

        collected.append({
            "post": post,
            "metrics": {
                "views": views,
                "likes": likes,
                "replies": replies,
                "reposts": reposts,
                "quotes": quotes,
            },
            "time_slot": time_slot,
            "er": round(er, 2),
        })
        print(
            f"    => 閲覧{views:,} いいね{likes} コメ{replies} "
            f"RP{reposts} ER{er:.2f}% [{time_slot}]",
            flush=True,
        )

    print(f"\n取得完了: {len(collected)}件 / 削除済み: {deleted_count}件", flush=True)

    # EXPERIMENT遵守率計算
    exp_posts_yd = [p for p in yesterday_posts if is_experiment_post(p)]
    experiment_rate = (len(exp_posts_yd) / len(yesterday_posts) * 100) if yesterday_posts else None
    if experiment_rate is not None:
        print(f"EXPERIMENT遵守率: {experiment_rate:.0f}% ({len(exp_posts_yd)}/{len(yesterday_posts)}件)", flush=True)

    # HC logから削除理由内訳を取得
    hc_delete_reasons = []
    for check in hc_log.get("checks", []):
        ts = check.get("timestamp", "")
        if ts[:10] != yesterday_str:
            continue
        for r in check.get("results", []):
            if r.get("deleted"):
                reason = r.get("reason", "不明")
                # 短縮
                if "24h" in reason:
                    hc_delete_reasons.append("24h経過・低パフォーマンス")
                elif "12h" in reason:
                    hc_delete_reasons.append("12h経過・低パフォーマンス")
                elif "時刻矛盾" in reason:
                    hc_delete_reasons.append("時刻矛盾")
                else:
                    hc_delete_reasons.append(reason[:30])

    # Markdown レポート生成
    report_content = build_report(
        yesterday_str, collected, deleted_count, now_str,
        prev_summary=prev_summary,
        hc_delete_reasons=hc_delete_reasons if hc_delete_reasons else None,
        experiment_rate=experiment_rate,
    )

    # 保存先ディレクトリ作成
    reports_dir = os.path.join(PROJECT_DIR, "state", "reports")
    os.makedirs(reports_dir, exist_ok=True)

    report_path = os.path.join(reports_dir, f"{yesterday_str}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"\nレポート生成完了: {report_path}", flush=True)

    # 構造化JSON保存（前日比較・PDCA連携用）
    total_views_json = sum(c["metrics"]["views"] for c in collected) if collected else 0
    avg_views_json = total_views_json / len(collected) if collected else 0
    avg_er_json = safe_avg([c["er"] for c in collected]) if collected else 0

    # パターン別集計
    pattern_summary = defaultdict(lambda: {"views": [], "er": [], "count": 0})
    for c in collected:
        pname = c["post"].get("pattern_name", "不明")
        pattern_summary[pname]["views"].append(c["metrics"]["views"])
        pattern_summary[pname]["er"].append(c["er"])
        pattern_summary[pname]["count"] += 1

    json_data = {
        "date": yesterday_str,
        "generated_at": now_str,
        "summary": {
            "posts_total": len(yesterday_posts),
            "posts_measured": len(collected),
            "posts_deleted": deleted_count,
            "total_views": total_views_json,
            "avg_views": round(avg_views_json, 1),
            "avg_er": round(avg_er_json, 2),
            "experiment_rate": round(experiment_rate, 1) if experiment_rate is not None else None,
        },
        "patterns": {
            pname: {
                "count": v["count"],
                "avg_views": round(safe_avg(v["views"]), 1),
                "avg_er": round(safe_avg(v["er"]), 2),
            }
            for pname, v in pattern_summary.items()
        },
        "delete_reasons": hc_delete_reasons,
    }
    json_path = os.path.join(reports_dir, f"{yesterday_str}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    print(f"JSON保存完了: {json_path}", flush=True)

    # GitHub Actions のサマリーとして表示
    print("\n" + "=" * 60, flush=True)
    print(report_content, flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
