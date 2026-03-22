# セットアップガイド

## 1. 前提条件

- Windows 11 + Git Bash (or WSL)
- Python 3.12+
- インターネット接続

## 2. APIキーの取得

### 2.1 Claude API (Anthropic)

1. https://console.anthropic.com/ にアクセス
2. アカウント作成 → APIキーを生成
3. `ANTHROPIC_API_KEY` をメモ

### 2.2 Threads API (Meta Developer)

1. Instagram占い用ビジネスアカウントを作成
2. https://developers.facebook.com/ にアクセス
3. 「アプリを作成」→ 「ビジネス」タイプを選択
4. Threads APIの利用申請
5. アクセストークン生成（60日有効、更新可能）
6. `THREADS_ACCESS_TOKEN` と `THREADS_USER_ID` をメモ

**注意**: 2025年9月以降、InstagramとThreadsの連携は不要になる予定

### 2.3 X (Twitter) API

1. https://developer.twitter.com/ にアクセス
2. Developer Portalでアプリ作成
3. Pay-Per-Use プラン ($5〜) を選択
4. OAuth 1.0a のキーを取得:
   - `X_API_KEY` (Consumer Key)
   - `X_API_SECRET` (Consumer Secret)
   - `X_ACCESS_TOKEN`
   - `X_ACCESS_TOKEN_SECRET`
   - `X_BEARER_TOKEN`

### 2.4 ASP登録

優先順位順:
1. **A8.net** (https://www.a8.net/) - 案件数最多、ヴェルニ11,000円
2. **afb** (https://www.afi-b.com/) - 報酬10%上乗せ
3. **アクセストレード** (https://www.accesstrade.ne.jp/) - 占い案件52件で最多

## 3. セットアップ手順

```bash
# 1. api-keys.env を作成
cp config/api-keys.env.example config/api-keys.env

# 2. api-keys.env を編集して実際のAPIキーを設定
# エディタで開いて各キーを設定

# 3. セットアップ確認
bash scripts/setup.sh

# 4. テスト実行（手動）
bash scripts/daily-run.sh
```

## 4. 自動実行の設定

### Windows (タスクスケジューラ)

1. 「タスクスケジューラ」を開く
2. 「基本タスクの作成」
3. 毎日実行の設定:
   - **Daily Run**: 毎日06:00 → `bash /path/to/scripts/daily-run.sh`
   - **投稿**: 08:00, 10:00, 12:00, 14:00, 16:00, 18:00, 20:00, 22:00, 00:00 → `python /path/to/agents/poster.py`

### Linux/Mac (cron)

```bash
crontab -e
# 以下を追加:
0 6 * * * bash /path/to/scripts/daily-run.sh
0 8,10,12,14,16,18,20,22,0 * * * bash /path/to/scripts/post-scheduler.sh
```

## 5. 緊急停止

```bash
# 全停止
bash scripts/kill-switch.sh on

# 状態確認
bash scripts/kill-switch.sh status

# 再開
bash scripts/kill-switch.sh off
```
