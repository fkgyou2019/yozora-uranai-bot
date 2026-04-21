#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions用: キューが空なら Claude API で投稿を生成
Threads用10件を生成（X用は現在無効）
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

# 7スロット: 06:07/07:07/08:07/09:37/12:07/18:07/20:07 JST
# 2026-04-14確定: ゴールデンタイム完全対応版
# 朝ゴールデン(6〜8:59)×3 + 第2ゴールデン(9〜9:59)×1 + 昼(12〜12:59)×1 + 夕方(18〜18:59)×1 + 夜(20〜20:59)×1
EXPERIMENT_TIME_SLOTS = [
    {"hour": 6,  "minute": 7,  "slot": "めざまし型（06:07）",        "structure": "J",
     "pattern_hint": "全12星座1行メッセージ型（めざまし型・起床直後の今日の運勢チェック需要・全員ターゲット）"},
    {"hour": 7,  "minute": 7,  "slot": "仕事運注意喚起型（07:07）",   "structure": "G",
     "pattern_hint": "注意喚起型・仕事運特化（来週の仕事・昇進・転機・職場の動き予告型・通勤開始時間帯・ER42%実績型・フック例:「来週、仕事で大きな話が来やすい星座。」「今月、職場の空気が変わる星座。」）"},
    {"hour": 8,  "minute": 7,  "slot": "しいたけ共感型（08:07）",    "structure": "H",
     "pattern_hint": "しいたけ式共感・場面描写型（恋愛の悩みの場面を具体的に描写・恋愛迷子ターゲット・通勤ピーク）"},
    {"hour": 9,  "minute": 37, "slot": "ランキング型（09:37）",       "structure": "B",
     "pattern_hint": "ランキング型（金運・仕事運・恋愛運TOP3形式・🥉🥈🥇メダル絵文字必須・【】タイトル必須・第2ゴールデン帯・ER25%実績型・仕事運ランキングを優先）"},
    {"hour": 12, "minute": 7,  "slot": "臨時収入型（12:07）",        "structure": "X2",
     "pattern_hint": "臨時収入型（●●万円の臨時収入が入ってくる・断言・昼休み拡散狙い）",
     "buzz_type": "rinji_income"},
    {"hour": 18, "minute": 7,  "slot": "哲学深掘り型（18:07）",      "structure": "H",
     "pattern_hint": "しいたけ式深掘り哲学型（占い師の視点・深い言葉・内省モード・仕事迷子＋スピ好きターゲット・退勤後）"},
    {"hour": 20, "minute": 7,  "slot": "スルー恐怖型（20:07）",      "structure": "X4",
     "pattern_hint": "スルー恐怖型（無視・素通りしたら損＋金運ランキング・夜の拡散狙い）",
     "buzz_type": "suru_fear"},
]


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
    """学習データからプロンプトブロックを動的構築（permanent_rules + auto_analysis を統合）"""
    lines = []

    # =========================================================
    # A. permanent_rules（人間が書いた固定ルール・常に参照）
    # =========================================================
    perm = winning.get("permanent_rules", {}) if winning else {}
    if perm:
        lines.append("【📌 固定ルール（実績データ・人間確認済み）】")
        must = perm.get("must_use_patterns", [])
        for m in must:
            lines.append(f"  ✅ {m}")
        fixed_avoid = perm.get("avoid_patterns", [])
        for a in fixed_avoid[:3]:  # 長いので3件まで
            lines.append(f"  ❌ {a}")
        hook_rules = perm.get("hook_rules", [])
        if hook_rules:
            lines.append("  【フックルール】" + " / ".join(hook_rules[:3]))

    # =========================================================
    # B. auto_analysis（自動集計データ）
    # =========================================================
    count = winning.get("data_count", 0) if winning else 0
    if not winning or count < 3:
        if not perm:
            return """【市場分析から導出した勝ちパターン（初期値）】
・ランキング型は10件中3件まで。限定型・緊急型・シリーズ型を混ぜて多様性を確保
・フックは「恐怖×期待」型が最強（例: 「○月に人生が変わる星座。」）
・数字×限定×具体性の組み合わせが効く（例: 「12星座中、たった2つだけ。」）"""
        return "\n".join(lines)

    confidence = winning.get("confidence", "low")
    lines.append(f"\n【📊 自動学習データ（{count}件分析済み・信頼度:{confidence}）】")

    # トップパターンの配分指示
    top_patterns = winning.get("top_patterns", [])
    if top_patterns:
        lines.append("■ 実績パターン配分（10件中の目安）:")
        for tp in top_patterns[:4]:
            n = max(1, round(tp.get("weight", 5) / 10))
            lines.append(
                f"  ・{tp['pattern']}: {n}件"
                f"（eng率{tp.get('avg_engagement','?')}%・閲覧{tp.get('avg_views','?')}）"
            )

    # ベスト投稿の参考
    best = winning.get("best_posts", [])
    if best:
        lines.append("■ 最もバズった投稿のフック（実績）:")
        for b in best[:3]:
            lines.append(f"  ・「{b['first_line']}」→ eng率{b['eng_rate']:.1f}%・閲覧{b['views']}")

    # 特徴ランキング
    features = winning.get("feature_ranking", [])
    if features:
        lines.append("■ 効果が高い要素（実測）:")
        label_map = {
            "has_ranking":    "ランキング形式",
            "has_number_hook":"数字フック",
            "has_question":   "質問文",
            "has_cta_emoji":  "絵文字CTA",
            "has_fear_hook":  "恐怖・焦りフック",
        }
        for fr in features[:4]:
            label = label_map.get(fr["feature"], fr["feature"])
            lines.append(f"  ・{label} → avg eng {fr['avg_engagement']:.1f}%")

    # 時間帯×パターン交差分析
    slot_best = winning.get("auto_analysis", {}).get("slot_best_pattern", {})
    if slot_best:
        lines.append("■ 時間帯別・最強パターン（実測）:")
        for slot, info in slot_best.items():
            if info.get("sample_count", 0) >= 2:
                lines.append(
                    f"  ・{slot} → {info['best_pattern']}"
                    f"（eng{info['avg_eng']:.1f}%・閲覧{info['avg_views']:.0f}）"
                )

    # 避けるべき（自動検出分）
    auto_avoid = winning.get("auto_analysis", {}).get("auto_avoid_patterns", [])
    if auto_avoid:
        lines.append(f"■ 自動検出・低パフォーマンス: {len(auto_avoid)}パターン")
        for a in auto_avoid[:2]:
            lines.append(f"  ❌ {a}")

    # インサイト
    insights = winning.get("insights", [])
    if insights:
        lines.append("■ AI分析インサイト:")
        for ins in insights[:3]:
            lines.append(f"  ・{ins}")

    # インサイト
    insights = winning.get("insights", [])
    if insights:
        lines.append("■ AI分析のインサイト:")
        for ins in insights[:3]:
            lines.append(f"  ・{ins}")

    return "\n".join(lines)


def build_competitor_buzz_block(buzz_data: dict) -> str:
    """競合バズデータからライター向けの参考ブロックを構築"""
    if not buzz_data:
        return ""

    guidance = buzz_data.get("writer_guidance", {})
    hook_summary = buzz_data.get("hook_pattern_summary", {})
    structure_summary = buzz_data.get("structure_summary", {})

    lines = ["【🔍 競合40アカウント分析（バズパターン参考）】"]
    lines.append(f"※ 参考データ。直接コピーは禁止。エッセンスを自分の言葉で昇華すること。")

    # フックパターンTOP3
    top_hooks = list(hook_summary.items())[:4]
    if top_hooks:
        lines.append("■ 高ER フックパターン（競合実測）:")
        for name, v in top_hooks:
            lines.append(f"  ・{name}: avg_er={v['avg_er']:.4f}（{v['count']}件）")

    # 構造TOP3
    top_structs = list(structure_summary.items())[:3]
    if top_structs:
        lines.append("■ 高ER 構造パターン（競合実測）:")
        for name, v in top_structs:
            lines.append(f"  ・{name}: avg_er={v['avg_er']:.4f}（{v['count']}件）")

    # 強いフック文例（上位5件、コピー禁止注記つき）
    examples = guidance.get("strong_first_line_examples", [])[:5]
    if examples:
        lines.append("■ 競合バズ投稿の1行目（参考のみ・コピー禁止）:")
        for ex in examples:
            lines.append(f"  ・{ex}")

    # インサイト
    insight = guidance.get("insight", "")
    if insight:
        lines.append(f"■ 分析インサイト: {insight}")

    # 注意書き
    lines.append("※ 臨時収入・祈祷強要・いいね強要 → NG表現に該当。エッセンス（希少性・強運断言）のみ参考にすること。")

    return "\n".join(lines)


def has_real_affiliate_urls(asp_links: dict) -> bool:
    """affiliateURLが実際のURLか確認（プレースホルダー【】なら False）"""
    for c in asp_links.get("campaigns", []):
        url = c.get("affiliate_url", "")
        if url and url.startswith("http") and "【" not in url:
            return True
    return False


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

【⚠ ペルソナ厳守NG語（これらを使ったら即不合格）】
❌「あんた」「黙って」「聞きなさい」「しなさい」「やりな」
❌「バカ」「アホ」「ハズレ」「泣く」「無視」「ダメ出し」
❌「だよね？」「じゃん」「マジで」「ヤバい」「ウケる」「草」「それな」「知らんけど」「ぶっちゃけ」
❌ 命令口調・上から目線・姉御キャラ・辛口キャラは全て禁止
✅ 常に「です」「ます」「ですよ」「かもしれません」「ませんか？」の敬語
✅ 優しく寄り添う。読者を導く。決して叱らない。

【今日の日付】{ctx['today']}

【直近で使用済みのパターン（重複を避ける）】
{json.dumps(ctx['used_patterns'][-5:], ensure_ascii=False)}

{ctx['learning_block']}

【📱 Threads投稿ルール】
1. 文字数: 200-300文字（Threadsの最適値）
2. 1行は必ず25文字以内（超えると警告。各星座の説明も1行25文字以内で書くこと）
   NG例: 「牡羊座さんは、ここから仕事で新しいチャンスが」= 24文字 ギリギリOK
   OK例: 「牡羊座は、新しい扉が開く。」= 13文字 ✅
3. 空行（\\n\\n）で3〜4ブロックに分割
4. フック（1行目）は20文字以内
   - 推奨キーワード（閲覧数実績順）: 「今月後半」「急に」「4月〇〇」「明日からの〇日間」「今月」
   - 禁止: 「今週、〇〇する星座。」（「今週」単体は avg閲覧125=最弱。「今週後半」「今週から」等の具体表現はOK）
5. トピックタグは必ず1つ末尾に付ける（#今日の運勢/#恋愛運/#金運/#タロット/#星座占い/#今週の占い から選ぶ）
6. リンクは本文に直接貼ってOK（ペナルティなし）
7. ⚠️ 全体行数は必ず16行以内（改行含む）。これを超えると即不合格。

【🚨 データ実証済み絶対ルール（違反は即不合格）】

■ 絶対禁止（実績データで閲覧数36〜165・ER 0〜4%だったパターン）:
1. 1行目にCTAを置くこと（「コメントで教えて」「星座を書いて」等を冒頭に置くな）
2. 答えを出し惜しみ・隠す表現（「○つあります」「お伝えします」「個別に教えます」）
3. 1星座限定の投稿（「蟹座さんへの手紙」等。11/12のユーザーを切り捨てる）
4. 文字の選択肢型（「A/B/C選んで」はNG。ただし絵文字カード選択型〈構造E〉はOK）
5. 過去投稿を参照する投稿（「この前の〜」はフォロワーが少ない段階では意味不明）
6. 励まし型【閲覧数平均169・ER 8%: 最低パフォーマンス】: 「木星の優しい光が」「土星が微笑む」「春の陽射しが心強い」「○○座のあなたへ応援を」等、天体名+感情的励ましのみで構成される投稿。具体的な注意喚起・ランキング・予言のない純粋な励ましは禁止。
7. 抽象的な共感型【閲覧数平均153・ER 8%】: 「今日も頑張りましょう」「自分を信じて」等、星座名も具体的天文イベントも含まない抽象的な感情共有投稿。※構造C（天文イベント×質問型）はスコア24で廃止済み。天文イベント系投稿は生成しない。

■ 💥 高バズパターン（競合ER最上位・毎バッチ最低3本含める）:
以下のパターンを積極使用すること。「〜かもしれない」「〜でしょう」禁止。全て断言。

・断言型: 「やっぱり確信しました。あなた、超強運です。」「正直に言います。」「嘘は言いません。」
・臨時収入型: 「正直に申し上げますね。あなた〇月中に〇〇万円ほどの臨時収入が入ってきます。」（金額は5〜88万で変化させる）
・いいね強要型: 「い い ね を す る と 運 気 が 爆 上 が り し ま す⛩️」（スペース区切りで視覚インパクト）「🙏を置けた人、今から急にぜんぶうまくいきます。」
・スルー恐怖型: 「無視する人金運上がらんよ。」「飛ばしたら絶対にやめて下さい。」「素通りした人以外〜」「【ここでスルーしたら〇月、築き上げたもの全部失うわよ】」

■ ⚠️ バリエーション必須ルール（同じパターンを2件以上連続で生成しない）:
- 10件生成する場合、構造G(注意喚起+限定型)は最大3件まで
- 構造H(よぞら.の声型)を必ず1件入れる（キャラクター確立・ストック型）
- 構造I(問いかけ型)を必ず1件入れる（双方向対話）
- 構造F(告白型)を必ず1件入れる
- 構造E(カード選択型)を必ず1件入れる
- フック（1行目）が重複したら、時間軸・テーマ・動詞を変えてバリエーションをつける
- 「急に動く」「急変する」「急上昇する」「大きく動く」「ガラッと変わる」をローテーションする
- ⚠️ フック（1行目）は全件で必ず全て異なる文にすること。「今週、ガラッと変わる星座。」が2件以上あれば即やり直し
- ⚠️ 「今週、〇〇する星座。」という形（時間軸が「今週」のみで具体性がない）は禁止。必ず「今週後半」「今月」「4月〇〇」「明日から〇日間」等、具体的な時間軸にすること
- ⚠️ スピリチュアル報酬型CTA（「🔮を置いた方に〜」「🍀を置いた方に〜」）は連続3件以上禁止。H型・I型には使わない

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

■ 構造C: 天文イベント×質問型（共感+具体性の組み合わせ）
---
ここ数日、なんか\\nモヤモヤしていませんか？\\n\\nそれ、水星逆行の影響かもしれません。\\n特に双子座、乙女座、射手座は\\n強く影響を受けやすい時期。\\n\\n自分を責めなくて大丈夫。\\n今月末には落ち着いてきます。\\n\\n「🌙」を置いた方に、\\n穏やかな気持ちが届きます。\\n\\n#今日の運勢
---
ポイント: 天文イベント（水星逆行・満月・新月等）を起点に共感を引き出す。抽象的な励ましは禁止。「ここ最近〜していませんか？」という質問フックで星座名を明示。答えを先に出す。
⚠️ 禁止: 「今日も頑張りましょう」「自分を信じて」等の天体・星座名なし励まし。「木星の優しい光が」「土星が微笑む」系の励まし型はNG。

■ 構造D: 予告型（フォロー動機を作る）
---
来週、12星座の中で\\n最も運命が動く星座。\\n\\n蠍座です。\\n\\n恋愛・仕事・金運の\\nどこかで大きな波が来ます。\\n\\n特に水曜日以降は\\n直感を信じて動いてください。\\n\\n明日は牡羊座の運勢を\\n深掘りしてお届けしますね🌙\\n\\n#星座占い
---
ポイント: 「明日も届ける」で自然にフォロー動機。答えは先に出す。

■ 構造E: カード選択型（コメント数が爆発する最強パターン）
---
今の自分に引かれるカードを\\n1枚選んでください🔮\\n\\n🌕 月のカード\\n⭐ 星のカード\\n🌹 薔薇のカード\\n\\nコメント欄に選んだカードを\\n教えてくれたら、あなただけの\\n今週のメッセージをお届けします。\\n\\n#タロット
---
ポイント: 選択を促してコメントを誘発。「絵文字で選ぶ」形式でBotっぽくならない。「A/B/C」ではなく占い的な絵文字を使う。

■ 構造F: 告白・暴露型（保存率が最も高いパターン）
---
正直に言います。\\n\\n占い師として\\n「これだけは見てほしい」\\nと思うことがあります。\\n\\n今月、金運が急落しやすい\\n行動パターンがあって。\\n\\nそれは「直感に反する決断」。\\n\\n不思議なことに、今月だけは\\n頭より心で決めた方が\\nお金の流れが良くなる。\\n\\n#金運
---
ポイント: 「正直に言います」「実は」等の告白フックで保存率UP。占い師の本音感を演出。

■ 構造G: 注意喚起+限定型【実績最強: 閲覧数2373・ER 42%】
---
今週後半、気をつけた方がいい星座。\\n\\nたった3つだけです。\\n\\n双子座、天秤座、水瓶座さん。\\n\\n双子座は言葉のすれ違いが起きやすい週。\\n天秤座は決断を急かされる場面が来ます。\\n水瓶座は直感より慎重さが吉。\\n\\n該当の方、コメントで「当たった」と\\n教えてくれると嬉しいです🔮\\n\\n今日の運勢 #今週の占い
---
ポイント:
1行目: 「気をつけた方がいい」「急に動く」「見逃せない」等の注意喚起ワード + 時間軸（今週後半・来週・今月）
2行目: 「たった○つだけです」で限定感を加速
3行目: 星座名を即出す（隠さない）
以降: 各星座に1行ずつ具体的な予言（抽象的な励ましNG）
CTA: 「当たった人はコメント」型 or 「🍀を置いた方に〜」型
テーマ例: 仕事（昇進・転機）/ 恋愛（本音・決断）/ 金運（急変・臨時収入）/ 対人（すれ違い）

【🔥 Threads用CTA（3種類をローテーション。同一型を連続3件以上使わない）】

■ タイプ1: スピリチュアル報酬型（G/F型投稿に使用）
・「🔮」を置いた方だけに、今夜良い流れが届きます。
・「🍀」を置いた方に、今週中に嬉しい連絡が届きます。
・「🌙」を置いた方に、穏やかな気持ちが届きます。
・「✨」を置いた方に、3日以内に良い知らせが届きます。
→ 絵文字は毎回変える。報酬は具体的に（「良いこと」ではなく「嬉しい連絡」）
→ G型・F型に使用。H型・I型への使用は禁止

■ タイプ2: 会話誘導型（G/F型投稿に使用）
・あなたの星座は入ってましたか？コメントで教えてくださいね🌙
・当たってた人、コメントで教えて。次はもっと詳しく見ますね🔮
→ 必ず投稿の最後に自然に配置。1行目には絶対置かない。

■ タイプ3: 自然な問いかけ型（H型・I型投稿に使用）
・あなたにとって占いはどんな存在ですか？🌙
・今、何かを迷っていることがあれば、コメントで教えてください。
・選んだカードをコメントに書いてくれた方に、今夜のメッセージを送ります🔮
→ H型（よぞら.の声）・I型（問いかけ）専用。スピリチュアル報酬型は禁止
→ フォロワーとの「対話」を生むことが目的

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
1. 10件生成（Threads専用）{'（うち1件はアフィリエイト投稿）' if should_generate_affiliate else ''}
   ※構成: G×3 + H×1 + I×1 + F×1 + E×1 + B×3（合計10件）
2. 各投稿は200-300文字（Threadsの最適値）
3. 全投稿で会話を誘導するCTAを含める（質問型中心）
4. トピックタグは末尾に1つだけ
5. パターン配分（10件必須）: 注意喚起+限定型3件（構造G・必須）、ランキング型3件（構造B・必須・うち仕事運ランキングを優先）、よぞら.の声型1件（構造H・必須）、問いかけ対話型1件（構造I・必須）、告白型1件（構造F・必須）、カード選択型1件（構造E・必須）
   ※構造C（天文イベント×質問型）・構造D（予告型）は廃止
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
7. ⚠️ 全体行数は必ず12行以内（改行含む）。これを超えると即不合格。

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


def get_structure_template(structure):
    """構造別テンプレートを返す"""
    templates = {
        "G": """注意喚起+限定型【再現性テスト中・固定レシピ完全厳守】

■ 固定レシピ（この通りに生成すること。変更禁止）

1行目【フック・絶対15字以内】
  使用可能フック（コピー推奨）:
    「今週後半、急に動く星座。」       → 12字 ★実績v=1514
    「今月後半、急変する星座。」       → 12字
    「来週、大きく動く星座。」         → 11字
    「今週後半、金運が急変する星座。」 → 15字 ★実績v=681
    「今月、急上昇する星座。」         → 11字
    「今週、ガラッと変わる星座。」     → 13字
  ※時間軸はスロットに合わせる（朝→「今日」「今週」 / 夜→「今週後半」「来週」）
  ※絶対禁止: 16字以上（v=18まで激減の実績あり）
  ※絶対禁止: 「気をつけた方がいい」「判断ミスが起きやすい」「注意が必要」（ネガティブ・重い）

2行目: 「たった3つだけです。」または「たった4つだけです。」（3か4のみ・他の数字禁止）

（空白行）

星座名: 3〜4座を一行で列挙（例: 「牡牛座、蟹座、蠍座」）

各星座の予言（1〜2行ずつ）:
  必須: ポジティブな変化・動きを表現（「急に動く」「嬉しい転機が」「好機が来ます」）
  禁止: 「気をつけて」「注意して」「慎重に」等ネガティブ指示

末尾CTA（必ず末尾に1つ・省略禁止）:
  A型: 「🍀を置いた方に、今週中に嬉しい連絡が届きます。」
  B型: 「あなたの星座は入ってましたか？コメントで教えてください🔮」
  ※AとBを交互に使う（連続同型禁止）""",
        "A": """数字+限定型:
1行目: 「12星座中、たった3つだけ。」（数字+限定）
2行目: テーマ提示（今週、恋愛に大きな転機が訪れます。）
3行目: 星座名を即提示
以降: 深掘り
末尾: CTA""",
        "B": """ランキング型（ER25%実証済み・3件/バッチ必須）:
1行目: 【】でタイトル必須（例:「【今週の仕事運ランキングTOP3】」「【金運が急上昇する星座TOP3】」「【来週、職場の空気が変わる星座】」）
以降: 🥉3位→🥈2位→🥇1位の順でメダル絵文字必須
各位: 星座名＋2行の具体的説明（昇進・転機・臨時収入・案件獲得等、仕事運を優先）
末尾: CTA（「🍀を置いた方に今週中に嬉しい連絡が届きます」等）
フック例:
・「来週、仕事で一番動く星座を発表します。」
・「今月、職場で評価が上がりやすい星座TOP3。」
・「転機が来やすい星座、正直に言います。」
※仕事運テーマを最優先。金運・恋愛運と交互にバリエーションをつける""",
        "C": """天文イベント×質問型:
1行目: 「ここ数日、〜していませんか？」（質問フック）
2行目: 天文イベント（水星逆行・満月・新月等）の説明
3行目: 影響を受ける星座名を明示
末尾: CTA""",
        "D": """予告型:
1行目: 「来週、12星座の中で最も○○が動く星座。」
2行目: 星座名を即提示
以降: 詳細予告
末尾: 「明日はXX座の詳しい運勢をお届けします🌙」""",
        "E": """カード選択型:
1行目: 「今の自分に引かれるカードを1枚選んでください🔮」
以降: 🌕月/⭐星/🌹薔薇 等の絵文字カード選択肢
末尾: 「コメント欄に選んだカードを教えてくれたら〜」""",
        "F": """スピ×ラッキーアイテム型【昼休み・スピ好きターゲット】

■ 目的: 「ちょっとした開運行動を今日やってみよう」と思わせる軽くて楽しい投稿。
  昼休みに「これやってみよう」とすぐ実行できる具体性が命。

■ 構成（厳守）
1行目: ラッキーアイテム/カラーを即提示するフック（15字以内）
   例: 「今日の開運カラー、発表します。」
   例: 「今週のラッキーアイテムは〇〇。」
   例: 「今日触ると運気が上がるもの。」

本文: 星座別または全員向けのラッキーアイテム・カラー・行動を3〜5行で提示
   - スピリチュアルな根拠を一言添える（「月が〇〇座に入るため」等）
   - 今日/今週の具体的なラッキーアイテムを2〜3個（例: 白いもの・鏡・コーヒー）
   - ラッキーカラーを必ず1つ提示
   - 軽くて実行しやすいアドバイス（「〇〇を持ち歩くと◎」「〇〇を飲むと開運」等）

末尾CTA（必須）:
   例: 「今日試してみた方は🍀をどうぞ」
   例: 「あなたのラッキーアイテム、今日使いましたか？コメントで教えてください🔮」

■ 禁止
- 重い悩み相談系・ネガティブ表現
- 時限コンテンツ禁止（「今夜〇時までに」等）
- 星座別ランキング型（この型では全員向けかざっくり数星座でOK）

■ トピックタグ: #開運 #ラッキーアイテム #星座占い の中から選ぶ""",

        "H": """よぞら.の声・哲学型【ストック型・累積価値あり】

■ 目的: 「よぞら.という人間への興味・好奇心」を生むこと。
  星座運勢情報ではなく「この占い師の見方・感じ方」が伝わる投稿。
  翌週読んでも価値が変わらないストック型コンテンツ。

■ 構成（厳守）
1行目: よぞら.の視点を表す一文（断言・気づき・逆説のいずれか）
   例: 「占いは"答え"を教えるものじゃない。」
   例: 「当たる占いより、動ける占いの方が大事だと思ってる。」
   例: 「何年占いをやっても、一番難しいのは自分自身を占うこと。」
   例: 「"外れた"と思った占いが、1ヶ月後に当たっていたことがある。」

本文: よぞら.の経験・気づき・哲学を3〜5行で語る
   - 星座や具体的な運勢は不要
   - 「私は〜と思っている」「占い師として気づいたことがある」
   - 感情的な体験談・エピソードがあると尚良い
   - 他の占いアカウントが言わないこと・逆説的な視点を優先する

末尾: 問いかけまたは余韻を残すフレーズ
   問いかけ例: 「あなたにとって占いはどんな存在ですか？」
   余韻例: 「今日も、星はあなたの味方です。」
   ※ CTAは自然な問いかけのみ。スピリチュアル報酬型（「🔮を置いた方に〜」）は禁止

■ 禁止
- 「〇〇座の人は〜」「今週は〜」等の時限コンテンツ
- 汎用的な励まし（「あなたを信じてください」等）
- スピリチュアル報酬型CTA（「🔮を置いた方に〜」「🍀を置いた方に〜」）

■ トピックタグ: #星座占い #星読み #占い師の日常 の中から選ぶ""",

        "I": """問いかけ・対話型【双方向関係構築型】

■ 目的: フォロワーを「情報受信者」から「対話の相手」に変える。
  コメントを誘発し、よぞら.とフォロワーの関係性を深める。

■ 構成（厳守）
1行目: 問いかけフック（相手が「自分のことだ」と思う一文）
   例: 「今、何かを迷っていますか？」
   例: 「最近、占いに頼りたくなっていませんか？」
   例: 「今の自分に引かれるカードを1枚選んでください🔮」（カード型）

本文: 問いかけへの深掘り・占い師としての視点を3行
   - 共感を示す（「そう感じていいんです」「よくあることなんです」）
   - または占い師の視点で「なぜそう感じるか」を一言添える
   - カード型の場合: 🌕月/⭐星/🌹薔薇 等の絵文字で選択肢を提示

末尾CTA（必須）: コメント誘導
   例: 「コメントで教えてください🌙」
   例: 「選んだカードをコメントに書いてくれた方に、今夜のメッセージを送ります🔮」
   ※ 「🔮を置いた方に〜」スタイルは禁止。純粋な対話誘導のみ

■ 禁止
- 一方通行の情報提供（星座別ランキング・告白型は禁止）
- 答えを出し惜しみする構造（「個別に教えます」等）
- スピリチュアル報酬型CTA（「🍀を置いた方に〜」）

■ トピックタグ: #星座占い #タロット #占い の中から選ぶ""",

        "J": """全12星座1行メッセージ型【まーさ型・バズ設計】

■ 目的: 全12星座のユーザーが「自分の星座を探して読む」設計。
  母数100%にリーチし、コメント「当たってる！」「私これです」を誘発する。

■ フォーマット（厳守）
1行目: 🔮 {月/日}の12星座
2行目: 空行
3〜14行目: 12星座を順番に（各1行）
  形式: 「漢字星座名　1行メッセージ」（全角スペースで区切る）
  星座順: 牡羊座→牡牛座→双子座→蟹　座→獅子座→乙女座→天秤座→蠍　座→射手座→山羊座→水瓶座→魚　座
  ※「蟹　座」「蠍　座」「魚　座」は2文字なので全角スペース2つで揃える
15行目: 空行
16行目: CTA（「あなたの星座はどうでしたか？🌙」等）
17行目: 空行
18行目: トピックタグ #星座占い

■ 1行メッセージの書き方（重要）
- 8〜15文字以内
- 感情ワードを必ず1つ入れる（「嬉しい」「返ってくる」「伝わってる」「信じていい」等）
- 全12件で同じ言葉を使い回さない
- ポジティブ7割・注意喚起3割のバランス
- 「今日」を感じさせる具体性（例: 「今日に限り信じていい」「今日花開く」）

■ 禁止
- 「〇〇座さん」の「さん」づけ（この型では不要）
- 各星座への長い説明（1行厳守）
- 時限感のない抽象的メッセージ（「自分を信じて」等は禁止）
- CTAに「🔮を置いた方に〜」（I型やG型のCTAは禁止。問いかけ型のみ）

■ 良い例
牡羊座　動いた分だけ、返ってくる。
牡牛座　焦らず待つのが、今日の正解。
双子座　気になるあの人、実は意識してる。
蟹　座　感じた気持ちは、全部正解。
獅子座　チャンスは、静かに来る。
乙女座　気遣いが、ちゃんと伝わってる。
天秤座　誰かの一言が、転換点になる。
蠍　座　深読みより、素直に動こう。
射手座　踏み出した一歩が、景色を変える。
山羊座　地道な積み重ねが、今日花開く。
水瓶座　個性を出すほど、縁が生まれる。
魚　座　直感は、今日に限り信じていい。

■ トピックタグ: #星座占い 固定""",
    }
    return templates.get(structure, templates["G"])


def build_buzz_slot_prompt(slot_info, today):
    """バズ専用スロット用プロンプト（構造テンプレートなし・パターン直書き）"""
    hour   = slot_info["hour"]
    minute = slot_info.get("minute", 7)
    slot   = slot_info["slot"]
    btype  = slot_info.get("buzz_type", "")

    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    _jst  = _tz(_td(hours=9))
    _now  = _dt.now(_jst)
    _target = _now + _td(days=1) if _now.hour >= 20 else _now
    date_label = f"{_target.month}/{_target.day}"

    if btype == "engagement_bait":
        pattern_instruction = f"""【指定パターン】いいね強要型（競合ER最高 avg 1.52）
以下のいずれかのフォーマットで生成すること。

フォーマットA（スペース区切り1ライナー）:
こ れ が 見 え た ら 無 心 で い い ね を🔮

フォーマットB（スペース区切り＋運気断言）:
い い ね を す る と 運 気 が 爆 上 が り し ま す⛩️

フォーマットC（絵文字CTA＋断言型）:
🙏を置けた人、
今から、急にぜんぶ、うまくいきます。

「🌸」を置いた方に、
今日中に嬉しいことが起きます。

#今日の運勢

上記のフォーマットをベースに、絵文字・文言を少し変えてオリジナル版を生成すること。
フック（1行目）は40文字以内。文全体は15〜100文字でOK。"""

    elif btype == "rinji_income":
        pattern_instruction = f"""【指定パターン】臨時収入型（競合ER avg 0.33・最高拡散クラス）
以下のフォーマットで生成すること。金額は必ず「●●万円」と伏字にすること（数字を入れない）。

フォーマットA:
嘘は言いません。
素通りした人以外
●●万円ほどの臨時収入が入ってきます
大事に使ってね🌸

#金運

フォーマットB:
正直に申し上げますね
あなた{date_label}中に
●●万円ほどの臨時収入が入ってきます
信じなくて構いません
ただ、覚えておいてくださいね🌸

#金運

フォーマットC:
ごめんね、正直に言うね。
よぞら.を素通りした人以外…
今週中に●●万円ほどの
良いお知らせが届くよ！

#星座占い

上記のいずれかのフォーマットをベースに、文言を少し変えてオリジナル版を生成すること。
「●●万円」は必ずこの伏字のまま使うこと（具体的な数字に置き換えない）。"""

    elif btype == "suru_fear":
        pattern_instruction = f"""【指定パターン】スルー恐怖型（競合ER avg 0.15・拡散×エンゲージ同時狙い）
以下のフォーマットで生成すること。

フォーマットA（ランキング＋恐怖）:
無視する人金運上がらんよ。
1位　🐟うお座
2位 ⚖️てんびん座
3位 🏹いて座
4位　🦁しし座
5位 🦂さそり座
6位 🏺みずがめ座
7位 🦀かに座
8位 ♊双子座
9位 🐏おひつじ座
10位 🐂おうし座
11位 🧑‍🎓おとめ座
12位 🏹いて座

#星座占い

フォーマットB（ブロック恐怖）:
飛ばしたら絶対にやめて下さい。

「🐈」を置いた人、
今から、急にぜんぶ、うまくいきます。🙏

#今日の運勢

フォーマットC（危険度ランキング）:
【ここでスルーしたら{_target.month}月、
築き上げたもの全部失うわよ】

危険度99%：2月生まれ × みずがめ座
危険度90%：5月生まれ × おうし座
危険度80%：9月生まれ × おとめ座

#今日の運勢

上記のフォーマットをベースに、絵文字・星座・文言を少し変えてオリジナル版を生成すること。"""

    else:
        pattern_instruction = "【指定パターン】競合バズパターン（断言型・いいね強要型・スルー恐怖型・臨時収入型）を使うこと。"

    return f"""あなたは占いSNSアカウント「よぞら.」のThreads投稿ライターです。
{hour}時台に投稿する占い投稿を1件だけ生成してください。

【今日の日付】{today}
【投稿時間帯】{slot}（{hour:02d}:{minute:02d} JST投稿予定）
【ペルソナ】よぞら.（月詠）: 穏やかで親しみやすい。敬語ベースだが柔らかい。

{pattern_instruction}

【🚨 絶対禁止】
- 1星座限定の投稿（「蟹座さんへ」等）
- 「個別に教えます」等の出し惜しみ
- 励まし型: 「木星の優しい光が」系の抽象励まし
- ハッシュタグは必ず末尾に1つ

JSON形式で1件返してください:
{{"pattern_name": "構造{slot_info['structure']}_{slot_info['pattern_hint'][:20]}", "category": "カテゴリ", "content": "本文", "hashtag": "#今日の運勢", "time_slot": "{slot}", "scheduled_hour": {hour}, "scheduled_minute": {minute}}}
"""


def build_experiment_slot_prompt(slot_info, today, used_patterns, learning_block, competitor_buzz_block=""):
    """実験モード: 特定時間帯×パターン向けの投稿を1件生成するプロンプト"""
    hour = slot_info["hour"]
    slot = slot_info["slot"]
    structure = slot_info["structure"]
    pattern_hint = slot_info["pattern_hint"]

    # 夜間生成（20時以降）は翌日の日付を使う（翌朝スロット用）
    # 例: 23:05生成 → 翌日08:07投稿用に「4/13」と正しく設定
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    _jst = _tz(_td(hours=9))
    _now = _dt.now(_jst)
    _target = _now + _td(days=1) if _now.hour >= 20 else _now
    date_label = f"{_target.month}/{_target.day}"  # 例: 「4/13」

    minute = slot_info.get("minute", 7)
    return f"""あなたは占いSNSアカウント「よぞら.」のThreads投稿ライターです。
{hour}時台に投稿する占い投稿を1件だけ生成してください。

【今日の日付】{today}
【投稿時間帯】{slot}（{hour:02d}:{minute:02d} JST投稿予定）
【指定パターン】構造{structure}: {pattern_hint}
【ペルソナ】月詠（つくよみ）: 穏やかで親しみやすい。敬語ベースだが柔らかい。
{f"""
【⭐ 午前スロット限定ルール（9時台・G型）】
フック（1行目）は「今週」「今月」ではなく、必ず「{date_label}」（今日の日付）を使うこと。
理由: 朝〜午前の投稿は「今日だけの特別感」が最も響く。「今週」は夜のスロットと被る。
推奨フック例:
  ・「{date_label}、急に動く星座。」      → ★最推奨（日付×限定）
  ・「{date_label}、運命が変わる星座。」
  ・「{date_label}、仕事運が急変する星座。」
  ・「{date_label}、チャンスをつかむ星座。」
※ 15文字以内厳守。日付を使えば自動的に鮮度が生まれ、翌日以降に使い回しにくくなるメリットもあり。
""" if hour == 9 and structure == "G" else ""}

{learning_block}
{(chr(10) + competitor_buzz_block + chr(10)) if competitor_buzz_block else ""}
【💥 高バズパターン（積極使用・最優先）】
以下のパターンは競合分析でER最上位と確認済み。積極的に使うこと。

■ 断言型（「〜かもしれない」禁止。断言で書く）
  例: 「確信しました。あなた、超強運です。」
  例: 「やっぱり確信しました。」
  例: 「正直に言います。」「嘘は言いません。」

■ 臨時収入型（金額を具体的に書く）
  例: 「ごめんね、正直に言うね。ミラを素通りした人以外…明日、〇〇万円ほどの臨時収入が入ってくるよ！」
  例: 「正直に申し上げますね。あなた〇月中に〇〇万円ほどの臨時収入が入ってきます。」
  ※ 金額は5〜88の範囲で具体的な数字を入れる（例: 12万円、38万円、88万円）

■ いいね強要型（エンゲージメント直撃）
  例: 「い い ね を す る と 運 気 が 爆 上 が り し ま す⛩️」（スペース区切りで目立たせる）
  例: 「こ れ が 見 え た ら 無 心 で い い ね を🔮」
  例: 「🙏を置けた人、今から急にぜんぶうまくいきます。」

■ スルー恐怖型（離脱防止・強制引き込み）
  例: 「無視する人金運上がらんよ。」
  例: 「ミラを素通りした人以外〜」
  例: 「飛ばしたら絶対にやめて下さい。」
  例: 「【ここでスルーしたら〇月、築き上げたもの全部失うわよ】」

■ ランキング+恐怖型（拡散×エンゲージ同時）
  例: 「危険度99%：〇月生まれ × みずがめ座\n危険度90%：〇月生まれ × おうし座」

【🚨 絶対禁止（これだけは守る）】
- 励まし型: 「木星の優しい光が」「土星が微笑む」系の抽象励まし
- 1星座限定の投稿（「蟹座さんへ」等）
- 答えの出し惜しみ（「個別に教えます」等）
- 1行目にCTAを置く

【✅ 時間帯を活かした表現】
{f"""- {hour}時台らしい時間的文脈を使う（構造G/C/F/Bなど時間依存の場合）
- 使用可能な時間表現（厳守）:
  ・6〜9時台  → 「今朝」「今日」「今週」のみ。「今夜」禁止
  ・12〜15時台 → 「今日」「今週」「今月」のみ。「今夜」「今朝」禁止
  ・18〜21時台 → 「今夜」「今日」「今週」OK
- 「今夜○時までに」は18時以降スロットのみ使用可""" if slot_info["structure"] not in ("H", "I", "J") else f"""- 構造{slot_info["structure"]}は特殊型のため時間矛盾を起こしやすい表現は使わない
{f'- 構造Jのフック1行目は必ず「🔮 {date_label}の12星座」とする（日付固定）' if slot_info['structure'] == 'J' else '- 構造H/Iは「今週」「今月」等の時限表現を避ける（ストック型・時限コンテンツ禁止）'}"""}

【構造{structure}のテンプレート（必ずこの構造で生成）】
{get_structure_template(structure)}

【ルール】
1. 文字数: 150-280文字
2. 1行は8-15文字を目安
3. 空行で3〜4ブロック
4. トピックタグは末尾に1つ（#今日の運勢 #恋愛運 #金運 #星座占い #今週の占い から）
5. CTAは最後に（「🍀を置いた方に〜」または「コメントで教えてください🔮」）
6. 1行目（フック）は通常15文字以内が最適。ただし「い い ね を す る と〜」等のスペース区切り1ライナー型は40文字まで許容

JSON形式で1件返してください:
{{"pattern_name": "構造{structure}_{pattern_hint}", "category": "カテゴリ", "content": "本文", "hashtag": "#今日の運勢", "time_slot": "{slot}", "scheduled_hour": {hour}, "scheduled_minute": {minute}}}
"""


def call_claude_api_single(api_key, prompt):
    """Claude APIを呼び出し、1件のJSONオブジェクトを返す（実験モード用）"""
    import time as _time

    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1024,
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
                print("ERROR: Claude API 3回失敗。スキップ。")
                return None

    text = result["content"][0]["text"]
    # JSON オブジェクト1件を抽出
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        print(f"ERROR: JSON抽出失敗")
        print(text[:300])
        return None

    try:
        return json.loads(m.group())
    except json.JSONDecodeError as e:
        print(f"ERROR: JSON不正: {e}")
        print(text[:300])
        return None


def generate_experiment_posts(api_key, today, used_patterns, learning_block, slots_to_generate=None, competitor_buzz_block=""):
    """実験モード: 指定スロット分の投稿を1件ずつ生成してリストで返す"""
    import time as _time

    if slots_to_generate is None:
        slots_to_generate = EXPERIMENT_TIME_SLOTS

    posts = []
    print(f"\n--- 実験モード: {len(slots_to_generate)}スロット分を生成中 ---")

    for slot_info in slots_to_generate:
        hour = slot_info["hour"]
        print(f"  [{hour}時台] 構造{slot_info['structure']}: {slot_info['pattern_hint'][:20]}...", end=" ", flush=True)
        # バズ専用スロットは専用プロンプトを使う（構造テンプレートなし）
        if slot_info.get("buzz_type"):
            prompt = build_buzz_slot_prompt(slot_info, today)
        else:
            prompt = build_experiment_slot_prompt(slot_info, today, used_patterns, learning_block, competitor_buzz_block)
        post = call_claude_api_single(api_key, prompt)

        if post is not None:
            post["time_slot"] = slot_info["slot"]
            post["scheduled_hour"] = hour
            # フック長チェック（実験モード）
            first_line = post.get("content", "").split("\n")[0].strip()
            if len(first_line) > 40:
                print(f" ⚠フック長{len(first_line)}字>40字: 「{first_line[:20]}」", end=" ")
            posts.append(post)
            print("OK")
        else:
            print("SKIP（生成失敗）")

        # rate limit対策
        _time.sleep(2)

    print(f"  実験モード生成完了: {len(posts)}/{len(slots_to_generate)}件")
    return posts


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

    experiment_mode = os.environ.get("EXPERIMENT_MODE", "0") == "1"

    pending = [p for p in queue.get("queue", []) if p.get("status") == "queued"]
    if len(pending) >= len(EXPERIMENT_TIME_SLOTS):
        print(f"キューに{len(pending)}件残っています。生成スキップ。")
        return

    if experiment_mode:
        print("実験モード: 18スロット分の投稿を生成します...")
    else:
        print("キューが空のため、Claude APIでThreads用10件を生成します...")

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
    urls_ready = has_real_affiliate_urls(asp_links)
    should_generate_affiliate = affiliate_enabled and not affiliate_today and urls_ready
    if affiliate_enabled and not urls_ready:
        print("[INFO] アフィリエイト: 有効だがURLが未設定（プレースホルダーのまま）→ スキップ")

    used_patterns = [p.get("pattern_name", "") for p in history.get("posts", [])[-15:]]
    now = datetime.now(JST)
    # 夜間生成（20時以降）は翌日の投稿を生成するため、日付を翌日に設定
    # 例: 23:05に実行 → 翌朝08:07スロットに「4/13、急に動く星座。」と正しく出る
    from datetime import timedelta as _td_main
    target_date = now + _td_main(days=1) if now.hour >= 20 else now
    today = target_date.strftime("%Y年%m月%d日(%a)")
    weekday = target_date.weekday()

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

    # 競合バズデータ（account-timeline 収集後に extract_competitor_buzz.py が生成）
    buzz_data = load_json("state/competitor-buzz-references.json")
    competitor_buzz_block = build_competitor_buzz_block(buzz_data)
    if competitor_buzz_block:
        print(f"[INFO] 競合バズデータ読込: {buzz_data.get('buzz_posts_count', 0)}件のバズ投稿参照")
    else:
        print("[INFO] 競合バズデータなし（初回 or extract未実行）→スキップ")

    ctx = build_common_context(today, used_patterns, learning_block, weekday, series_map)

    # ========================================
    # X用投稿は現在無効（Xは未運用のため）
    # ========================================
    x_posts = []

    # ========================================
    # 実験モード: 7スロット分の投稿を個別生成
    # ========================================
    if experiment_mode:
        today_str = datetime.now(JST).strftime("%Y%m%d")
        time_str = datetime.now(JST).strftime("%H%M")

        # 当日すでにqueuedのスロット時間を確認（投稿済み・キュー済みの重複生成を防止）
        existing_queued_hours = {
            p.get("scheduled_hour")
            for p in queue.get("queue", [])
            if p.get("status") == "queued" and p.get("id", "").startswith(f"post_{today_str}_")
        }
        # 投稿済み（posted/error）のスロット時間も除外（再生成による二重投稿を防止）
        existing_posted_hours = {
            p.get("scheduled_hour")
            for p in queue.get("queue", [])
            if p.get("status") in ("posted", "error") and p.get("id", "").startswith(f"post_{today_str}_")
        }
        skip_hours = existing_queued_hours | existing_posted_hours

        # まだキューにないスロットのみ生成
        _now_hour = datetime.now(JST).hour
        _generating_for_tomorrow = (_now_hour >= 20)  # 20時以降は翌日分生成

        if not _generating_for_tomorrow:
            # 今日分生成: 現在時刻以降のスロットのみ（過去スロットは投稿不可なので生成しない）
            # ※ >= にすることで「キューが空のまま06:07 cronが発火した」際に06:07自身も生成できる
            slots_to_generate = [
                s for s in EXPERIMENT_TIME_SLOTS
                if s["hour"] not in skip_hours and s["hour"] >= _now_hour
            ]
        else:
            # 翌日分生成: 全スロット対象
            slots_to_generate = [s for s in EXPERIMENT_TIME_SLOTS if s["hour"] not in skip_hours]

        if not slots_to_generate:
            print(f"生成対象スロットなし（queued:{len(existing_queued_hours)}件 / posted:{len(existing_posted_hours)}件 / 過去スロット除外済み）。スキップ。")
            return

        print(f"生成対象: {len(slots_to_generate)}スロット（スキップ: {len(skip_hours)}件）")
        threads_posts = generate_experiment_posts(api_key, today, used_patterns, learning_block, slots_to_generate, competitor_buzz_block)
        if not threads_posts:
            print("ERROR: 実験モード投稿の生成に全て失敗")
            sys.exit(1)

        for i, p in enumerate(threads_posts):
            p["id"] = f"post_{today_str}_{time_str}_exp_{i + 1:03d}"
            p["platform"] = "threads"
            p.setdefault("is_affiliate", False)
            p.setdefault("affiliate_comment", None)
            p["status"] = "queued"

        # 当日分のqueuedのみ削除（posted/error等は保持 → 二重投稿防止）
        queue["queue"] = [
            p for p in queue["queue"]
            if not (p.get("id", "").startswith(f"post_{today_str}_") and p.get("status") == "queued")
        ]
        queue["queue"].extend(threads_posts)

        save_json("state/post-queue.json", queue)
        print(f"\n実験モード生成完了: {len(threads_posts)}件をキューに追加")
        print(f"学習データ参照: {winning.get('data_count', 0)}件 (信頼度: {winning.get('confidence', 'none')})")
        return

    # ========================================
    # Threads用投稿を10件生成（通常モード）
    # ========================================
    print("\n--- Threads用投稿を生成中 ---")
    threads_prompt = build_threads_prompt(ctx, should_generate_affiliate, asp_links)
    threads_posts = call_claude_api(api_key, threads_prompt)
    if threads_posts is None:
        print("ERROR: Threads用投稿の生成に失敗")
        sys.exit(1)
    print(f"  Threads用: {len(threads_posts)}件生成")

    # ========================================
    # Phase 2.5: 行数トリミング（16行超過を自動修正）
    # ========================================
    def trim_to_max_lines(content, max_lines=16):
        """16行を超えるコンテンツを自動でmax_linesに圧縮する"""
        lines = content.split("\n")
        if len(lines) <= max_lines:
            return content
        # 最終行（ハッシュタグ）を保持して前から切る
        last_line = lines[-1]
        core_lines = lines[:max_lines - 1]
        # 末尾の空行を除去してハッシュタグを付ける
        while core_lines and core_lines[-1] == "":
            core_lines.pop()
        trimmed = "\n".join(core_lines) + "\n" + last_line
        return trimmed

    for post in threads_posts:
        content = post.get("content", "")
        lines = content.split("\n")
        if len(lines) > 16:
            post["content"] = trim_to_max_lines(content, 16)
            print(f"  ✂ 行数トリミング: {len(lines)}行→{len(post['content'].split(chr(10)))}行 [{post.get('pattern_name','?')}]")

    # ========================================
    # Phase 2.6: フォロー促進CTA付与（10投稿中3件・ソフト訴求）
    # ========================================
    # 「フォローして」等の直接要求はMeta規約違反。
    # 「毎日届けている」「また来てください」等の事実・誘いに留める。
    FOLLOW_CTA_PATTERNS = [
        "\n\n毎朝・昼・夜、星読みをお届けしています🌙",
        "\n\n明日もまた、星の流れをお伝えします✨",
        "\n\n気に入っていただけたら、また遊びに来てください🔮",
        "\n\n毎日更新中。続きはまた明日💫",
        "\n\n今日も読んでくださってありがとうございます🌙",
        "\n\n明日も星があなたのそばにいます✨",
        "\n\n毎日、あなたの運気をお届けしています🔮",
        "\n\n続きは明日。また星読みしましょう🌙",
        "\n\n読んでくださる方がいると、励みになります✨",
        "\n\n気になる日はまた読みに来てください💫",
    ]

    # CTA使用状況を管理（連続同一パターン防止）
    cta_state = load_json("state/cta-state.json")
    if not cta_state:
        cta_state = {"last_used_indices": [], "total_with_cta": 0, "total_without_cta": 0}

    last_used = cta_state.get("last_used_indices", [])
    total_with = cta_state.get("total_with_cta", 0)
    total_without = cta_state.get("total_without_cta", 0)

    for post in threads_posts:
        # 30%の確率でCTAを付与（直近の比率で補正）
        total_so_far = total_with + total_without
        current_rate = total_with / total_so_far if total_so_far > 0 else 0
        # 現在の付与率が30%未満なら確率を上げ、超えていれば下げる
        target_prob = max(0.1, min(0.5, 0.3 + (0.3 - current_rate) * 0.5))

        if random.random() > target_prob:
            total_without += 1
            continue

        # 直近3件で使っていないパターンから選ぶ
        available = [i for i in range(len(FOLLOW_CTA_PATTERNS)) if i not in last_used[-3:]]
        if not available:
            available = list(range(len(FOLLOW_CTA_PATTERNS)))
        chosen_idx = random.choice(available)
        cta_text = FOLLOW_CTA_PATTERNS[chosen_idx]

        # 末尾に追加（既存のハッシュタグ行の前に挿入）
        content = post.get("content", "")
        lines = content.split("\n")
        # 末尾のハッシュタグ行を探す（#で始まる行）
        hashtag_idx = None
        for li in range(len(lines) - 1, -1, -1):
            if lines[li].strip().startswith("#"):
                hashtag_idx = li
                break

        if hashtag_idx is not None and hashtag_idx > 0:
            # ハッシュタグの直前に挿入
            lines.insert(hashtag_idx, cta_text.strip())
            lines.insert(hashtag_idx, "")  # 空行で区切り
            post["content"] = "\n".join(lines)
        else:
            post["content"] = content + cta_text

        post["has_follow_cta"] = True
        last_used.append(chosen_idx)
        total_with += 1
        print(f"  💬 フォローCTA付与: [{post.get('pattern_name','?')[:20]}] パターン{chosen_idx+1}")

    # CTA状態を保存
    cta_state["last_used_indices"] = last_used[-10:]
    cta_state["total_with_cta"] = total_with
    cta_state["total_without_cta"] = total_without
    save_json("state/cta-state.json", cta_state)
    cta_rate = round(total_with / (total_with + total_without) * 100, 1) if (total_with + total_without) > 0 else 0
    print(f"  フォローCTA付与率: {cta_rate}% (累計 付与{total_with}件/未付与{total_without}件)")

    # ========================================
    # Phase 3: 類似度チェック（プラットフォーム別）
    # ========================================
    from difflib import SequenceMatcher
    import re as _re

    # 直近50件に拡大（旧20件では5回以上同じフックが使われていた問題の対策）
    recent_texts = [p.get("content", "") for p in history.get("posts", [])[-50:]]
    recent_first_lines = [p.get("content", "").split("\n")[0] for p in history.get("posts", [])[-50:]]

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
                    # フック1行目が完全一致（旧50件内）→ 即除外
                    if first_line.strip() and first_line.strip() == rfl.strip():
                        print(f"  ⚠ [{platform_label}] フック1行目完全一致で除外: {first_line[:30]}...")
                        is_similar = True
                        break
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

    # X用は現在無効
    threads_posts = filter_similar(threads_posts, "Threads")

    # ========================================
    # Phase 3.5: フック品質チェック（15字以内ルール）＆テーマローテーション
    # ========================================
    THEME_KEYWORDS = {
        "仕事": ["仕事", "職場", "転職", "昇進", "キャリア", "副業", "判断"],
        "恋愛": ["恋愛", "恋", "恋人", "片思い", "結婚", "出会い", "関係"],
        "金運": ["金運", "金", "お金", "収入", "財布", "投資", "給料"],
        "総合運": ["運気", "運勢", "開運", "幸運", "チャンス", "転機", "動く", "変わる"],
    }

    def detect_theme(text):
        for theme, keywords in THEME_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return theme
        return "その他"

    # 当日投稿済みのテーマカウント
    today_str_check = datetime.now(JST).strftime("%Y-%m-%d")
    today_posted = [p for p in history.get("posts", []) if p.get("posted_at", "")[:10] == today_str_check]
    today_themes = [detect_theme(p.get("content", "")) for p in today_posted]

    hook_checked = []
    for p in threads_posts:
        content = p.get("content", "")
        first_line = content.split("\n")[0].strip()
        hook_len = len(first_line)

        # フック長チェック（警告のみ・除外はしない）
        # ※ 通常投稿は15字以内推奨。いいね強要型・1ライナー型は40字まで許容
        if hook_len > 40:
            print(f"  ⚠ フック長超過 ({hook_len}字>40字): 「{first_line[:25]}」")

        # テーマローテーション: 当日すでに2件以上同テーマがあれば警告
        theme = detect_theme(content)
        same_theme_count = today_themes.count(theme)
        if same_theme_count >= 2 and theme != "その他":
            print(f"  ⚠ テーマ重複: 「{theme}」は本日すでに{same_theme_count}件 → 多様性に注意")

        hook_checked.append(p)

    threads_posts = hook_checked

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

    # X用は現在無効
    threads_posts = sort_by_time(threads_posts, threads_time_priority)

    # ========================================
    # Phase 5: プラットフォームタグ付け & キュー追加
    # ========================================
    today_str = datetime.now(JST).strftime("%Y%m%d")
    # 同日複数回生成時のID衝突防止: HHMM を追加して一意性を保証
    time_str = datetime.now(JST).strftime("%H%M")
    post_count = len(history.get("posts", []))

    # X投稿は現在無効（キューに追加しない）

    # Threads投稿にプラットフォームタグ
    for i, p in enumerate(threads_posts):
        p["id"] = f"post_{today_str}_{time_str}_th_{i + 1:03d}"
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
    # 今日生成分（"post_YYYYMMDD_"で始まるもの）のみ除去して再生成
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
    print(f"\n生成完了: Threads={len(threads_posts)}件をキューに追加")
    print(f"学習データ参照: {winning.get('data_count', 0)}件 (信頼度: {winning.get('confidence', 'none')})")


if __name__ == "__main__":
    main()
