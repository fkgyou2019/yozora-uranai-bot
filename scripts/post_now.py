#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""即座に1件投稿するスクリプト"""
import sys, json, os, urllib.request, urllib.parse, re, traceback
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8")
JST = timezone(timedelta(hours=9))

# env読み込み
env_path = os.path.join(os.path.dirname(__file__), "..", "config", "api-keys.env")
if os.path.exists(env_path):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

uid = os.environ.get("THREADS_USER_ID", "")
token = os.environ.get("THREADS_ACCESS_TOKEN", "")
api_key = os.environ.get("ANTHROPIC_API_KEY", "")

now = datetime.now(JST)
hour = now.hour
if hour < 12:
    time_context, mood = "朝〜午前", "爽やかで前向きな"
elif hour < 17:
    time_context, mood = "午後", "穏やかで落ち着いた"
else:
    time_context, mood = "夜", "癒し系で温かい"

prompt = f"""あなたは占いSNSアカウント「よぞら.」のバズ投稿を量産するライターです。

【実績データに基づくバズ構造（これに従え）】
以下は実際のエンゲージメント率データです。この構造を必ず再現してください。

■ バズった投稿（eng 20-50%）の共通構造：
1行目（フック）: 「時期」×「限定」×「焦らし」で構成。答えを1行目に書かない。
  成功例: 「来週、仕事で大きな話が」(eng50%)
  成功例: 「来週、金星が動く星座。」(eng37%)
  成功例: 「今週後半、気をつけた方がいい星座。」(eng20%)
  成功例: 「【金運が急上昇する星座TOP3】」(eng36%)

2-3行目: 具体的な星座名（1-3個）を含む。「たった○つだけ」「○つの星座に」等の限定表現。

最終ブロック: CTA「🔮を置いた方に○○が届きます」系。これがある投稿のavg eng=20.7%、ない投稿は8.2%。

■ 死んだ投稿（eng 0-3%）の共通パターン（絶対避けろ）：
  失敗例: 「金曜日は感情の波が大きい日。」→ 抽象的・緊急性なし
  失敗例: 「朝日とともに運気が変わる」→ ポエム調・具体性なし
  失敗例: 「金曜日の夜は、」→ 誰にでも当てはまる・限定感ゼロ

【フック型（以下のどれか1つを必ず使え）】
A. 予告×焦らし型: 「来週、○○が変わる星座。」「今月後半、△△が起きる。」
B. ランキング型: 「【○○ランキングTOP3】」（eng最強=26.6%）
C. 警告型: 「○○座さん、今週は気をつけて。」
D. 限定数字型: 「12星座中、たった○つだけ。」

【ペルソナ】穏やかで神秘的、親しみやすい。敬語ベース。
【絶対NG】「あんた」「しなさい」「黙って」等の命令口調
【日時】{now.strftime('%Y年%m月%d日 %H:%M')} ({time_context})

【フォーマットルール】
- 150-250文字
- 1行20文字以内（スマホ幅に収まる）
- 3-5ブロックに空行で分割
- フック（1行目）は15文字以内で「続きを読みたい」と思わせる
- 末尾に必ずCTA:「🔮を置いた方に○○が届きます」
- ハッシュタグ1つ（本文の単語と重複禁止）

JSON形式で出力（余計なテキストなし）：
{{"content": "投稿本文", "pattern_name": "パターン名"}}"""

body = json.dumps({
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": prompt}]
}).encode("utf-8")

req = urllib.request.Request(
    "https://api.anthropic.com/v1/messages",
    data=body,
    headers={"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
    method="POST"
)

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    text = result["content"][0]["text"]
    # マークダウンコードブロック除去
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    m = re.search(r"\{[^{}]*\"content\"[^{}]*\"pattern_name\"[^{}]*\}", text, re.DOTALL)
    if not m:
        m = re.search(r"\{.*?\}", text, re.DOTALL)
    raw = m.group()
    # JSON文字列内の生改行をエスケープ
    raw = re.sub(r'(?<=": ")(.*?)(?="[,}])', lambda x: x.group().replace('\n', '\\n'), raw, flags=re.DOTALL)
    post_data = json.loads(raw)
    content = post_data["content"]

    # 投稿
    url = f"https://graph.threads.net/v1.0/{uid}/threads"
    params = {"media_type": "TEXT", "text": content, "auto_publish_text": "true", "access_token": token}
    data = urllib.parse.urlencode(params).encode("utf-8")
    req2 = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req2, timeout=30) as resp:
        result2 = json.loads(resp.read().decode("utf-8"))

    print(f"✅ 投稿成功 ID={result2.get('id')}")
    print(f"内容:\n{content}")
except Exception as e:
    print(f"❌ エラー: {e}")
    traceback.print_exc()
    sys.exit(1)
