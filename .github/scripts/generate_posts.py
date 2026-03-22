#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions用: キューが空なら Claude API で投稿を10件生成
学習データ（winning-patterns.json）を参照し、日々進化するプロンプトを構築
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

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


def build_learning_block(winning):
    """学習データからプロンプトブロックを動的構築"""
    if not winning or winning.get("data_count", 0) < 3:
        return """【市場分析から導出した勝ちパターン（初期値）】
・ランキング型（第1位〜第3位 or 第5位）を10件中5件以上にする
・フックは「恐怖×期待」型が最強（例: 「○月に人生が変わる星座。」）
・数字×限定×具体性の組み合わせが効く（例: 「12星座中、たった2つだけ。」）"""

    lines = []
    confidence = winning.get("confidence", "low")
    count = winning.get("data_count", 0)
    lines.append(f"【自己学習データ（{count}件分析済み・信頼度:{confidence}）】")

    # トップパターンの配分指示
    top_patterns = winning.get("top_patterns", [])
    if top_patterns:
        lines.append("■ パターン配分（10件中の目安）:")
        for tp in top_patterns[:4]:
            n = max(1, round(tp["weight"] / 10))
            lines.append(f"  ・{tp['pattern']}: {n}件（eng率{tp.get('avg_engagement', '?')}%）")

    # ベスト投稿の参考
    best = winning.get("best_posts", [])
    if best:
        lines.append("■ 最もバズった投稿のフック:")
        for b in best[:3]:
            lines.append(f"  ・「{b['first_line']}」→ eng率{b['eng_rate']:.1f}%")

    # 特徴ランキング
    features = winning.get("feature_ranking", [])
    if features:
        lines.append("■ 効果が高い要素:")
        label_map = {
            "has_ranking": "ランキング形式",
            "has_number_hook": "数字フック",
            "has_question": "質問文",
            "has_cta_emoji": "絵文字CTA",
            "has_fear_hook": "恐怖・焦りフック",
        }
        for fr in features[:4]:
            label = label_map.get(fr["feature"], fr["feature"])
            lines.append(f"  ・{label} → avg eng {fr['avg_engagement']:.1f}%")

    # 避けるべき
    avoid = winning.get("avoid_patterns", [])
    if avoid:
        lines.append(f"■ 避けるべきパターン: {', '.join(avoid)}")

    # インサイト
    insights = winning.get("insights", [])
    if insights:
        lines.append("■ AI分析のインサイト:")
        for ins in insights[:3]:
            lines.append(f"  ・{ins}")

    return "\n".join(lines)


def main():
    queue = load_json("state/post-queue.json")
    if not queue:
        queue = {"queue": []}

    pending = [p for p in queue.get("queue", []) if p.get("status") == "queued"]
    if pending:
        print(f"キューに{len(pending)}件残っています。生成スキップ。")
        return

    print("キューが空のため、Claude APIで10件生成します...")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY が未設定")
        sys.exit(1)

    history = load_json("state/post-history.json")
    if not history:
        history = {"posts": []}

    winning = load_json("state/winning-patterns.json")

    used_patterns = [p.get("pattern_name", "") for p in history.get("posts", [])[-15:]]
    now = datetime.now(JST)
    today = now.strftime("%Y年%m月%d日(%a)")
    weekday = now.weekday()  # 0=月 1=火 ... 6=日

    # 曜日別シリーズコンテンツ
    series_map = {
        0: "【月曜定番】今週の星座ランキング（総合運TOP5）。毎週月曜に発表する定番シリーズ。",
        1: "【火曜定番】○座さんへの手紙（1つの星座を深掘りする個別メッセージ）。毎週火曜。",
        2: "【水曜定番】週の折り返しタロット1枚引き。今週後半のキーカード。",
        3: "【木曜定番】今週後半の注意星座3つ。木曜に出すことで「あと2日気をつけよう」と思わせる。",
        4: "【金曜定番】週末の開運アクション。具体的にやるべきことを3つ提示。",
        5: "【土曜定番】来週の予告（来週最も運気が動く星座を先出し）。フォロー継続理由を作る。",
        6: "【日曜定番】1週間の振り返り＆来週への準備メッセージ。",
    }
    today_series = series_map.get(weekday, "")

    # 学習データからプロンプトブロックを動的構築
    learning_block = build_learning_block(winning)

    prompt = f"""あなたは占いSNSアカウント「よぞら.」の投稿ライターです。

【ペルソナ】
名前: 月詠（つくよみ）
トーン: 穏やかで神秘的、でも親しみやすい。敬語ベース。
絵文字: 🔮✨🌙⭐ を控えめに使用（1投稿に2-3個まで）

【今日の日付】{today}

【直近で使用済みのパターン（重複を避ける）】
{json.dumps(used_patterns[-5:], ensure_ascii=False)}

{learning_block}

【📱 スマホ改行ルール（違反=不合格）】
1. 1行は最大25文字（\\nで改行）
2. 改行なし30文字以上のベタ打ちは禁止
3. 空行（\\n\\n）で3〜5ブロックに分割
4. 1ブロックは最大40文字
5. フック（1行目）は20文字以内

【構造テンプレート】
[フック20文字以内]\\n\\n[ブロック1]\\n\\n[ブロック2]\\n\\n[ブロック3]\\n\\n[CTA+ハッシュタグ]

【🏆 バズ実績のある投稿構造（この構造を真似ること）】

■ 構造A: ランキング型（eng率 8-10%。最もバズる）
---
【金運が爆発する星座TOP3】\\n\\n🥉第3位：射手座\\n思わぬ臨時収入が\\n舞い込む予感です。\\n\\n🥈第2位：蠍座\\n投資や副業に\\n追い風が吹きます。\\n\\n🥇第1位：牡牛座\\n今月中に大きな\\nお金の流れが変わります。\\n\\n「✨」を置いた方から順に\\n金運の波が届きます。\\n\\n#金運
---
ポイント: 3位→1位の順。各星座2行の短い解説。CTA+報酬で締め。

■ 構造B: 限定×恐怖型（eng率 10%超え。最強フック）
---
12星座中、たった2つだけ。\\n\\n今週、運命が大きく\\n動く星座があります。\\n\\nそれは…\\n蟹座と射手座です。\\n\\n特に蟹座は来週、\\n大きな決断を迫られるかも。\\nでも大丈夫。\\nあなたの直感を信じれば、\\n必ず正解に辿り着けます。\\n\\n「🔮」を置いた方に\\n今夜良い流れが届きます。\\n\\n#星座占い
---
ポイント: 「たった○つだけ」で焦らし。「それは…」で1行空け。

■ 構造C: 緊急×時限型（スピ層に刺さる）
---
今夜23時までに\\nこれをやってください。\\n\\n枕元にコップ1杯の水を置く。\\nそれだけで、\\n明日の運気が整います。\\n\\n特に蠍座と魚座の方は\\n効果が出やすい時期です。\\n\\n「🌙」を置いた方だけに\\n明日の開運ヒントが届きます。\\n\\n#今日の運勢
---
ポイント: 時限性「今夜」「3日以内」で行動を促す。具体的アクション。

【🔥 CTA（コメント誘発）ルール ※全投稿に必須】
最後のブロックに「絵文字を置く」CTAを必ず入れる。
CTAには必ず「置くとこうなる」というスピリチュアル報酬を添える。

■ CTA 報酬レベル（ドラマチックに書くこと）:
・「🔮」を置いた方に、\\n今夜良い流れが届きます。
・「✨」を置いて受け取って。\\n3日以内に嬉しい変化が起きます。
・「🌙」を置いた方だけに、\\n明日の開運ヒントが届きますよ。
・「🌸」を置くと、\\n春の良縁が動き出します。
・「⭐」を置いた方から順に、\\n運気の流れが変わり始めます。
・「🍀」を置いた方に、\\n今週中に嬉しい連絡が届きます。

■ CTA NG（こう書いたら不合格）:
・「🔮を置いてね」（報酬なし→動機不足）
・「コメントしてください」（直接的すぎ→Bot臭い）
・「いいねとフォローお願いします」（乞食感→論外）
・同じ絵文字を2件以上で使う（バリエーション必須）

【緊急性ワード（10件中3件以上に入れる）】
「今夜」「今週中に」「3日以内に」「明日の朝」「今月中に」「48時間以内」
→ 時限性があると行動率が2倍になる

【星座バリエーションルール】
・10件で12星座を満遍なく使う
・同じ星座を3件以上で1位にしない
・「○座と○座」のペア指名は効果的

【Bot臭さ排除ルール】
1. ハッシュタグはcontentの末尾に自然に含める（hashtagフィールドは空文字）
2. 締め文とハッシュタグのキーワード重複禁止
3. 10件全て異なるCTA絵文字・締め方にする
4. 「いかがでしたか？」等のBot定型文は禁止
5. 人間が書いたように見える文体を最優先
6. 「占い師として」「プロの見解」等の権威主張禁止

【フックの型（この3つから選ぶ）】
A. 恐怖×期待型: 「○月に人生が変わる星座。」「無視すると損する星座TOP3」
B. 限定型: 「12星座中、たった2つだけ。」「今週の注意星座、3つ。」
C. ランキング見出し型: 「【金運が爆発する星座TOP3】」「【急に嬉しい連絡がくる星座】」

【📅 今日のシリーズコンテンツ（10件中1件目に必ず入れる）】
{today_series}
→ 1件目はこのシリーズ投稿にする。フックに「【毎週○曜】」を入れて定番感を出す。
→ 「来週も見たい」と思わせる内容にする。

【フォロー導線（10件中2件に入れる）】
CTAの後に、以下のようなフォロー誘導を自然に入れる:
・「フォローしておくと\\n明日の運勢も届きますよ🌙」
・「毎日届く星占い、\\nフォローで受け取れます✨」
※ 全投稿に入れるとBot臭くなるので、10件中2件だけ。

【生成ルール】
1. 10件生成（アフィリエイトなし）
2. 各投稿は150-300文字
3. 具体的な星座名を含める
4. ランキング型を10件中5件以上にする（最もバズるため）
5. 全投稿にCTA（絵文字を置く＋報酬）を含める
6. ハッシュタグはcontentに含める（hashtagフィールドは空文字）
7. 使用可能ハッシュタグ: #今日の運勢 #恋愛運 #金運 #仕事運 #タロット #星座占い
8. 10件全てのフック・CTA・星座の組み合わせをユニークにする
9. 1件目は今日のシリーズコンテンツ（上記参照）
10. 10件中2件にフォロー導線を含める（上記参照）

【出力形式】厳密にJSON配列のみ。余計なテキスト不要。
[
  {{
    "pattern_name": "パターン名",
    "category": "カテゴリ",
    "content": "投稿本文（\\nで改行、末尾にCTA+ハッシュタグ含む）",
    "hashtag": "",
    "quality_score": {{"hook":8,"usefulness":8,"specificity":8,"tempo":8,"persona_match":8,"mobile_readability":8,"average":8.0}}
  }}
]
"""

    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4096,
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

    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    text = result["content"][0]["text"]
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        print("ERROR: JSON抽出失敗")
        print(text[:500])
        sys.exit(1)

    posts = json.loads(m.group())
    today_str = datetime.now(JST).strftime("%Y%m%d")
    post_count = len(history.get("posts", []))

    for i, p in enumerate(posts):
        p["id"] = f"post_{today_str}_{post_count + i + 1:03d}"
        p["platform"] = "threads"
        p["is_affiliate"] = False
        p["affiliate_comment"] = None
        p["status"] = "queued"
        queue["queue"].append(p)

    save_json("state/post-queue.json", queue)
    print(f"生成完了: {len(posts)}件をキューに追加")
    print(f"学習データ参照: {winning.get('data_count', 0)}件 (信頼度: {winning.get('confidence', 'none')})")


if __name__ == "__main__":
    main()
