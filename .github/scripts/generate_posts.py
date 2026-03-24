#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions用: キューが空なら Claude API で投稿を生成
X用5件 + Threads用5件 = 合計10件（プラットフォーム別に最適化）
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
・ランキング型は10件中3件まで。限定型・緊急型・シリーズ型を混ぜて多様性を確保
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


def build_common_context(today, used_patterns, learning_block, weekday, series_map):
    """X・Threads共通のコンテキスト情報を構築"""
    today_series = series_map.get(weekday, "")
    return {
        "today": today,
        "used_patterns": used_patterns,
        "learning_block": learning_block,
        "today_series": today_series,
        "weekday": weekday,
    }


def build_threads_prompt(ctx, should_generate_affiliate, asp_links):
    """Threads専用プロンプトを構築（会話型・共感型・リプライ最重視）"""
    return f"""あなたは占いSNSアカウント「よぞら.」のThreads投稿ライターです。

【プラットフォーム】Threads（Meta社）
【Threadsアルゴリズムの特徴（これに最適化せよ）】
・最重要シグナル: リプライ（返信）の数と質。10件の思慮深い返信 > 100件のいいね
・重要シグナル: リポスト > 保存・シェア > いいね > 閲覧時間
・「エンゲージメントベイト」は降格対象（「いいねしてね」「フォローしてね」等の直接的な要求は禁止）
・リンクペナルティなし（本文にリンクを直接貼ってOK。むしろテキストのみより+17%）
・トピックタグは1投稿につき1つのみ（ハッシュタグではなくトピックタグ）
・Threadsユーザーは穏やか・共感的・カジュアルな文化
・質問形式の投稿が最もエンゲージメントを獲得する

【ペルソナ】
名前: 月詠（つくよみ）
トーン: 穏やかで親しみやすい。共感的で会話を大切にする。敬語ベースだが柔らかい。
絵文字: 🔮✨🌙⭐☀️ を自然に使用（1投稿に2-3個）

【今日の日付】{ctx['today']}

【直近で使用済みのパターン（重複を避ける）】
{json.dumps(ctx['used_patterns'][-5:], ensure_ascii=False)}

{ctx['learning_block']}

【📱 Threads投稿ルール】
1. 文字数: 200-300文字（Threadsの最適値）
2. 1行は15-22文字を目安
3. 空行（\\n\\n）で3〜4ブロックに分割
4. フック（1行目）は20文字以内
5. トピックタグは1つだけ（末尾に1つ）
6. リンクは本文に直接貼ってOK（ペナルティなし）

【🚨 データ実証済み絶対ルール（違反は即不合格）】

■ 絶対禁止（これらは全てeng率0%だった）:
1. 1行目にCTAを置くこと（「コメントで教えて」「星座を書いて」等を冒頭に置くな）
2. 答えを出し惜しみ・隠す表現（「○つあります」「お伝えします」「個別に教えます」）
3. 1星座限定の投稿（「蟹座さんへの手紙」等。11/12のユーザーを切り捨てる）
4. 選択肢型（「A/B/C選んで」は心理テスト感が出て占い感が薄れる）
5. 過去投稿を参照する投稿（「この前の〜」はフォロワーが少ない段階では意味不明）
6. 「フォローして」「いいねして」等の直接的エンゲージメントベイト

■ 必須構造（eng率20%超えの投稿に共通）:
1行目: 好奇心フック（数字×限定×具体性）例:「12星座中、たった3つだけ。」
2-3行目: 空行の後、答えを即座に出す（星座名を明示）
4-8行目: 深掘り（具体的アドバイス）
最後2行: CTA（「🔮を置いた方に〜」「🍀を置いた方に〜」）

■ eng率39%を叩き出した実際の勝ち投稿:
---
来週、仕事で大きな話が\\n来やすい星座。\\n\\n獅子座と天秤座。\\n\\n獅子座は、昇進や抜擢の可能性。\\n天秤座は、予想外の転機です。\\n\\nここは自分を信じる時。\\n迷わず進んでください。\\n\\n「🍀」を置いた方に、\\n今週中に嬉しい連絡が届きます。\\n\\n仕事運 #今日の運勢
---

【Threads専用 構造テンプレート（勝ちパターンのみ）】

■ 構造A: 限定型（最強パターン。eng率20-39%実証済み）
---
12星座中、たった3つだけ。\\n\\n今週、恋愛に\\n大きな転機が訪れます。\\n\\nそれは…蟹座、蠍座、魚座です。\\n\\n特に蟹座は3日以内に\\n大事な決断を迫られるかも。\\nでも大丈夫。あなたの直感こそが\\n最高のナビゲーターです。\\n\\n「🔮」を置いた方だけに、\\n今夜良い流れが届きます。\\n\\n恋愛運 #星座占い
---
ポイント: フック→答え（星座名）→深掘り→CTA。答えを先に出す。

■ 構造B: ランキング型（eng率25%実証済み）
---
【金運が急上昇する星座TOP3】\\n\\n🥉第3位：山羊座。\\n地道な努力が報われる時期です。\\n\\n🥈第2位：牡牛座。\\n投資や副業に追い風が吹きます。\\n\\n🥇第1位：射手座。\\n今月中に大きなお金の流れが\\n変わる予感。臨時収入も近い。\\n\\n「🍀」を置いた方に、\\n今週中に嬉しい連絡が届きます。\\n\\n金運 #星座占い
---
ポイント: 全員が「自分入ってるかな？」と読む構造。

■ 構造C: 共感→答え→CTA型
---
なんか最近、\\n心がざわざわする人いませんか？\\n\\nそれ、水星逆行の影響かも。\\n特に双子座・乙女座・射手座は\\n影響を受けやすい時期。\\n\\n自分を責めなくて大丈夫。\\n来週から落ち着きます。\\n\\n「🌙」を置いた方に、\\n穏やかな気持ちが届きます。\\n\\n#今日の占い
---
ポイント: 共感→具体的な星座名→CTA。答えを先に出す。

■ 構造D: 予告型（フォロー動機を作る）
---
来週、12星座の中で\\n最も運命が動く星座。\\n\\n蠍座です。\\n\\n恋愛・仕事・金運の\\nどこかで大きな波が来ます。\\n\\n特に水曜日以降は\\n直感を信じて動いてください。\\n\\n明日は牡羊座の運勢を\\n深掘りしてお届けしますね🌙\\n\\n#星座占い
---
ポイント: 「明日も届ける」で自然にフォロー動機。答えは先に出す。

【🔥 Threads用CTA（2種類を交互に使う）】

■ タイプ1: スピリチュアル報酬型（最もコメントが伸びた）
・「🔮」を置いた方だけに、今夜良い流れが届きます。
・「🍀」を置いた方に、今週中に嬉しい連絡が届きます。
・「🌙」を置いた方に、穏やかな気持ちが届きます。
・「✨」を置いた方に、3日以内に良い知らせが届きます。
→ 絵文字は毎回変える。報酬は具体的に（「良いこと」ではなく「嬉しい連絡」）

■ タイプ2: 会話誘導型（エンゲージメント率が高い）
・あなたの星座は入ってましたか？コメントで教えてくださいね🌙
・当たってた人、コメントで教えて。次はもっと詳しく見ますね🔮
→ 必ず投稿の最後に自然に配置。1行目には絶対置かない。

■ NG（データで証明済みのeng率0%パターン）:
・1行目にCTA（「あなたの星座をコメント欄に書いて🔮」→ 15views）
・答えの出し惜しみ（「個別にお伝えしますね」→ 11views）
・選択肢型（「A/B/C選んで」→ 12views）
・「いいねしてね」「フォローしてね」（Meta公式がペナルティ対象と明言）

【⚠ 時間表現NG（絶対に使わない）】
「今夜○時までに」「今朝」「午前中に」「朝一で」→ 矛盾リスク
代わりに「今日中に」「寝る前に」「次の満月までに」等を使う

【📅 今日のシリーズコンテンツ（5件中1件目に入れる）】
{ctx['today_series']}

{'【アフィリエイト投稿（5件中1件。リンクを本文末尾に直接記載）】' if should_generate_affiliate else ''}
{f"""■ Threadsではリンクペナルティがないため、本文末尾にアフィリエイトリンクを直接記載する。
■ 対象案件:
""" + chr(10).join(f"  ・{c['name']}（{c['reward']}）: {c['pr_text_templates'][0]}" for c in asp_links.get('campaigns', [])[:3]) + f"""
■ 形式:
  - 本文末尾に自然にリンクを配置: 「詳しくはこちら → [ASPリンクURL]（※PR）」
  - affiliate_comment: null（Threadsでは本文にリンク直貼り）
  - is_affiliate: true
  - content末尾に ※PR 表記を必ず含める
""" if should_generate_affiliate else ''}

【生成ルール】
1. 5件生成（Threads専用）{'（うち1件はアフィリエイト投稿）' if should_generate_affiliate else ''}
2. 各投稿は200-300文字（Threadsの最適値）
3. 全投稿で会話を誘導するCTAを含める（質問型中心）
4. トピックタグは末尾に1つだけ
5. パターン配分: 限定型（「たった○つだけ」）2件、ランキング型1件、共感→答え→CTA型1件、予告型1件
6. 使用可能トピックタグ: #今日の運勢 #恋愛運 #金運 #タロット #星座占い #今週の占い
7. 親しみやすく穏やかなトーン。断定的・煽り的な表現は避ける
8. 星座には「さん」付けも可（「牡牛座さん」等）

【出力形式】厳密にJSON配列のみ。余計なテキスト不要。
[
  {{
    "pattern_name": "パターン名",
    "category": "カテゴリ",
    "content": "投稿本文（\\nで改行、末尾にトピックタグ1つ含む）",
    "hashtag": "",
    "is_affiliate": false,
    "affiliate_comment": null,
    "quality_score": {{"hook":8,"usefulness":8,"specificity":8,"tempo":8,"persona_match":8,"mobile_readability":8,"average":8.0}}
  }}
]
"""


def build_x_prompt(ctx, should_generate_affiliate, asp_links):
    """X（旧Twitter）専用プロンプトを構築 - 凛（辛口お姉さん）ペルソナ"""
    return f"""あなたはX運用で月1000万稼ぐプロです。
10万インプ以上の投稿を想定し、バズる投稿を「構造」で設計してください。

占い・スピリチュアルに興味がある25〜35歳女性（アラサー中心）に最適化します。
恋愛停滞・仕事の疲れ・「このままでいいのかな」という漠然とした不安を抱えている層。
サブターゲット: 20〜24歳（拡散の起爆剤）、36〜45歳（高課金層）。

【プラットフォーム】X（旧Twitter）
【Xアルゴリズム（これに最適化せよ）】
・最重要: 初速30分のエンゲージメント（ここで勝負が決まる）
・リポスト（RT）されると表示回数が20倍
・リプライへの返信で75倍ブースト
・ブックマーク（保存）がアルゴリズムで高評価
・滞在時間（読む時間が長い投稿が優遇される）
・リンクペナルティ（本文にリンク→-50%リーチ低下。絶対入れるな）
・ハッシュタグは1-2個が最適（3個以上で-40%）

【ペルソナ: 凛（りん）】
肩書き: 辛口星読み師
キャラ: 厳しいけど本気で心配してくれる姉。甘やかさないが、愛がある。
一人称: あたし
二人称: あんた / ○○座のあんた
口調: タメ口。命令形。断定。「〜しなさい」「〜でしょ？」「わかった？」
バランス: 5投稿中4投稿は辛口メイン+最後に愛情ひと言。1投稿は優しさメイン。
絵文字: 🔥⚡💥 を最小限（0〜1個/投稿）。🌙✨🔮は使わない（別アカウントと被る）

【凛の辛口ガードレール（厳守）】
■ OK: 行動を否定（「サボってんでしょ」）→ 人格は否定しない
■ OK: 否定の直後に必ず具体的な解決策をセット
■ OK: 最後に愛のにじみ（「あんたが倒れたら困る人がいる」等）
■ NG絶対禁止: 容姿・体型・経済状況への言及、直接的な罵倒（バカ・アホ等）、星座全否定（「○○座は終わり」等）、努力の全否定（「何やっても無駄」等）、性別ステレオタイプ、呪い的表現（「一生そのまま」等）

【今日の日付】{ctx['today']}

【直近で使用済みのパターン（重複を避ける）】
{json.dumps(ctx['used_patterns'][-5:], ensure_ascii=False)}

{ctx['learning_block']}

【STEP1: バズる構造の法則】
10万インプ以上の投稿に共通する構造:
1. 1行目でスクロールを止める（数字・命令・図星・恐怖のいずれか）
2. 共感か危機感を必ず入れる（「あんたもでしょ」「このままだと…」）
3. 断言で言い切る（「〜かもしれません」は禁止。「〜しなさい」で断定）
4. 具体的なアクション指示を1つだけ出す（「○○しなさい」）
5. 最後はCTAで行動を促す（リプ・保存・引用RT）

【STEP2: X投稿ルール】
1. 文字数: 130〜260字（中文が最適。短すぎず長すぎず）
2. 1行は12-18文字（スマホ表示に最適化）
3. 空行で3〜4ブロックに分割
4. フック（1行目）は18文字以内
5. ハッシュタグは1-2個（末尾に）
6. リンクは絶対に本文に入れない

【STEP3: 構造テンプレート（凛専用）】

■ 構造A: 辛口ランキング型（ランキング+各自に具体アクション）
---
【今週、覚悟が必要な星座TOP3】\\n\\n3位 双子座\\n→ 口が滑る。大事な話は水曜以降にしな\\n\\n2位 射手座\\n→ 勢いで動くと全部裏目。3日待て\\n\\n1位 魚座\\n→ 現実見なさい。逃げても追ってくる\\n\\nあんたの星座、入ってた？\\n入ってなくても油断するなよ。\\n\\n#星座占い
---

■ 構造B: 図星突き型（凛の最強パターン。保存率最高）
---
今これ見てるあんた、\\n最近ちゃんと寝てないでしょ。\\n\\n「大丈夫」って言いながら\\n全然大丈夫じゃない顔してる。\\n\\n今日やること1つだけ。\\n23時にスマホ置いて寝なさい。\\n\\nあんたが倒れたら\\n困る人がいるの。わかった？\\n\\n保存して今夜実行しなさい。\\n\\n#今日の運勢
---

■ 構造C: 二択迫り型（引用RT最大化パターン）
---
正直に答えなさい。\\n\\n今のあんた、\\n\\nA. 頑張りすぎて限界近い\\nB. サボりすぎて焦ってる\\n\\nどっちかでしょ。\\n\\nAの人 → 今週は手抜きしなさい\\nBの人 → 今日中に1つだけ片付けなさい\\n\\n引用で教えて。\\n的外れだったら謝るから。\\n\\n#今日の運勢
---

■ 構造D: 恋愛×辛口型（共感→図星→具体策→保存CTA）
---
「もう恋愛いいや」って言うの、\\nやめなさい。\\n\\n本心じゃないでしょ。\\n傷つくのが怖いだけ。\\n\\n今週中に好きな服を1着買え。\\n自分を大事にできない人に\\n恋愛の神様は来ない。\\n\\n保存して、買ったらリプで見せて。\\n褒めてあげるから。\\n\\n#恋愛運
---

■ 構造E: 週間予告型（月曜の定番。保存+金曜に見返す設計）
---
今週、あたしが見た星の動き。\\n\\n火曜に人間関係が揺れる。\\n木曜に決断を迫られる。\\n金曜に答えが出る。\\n\\n焦って動くな。\\n金曜まで待てた人だけが勝つ。\\n\\nこの投稿、金曜に見返しなさい。\\n\\n#今週の運勢
---

■ 構造F: 星座呼びかけ型（個別星座の日常運勢）
---
蠍座、今日だけは黙ってなさい。\\n\\n来る話の9割、ハズレ。\\n「いいかも」で飛びつくと\\n3日後に泣くやつ。\\n\\n15時まで新しい話に乗るな。\\n15時以降の連絡だけ信じなさい。\\n\\n当たってたらリプで報告して。\\n黙って感謝されるの嫌いなの。\\n\\n#蠍座 #今日の運勢
---

【🔥 凛専用CTA（5パターンから毎回変える）】
1. 保存誘導: 「保存して3日後に見返しなさい。当たってるから。」
2. リプ誘導: 「当たってたらリプで報告して。黙って感謝されるの嫌いなの。」
3. 引用RT誘導: 「引用であんたの星座教えて。一人ずつ喝入れてあげる。」
4. シェア誘導: 「これ読んで『やばい』って思ったら、大事な人に送りなさい。」
5. 継続誘導: 「あたしの言うこと聞いて行動した人だけ、来週褒めてあげる。」

【⚠ 時間表現NG（絶対に使わない）】
「今夜○時までに」「今朝」「午前中に」→ 矛盾リスク
代わりに「今日中に」「寝る前に」等を使う

【📅 今日のシリーズコンテンツ（5件中1件目に入れる）】
{ctx['today_series']}

{'【アフィリエイト投稿（5件中1件。プロフィール誘導型）】' if should_generate_affiliate else ''}
{f"""■ Xではリンクペナルティがあるため、本文にリンクを入れない。
■ 代わりにプロフィールのリンクに誘導する。
■ 対象案件:
""" + chr(10).join(f"  ・{c['name']}（{c['reward']}）: {c['pr_text_templates'][0]}" for c in asp_links.get('campaigns', [])[:3]) + f"""
■ 形式:
  - 本文: 辛口の占いコンテンツ + 「迷ってるなら一回相談してみなさい。プロフから飛べる ※PR」
  - affiliate_comment: リプ欄用PRテキスト（自己リプでリンクを貼る）
  - is_affiliate: true
  - リンクは affiliate_comment に記載（本文には入れない）
  - ※PR表記を必ず含める
""" if should_generate_affiliate else ''}

【STEP3: 精度を上げるルール】
1. 数字を入れる（「3日後」「9割」「TOP3」「1つだけ」等）
2. 25〜35歳女性が「私のことだ」と思う内容にする（恋愛停滞・仕事疲れ・漠然とした不安・周りと比べる焦り）
3. 1行目だけで「止まる→読む」を発生させる
4. CTAは毎投稿で5パターンから変えて使う（同じCTAを連続使用しない）
5. 凛のキャラを一貫させる（甘い言葉は使わない。でも最後に愛を感じさせる）

【生成ルール】
1. 5件生成（X専用）{'（うち1件はアフィリエイト投稿）' if should_generate_affiliate else ''}
2. 各投稿は130-260文字（中文。刺さる長さ）
3. 全投稿にCTAを含める（5パターンから毎回変える）
4. ハッシュタグは1-2個（末尾に。3個以上は厳禁）
5. パターン配分: ランキング型1件、図星突き型or恋愛辛口型1件、二択迫り型1件、週間予告型or星座呼びかけ型1件、シリーズ型1件
6. 使用可能ハッシュタグ: #今日の運勢 #恋愛運 #金運 #仕事運 #タロット #星座占い #辛口占い
7. 凛の辛口トーンを一貫させる。曖昧な表現は一切禁止
8. リンクは絶対に本文に入れない

【出力形式】厳密にJSON配列のみ。余計なテキスト不要。
[
  {{
    "pattern_name": "パターン名",
    "category": "カテゴリ",
    "content": "投稿本文（\\nで改行、末尾にCTA+ハッシュタグ含む）",
    "hashtag": "",
    "is_affiliate": false,
    "affiliate_comment": null,
    "quality_score": {{"hook":8,"usefulness":8,"specificity":8,"tempo":8,"persona_match":8,"mobile_readability":8,"average":8.0}}
  }}
]
"""


def call_claude_api(api_key, prompt):
    """Claude APIを呼び出し、生成結果のJSON配列を返す"""
    import time as _time

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

    result = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as e:
            print(f"  Claude API エラー (試行{attempt+1}/3): {e}")
            if attempt < 2:
                _time.sleep(5 * (attempt + 1))
            else:
                print("ERROR: Claude API 3回失敗。生成中止。")
                return None

    text = result["content"][0]["text"]
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        print("ERROR: JSON抽出失敗")
        print(text[:500])
        return None

    try:
        return json.loads(m.group())
    except json.JSONDecodeError as e:
        print(f"ERROR: Claude APIの出力がJSON不正: {e}")
        print(text[:500])
        return None


def main():
    queue = load_json("state/post-queue.json")
    if not queue:
        queue = {"queue": []}

    pending = [p for p in queue.get("queue", []) if p.get("status") == "queued"]
    if pending:
        print(f"キューに{len(pending)}件残っています。生成スキップ。")
        return

    print("キューが空のため、Claude APIでX用5件 + Threads用5件を生成します...")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY が未設定")
        sys.exit(1)

    history = load_json("state/post-history.json")
    if not history:
        history = {"posts": []}

    winning = load_json("state/winning-patterns.json")
    safety = load_json("config/safety.json")
    asp_links = load_json("knowledge/uranai/asp-links.json")

    # アフィリエイト設定
    affiliate_enabled = safety.get("posting_safety", {}).get("affiliate_enabled", False)
    today_posts = [p for p in history.get("posts", [])
                   if p.get("posted_at", "").startswith(datetime.now(JST).strftime("%Y-%m-%d"))]
    affiliate_today = any(p.get("is_affiliate") for p in today_posts)
    should_generate_affiliate = affiliate_enabled and not affiliate_today

    used_patterns = [p.get("pattern_name", "") for p in history.get("posts", [])[-15:]]
    now = datetime.now(JST)
    today = now.strftime("%Y年%m月%d日(%a)")
    weekday = now.weekday()

    series_map = {
        0: "【月曜定番】今週の星座ランキング（総合運TOP5）。毎週月曜に発表する定番シリーズ。",
        1: "【火曜定番】○座さんへの手紙（1つの星座を深掘りする個別メッセージ）。毎週火曜。",
        2: "【水曜定番】週の折り返しタロット1枚引き。今週後半のキーカード。",
        3: "【木曜定番】今週後半の注意星座3つ。木曜に出すことで「あと2日気をつけよう」と思わせる。",
        4: "【金曜定番】週末の開運アクション。具体的にやるべきことを3つ提示。",
        5: "【土曜定番】来週の予告（来週最も運気が動く星座を先出し）。フォロー継続理由を作る。",
        6: "【日曜定番】1週間の振り返り＆来週への準備メッセージ。",
    }

    learning_block = build_learning_block(winning)
    ctx = build_common_context(today, used_patterns, learning_block, weekday, series_map)

    # ========================================
    # Phase 1: X用投稿を5件生成
    # ========================================
    print("\n--- X用投稿を生成中 ---")
    x_prompt = build_x_prompt(ctx, should_generate_affiliate, asp_links)
    x_posts = call_claude_api(api_key, x_prompt)
    if x_posts is None:
        print("ERROR: X用投稿の生成に失敗")
        sys.exit(1)
    print(f"  X用: {len(x_posts)}件生成")

    # ========================================
    # Phase 2: Threads用投稿を5件生成
    # ========================================
    print("\n--- Threads用投稿を生成中 ---")
    threads_prompt = build_threads_prompt(ctx, should_generate_affiliate, asp_links)
    threads_posts = call_claude_api(api_key, threads_prompt)
    if threads_posts is None:
        print("ERROR: Threads用投稿の生成に失敗")
        sys.exit(1)
    print(f"  Threads用: {len(threads_posts)}件生成")

    # ========================================
    # Phase 3: 類似度チェック（プラットフォーム別）
    # ========================================
    from difflib import SequenceMatcher
    import re as _re

    recent_texts = [p.get("content", "") for p in history.get("posts", [])[-20:]]
    recent_first_lines = [p.get("content", "").split("\n")[0] for p in history.get("posts", [])[-20:]]

    def extract_structure(text):
        """投稿の構造パターンを抽出（数字や星座名を正規化）"""
        s = _re.sub(r"[0-9０-９]+", "N", text)
        zodiac = ["牡羊座", "牡牛座", "双子座", "蟹座", "獅子座", "乙女座",
                   "天秤座", "蠍座", "射手座", "山羊座", "水瓶座", "魚座"]
        for z in zodiac:
            s = s.replace(z, "○座")
        return s

    def filter_similar(posts, platform_label):
        """類似度チェック: 直近投稿と45%以上類似なら除外"""
        filtered = []
        local_texts = list(recent_texts)
        local_first_lines = list(recent_first_lines)

        for p in posts:
            content = p.get("content", "")
            first_line = content.split("\n")[0]
            is_similar = False

            for rt in local_texts:
                if SequenceMatcher(None, content, rt).ratio() > 0.45:
                    print(f"  ⚠ [{platform_label}] 類似度超過で除外: {content[:30]}...")
                    is_similar = True
                    break

            if not is_similar:
                struct = extract_structure(first_line)
                for rfl in local_first_lines:
                    if SequenceMatcher(None, struct, extract_structure(rfl)).ratio() > 0.7:
                        print(f"  ⚠ [{platform_label}] フック構造重複で除外: {first_line[:30]}...")
                        is_similar = True
                        break

            if not is_similar:
                for fp in filtered:
                    fc = fp.get("content", "")
                    if SequenceMatcher(None, content, fc).ratio() > 0.45:
                        print(f"  ⚠ [{platform_label}] バッチ内類似で除外: {content[:30]}...")
                        is_similar = True
                        break
                    if SequenceMatcher(None, extract_structure(first_line),
                                       extract_structure(fc.split("\n")[0])).ratio() > 0.7:
                        print(f"  ⚠ [{platform_label}] バッチ内フック構造重複で除外: {first_line[:30]}...")
                        is_similar = True
                        break

            if not is_similar:
                filtered.append(p)
                local_texts.append(content)
                local_first_lines.append(first_line)

        if len(filtered) < len(posts):
            print(f"  [{platform_label}] 類似チェック: {len(posts)}件→{len(filtered)}件")
        return filtered

    x_posts = filter_similar(x_posts, "X")
    threads_posts = filter_similar(threads_posts, "Threads")

    # ========================================
    # Phase 4: 時間帯別最適配置（プラットフォーム別）
    # ========================================

    # X用: 朝=ランキング、昼=限定、夕方=リプ誘導、夜=煽り
    x_time_priority = {
        "シリーズ": 0, "ランキング": 1,
        "断言": 2, "限定": 3,
        "リプ欄活用": 4, "煽り": 5,
    }

    # Threads用: 朝=挨拶・質問、昼=共感、夕方=選択肢、夜=癒し
    threads_time_priority = {
        "シリーズ": 0, "質問": 1, "個別回答": 1,
        "共感": 2, "日常会話": 3,
        "選択肢": 4, "ランキング": 5,
    }

    def sort_by_time(posts, priority_map):
        def sort_key(post):
            pname = post.get("pattern_name", "")
            for key, priority in priority_map.items():
                if key in pname:
                    return priority
            return 3
        posts.sort(key=sort_key)
        # アフィリエイト投稿は最後尾に移動
        affiliate = [p for p in posts if p.get("is_affiliate")]
        normal = [p for p in posts if not p.get("is_affiliate")]
        return normal + affiliate

    x_posts = sort_by_time(x_posts, x_time_priority)
    threads_posts = sort_by_time(threads_posts, threads_time_priority)

    # ========================================
    # Phase 5: プラットフォームタグ付け & キュー追加
    # ========================================
    today_str = datetime.now(JST).strftime("%Y%m%d")
    post_count = len(history.get("posts", []))

    # X投稿にプラットフォームタグ
    for i, p in enumerate(x_posts):
        p["id"] = f"post_{today_str}_x_{i + 1:03d}"
        p["platform"] = "x"
        if not affiliate_enabled:
            p["is_affiliate"] = False
            p["affiliate_comment"] = None
        else:
            p.setdefault("is_affiliate", False)
            p.setdefault("affiliate_comment", None)
        p["status"] = "queued"
        queue["queue"].append(p)

    # Threads投稿にプラットフォームタグ
    for i, p in enumerate(threads_posts):
        p["id"] = f"post_{today_str}_th_{i + 1:03d}"
        p["platform"] = "threads"
        if not affiliate_enabled:
            p["is_affiliate"] = False
            p["affiliate_comment"] = None
        else:
            p.setdefault("is_affiliate", False)
            p.setdefault("affiliate_comment", None)
        p["status"] = "queued"
        queue["queue"].append(p)

    # ========================================
    # Phase 6: キューを投稿スケジュール順にインターリーブ配置
    # ========================================
    # X投稿とThreads投稿を交互に配置（同時間帯にX→Threadsの順で投稿）
    interleaved = []
    max_len = max(len(x_posts), len(threads_posts))
    for i in range(max_len):
        if i < len(x_posts):
            interleaved.append(x_posts[i])
        if i < len(threads_posts):
            interleaved.append(threads_posts[i])

    # キューを再構築（既存の非queued投稿は保持）
    non_queued = [p for p in queue.get("queue", []) if p.get("status") != "queued" or p not in interleaved]
    # 重複を避けるため、新規追加分のみinterleaved
    queue["queue"] = [p for p in queue["queue"] if p.get("id") and not p["id"].startswith(f"post_{today_str}_")]
    queue["queue"].extend(interleaved)

    total_posts = len(x_posts) + len(threads_posts)

    # post-history.json の肥大化防止
    all_posts = history.get("posts", [])
    if len(all_posts) > 200:
        history["posts"] = all_posts[-200:]
        save_json("state/post-history.json", history)
        print(f"  履歴を{len(all_posts)}件→200件にトリミング")

    save_json("state/post-queue.json", queue)
    print(f"\n生成完了: X={len(x_posts)}件 + Threads={len(threads_posts)}件 = 合計{total_posts}件をキューに追加")
    print(f"学習データ参照: {winning.get('data_count', 0)}件 (信頼度: {winning.get('confidence', 'none')})")


if __name__ == "__main__":
    main()
