#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
投稿検証スクリプト: 投稿スロットの5分後に実行し、投稿成功を確認。
失敗していればキューから再投稿、キュー空ならClaude APIで生成→即投稿。
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------------------
# 類似度チェック（重複・類似コンテンツ防止）
# ---------------------------------------------------------------------------

SIMILARITY_THRESHOLD = 0.60  # 60%以上で類似とみなす

def bigram_similarity(a: str, b: str) -> float:
    """バイグラムJaccard類似度（0.0〜1.0）"""
    def make_bigrams(s):
        s = re.sub(r'\s+', '', s)  # 空白除去
        return set(s[i:i+2] for i in range(len(s) - 1))
    sa, sb = make_bigrams(a), make_bigrams(b)
    if not sa or not sb:
        return 0.0
    intersection = len(sa & sb)
    union = len(sa | sb)
    return intersection / union if union > 0 else 0.0


def is_too_similar_to_recent(content: str, history: dict, n: int = 10) -> tuple:
    """直近n件の投稿と類似度チェック。類似なら(True, score, similar_content)を返す"""
    recent_posts = history.get("posts", [])[-n:]
    for p in reversed(recent_posts):
        existing = p.get("content", p.get("content_preview", ""))
        if not existing:
            continue
        score = bigram_similarity(content, existing)
        if score >= SIMILARITY_THRESHOLD:
            return True, score, existing[:50]
    return False, 0.0, ""
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def log(level, msg):
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {msg}")


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


# ---------------------------------------------------------------------------
# Threads API: 最新投稿を取得
# ---------------------------------------------------------------------------

def fetch_latest_thread(user_id, access_token):
    """Threads APIで最新投稿のtimestampを取得"""
    url = (
        f"https://graph.threads.net/v1.0/{user_id}/threads"
        f"?fields=id,text,timestamp"
        f"&limit=1"
        f"&access_token={urllib.parse.quote(access_token, safe='')}"
    )
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        posts = data.get("data", [])
        if posts:
            return posts[0]
        return None
    except Exception as e:
        log("ERROR", f"Threads API 最新投稿取得失敗: {e}")
        return None


def is_recent_post(post, minutes=15):
    """投稿が直近N分以内かチェック"""
    if not post or "timestamp" not in post:
        return False
    # Threads APIのtimestampは ISO 8601 (例: 2024-01-01T12:00:00+0000)
    ts_str = post["timestamp"]
    try:
        # タイムゾーン付きISO形式をパース
        ts = datetime.fromisoformat(ts_str.replace("+0000", "+00:00"))
    except ValueError:
        # フォールバック: 手動パース
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            log("WARN", f"timestamp パース失敗: {ts_str}")
            return False

    now = datetime.now(timezone.utc)
    elapsed = (now - ts).total_seconds()
    log("INFO", f"最新投稿: {elapsed:.0f}秒前 (閾値: {minutes * 60}秒)")
    return elapsed <= minutes * 60


# ---------------------------------------------------------------------------
# Threads API: テキスト投稿
# ---------------------------------------------------------------------------

def threads_post_text(text, user_id, access_token):
    """Threads APIでテキスト投稿（auto_publish_text で1コール完了）"""
    url = f"https://graph.threads.net/v1.0/{user_id}/threads"
    params = {
        "media_type": "TEXT",
        "text": text,
        "auto_publish_text": "true",
        "access_token": access_token,
    }
    data = urllib.parse.urlencode(params).encode("utf-8")

    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return result.get("id")
        except (urllib.error.URLError, OSError) as e:
            log("WARN", f"Threads API リトライ {attempt + 1}/3: {e}")
            if attempt < 2:
                time.sleep(5)
            else:
                raise


# ---------------------------------------------------------------------------
# Claude API: 緊急1件生成
# ---------------------------------------------------------------------------

def generate_emergency_post(api_key, recent_contents=None):
    """Claude APIで占い投稿を1件だけ緊急生成"""
    now = datetime.now(JST)

    # 直近投稿を避けるためのヒント
    avoid_hint = ""
    if recent_contents:
        avoid_hint = "\n【直近の投稿（これと被らないようにする）】\n"
        for i, c in enumerate(recent_contents[-3:], 1):
            avoid_hint += f"{i}. {c}\n"

    prompt = f"""あなたは占いSNSアカウント「よぞら.」のThreads投稿ライターです。
今すぐ投稿する占い投稿を1件だけ生成してください。

【今日の日付】{now.strftime('%Y年%m月%d日')}
【ペルソナ】月詠（つくよみ）: 穏やかで親しみやすい。敬語ベースだが柔らかい。
【絵文字】🔮✨🌙⭐☀️ を自然に使用（1投稿に2-3個）
{avoid_hint}
【🚨 絶対禁止パターン（これらは閲覧数0〜100の最低パフォーマンス。即不合格）】
❌ 励まし型: 「木星の優しい光が」「土星が微笑む日」「春の陽射しが心強い」「あなたへの応援メッセージ」等、天体名+感情的励ましだけの投稿
❌ 抽象共感型: 「今日も頑張りましょう」「自分を信じて」等、星座名も具体的予言もない抽象的な内容
❌ 1星座限定: 「牡羊座さんへ」「蟹座の日🌙」等、12星座中1つしか対象にしない投稿（11/12のユーザーを切り捨てる）
❌ 答えの出し惜しみ: 「○つあります」「個別に教えます」等、星座名を最後まで明かさない構造
❌ 1行目にCTA: 「コメントで教えてください」「星座を書いて」を冒頭に置く

【✅ 必須パターン: 注意喚起+限定型（実績閲覧数2373・ER 42%）】
以下のどれかの構造で生成すること:

■ 構造G（最強）: 注意喚起+限定型
フック例:「今週後半、気をつけた方がいい星座。」「来週、仕事で大きな動きがある星座。」
       「今月、お金の流れが急変する星座。」「○日以内に転機が来やすい星座。」
構造: [時間軸+注意喚起フック] → [たった○つだけ] → [星座名を即提示] → [各星座への具体予言] → [CTA]

■ 構造A（代替）: 数字+限定型
フック例:「12星座中、たった3つだけ。」「12星座中、この2つだけ要注意。」

■ 構造B（代替）: ランキング型
フック例:「【今週の金運ランキングTOP3】」「【来週、恋愛が動く星座TOP3】」

【ルール】
1. 文字数: 150-300文字
2. 1行は8-15文字を目安（短行で読みやすく）
3. 空行で3〜4ブロックに分割
4. フック（1行目）は20文字以内
5. トピックタグは末尾に1つだけ（#今日の運勢 #恋愛運 #金運 #星座占い #今週の占い から選ぶ）
6. CTAは必ず最後に配置（「🔮を置いた方に今夜良い知らせが届きます」「🍀を置いた方に今週中に嬉しい連絡が届きます」等）
7. 直近の投稿と全く異なるテーマ・星座を選ぶこと

JSON形式で1件だけ返してください:
{{"content": "投稿本文", "hashtag": "#今週の占い"}}
"""

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

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as e:
            log("WARN", f"Claude API リトライ {attempt + 1}/3: {e}")
            if attempt < 2:
                time.sleep(5)
            else:
                log("ERROR", "Claude API 3回失敗。生成中止。")
                return None

    text = result["content"][0]["text"]

    # サニタイズ: マークダウンコードブロック除去
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    # 制御文字を除去（改行・タブは保持）
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)

    # JSONオブジェクトを抽出
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        log("ERROR", f"Claude応答からJSONを抽出できません: {text[:200]}")
        return None

    json_str = m.group()
    try:
        post_data = json.loads(json_str)
        return post_data
    except json.JSONDecodeError as e:
        log("WARN", f"JSON パース1回目失敗: {e}")
        # フォールバック: 文字列値内の生改行をエスケープして再パース
        try:
            sanitized = re.sub(
                r'("(?:[^"\\]|\\.)*")',
                lambda m: m.group(0).replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t'),
                json_str
            )
            post_data = json.loads(sanitized)
            log("INFO", "サニタイズ後のJSON パース成功")
            return post_data
        except json.JSONDecodeError as e2:
            log("ERROR", f"JSON パースエラー（サニタイズ後も失敗）: {e2}")
            log("ERROR", f"対象テキスト: {json_str[:300]}")
            return None


# ---------------------------------------------------------------------------
# state更新
# ---------------------------------------------------------------------------

def record_post_to_history(post_id, content, hashtag=""):
    """投稿成功をhistory/queueに記録"""
    now = datetime.now(JST)

    # post-history.json に追加
    history = load_json("state/post-history.json")
    if "posts" not in history:
        history["posts"] = []
    history["posts"].append({
        "id": f"verify-retry-{now.strftime('%Y%m%d%H%M%S')}",
        "content": content,
        "hashtag": hashtag,
        "platform": "threads",
        "status": "posted",
        "posted_at": now.isoformat(),
        "platform_post_id": post_id,
        "source": "post-verify",
    })
    save_json("state/post-history.json", history)

    # system-status.json 更新
    status = load_json("state/system-status.json")
    today = now.strftime("%Y-%m-%d")
    if status.get("daily_post_date") != today:
        status["daily_post_count"] = 0
        status["daily_post_date"] = today
    status["daily_post_count"] = status.get("daily_post_count", 0) + 1
    status["consecutive_errors"] = 0
    save_json("state/system-status.json", status)

    log("INFO", f"state更新完了 (post_id={post_id})")


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def main():
    access_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    user_id = os.environ.get("THREADS_USER_ID", "")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if not access_token or not user_id:
        log("ERROR", "THREADS_ACCESS_TOKEN / THREADS_USER_ID が未設定")
        sys.exit(1)

    # kill switch チェック
    status = load_json("state/system-status.json")
    if status.get("kill_switch", False):
        log("INFO", "KILL_SWITCH がONのため停止")
        return

    # ステップ1: 最新投稿を確認
    log("INFO", "最新投稿を確認中...")
    latest = fetch_latest_thread(user_id, access_token)

    if is_recent_post(latest, minutes=15):
        log("INFO", "OK: 直近15分以内に投稿あり。再投稿不要。")
        return

    log("WARN", "直近15分以内に投稿なし。再投稿を開始します。")

    # ステップ2: キューから1件取り出して投稿（類似度チェック付き）
    queue = load_json("state/post-queue.json")
    history = load_json("state/post-history.json")
    pending = [p for p in queue.get("queue", []) if p.get("status") == "queued"]

    if pending:
        # 直近投稿と類似しないものを選ぶ
        selected_post = None
        for candidate in pending:
            cand_content = candidate.get("content", "")
            is_similar, sim_score, similar_preview = is_too_similar_to_recent(
                cand_content, history, n=10
            )
            if is_similar:
                log("WARN", f"類似投稿スキップ (score={sim_score:.2f}): {cand_content[:30]}... ← 類似: {similar_preview}")
            else:
                selected_post = candidate
                break

        if selected_post is None:
            log("WARN", "キュー内の全候補が直近投稿と類似 → Claude緊急生成にフォールスルー")
        else:
            post = selected_post
            content = post.get("content", "")
            hashtag = post.get("hashtag", "")
            full_text = f"{content}\n\n{hashtag}" if hashtag else content

            log("INFO", f"キューから再投稿: {content[:30]}...")
            try:
                post_id = threads_post_text(full_text, user_id, access_token)
                log("INFO", f"再投稿成功: {post_id}")

                # キューから削除
                post["status"] = "posted"
                post["posted_at"] = datetime.now(JST).isoformat()
                post["platform_post_id"] = post_id
                post["source"] = "post-verify"
                queue["queue"] = [p for p in queue["queue"] if p.get("id") != post.get("id")]
                save_json("state/post-queue.json", queue)

                record_post_to_history(post_id, content, hashtag)
                return
            except Exception as e:
                log("ERROR", f"キューからの再投稿失敗: {e}")
                # フォールスルーしてClaude生成を試行

    # ステップ3: キュー空 or キュー投稿失敗 → Claude APIで生成→即投稿
    if not api_key:
        log("ERROR", "ANTHROPIC_API_KEY が未設定。生成できません。")
        sys.exit(1)

    log("INFO", "キューが空のためClaude APIで緊急生成...")

    # 直近投稿内容を取得（プロンプトに含めて類似回避）
    history_for_gen = load_json("state/post-history.json")
    recent_contents = [
        p.get("content", "")[:80]
        for p in history_for_gen.get("posts", [])[-5:]
        if p.get("content")
    ]

    # 最大2回リトライ（類似度チェック通過まで）
    post_data = None
    for gen_attempt in range(2):
        candidate_data = generate_emergency_post(api_key, recent_contents)
        if not candidate_data:
            break
        cand_content = candidate_data.get("content", "")
        is_similar, sim_score, similar_preview = is_too_similar_to_recent(
            cand_content, history_for_gen, n=10
        )
        if is_similar and gen_attempt == 0:
            log("WARN", f"緊急生成が直近投稿と類似 (score={sim_score:.2f}) → 再生成試みます")
            recent_contents.append(cand_content[:80])  # 再生成時のヒントに
            continue
        post_data = candidate_data
        break

    if not post_data:
        log("ERROR", "緊急生成失敗。次回スロットに委ねます。")
        sys.exit(0)

    content = post_data.get("content", "")
    hashtag = post_data.get("hashtag", "")
    full_text = f"{content}\n\n{hashtag}" if hashtag else content

    log("INFO", f"生成完了。即投稿: {content[:30]}...")
    try:
        post_id = threads_post_text(full_text, user_id, access_token)
        log("INFO", f"緊急投稿成功: {post_id}")
        record_post_to_history(post_id, content, hashtag)
    except Exception as e:
        log("ERROR", f"緊急投稿失敗: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
