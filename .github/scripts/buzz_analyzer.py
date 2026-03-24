#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
バズ投稿深層分析: state/buzz-collection.json の未分析投稿を
Claude API (claude-haiku-4-5) で分析し state/buzz-analysis.json に保存
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MAX_POSTS = 20


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


SYSTEM_PROMPT = "あなたはSNSバズ投稿の分析エキスパートです。指示された形式のJSONのみを返してください。"

USER_PROMPT_TEMPLATE = """以下のスピ占い系のバズ投稿を分析し、なぜバズったのかを解明してください。

【分析対象の投稿】
{posts_json}

【分析項目】
1. フック分析: 各投稿の1行目（フック）のパターンを分類
   - 数字インパクト型 / 恐怖・焦り型 / 限定型 / 呼びかけ型 / 暴露型 / ランキング型
2. 構造分析: 文字数、改行パターン、ブロック構成
3. CTA分析: 行動喚起の手法（絵文字設置、質問、保存推奨、シェア誘導）
4. 心理トリガー分析: どの心理的要素が効いているか
   - FOMO（見逃し恐怖）/ 好奇心ギャップ / 自己関連性 / 社会的証明 / 限定性
5. エンゲージメント要因: なぜ「いいね」「コメント」「シェア」されるのか
6. 改善ポイント: 自アカウントに取り入れるべき要素

【出力形式】
以下のJSON形式のみを返してください（説明文不要）:
{{
  "patterns_found": [
    {{
      "pattern_name": "パターン名",
      "frequency": "出現回数",
      "avg_engagement": "平均エンゲージメント",
      "key_elements": ["要素1", "要素2"],
      "example_hook": "フック例"
    }}
  ],
  "psychological_triggers": [
    {{
      "trigger": "トリガー名",
      "effectiveness": "high/medium/low",
      "example": "具体例"
    }}
  ],
  "structure_insights": {{
    "optimal_length": "最適文字数範囲",
    "line_break_pattern": "改行パターン",
    "block_structure": "ブロック構成"
  }},
  "cta_insights": [
    {{
      "type": "CTAタイプ",
      "effectiveness": "high/medium/low",
      "example": "具体例"
    }}
  ],
  "actionable_improvements": [
    "改善提案1",
    "改善提案2"
  ],
  "best_hooks_to_copy": [
    {{
      "original": "元のフック",
      "adapted": "自アカウント向けにアレンジしたフック例",
      "category": "数字インパクト/呼びかけ・指名/暴露・ぶっちゃけ/逆張り・意外性/緊急・限定感/共感・あるある/質問・参加型/断言・確信/ストーリー導入 のいずれか",
      "why_it_works": "効く理由"
    }}
  ]
}}"""


def main():
    # 1. buzz-collection.json を読み込み
    collection = load_json("state/buzz-collection.json")
    posts = collection.get("posts", [])

    if not posts:
        print("[INFO] buzz-collection.json に投稿がありません。終了します。")
        return

    # 2. analyzed == false の投稿を最大20件取得
    unanalyzed = [p for p in posts if not p.get("analyzed", False)]

    if not unanalyzed:
        print("[INFO] 未分析の投稿なし。終了します。")
        return

    targets = unanalyzed[:MAX_POSTS]
    print(f"[INFO] 未分析投稿: {len(unanalyzed)}件 → 今回分析: {len(targets)}件")

    # 3. Claude API に分析依頼
    posts_for_prompt = []
    for i, p in enumerate(targets, 1):
        posts_for_prompt.append({
            "no": i,
            "content": p.get("content", ""),
            "metrics": p.get("metrics", {}),
        })

    user_prompt = USER_PROMPT_TEMPLATE.format(
        posts_json=json.dumps(posts_for_prompt, ensure_ascii=False, indent=2)
    )

    print("[INFO] Claude API に分析を依頼中...")
    analysis = call_claude(SYSTEM_PROMPT, user_prompt)

    # 4. 結果の組み立て
    now = datetime.now(JST)
    empty_analysis = {
        "patterns_found": [],
        "psychological_triggers": [],
        "structure_insights": {
            "optimal_length": "",
            "line_break_pattern": "",
            "block_structure": ""
        },
        "cta_insights": [],
        "actionable_improvements": [],
        "best_hooks_to_copy": [],
    }

    if analysis is None:
        print("[WARN] Claude API から分析結果を取得できませんでした。空の分析結果で保存します。")
        analysis = empty_analysis
        key_finding = "分析失敗（APIエラー）"
    else:
        print("[INFO] 分析完了")
        # key_finding をパターンから生成
        patterns = analysis.get("patterns_found", [])
        if patterns:
            top_pattern = patterns[0].get("pattern_name", "不明")
            key_finding = f"トップパターン: {top_pattern}"
        else:
            key_finding = "パターン抽出なし"

    # 5. state/buzz-analysis.json に保存
    existing = load_json("state/buzz-analysis.json")
    history = existing.get("history", [])
    history.append({
        "date": now.strftime("%Y-%m-%d"),
        "posts_analyzed": len(targets),
        "key_finding": key_finding,
    })

    output = {
        "last_analyzed": now.isoformat(),
        "total_analyzed": existing.get("total_analyzed", 0) + len(targets),
        "analysis": analysis,
        "history": history,
    }
    save_json("state/buzz-analysis.json", output)
    print(f"[INFO] state/buzz-analysis.json を更新（累計分析: {output['total_analyzed']}件）")

    # 6. buzz-collection.json の該当投稿の analyzed を true に更新
    target_ids = set()
    for t in targets:
        tid = t.get("id") or t.get("tweet_id") or t.get("content", "")[:50]
        target_ids.add(tid)

    for p in posts:
        pid = p.get("id") or p.get("tweet_id") or p.get("content", "")[:50]
        if pid in target_ids:
            p["analyzed"] = True

    collection["posts"] = posts
    save_json("state/buzz-collection.json", collection)
    print(f"[INFO] buzz-collection.json の {len(targets)}件を analyzed=true に更新")

    # 結果サマリー表示
    print("\n=== 分析結果サマリー ===")
    for pat in analysis.get("patterns_found", [])[:3]:
        print(f"  パターン: {pat.get('pattern_name', '?')} (出現: {pat.get('frequency', '?')})")
    for trigger in analysis.get("psychological_triggers", [])[:3]:
        print(f"  心理トリガー: {trigger.get('trigger', '?')} ({trigger.get('effectiveness', '?')})")
    for imp in analysis.get("actionable_improvements", [])[:3]:
        print(f"  改善: {imp}")
    print(f"\n  key_finding: {key_finding}")


if __name__ == "__main__":
    main()
