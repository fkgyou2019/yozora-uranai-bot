/**
 * よぞら. 自動返信Worker
 * 3分ごとにThreadsの新コメントをチェックし、Claude APIで返信を生成・投稿する
 */

const THREADS_API = "https://graph.threads.net/v1.0";
const CLAUDE_API = "https://api.anthropic.com/v1/messages";
const MAX_REPLIES_PER_RUN = 3;
const MAX_RETRIES = 3;
const SIMILARITY_THRESHOLD = 0.85;
const SELF_USERNAME = "yozora.uranai";
const NG_WORDS = ["あんた", "しなさい", "黙って", "バカ", "アホ", "うるさい", "知らねえ", "やれよ"];
const BANNED_HOURS = [1, 2, 3, 4, 5, 6];

// 類似度計算（bigram方式）
function calcSimilarity(a, b) {
  if (!a || !b) return 0;
  const bigrams = (s) => {
    const set = new Set();
    for (let i = 0; i < s.length - 1; i++) set.add(s.slice(i, i + 2));
    return set;
  };
  const setA = bigrams(a);
  const setB = bigrams(b);
  let intersection = 0;
  for (const bg of setA) if (setB.has(bg)) intersection++;
  const union = setA.size + setB.size - intersection;
  return union === 0 ? 0 : intersection / union;
}

// ペルソナ違反チェック
function hasPersonaViolation(text) {
  for (const ng of NG_WORDS) {
    if (text.includes(ng)) return ng;
  }
  return null;
}

// 星座名リスト
const ZODIAC_NAMES = [
  "牡羊座","おひつじ座","牡牛座","おうし座","双子座","ふたご座",
  "蟹座","かに座","獅子座","しし座","乙女座","おとめ座",
  "天秤座","てんびん座","蠍座","さそり座","射手座","いて座",
  "山羊座","やぎ座","水瓶座","みずがめ座","魚座","うお座"
];

// Threads API GET
async function threadsGet(endpoint, token) {
  const sep = endpoint.includes("?") ? "&" : "?";
  const url = `${THREADS_API}/${endpoint}${sep}access_token=${token}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Threads API ${res.status}: ${await res.text()}`);
  return res.json();
}

// Threads API POST (返信)
async function threadsReply(text, replyToId, userId, token) {
  const url = `${THREADS_API}/${userId}/threads`;
  const params = new URLSearchParams({
    media_type: "TEXT",
    text: text,
    reply_to_id: replyToId,
    auto_publish_text: "true",
    access_token: token,
  });
  const res = await fetch(url, { method: "POST", body: params });
  if (!res.ok) throw new Error(`Reply failed ${res.status}: ${await res.text()}`);
  return res.json();
}

// Claude APIで返信生成
async function generateReply(commentText, postText, commenterName, recentReplies, apiKey) {
  const hour = new Date().toLocaleString("ja-JP", { timeZone: "Asia/Tokyo", hour: "numeric", hour12: false });
  let timeContext = "現在は昼です。";
  if (hour >= 5 && hour < 11) timeContext = "現在は朝です。朝らしい挨拶を入れてもOK";
  else if (hour >= 17 && hour < 22) timeContext = "現在は夜です。夜らしい挨拶を入れてもOK";
  else if (hour >= 22 || hour < 5) timeContext = "現在は深夜です。「遅い時間にありがとう」等の気遣いを入れてもOK";

  // 星座検出
  let zodiacHint = "";
  const foundZodiac = ZODIAC_NAMES.find(z => commentText.includes(z));
  if (foundZodiac) {
    zodiacHint = `\n※ この方は「${foundZodiac}」と星座を教えてくれました。この星座に個別のアドバイスを2-3行で具体的に伝えてください。`;
  }

  // 絵文字コメント検出
  let emojiHint = "";
  const stripped = commentText.trim();
  const isEmojiOnly = stripped.length <= 3 && !/[a-zA-Zぁ-んァ-ヶ亜-熙]/.test(stripped);
  if (isEmojiOnly) {
    const emojiMap = {
      "🔮": "水晶玉→占いへの関心。「見えてきましたよ」系",
      "✨": "キラキラ→ポジティブ。「輝きが増しますね」系",
      "🌙": "月→神秘。「月の力が味方してます」系",
      "🌸": "桜→春・恋愛。「春の風が吹いてきましたね」系",
    };
    for (const [emoji, hint] of Object.entries(emojiMap)) {
      if (stripped.includes(emoji)) {
        emojiHint = `\n※ 絵文字の解釈ヒント: ${hint}`;
        break;
      }
    }
  }

  // 直近返信（重複防止）
  let recentBlock = "";
  if (recentReplies.length > 0) {
    recentBlock = "【直近の返信（これと同じ言い回しは絶対に使うな）】\n";
    for (const r of recentReplies.slice(-5)) {
      recentBlock += `・${r.slice(0, 40)}\n`;
    }
  }

  const prompt = `あなたは占いSNSアカウント「よぞら.」の運営者・月詠（つくよみ）です。
穏やかで温かい人柄。フレンドリーだが、ほんの少し神秘的。

【コメントしてくれた人】@${commenterName} さん

【元の投稿（あなたが書いたもの）】
${postText.slice(0, 200)}

【届いたコメント】
${commentText}${zodiacHint}${emojiHint}

【${timeContext}】

${recentBlock}
【返信ルール】
1. 冒頭に「@${commenterName} さん\\n\\n」から始めること（必須。さんの後に必ず改行＋空行）
2. ${foundZodiac ? "星座コメントの場合は3行以内で個別アドバイスを含める" : "1-2行の短い返信（40-60文字が理想）"}
3. 返信全体は3行以内に収めること（短く簡潔に）
4. 1行は20文字以内にすること（超えたら改行する）
5. 絵文字は1個まで（🌙✨🔮⭐のいずれか）

【最重要：人間味のあるコミュニケーションをしろ】
あなたはBotではない。友人に返信するように自然な会話をしろ。
以下のパターンをランダムに使い分けること：

A.「感謝+共感」型:
  - 「返信ありがとう！嬉しいです🌙」
  - 「コメントありがとうね✨」

B.「相手に触れる」型:
  - 「${commenterName}さん、いつもコメントくれて嬉しい🌙」
  - 「${commenterName}さんも同じこと感じてたんですね」

C.「会話を広げる」型:
  - 「気になりますよね、また詳しく書きますね」
  - 「それ分かります！私もそう思います🌙」

【禁止（Bot感が出るため絶対NG）】
- 毎回「ありがとうございます」で始める
- 「受け取ってくださり」
- 「良い流れが届きますように」
- 「素敵なタイミングですね」
- 同じ定型文の繰り返し

返信テキストだけを出力してください。JSON不要。`;

  const res = await fetch(CLAUDE_API, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: "claude-haiku-4-5-20251001",
      max_tokens: 256,
      messages: [{ role: "user", content: prompt }],
    }),
  });

  if (!res.ok) throw new Error(`Claude API ${res.status}: ${await res.text()}`);
  const data = await res.json();
  return data.content[0].text.trim();
}

export default {
  async scheduled(event, env, ctx) {
    const token = env.THREADS_ACCESS_TOKEN;
    const userId = env.THREADS_USER_ID;
    const apiKey = env.ANTHROPIC_API_KEY;

    const jstHour = parseInt(new Date().toLocaleString("ja-JP", { timeZone: "Asia/Tokyo", hour: "numeric", hour12: false }));
    console.log(`[JST ${jstHour}時] 自動返信チェック開始`);

    // 深夜帯（1-6時）はスキップ
    if (BANNED_HOURS.includes(jstHour)) {
      console.log(`深夜${jstHour}時のためスキップ`);
      return;
    }

    let consecutiveErrors = 0;

    try {
      // 最新投稿10件を取得
      const threadsData = await threadsGet(
        `${userId}/threads?fields=id,text,timestamp&limit=10`, token
      );
      const posts = threadsData.data || [];

      let repliedCount = 0;
      const recentReplies = [];

      for (const post of posts) {
        if (repliedCount >= MAX_REPLIES_PER_RUN) break;

        // 各投稿のコメントを取得
        let commentsData;
        try {
          commentsData = await threadsGet(
            `${post.id}/replies?fields=id,text,username,timestamp&limit=25`, token
          );
        } catch (e) {
          continue;
        }

        const comments = commentsData.data || [];
        const repliedUsersThisPost = new Set();

        for (const comment of comments) {
          if (repliedCount >= MAX_REPLIES_PER_RUN) break;
          if (comment.username === SELF_USERNAME) continue;

          // 同一投稿内で同じユーザーに1回まで
          if (repliedUsersThisPost.has(comment.username)) continue;

          // KVで返信済みチェック
          const replyKey = `replied:${comment.id}`;
          const alreadyReplied = await env.REPLIED_IDS.get(replyKey);
          if (alreadyReplied) continue;

          // 自分の返信が既にあるかチェック（同じユーザーへの返信を探す）
          const hasMyReply = comments.some(
            c => c.username === SELF_USERNAME &&
            comments.indexOf(c) > comments.indexOf(comment)
          );
          if (hasMyReply) {
            await env.REPLIED_IDS.put(replyKey, "1", { expirationTtl: 604800 });
            continue;
          }

          // いいねだけ（コメントテキストが空）の場合スキップ
          if (!comment.text || comment.text.trim() === "") {
            await env.REPLIED_IDS.put(replyKey, "skip", { expirationTtl: 604800 });
            continue;
          }

          // 返信生成（類似度チェック+ペルソナチェック+リトライ付き）
          try {
            let replyText = null;
            let attempts = 0;

            while (attempts < MAX_RETRIES) {
              attempts++;
              const candidate = await generateReply(
                comment.text, post.text, comment.username, recentReplies, apiKey
              );

              // ペルソナ違反チェック
              const violation = hasPersonaViolation(candidate);
              if (violation) {
                console.log(`ペルソナ違反「${violation}」検出。再生成(${attempts}/${MAX_RETRIES})`);
                continue;
              }

              // 類似度チェック（直近返信との比較）
              let tooSimilar = false;
              for (const prev of recentReplies) {
                const sim = calcSimilarity(candidate, prev);
                if (sim >= SIMILARITY_THRESHOLD) {
                  console.log(`類似度${(sim * 100).toFixed(0)}% ≧ ${SIMILARITY_THRESHOLD * 100}%。再生成(${attempts}/${MAX_RETRIES})`);
                  tooSimilar = true;
                  break;
                }
              }
              if (tooSimilar) continue;

              replyText = candidate;
              break;
            }

            if (!replyText) {
              console.log(`@${comment.username}: ${MAX_RETRIES}回再生成しても合格せず。スキップ`);
              consecutiveErrors++;
              if (consecutiveErrors >= 3) {
                console.log("連続エラー3回。このrunを終了");
                return;
              }
              continue;
            }

            consecutiveErrors = 0;

            // ★ KVに先書き（Workerタイムアウト前に必ず完了させスパム防止）
            // reply前に書くことで、timeout発生時も「返信済み」として記録される
            await env.REPLIED_IDS.put(replyKey, "1", { expirationTtl: 604800 });
            repliedUsersThisPost.add(comment.username);

            // ★ comment.id を使って返信（post.idはNG → 元投稿へのスパム連投になる）
            await threadsReply(replyText, comment.id, userId, token);
            recentReplies.push(replyText);
            repliedCount++;

            console.log(`返信: @${comment.username} → ${replyText.slice(0, 30)}...`);
          } catch (e) {
            console.error(`返信エラー @${comment.username}: ${e.message}`);
            consecutiveErrors++;
            if (consecutiveErrors >= 3) {
              console.log("連続エラー3回。このrunを終了");
              return;
            }
          }
        }
      }

      console.log(`完了: ${repliedCount}件返信`);
    } catch (e) {
      console.error(`致命的エラー: ${e.message}`);
    }
  },

  async fetch(request, env, ctx) {
    return new Response("よぞら. Auto Reply Worker is running.", {
      headers: { "content-type": "text/plain" },
    });
  },
};
