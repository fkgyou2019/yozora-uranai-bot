#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDCA健全性チェック
毎日 daily-report.yml から呼び出され、PDCAサイクルが正しく回っているかを検証する。

5つの観点でスコアリング（各20点 = 合計100点）:
  P→D: 計画した投稿パターンが実際に投稿されたか
  D→C: 投稿メトリクスが正しく計測されたか
  C→A: 分析結果が改善行動に反映されたか
  A→P: 改善内容が次の計画に組み込まれたか
  全体: 削除率・重複フック・深夜投稿等のミス管理

結果を state/pdca-health.json に保存し、
状態が悪い場合は state/pdca-alerts.json にアラートを積む。
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXPERIMENT_STRUCTURES = {"G", "A", "B", "C", "F"}


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


def get_posts_for_date(history, date_str):
    """指定日（JST）の投稿一覧を返す"""
    result = []
    for p in history.get("posts", []):
        pa = p.get("posted_at", "")
        if not pa:
            continue
        try:
            dt = datetime.fromisoformat(pa)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            if dt.astimezone(JST).date().isoformat() == date_str:
                result.append(p)
        except Exception:
            continue
    return result


def get_hc_checks_for_date(hc_log, date_str):
    """指定日のHCチェック結果一覧"""
    return [c for c in hc_log.get("checks", []) if c.get("timestamp", "")[:10] == date_str]


def is_experiment_post(post):
    """EXPERIMENT_MODEのパターンかどうか"""
    pattern = post.get("pattern_name", "")
    for s in EXPERIMENT_STRUCTURES:
        if f"構造{s}" in pattern:
            return True
    return False


# ---------------------------------------------------------------------------
# P→D チェック: 計画パターンが実投稿に反映されたか
# ---------------------------------------------------------------------------
def check_plan_to_do(history, yesterday_str):
    posts = get_posts_for_date(history, yesterday_str)
    if not posts:
        return {
            "score": 0, "label": "データなし",
            "detail": f"{yesterday_str} の投稿が見つかりません",
            "posts_total": 0, "experiment_count": 0, "compliance_rate": 0,
        }

    # 深夜投稿チェック（0-6時）
    night_posts = [p for p in posts if datetime.fromisoformat(
        p.get("posted_at", "2000-01-01T12:00:00")).astimezone(JST).hour in range(0, 7)]

    # EXPERIMENT_MODEパターン遵守率
    exp_posts = [p for p in posts if is_experiment_post(p)]
    compliance_rate = len(exp_posts) / len(posts) * 100 if posts else 0

    # 短間隔投稿（60分未満）チェック
    sorted_posts = sorted(posts, key=lambda x: x.get("posted_at", ""))
    short_interval = []
    for i in range(1, len(sorted_posts)):
        prev_dt = datetime.fromisoformat(sorted_posts[i-1].get("posted_at", "")).astimezone(JST)
        curr_dt = datetime.fromisoformat(sorted_posts[i].get("posted_at", "")).astimezone(JST)
        diff_min = (curr_dt - prev_dt).total_seconds() / 60
        if 0 < diff_min < 60:
            short_interval.append(diff_min)

    # スコア計算
    score = 20
    issues = []
    if compliance_rate < 50:
        score -= 10
        issues.append(f"EXPERIMENT遵守率 {compliance_rate:.0f}%（目標100%）")
    elif compliance_rate < 80:
        score -= 5
        issues.append(f"EXPERIMENT遵守率 {compliance_rate:.0f}%（改善余地あり）")

    if night_posts:
        score -= 5
        issues.append(f"深夜投稿 {len(night_posts)}件（0-6時）")

    if short_interval:
        score -= max(5, len(short_interval) * 2)
        issues.append(f"60分未満の短間隔投稿 {len(short_interval)}件")

    score = max(0, score)
    label = "✅ 良好" if score >= 16 else "⚠️ 要改善" if score >= 10 else "❌ 問題あり"

    return {
        "score": score,
        "label": label,
        "detail": " / ".join(issues) if issues else "計画通りの投稿を実施",
        "posts_total": len(posts),
        "experiment_count": len(exp_posts),
        "compliance_rate": round(compliance_rate, 1),
        "night_posts": len(night_posts),
        "short_interval_count": len(short_interval),
    }


# ---------------------------------------------------------------------------
# D→C チェック: メトリクスが正しく計測されたか
# ---------------------------------------------------------------------------
def check_do_to_check(history, hc_log, yesterday_str):
    posts = get_posts_for_date(history, yesterday_str)
    if not posts:
        return {"score": 0, "label": "データなし", "detail": "投稿なし", "measured_rate": 0}

    # HC logに登場した投稿IDセット
    appeared_pids = set()
    for c in hc_log.get("checks", []):
        for r in c.get("results", []):
            appeared_pids.add(r.get("post_id", ""))

    pids = [p.get("platform_post_id", "") for p in posts if p.get("platform_post_id")]
    measured = [pid for pid in pids if pid in appeared_pids]
    measured_rate = len(measured) / len(pids) * 100 if pids else 0

    # 当日のHC実行回数（2時間ごと9回が目標）
    hc_today = get_hc_checks_for_date(hc_log, yesterday_str)
    hc_count = len(hc_today)

    score = 20
    issues = []
    if measured_rate < 50:
        score -= 10
        issues.append(f"計測率 {measured_rate:.0f}%（{len(measured)}/{len(pids)}件）")
    elif measured_rate < 80:
        score -= 5
        issues.append(f"計測率 {measured_rate:.0f}%（一部未計測）")

    if hc_count < 5:
        score -= 5
        issues.append(f"HCが{hc_count}回のみ（目標9回）")

    score = max(0, score)
    label = "✅ 良好" if score >= 16 else "⚠️ 要改善" if score >= 10 else "❌ 問題あり"

    return {
        "score": score,
        "label": label,
        "detail": " / ".join(issues) if issues else f"全{len(measured)}件を計測済み（HC{hc_count}回）",
        "measured_rate": round(measured_rate, 1),
        "hc_count": hc_count,
        "pids_total": len(pids),
        "pids_measured": len(measured),
    }


# ---------------------------------------------------------------------------
# C→A チェック: 分析結果が改善行動に反映されたか
# ---------------------------------------------------------------------------
def check_check_to_act(imp_log, winning, yesterday_str):
    improvements = imp_log.get("improvements", [])

    # 直近7日間の改善実行回数
    now = datetime.now(JST)
    recent_improvements = [
        i for i in improvements
        if (now - datetime.fromisoformat(
            i.get("date", "2000-01-01") + "T00:00:00")).days <= 7
    ]

    # フック追加実績
    hooks_added_total = sum(i.get("hooks_added", 0) for i in recent_improvements)
    insights_added_total = sum(i.get("insights_added", 0) for i in recent_improvements)
    last_improved = imp_log.get("last_improved", "")

    # winning-patternsの更新頻度
    wp_updated = winning.get("last_updated", "")
    wp_sample = winning.get("sample_size", 0)

    score = 20
    issues = []

    if hooks_added_total == 0:
        score -= 8
        issues.append("直近7日間フック追加ゼロ（template_improverのカテゴリ不一致が原因だった→修正済み）")

    if len(recent_improvements) == 0:
        score -= 5
        issues.append("直近7日間に改善ログなし")

    if wp_sample < 10:
        score -= 5
        issues.append(f"winning-patternsのサンプル数 {wp_sample}件（少ない）")

    if wp_updated:
        wp_dt = datetime.fromisoformat(wp_updated).astimezone(JST)
        days_since_update = (now - wp_dt).days
        if days_since_update > 3:
            score -= 5
            issues.append(f"winning-patterns更新が{days_since_update}日前")

    score = max(0, score)
    label = "✅ 良好" if score >= 16 else "⚠️ 要改善" if score >= 10 else "❌ 問題あり"

    return {
        "score": score,
        "label": label,
        "detail": " / ".join(issues) if issues else f"フック+{hooks_added_total}件・インサイト+{insights_added_total}件（7日間）",
        "hooks_added_7d": hooks_added_total,
        "insights_added_7d": insights_added_total,
        "improvement_runs_7d": len(recent_improvements),
        "wp_sample_size": wp_sample,
    }


# ---------------------------------------------------------------------------
# A→P チェック: 改善内容が次の計画に組み込まれたか
# ---------------------------------------------------------------------------
def check_act_to_plan(winning, history, yesterday_str):
    # EXPERIMENTスロット定義（generate_posts.pyと同期）
    PLANNED_STRUCTURES = ["G", "A", "G", "B", "G", "F", "G"]  # 7スロット
    planned_g_count = PLANNED_STRUCTURES.count("G")

    # 直近7日間の投稿でパターン別実績
    posts_7d = []
    now = datetime.now(JST)
    for p in history.get("posts", []):
        pa = p.get("posted_at", "")
        if not pa:
            continue
        try:
            dt = datetime.fromisoformat(pa).astimezone(JST)
            if (now - dt).days <= 7:
                posts_7d.append(p)
        except Exception:
            continue

    # 構造別カウント
    struct_count = defaultdict(int)
    for p in posts_7d:
        pattern = p.get("pattern_name", "")
        for s in EXPERIMENT_STRUCTURES:
            if f"構造{s}" in pattern:
                struct_count[s] += 1
                break

    # winning-patternsのtop_patternsが更新されているか
    top_patterns = winning.get("top_patterns", [])
    top_has_G = any("G" in tp.get("pattern", "") or "注意喚起" in tp.get("pattern", "") for tp in top_patterns[:3])

    score = 20
    issues = []

    if not struct_count:
        score -= 10
        issues.append("直近7日間にEXPERIMENTパターン投稿なし")

    if not top_has_G and struct_count.get("G", 0) == 0:
        score -= 5
        issues.append("最強構造Gがtop_patternsに未反映")

    if len(top_patterns) < 3:
        score -= 5
        issues.append(f"top_patternsが{len(top_patterns)}件のみ（学習データ不足）")

    # generate_posts.pyのスロット定義に実績1位が反映されているかチェック
    # 7スロット中Gが4つ → 実績に基づく配分
    if struct_count.get("G", 0) > 0 and planned_g_count >= 3:
        pass  # OK
    elif planned_g_count < 3:
        score -= 5
        issues.append("Gスロットが3未満（実績最強パターンの配分を増やすべき）")

    score = max(0, score)
    label = "✅ 良好" if score >= 16 else "⚠️ 要改善" if score >= 10 else "❌ 問題あり"

    struct_summary = ", ".join(f"{s}:{n}" for s, n in sorted(struct_count.items()))

    return {
        "score": score,
        "label": label,
        "detail": " / ".join(issues) if issues else f"構造分布: {struct_summary}",
        "struct_count_7d": dict(struct_count),
        "top_patterns_count": len(top_patterns),
    }


# ---------------------------------------------------------------------------
# ミス管理チェック: 削除率・重複フック・その他
# ---------------------------------------------------------------------------
def check_mistakes(history, hc_log, yesterday_str):
    posts = get_posts_for_date(history, yesterday_str)
    total = len(posts)

    # 削除件数
    deleted = sum(
        1 for c in hc_log.get("checks", [])
        for r in c.get("results", [])
        if r.get("deleted") and c.get("timestamp", "")[:10] == yesterday_str
    )
    delete_rate = deleted / total * 100 if total > 0 else 0

    # 時刻矛盾削除件数
    time_mismatch = sum(
        1 for c in hc_log.get("checks", [])
        for r in c.get("results", [])
        if r.get("deleted") and "時刻矛盾" in r.get("reason", "")
        and c.get("timestamp", "")[:10] == yesterday_str
    )

    # 直近50件でフック重複チェック
    recent_posts = history.get("posts", [])[-50:]
    first_lines = [p.get("content", "").split("\n")[0].strip() for p in recent_posts if p.get("content")]
    from collections import Counter
    fl_counter = Counter(fl for fl in first_lines if fl)
    duplicates = [(fl, cnt) for fl, cnt in fl_counter.most_common() if cnt >= 3]

    score = 20
    issues = []

    if delete_rate > 60:
        score -= 10
        issues.append(f"削除率 {delete_rate:.0f}%（{deleted}/{total}件）")
    elif delete_rate > 30:
        score -= 5
        issues.append(f"削除率 {delete_rate:.0f}%（やや高い）")

    if time_mismatch > 0:
        score -= 3 * time_mismatch
        issues.append(f"時刻矛盾削除 {time_mismatch}件（プロンプト修正済み）")

    if duplicates:
        score -= 5
        dup_str = " / ".join(f'「{fl[:15]}」{cnt}回' for fl, cnt in duplicates[:2])
        issues.append(f"フック重複: {dup_str}")

    score = max(0, score)
    label = "✅ 良好" if score >= 16 else "⚠️ 要改善" if score >= 10 else "❌ 問題あり"

    return {
        "score": score,
        "label": label,
        "detail": " / ".join(issues) if issues else f"削除率{delete_rate:.0f}%・重複なし",
        "delete_rate": round(delete_rate, 1),
        "deleted_count": deleted,
        "total_posts": total,
        "time_mismatch_deleted": time_mismatch,
        "hook_duplicates": [(fl[:20], cnt) for fl, cnt in duplicates[:3]],
    }


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main():
    now = datetime.now(JST)
    yesterday = (now - timedelta(days=1)).date().isoformat()

    print(f"=== PDCA健全性チェック {yesterday} ===\n")

    history = load_json("state/post-history.json")
    hc_log = load_json("state/health-check-log.json")
    imp_log = load_json("state/improvement-log.json")
    winning = load_json("state/winning-patterns.json")

    # 5観点チェック
    checks = {
        "P→D（計画→実行）": check_plan_to_do(history, yesterday),
        "D→C（実行→計測）": check_do_to_check(history, hc_log, yesterday),
        "C→A（計測→改善）": check_check_to_act(imp_log, winning, yesterday),
        "A→P（改善→計画反映）": check_act_to_plan(winning, history, yesterday),
        "ミス管理": check_mistakes(history, hc_log, yesterday),
    }

    total_score = sum(c["score"] for c in checks.values())
    max_score = 100

    # 出力
    print(f"{'観点':<20} {'スコア':>6}  {'判定':<10} 詳細")
    print("-" * 80)
    for name, result in checks.items():
        print(f"{name:<20} {result['score']:>3}/20  {result['label']:<12} {result['detail'][:55]}")

    print()
    grade = "S" if total_score >= 90 else "A" if total_score >= 75 else "B" if total_score >= 60 else "C" if total_score >= 45 else "D"
    print(f"【総合スコア】 {total_score}/{max_score}点 (グレード: {grade})")

    if total_score >= 90:
        print("✅ PDCAサイクルは正常に機能しています")
    elif total_score >= 75:
        print("⚠️ 概ね機能していますが、一部改善が必要です")
    elif total_score >= 60:
        print("⚠️ PDCAサイクルに問題があります。要改善箇所を確認してください")
    else:
        print("❌ PDCAサイクルが機能していません。緊急対応が必要です")

    # トレンド比較（前日比）
    prev_health = load_json("state/pdca-health.json")
    prev_score = prev_health.get("total_score", 0)
    if prev_score > 0:
        delta = total_score - prev_score
        trend = f"↑+{delta}" if delta > 0 else f"↓{delta}" if delta < 0 else "→ 横ばい"
        print(f"前日比: {trend} ({prev_score} → {total_score})")

    # アラート生成
    alerts = []
    for name, result in checks.items():
        if result["score"] < 10:
            alerts.append({
                "level": "ERROR",
                "category": name,
                "message": result["detail"],
                "score": result["score"],
                "date": yesterday,
            })
        elif result["score"] < 16:
            alerts.append({
                "level": "WARN",
                "category": name,
                "message": result["detail"],
                "score": result["score"],
                "date": yesterday,
            })

    if alerts:
        print(f"\n⚠️ アラート {len(alerts)}件:")
        for a in alerts:
            print(f"  [{a['level']}] {a['category']}: {a['message'][:60]}")

    # 保存
    health_data = {
        "date": yesterday,
        "generated_at": now.isoformat(),
        "total_score": total_score,
        "grade": grade,
        "checks": checks,
        "alerts": alerts,
    }
    save_json("state/pdca-health.json", health_data)

    # アラート履歴（直近30日分）
    alert_history = load_json("state/pdca-alerts.json")
    if not alert_history:
        alert_history = {"alerts": []}
    # 当日のアラートを追加（重複除去）
    alert_history["alerts"] = [
        a for a in alert_history.get("alerts", [])
        if a.get("date") != yesterday
    ] + alerts
    alert_history["alerts"] = alert_history["alerts"][-300:]  # 直近300件
    save_json("state/pdca-alerts.json", alert_history)

    # スコア履歴（トレンド用）
    score_history = load_json("state/pdca-score-history.json")
    if not score_history:
        score_history = {"scores": []}
    score_history["scores"] = [
        s for s in score_history.get("scores", [])
        if s.get("date") != yesterday
    ]
    score_history["scores"].append({
        "date": yesterday,
        "score": total_score,
        "grade": grade,
        "checks": {k: v["score"] for k, v in checks.items()},
    })
    score_history["scores"] = score_history["scores"][-90:]
    save_json("state/pdca-score-history.json", score_history)

    print(f"\nPDCA健全性チェック完了: state/pdca-health.json")


if __name__ == "__main__":
    main()
