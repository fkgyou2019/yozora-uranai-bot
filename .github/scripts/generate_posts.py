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
    safety = load_json("config/safety.json")
    asp_links = load_json("knowledge/uranai/asp-links.json")

    # アフィリエイト設定
    affiliate_enabled = safety.get("posting_safety", {}).get("affiliate_enabled", False)
    # 今日既にアフィリエイト投稿しているかチェック
    today_posts = [p for p in history.get("posts", [])
                   if p.get("posted_at", "").startswith(datetime.now(JST).strftime("%Y-%m-%d"))]
    affiliate_today = any(p.get("is_affiliate") for p in today_posts)
    should_generate_affiliate = affiliate_enabled and not affiliate_today

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
スマホ画面は1行約22文字表示される。画面幅を活かすこと。
1. 1行は15-22文字を目安。12文字未満の行はフック行と締め行のみ許可
2. 改行なし25文字以上のベタ打ちは禁止（自動折り返しが入ると見た目が崩れる）
3. 空行（\\n\\n）で3〜4ブロックに分割（5ブロック以上は縦長すぎ）
4. 1ブロックは30-60文字（2-3行）
5. フック（1行目）は20文字以内
6. 全体で空行含めて12行以内を目標
7. 1行8文字以下の短い行を連発しない（スカスカに見え、おじさん構文と見なされる）

【構造テンプレート】
[フック20文字以内]\\n\\n[ブロック1: 2-3行で30-60文字]\\n\\n[ブロック2: 2-3行で30-60文字]\\n\\n[CTA+ハッシュタグ]

【🏆 バズ実績のある投稿構造（この構造を真似ること）】

■ 構造A: ランキング型（eng率21%。安定して高い）
---
【金運が爆発する星座TOP3】\\n\\n🥉第3位：射手座。思わぬ臨時収入が舞い込む予感です。\\n\\n🥈第2位：蠍座。投資や副業に追い風が吹きます。\\n\\n🥇第1位：牡牛座。今月中に大きなお金の流れが変わります。\\n\\n「✨」を置いた方から順に金運の波が届きます。\\n\\n#金運
---
ポイント: 3位→1位の順。各星座は1-2行でコンパクトに。全体12行以内。

■ 構造B: 限定×恐怖型（views最多354。拡散力が最強）
---
12星座中、たった2つだけ。\\n\\n今週、運命が大きく動く星座があります。\\nそれは…蟹座と射手座です。\\n\\n特に蟹座は来週、大きな決断を迫られるかも。でも大丈夫。あなたの直感を信じれば必ず正解に辿り着けます。\\n\\n「🔮」を置いた方に今夜良い流れが届きます。\\n\\n#星座占い
---
ポイント: 1行15-20文字。ブロック内は2-3行にまとめる。縦長にしない。

■ 構造C: コメント誘導型（返信=アルゴリズム最強加点。最重要パターン）
---
あなたの星座、コメント欄に書いて🔮\\n\\n来週「人生が動く」星座が3つあります。\\n\\nコメントで星座を教えてくれたら\\n個別にお伝えしますね。\\n\\nちなみに火の星座（牡羊・獅子・射手）は\\n特に大きな変化がありそう✨\\n\\n#星座占い
---
ポイント: 「コメントで教えて→個別に伝える」の約束で返信を誘発。返信数はアルゴリズムで最も重視される。

■ 構造D: 選択肢型（参加型でコメント誘発）
---
直感で選んで🔮\\n\\nA. 🌙 月\\nB. ⭐ 星\\nC. ☀️ 太陽\\n\\n選んだ？\\n\\nA → 今週中に嬉しい連絡が届きます\\nB → 来月、新しい出会いがあります\\nC → 3日以内にお金のラッキーが✨\\n\\n#タロット
---
ポイント: 選ぶ行為自体が楽しい。結果を知りたくてコメントする人も多い。

■ 構造E: 共感×解決型（フォロワー定着に効く）
---
なんか最近、全部うまくいかない気がしてる人。\\n\\nそれ、水星逆行の影響かもしれません。\\n\\n今の時期は\\n「考えがまとまらない」のが普通。\\n\\n自分を責めなくて大丈夫。\\n4月に入ったらスッキリします🌙\\n\\n#今日の運勢
---
ポイント: 「あなただけじゃない」という安心感。スピリチュアルな理由付けで納得感を与える。

■ 構造F: 暴露・ぶっちゃけ型（シェアされやすい）
---
占い師として言いにくいんだけど。\\n\\n「相性最悪」って出ても\\n別れなくていいです。\\n\\n相性って\\n「努力の方向性」を示すもの。\\n\\n最悪の相性＝\\n最高の成長パートナーって\\n意外と多いんですよ✨\\n\\n#星座占い
---
ポイント: 「本音を言う」感が信頼感と話題性を生む。

【🔥 CTA（コメント誘発）ルール ※全投稿に必須】
投稿の最後に、コメントを誘発するCTAを入れる。
CTAは以下の4タイプから選び、10件でバランスよく混ぜる。

■ CTAタイプ1: 絵文字を置く型（10件中3件まで）
・「🔮」を置いた方に、今夜良い流れが届きます。
・「✨」を置いて受け取って。3日以内に嬉しい変化が起きます。
・「🌙」を置いた方だけに、明日の開運ヒントが届きますよ。
※同じ絵文字を2件以上で使わない

■ CTAタイプ2: 質問・回答型（10件中3件。返信率が最も高い）
・「あなたの星座、コメントで教えて。個別に今週の運勢お伝えします🔮」
・「A/B/Cどれを選んだ？コメントで教えてくれたら深掘りしますね✨」
・「当たってた人、コメントで教えて。次はもっと詳しく見ますね🌙」
※質問→回答の約束で返信を誘発。返信=アルゴリズム最強加点

■ CTAタイプ3: 保存推奨型（10件中2件）
・「保存しておくと、該当日に見返せますよ✨」
・「この情報、週末にもう一度チェックしてみて🌙」
※保存数もアルゴリズム加点になる

■ CTAタイプ4: シェア誘導型（10件中2件）
・「これ当てはまる友達いたら、教えてあげて✨」
・「○○座の友達にも見せてあげてね🌙」
※シェア=新規リーチ獲得

■ CTA NG（こう書いたら不合格）:
・「🔮を置いてね」（報酬なし→動機不足）
・「コメントしてください」「フォローしてね」（直接的すぎ→Bot臭い）
・「いいねとフォローお願いします」（乞食感→論外）
・10件中4件以上が同じCTAタイプ（マンネリ化）

【緊急性ワード（10件中3件以上に入れる）】
「今週中に」「3日以内に」「明日の朝」「今月中に」「48時間以内」
→ 時限性があると行動率が2倍になる

【⚠ 時間表現NG（絶対に使わない）】
以下の表現は投稿時刻によって矛盾するため禁止:
・「今夜○時までに」「今朝」「午前中に」「朝一で」
・特定の時刻を含む表現（「23時までに」等）
・代わりに「今日中に」「寝る前に」「次の満月までに」等、時刻に依存しない表現を使う

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

{'【アフィリエイト投稿ルール（10件中1件目をアフィリエイト投稿にする）】' if should_generate_affiliate else ''}
{f"""■ アフィリエイト対象案件:
""" + chr(10).join(f"  ・{c['name']}（{c['reward']}）: {c['pr_text_templates'][0]}" for c in asp_links.get('campaigns', [])[:3]) + f"""

■ アフィリエイト投稿の形式:
  - 本文: 有益な占いコンテンツ（リンクなし、普通の投稿と同じ品質）
  - affiliate_comment: コメント欄用PRテキスト（「ちなみに〜」で始まる自然な導入 + ASPリンクURL + ※PR表記）
  - is_affiliate: true
  - カテゴリ: 恋愛運（電話占い案件と相性◎）
  - 投稿時間: 21:37枠（夜のリラックスタイム=CV率最高）

■ affiliate_commentのテンプレート:
  "ちなみに、本格的な鑑定を受けたい方はこちらもおすすめです👇\\n初回無料体験あり🔮\\n[ASPリンクURL]\\n※PR"
""" if should_generate_affiliate else ''}

【生成ルール】
1. 10件生成{'（うち1件目はアフィリエイト投稿）' if should_generate_affiliate else '（アフィリエイトなし）'}
2. 各投稿は150-300文字
3. 具体的な星座名を含める
4. パターン配分（厳守）:
   ・ランキング型: 2件（安定して高エンゲージメント）
   ・コメント誘導型: 3件（質問→個別回答の約束で返信を誘発。最重要）
   ・限定×恐怖型: 2件（views最多。拡散力が高い）
   ・選択肢型: 1件（参加型でコメント増）
   ・共感型 or 暴露型 or タロット型: 1件（バリエーション）
   ・シリーズ型（今日の曜日定番）: 1件
5. 全投稿にCTAを含める（4タイプをバランスよく）
6. ハッシュタグはcontentに含める（hashtagフィールドは空文字）
7. 使用可能ハッシュタグ: #今日の運勢 #恋愛運 #金運 #仕事運 #タロット #星座占い
8. 10件全てのフック・CTA・星座の組み合わせをユニークにする。「12星座中、たった○つだけ。」のフックは10件中1件まで。同じフック構造の繰り返し禁止
9. 1件目は今日のシリーズコンテンツ（上記参照）
10. 10件中2件にフォロー導線を含める（上記参照）
11. カテゴリも分散させる: 恋愛運3件、金運2件、仕事運2件、総合運/開運3件

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
{'※ 1件目はis_affiliate: true、affiliate_commentにPRテキストを入れること' if should_generate_affiliate else ''}
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

    # Claude APIリトライ（最大3回）
    import time as _time
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
                sys.exit(1)

    text = result["content"][0]["text"]
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        print("ERROR: JSON抽出失敗")
        print(text[:500])
        sys.exit(1)

    try:
        posts = json.loads(m.group())
    except json.JSONDecodeError as e:
        print(f"ERROR: Claude APIの出力がJSON不正: {e}")
        print(text[:500])
        sys.exit(1)
    today_str = datetime.now(JST).strftime("%Y%m%d")
    post_count = len(history.get("posts", []))

    # --- 類似度チェック: 直近投稿と45%以上類似なら除外 ---
    from difflib import SequenceMatcher
    import re as _re
    recent_texts = [p.get("content", "") for p in history.get("posts", [])[-20:]]
    recent_first_lines = [p.get("content", "").split("\n")[0] for p in history.get("posts", [])[-20:]]
    filtered = []

    def extract_structure(text):
        """投稿の構造パターンを抽出（数字や星座名を正規化）"""
        s = _re.sub(r"[0-9０-９]+", "N", text)
        zodiac = ["牡羊座", "牡牛座", "双子座", "蟹座", "獅子座", "乙女座",
                   "天秤座", "蠍座", "射手座", "山羊座", "水瓶座", "魚座"]
        for z in zodiac:
            s = s.replace(z, "○座")
        return s

    for p in posts:
        content = p.get("content", "")
        first_line = content.split("\n")[0]
        is_similar = False

        # 1. 文字列類似度チェック（直近投稿）
        for rt in recent_texts:
            if SequenceMatcher(None, content, rt).ratio() > 0.45:
                print(f"  ⚠ 類似度超過で除外: {content[:30]}...")
                is_similar = True
                break

        # 2. 構造パターン重複チェック（1行目の構造が同じ＝マンネリ）
        if not is_similar:
            struct = extract_structure(first_line)
            for rfl in recent_first_lines:
                if SequenceMatcher(None, struct, extract_structure(rfl)).ratio() > 0.7:
                    print(f"  ⚠ フック構造重複で除外: {first_line[:30]}...")
                    is_similar = True
                    break

        # 3. 今回バッチ内での類似度
        if not is_similar:
            for fp in filtered:
                fc = fp.get("content", "")
                if SequenceMatcher(None, content, fc).ratio() > 0.45:
                    print(f"  ⚠ バッチ内類似で除外: {content[:30]}...")
                    is_similar = True
                    break
                # バッチ内フック構造重複
                if SequenceMatcher(None, extract_structure(first_line),
                                   extract_structure(fc.split("\n")[0])).ratio() > 0.7:
                    print(f"  ⚠ バッチ内フック構造重複で除外: {first_line[:30]}...")
                    is_similar = True
                    break

        if not is_similar:
            filtered.append(p)
            recent_texts.append(content)
            recent_first_lines.append(first_line)

    if len(filtered) < len(posts):
        print(f"  類似チェック: {len(posts)}件→{len(filtered)}件")
    posts = filtered
    # --- 類似度チェック終了 ---

    # --- 時間帯別最適配置: キューの順番を最適化 ---
    # 朝(7-9時)=シリーズ型・ランキング型、昼(10-12時)=共感型・限定型、
    # 夕方(15-19時)=コメント誘導型・選択肢型、夜(20-21時)=暴露型・限定型
    time_priority = {
        "シリーズ": 0, "ランキング型": 1,  # 朝向け
        "共感": 2, "星座別": 3,             # 昼向け
        "コメント誘導型": 4, "選択肢型": 5, # 夕方向け（アクティブ時間帯に参加型）
        "限定": 6, "暴露": 7, "タロット": 8, # 夜向け（じっくり読む時間帯）
    }
    def sort_key(post):
        pname = post.get("pattern_name", "")
        for key, priority in time_priority.items():
            if key in pname:
                return priority
        return 5  # デフォルトは中間

    posts.sort(key=sort_key)

    # アフィリエイト投稿は最後尾に移動（21:37枠で投稿されるように）
    affiliate_posts = [p for p in posts if p.get("is_affiliate")]
    normal_posts = [p for p in posts if not p.get("is_affiliate")]
    posts = normal_posts + affiliate_posts

    for i, p in enumerate(posts):
        p["id"] = f"post_{today_str}_{post_count + i + 1:03d}"
        p["platform"] = "threads"
        # アフィリエイトが無効なら強制off、有効なら生成結果を尊重
        if not affiliate_enabled:
            p["is_affiliate"] = False
            p["affiliate_comment"] = None
        else:
            p.setdefault("is_affiliate", False)
            p.setdefault("affiliate_comment", None)
        p["status"] = "queued"
        queue["queue"].append(p)

    # post-history.json の肥大化防止: 直近200件のみ保持
    all_posts = history.get("posts", [])
    if len(all_posts) > 200:
        history["posts"] = all_posts[-200:]
        save_json("state/post-history.json", history)
        print(f"  履歴を{len(all_posts)}件→200件にトリミング")

    save_json("state/post-queue.json", queue)
    print(f"生成完了: {len(posts)}件をキューに追加")
    print(f"学習データ参照: {winning.get('data_count', 0)}件 (信頼度: {winning.get('confidence', 'none')})")


if __name__ == "__main__":
    main()
