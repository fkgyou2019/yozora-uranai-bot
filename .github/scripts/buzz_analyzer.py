#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
バズ投稿収集・分析スクリプト

Threads.netの占いジャンルのバズ投稿を分析し、勝ちパターンを抽出する。
Threads APIでは他ユーザーの投稿取得が不可のため、以下の代替アプローチ:
  1. state/buzz-examples.json に蓄積されたバズ事例を構造分析
  2. state/performance-data.json の自分の投稿データと比較
  3. Claude API (claude-haiku-4-5) で勝ちパターンを抽出
  4. state/winning-patterns.json に反映 → generate_posts.py が参照
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

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


def load_env_fallback():
    """環境変数が未設定の場合、config/api-keys.env からフォールバック読み込み"""
    env_path = os.path.join(PROJECT_DIR, "config", "api-keys.env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and val and not os.environ.get(key):
                os.environ[key] = val


def call_claude(system_prompt, user_prompt):
    """Claude API を呼び出し、JSON レスポンスをパースして返す"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[ERROR] ANTHROPIC_API_KEY が設定されていません")
        return None

    url = "https://api.anthropic.com/v1/messages"
    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}]
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    })

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
    except Exception as e:
        print(f"[ERROR] Claude API 呼び出し失敗: {e}")
        return None

    text = result["content"][0]["text"]
    # JSON部分を抽出（```json ... ``` で囲まれている場合の処理）
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    try:
        return json.loads(text.strip())
    except json.JSONDecodeError as e:
        print(f"[ERROR] Claude レスポンスの JSON パース失敗: {e}")
        print(f"[DEBUG] レスポンス先頭200文字: {text[:200]}")
        return None


# ---------------------------------------------------------------------------
# 自分の投稿メトリクス取得（Threads API）
# ---------------------------------------------------------------------------

def fetch_own_metrics():
    """Threads API から自分の投稿メトリクスを取得（threads_basic権限）"""
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    user_id = os.environ.get("THREADS_USER_ID", "")

    if not token or not user_id:
        print("[INFO] Threads API 認証情報なし。自分の投稿メトリクス取得をスキップ。")
        return []

    url = (
        f"https://graph.threads.net/v1.0/{user_id}/threads"
        f"?fields=id,text,timestamp,like_count,reply_count,repost_count"
        f"&limit=25"
        f"&access_token={token}"
    )

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        posts = data.get("data", [])
        print(f"[INFO] Threads API から自分の投稿 {len(posts)}件を取得")
        return posts
    except Exception as e:
        print(f"[WARN] Threads API 呼び出し失敗: {e}")
        return []


# ---------------------------------------------------------------------------
# プロンプト
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """あなたはSNSバズ投稿の分析エキスパートです。
占い・スピリチュアル・星座運勢ジャンルの投稿を専門的に分析します。
指示された形式のJSONのみを返してください。"""

ANALYSIS_PROMPT_TEMPLATE = """以下の2種類のデータを分析し、Threads.netの占いジャンルにおける「勝ちパターン」を抽出してください。

【データ1: 他アカウントのバズ投稿事例】
{buzz_examples_json}

【データ2: 自分の投稿パフォーマンスデータ】
{own_posts_json}

【分析指示】
1. フック（1行目）の型を分類
   - 数字インパクト型（例: 12星座中たった2つだけ）
   - ランキング型（例: 【○○TOP3】）
   - 恐怖・焦り型（例: ○月に注意すべき星座）
   - 限定型（例: ○座さんだけに）
   - 呼びかけ型（例: ○座さんへ）
   - 断定型（例: 来週、仕事で大きな話が来やすい星座。）

2. CTA（最後の行動喚起）の型を分類
   - 絵文字CTA型（例: 🍀を置いた方に届きます）
   - 質問型（例: あなたの星座はどれ？）
   - 保存推奨型（例: 保存して毎日チェック）

3. 構造パターン
   - 最適文字数
   - 改行パターン（1行あたり文字数）
   - 絵文字使用率
   - ブロック構成

4. エンゲージメント要因分析
   - バズ投稿と自分の投稿の差分
   - いいね数・コメント数の比率から見る改善点
   - コメントが多い投稿の特徴

5. 具体的改善アクション
   - 自分の投稿で取り入れるべき要素TOP5

【出力形式】
以下のJSON形式のみを返してください（説明文不要）:
{{
  "hook_patterns": [
    {{
      "type": "型名",
      "effectiveness": "high/medium/low",
      "avg_engagement": "平均エンゲージメント",
      "example": "具体例",
      "frequency": "出現頻度"
    }}
  ],
  "cta_patterns": [
    {{
      "type": "CTAタイプ",
      "effectiveness": "high/medium/low",
      "example": "具体例",
      "comment_boost": "コメント増加効果の有無"
    }}
  ],
  "structure_insights": {{
    "optimal_char_count": "最適文字数範囲",
    "line_break_pattern": "改行パターン",
    "emoji_usage": "絵文字使用パターン",
    "block_structure": "ブロック構成"
  }},
  "engagement_gap_analysis": {{
    "buzz_vs_own_diff": ["差分ポイント1", "差分ポイント2"],
    "comment_drivers": ["コメントを増やす要素1", "要素2"],
    "like_drivers": ["いいねを増やす要素1", "要素2"]
  }},
  "winning_formula": {{
    "pattern_weights": [
      {{"pattern": "パターン名", "weight": 35, "reason": "理由"}},
      {{"pattern": "パターン名", "weight": 25, "reason": "理由"}}
    ],
    "must_have_elements": ["必須要素1", "必須要素2"],
    "avoid_elements": ["避けるべき要素1"]
  }},
  "actionable_improvements": [
    "改善アクション1",
    "改善アクション2",
    "改善アクション3",
    "改善アクション4",
    "改善アクション5"
  ],
  "best_hooks_to_adapt": [
    {{
      "original": "元のフック",
      "adapted": "自分用にアレンジしたフック",
      "category": "フックカテゴリ",
      "why_it_works": "効く理由"
    }}
  ]
}}"""


# ---------------------------------------------------------------------------
# winning-patterns.json への反映
# ---------------------------------------------------------------------------

def merge_to_winning_patterns(analysis, buzz_examples, own_posts):
    """分析結果を既存の winning-patterns.json にマージする"""
    existing = load_json("state/winning-patterns.json")
    now = datetime.now(JST)

    # パターン重み配分を構築
    winning_formula = analysis.get("winning_formula", {})
    pattern_weights = winning_formula.get("pattern_weights", [])

    top_patterns = {}
    for pw in pattern_weights:
        key = pw["pattern"].replace(" ", "_").lower()
        top_patterns[key] = {
            "avg_eng": 0,
            "avg_views": 0,
            "weight": pw["weight"] / 100.0,
            "description": f"{pw['pattern']} - {pw.get('reason', '')}",
        }

    # 既存パターンの数値を可能な範囲で保持
    if existing.get("top_patterns"):
        for k, v in existing["top_patterns"].items():
            if k in top_patterns:
                top_patterns[k]["avg_eng"] = v.get("avg_eng", 0)
                top_patterns[k]["avg_views"] = v.get("avg_views", 0)

    # CTA例の構築
    cta_patterns = analysis.get("cta_patterns", [])
    cta_examples = [cp["example"] for cp in cta_patterns if cp.get("example")]
    if not cta_examples:
        cta_examples = existing.get("rules", {}).get("cta_examples", [
            "「🍀」を置いた方に、今週中に嬉しい連絡が届きます。",
            "🔮を置いた方に良い出会いが届きます。",
        ])

    # ベストフック
    best_hooks = analysis.get("best_hooks_to_adapt", [])
    top_hook_styles = []
    for bh in best_hooks[:5]:
        top_hook_styles.append({
            "style": bh.get("category", "不明"),
            "example": bh.get("adapted", bh.get("original", "")),
            "original": bh.get("original", ""),
            "why_it_works": bh.get("why_it_works", ""),
        })

    # 避けるべき要素
    avoid = winning_formula.get("avoid_elements", [])

    result = {
        "last_updated": now.isoformat(),
        "sample_size": len(buzz_examples) + len(own_posts),
        "data_sources": {
            "buzz_examples": len(buzz_examples),
            "own_posts": len(own_posts),
        },
        "top_patterns": top_patterns,
        "rules": {
            "all_posts_must_have_cta": True,
            "cta_examples": cta_examples,
            "avoid_questions_in_hook": True,
            "best_time_slots": existing.get("rules", {}).get("best_time_slots", [
                "07:07", "14:07", "20:37", "22:06"
            ]),
        },
        "hook_patterns": analysis.get("hook_patterns", []),
        "cta_patterns": cta_patterns,
        "structure_insights": analysis.get("structure_insights", {}),
        "engagement_gap_analysis": analysis.get("engagement_gap_analysis", {}),
        "winning_formula": winning_formula,
        "top_hook_styles": top_hook_styles,
        "avoid_patterns": avoid,
        "actionable_improvements": analysis.get("actionable_improvements", []),
    }

    save_json("state/winning-patterns.json", result)
    print(f"[INFO] state/winning-patterns.json を更新 "
          f"(バズ事例{len(buzz_examples)}件 + 自分{len(own_posts)}件)")
    return result


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    load_env_fallback()

    # 1. buzz-examples.json を読み込む
    buzz_data = load_json("state/buzz-examples.json")
    examples = buzz_data.get("examples", [])
    print(f"[INFO] バズ事例データ: {len(examples)}件")

    # 2. 自分の投稿データを読み込む (performance-data.json)
    perf = load_json("state/performance-data.json")
    own_posts = perf.get("posts", [])
    print(f"[INFO] 自分の投稿データ: {len(own_posts)}件")

    # 3. Threads API から最新の自分のメトリクスも取得（可能であれば）
    api_posts = fetch_own_metrics()
    if api_posts:
        # API取得分を own_posts に追加（重複排除はIDベース）
        existing_ids = {p.get("platform_post_id") for p in own_posts}
        for ap in api_posts:
            if ap.get("id") not in existing_ids:
                own_posts.append({
                    "id": f"api_{ap.get('id', '')}",
                    "platform_post_id": ap.get("id", ""),
                    "content_preview": (ap.get("text", "")[:80] if ap.get("text") else ""),
                    "first_line": (ap.get("text", "").split("\n")[0] if ap.get("text") else ""),
                    "metrics": {
                        "likes": ap.get("like_count", 0),
                        "replies": ap.get("reply_count", 0),
                        "reposts": ap.get("repost_count", 0),
                    },
                    "posted_at": ap.get("timestamp", ""),
                })

    # データが全くない場合
    if not examples and not own_posts:
        print("[WARN] 分析対象のデータがありません。")
        print("[INFO] state/buzz-examples.json にバズ事例を追加してください。")
        return

    # 4. Claude API で構造分析
    # バズ事例データの整形
    buzz_for_prompt = []
    for ex in examples:
        buzz_for_prompt.append({
            "account": ex.get("account", "unknown"),
            "text": ex.get("text", ""),
            "likes": ex.get("likes", 0),
            "comments": ex.get("comments", 0),
            "tags": ex.get("tags", []),
        })

    # 自分の投稿データの整形（上位10件）
    own_sorted = sorted(
        own_posts,
        key=lambda p: p.get("metrics", {}).get("engagement_rate",
                     p.get("metrics", {}).get("likes", 0)),
        reverse=True,
    )
    own_for_prompt = []
    for p in own_sorted[:10]:
        own_for_prompt.append({
            "first_line": p.get("first_line", ""),
            "content_preview": p.get("content_preview", ""),
            "metrics": p.get("metrics", {}),
            "pattern": p.get("pattern_name", ""),
            "features": p.get("features", {}),
        })

    user_prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        buzz_examples_json=json.dumps(buzz_for_prompt, ensure_ascii=False, indent=2),
        own_posts_json=json.dumps(own_for_prompt, ensure_ascii=False, indent=2),
    )

    print("[INFO] Claude API に分析を依頼中...")
    analysis = call_claude(SYSTEM_PROMPT, user_prompt)

    if analysis is None:
        print("[ERROR] Claude API から分析結果を取得できませんでした。")
        # 既存の winning-patterns.json を壊さないように終了
        return

    print("[INFO] 分析完了")

    # 5. winning-patterns.json に反映
    result = merge_to_winning_patterns(analysis, examples, own_posts)

    # 6. buzz-examples.json の last_analyzed を更新
    buzz_data["last_analyzed"] = datetime.now(JST).isoformat()
    save_json("state/buzz-examples.json", buzz_data)

    # 7. buzz-analysis.json にも履歴を追記（既存フローとの互換性）
    now = datetime.now(JST)
    existing_analysis = load_json("state/buzz-analysis.json")
    history = existing_analysis.get("history", [])
    history.append({
        "date": now.strftime("%Y-%m-%d"),
        "posts_analyzed": len(examples) + len(own_posts),
        "source": "buzz_analyzer_v2",
        "key_finding": analysis.get("actionable_improvements", ["分析完了"])[0],
    })

    analysis_output = {
        "last_analyzed": now.isoformat(),
        "total_analyzed": existing_analysis.get("total_analyzed", 0) + len(examples) + len(own_posts),
        "analysis": analysis,
        "history": history,
    }
    save_json("state/buzz-analysis.json", analysis_output)

    # 結果サマリー表示
    print("\n=== 分析結果サマリー ===")
    for hp in analysis.get("hook_patterns", [])[:3]:
        print(f"  フックパターン: {hp.get('type', '?')} "
              f"(効果: {hp.get('effectiveness', '?')})")
    for cp in analysis.get("cta_patterns", [])[:3]:
        print(f"  CTAパターン: {cp.get('type', '?')} "
              f"(効果: {cp.get('effectiveness', '?')})")
    for imp in analysis.get("actionable_improvements", [])[:5]:
        print(f"  改善: {imp}")

    wf = analysis.get("winning_formula", {})
    for pw in wf.get("pattern_weights", [])[:3]:
        print(f"  勝ちパターン: {pw.get('pattern', '?')} "
              f"(重み: {pw.get('weight', '?')}%)")

    print(f"\n[INFO] 完了。winning-patterns.json を更新しました。")


if __name__ == "__main__":
    main()
