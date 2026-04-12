# Google Sheets レビューダッシュボード セットアップ手順

完了までの所要時間: 約20分

---

## STEP 1: Google Cloud サービスアカウント作成

### 1-1. Google Cloud Console にアクセス
→ https://console.cloud.google.com/

### 1-2. プロジェクト選択または新規作成
- 画面上部の「プロジェクトを選択」→「新しいプロジェクト」
- プロジェクト名: `yozora-uranai`（任意）
- 「作成」をクリック

### 1-3. Google Sheets API を有効化
1. 左メニュー「APIとサービス」→「ライブラリ」
2. 検索欄に「Google Sheets API」と入力
3. 「Google Sheets API」を選択 → 「有効にする」

### 1-4. サービスアカウント作成
1. 左メニュー「APIとサービス」→「認証情報」
2. 「認証情報を作成」→「サービスアカウント」
3. 以下を入力:
   - サービスアカウント名: `yozora-sheets`
   - サービスアカウントID: `yozora-sheets`（自動入力）
4. 「作成して続行」→「完了」

### 1-5. JSONキーを発行
1. 作成したサービスアカウントをクリック
2. 上部タブ「キー」→「鍵を追加」→「新しい鍵を作成」
3. 「JSON」を選択 → 「作成」
4. JSONファイルがダウンロードされる（**このファイルを大切に保管**）

---

## STEP 2: スプレッドシートの共有設定

### 2-1. スプレッドシートを開く
→ https://docs.google.com/spreadsheets/d/1b_n18RRelWvYxy2DHJEpn6v40I735ouro8-NZdeUn2A/

### 2-2. サービスアカウントと共有
1. 右上「共有」ボタンをクリック
2. ダウンロードしたJSONファイルを開き `client_email` の値をコピー
   ```
   "client_email": "yozora-sheets@yozora-uranai.iam.gserviceaccount.com"
   ```
3. 共有ダイアログにメールアドレスを貼り付け
4. 権限: **「編集者」** を選択
5. 「送信」（通知メールは不要なのでチェックを外してOK）

---

## STEP 3: GitHub Secrets に登録

### 3-1. JSONキーの内容をコピー
ダウンロードしたJSONファイルをテキストエディタで開き、**全内容をコピー**

### 3-2. GitHub Secrets に追加
1. GitHubリポジトリ → 「Settings」→「Secrets and variables」→「Actions」
2. 「New repository secret」をクリック
3. 以下を入力:
   - Name: `GOOGLE_SHEETS_CREDENTIALS`
   - Secret: コピーしたJSON全文を貼り付け
4. 「Add secret」をクリック

---

## STEP 4: GASスクリプトをシートに設定

### 4-1. スプレッドシートでApps Scriptを開く
1. スプレッドシートを開く
2. 上部メニュー「拡張機能」→「Apps Script」

### 4-2. スクリプトを貼り付け
1. 既存のコード（`function myFunction() {}`）を全て削除
2. `gas/review_dashboard.gs` の内容を**全てコピー&ペースト**
3. 「保存」（Ctrl+S）

### 4-3. GITHUB_TOKEN をスクリプトプロパティに設定
1. Apps Script 画面左の「⚙️プロジェクトの設定」をクリック
2. 「スクリプト プロパティ」セクション → 「スクリプト プロパティを追加」
3. 以下を入力:
   - プロパティ: `GITHUB_TOKEN`
   - 値: GitHub の Personal Access Token（下記手順で取得）
4. 「スクリプト プロパティを保存」

#### GitHub Personal Access Token の取得方法
1. GitHub → 右上アイコン → Settings
2. 左下「Developer settings」→「Personal access tokens」→「Tokens (classic)」
3. 「Generate new token (classic)」
4. 名前: `yozora-gas-webhook`
5. Expiration: `No expiration`（または1年）
6. スコープ: **`repo`** にチェック
7. 「Generate token」→ トークンをコピー（**一度しか表示されない**）

### 4-4. トリガーをセットアップ（一度だけ実行）
1. Apps Script 画面で関数選択ドロップダウンを `setupTriggers` に変更
2. 「▶実行」をクリック
3. 権限の確認ダイアログ → 「権限を確認」→ Googleアカウントを選択 → 「許可」
4. 「セットアップ完了」のダイアログが表示されればOK

### 4-5. 設定確認
1. 関数を `checkSettings` に変更して「▶実行」
2. `GITHUB_TOKEN: ✅ 設定済み` と表示されればOK

---

## セットアップ完了後の動作

| 時刻 | 動作 |
|------|------|
| 23:05 | 翌日の投稿7件を生成 → シートの新しいタブ（例: `2026-04-14`）に自動書き込み |
| 06:00 | メールで「投稿候補7件 レビュー待ち」通知 |
| 〜07:50 | シートを確認 → G列✅で承認 / H列🔄で再作成 / I列にメモ |
| 各スロット直前 | ✅承認済みのスロットのみ投稿 / 未承認はスキップ |

---

## よくある問題

### 「GOOGLE_SHEETS_CREDENTIALS が未設定」と出る
→ STEP 3 を再確認。Secret名が `GOOGLE_SHEETS_CREDENTIALS`（大文字・アンダースコア）か確認。

### Sheetsにデータが書き込まれない
→ STEP 2-2 のスプレッドシート共有設定を確認。サービスアカウントが「編集者」になっているか。

### 「再作成🔄チェックしたが再生成されない」
→ GASの `GITHUB_TOKEN` が正しく設定されているか `checkSettings()` で確認。
→ GitHub Actions の「Actions」タブで `Regenerate Slot` ワークフローが実行されているか確認。

### 承認したのに投稿されない
→ シートのJ列（ステータス）が `approved` になっているか確認。
→ GASの `onSheetEdit` トリガーが動作しているか確認（Apps Script → トリガー一覧）。
