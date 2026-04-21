# Threads Manager 復活実装 設計ドキュメント

**作成日**: 2026-04-22
**作成者**: Claude (Opus 4.7) / たちこさん協議による
**ステータス**: Design approved, pending implementation plan

---

## 1. 背景と目的

### 背景
- 前セッションで `apps/threads-manager/` と `apps/x-manager/` を実装したが、未コミットのまま消失。
- 今回は **Threads Manager のみ** を復活実装する（X Manager は再実装しない）。
- 既存の占い自動運用システム（GitHub Actions による Threads 自動投稿）は**停止しない**。このツールは**可視化＋手動介入**を担う。

### 目的
**複数の Threads アカウントを、ログイン／ログアウトせずに切り替えて管理するデスクトップアプリケーション** を構築する。

Xboard（`https://x-board.net/lp`）の3カラムレイアウトを踏襲しつつ、操作は全て Threads Graph API 経由で行う。

---

## 2. スコープと非スコープ

### MVP スコープ
- **アカウント管理**: CRUD（追加・編集・削除・一覧）
- **OAuth 接続**: Threads Graph API の App 認可フローを経由して Access Token を自動取得
- **アカウント切り替え UI**: 3カラム構成で直感的に切り替え
- **閲覧中心**: 投稿履歴・メトリクス・コメントを表示
- **手動アクション**: 必要時に投稿削除・手動返信・手動投稿

### 明示的な非スコープ
- **内蔵ブラウザで Threads.net を操作する機能**: 将来拡張の余地を残すが、MVP には含めない。
- **自動投稿**: 既存の GitHub Actions 自動投稿システム側が担うため、本ツールでは実装しない。
- **X（旧 Twitter）アカウント管理**: X Manager は今回再実装しない。
- **一括操作**（複数アカウント同時投稿・いいね等）: MVP 後の拡張候補。
- **配布パッケージ化（インストーラー生成）**: 開発者自身（たちこさん）が `npm run dev` で起動できれば十分。

---

## 3. 技術スタック（確定）

| レイヤ | 採用技術 | バージョン目安 | 理由 |
|--------|---------|--------------|------|
| アプリフレーム | Electron | 最新安定版（^32.x） | Next.js を維持したまま単体アプリ化。将来の内蔵ブラウザ機能 (BrowserView) への拡張経路 |
| UI フレームワーク | Next.js | 14.x (App Router) | 前セッションパターン踏襲、API Routes 利用 |
| 言語 | TypeScript | 5.x | 既存踏襲 |
| スタイル | Tailwind CSS | 3.x | 既存踏襲 |
| 永続化 | JSON ファイル (既存 `config/accounts.json` + 新規 `state/threads-manager/` キャッシュ) | - | 既存システムとの同居、後から SQLite 移行可能 |
| API クライアント | Node.js 組み込み fetch | - | Electron main 側で Threads Graph API v1.0 を叩く |
| OAuth コールバック | **固定ポート (47823) の独立 HTTP サーバー** を Electron main で起動 | - | Threads App に登録するリダイレクト URI が固定である必要があるため、Next.js dev server の動的ポートとは分離 |
| ファイルロック | `proper-lockfile` または同等の mtime ベース楽観的並行制御 | - | 既存 Python auto-poster との書き込み競合防止 |

### 実行モード（MVP）
- **`npm run dev` での開発起動のみ想定**（配布パッケージ化は非スコープ §2）。
- Next.js は dev server として Electron main から子プロセスで起動（固定ポート 3050 を使用）。
- API Routes は dev server 上で動作。
- **本番パッケージング（`next build` 後の exe 化）は今回範囲外**。将来実施する場合は Next.js を IPC + 静的 export に置き換える設計変更を伴う。

### 採用しない選択肢
- **Tauri**: Rust 側の記述が必要で、Next.js フル機能（API Routes）を活かせない。将来の BrowserView 拡張も Electron の方が容易。
- **SQLite（better-sqlite3）**: 初期は不要。5〜20 アカウント規模なら JSON で十分。必要になれば段階的に移行。
- **Python エージェント呼び出し（ハイブリッド）**: サブプロセス起動のオーバーヘッドとエラーハンドリング複雑化で MVP には過剰。

---

## 4. アーキテクチャ

### プロセス構成

```
┌──────────────────────────────────────────────────────┐
│ Electron Main Process (Node.js)                       │
│  ├─ Next.js dev server を子プロセスで起動              │
│  │   └─ 固定ポート 3050 (localhost:3050)              │
│  ├─ OAuth callback 専用 HTTP サーバー                  │
│  │   └─ 固定ポート 47823 (localhost:47823/callback)   │
│  ├─ ファイル I/O (config/, state/) + ファイルロック    │
│  └─ IPC ブリッジ（将来の内蔵ブラウザ用に確保）          │
└─────────────────┬────────────────────────────────────┘
                  │ BrowserWindow.loadURL("http://localhost:3050")
┌─────────────────▼────────────────────────────────────┐
│ Electron Renderer Process (Chromium)                  │
│  └─ Next.js UI (React)                                │
│     ├─ API 呼び出しは内部 API Routes 経由              │
│     └─ Threads API は触らない（mainプロセスが代行）     │
└──────────────────────────────────────────────────────┘
```

### ポート割り当てと Threads App 登録
- **Next.js dev server**: `localhost:3050`
- **OAuth callback**: `localhost:47823/callback`
- Threads App コンソールには **`http://localhost:47823/callback`** を Redirect URI として登録する必要がある（ドキュメント §7 の OAuth フローを参照）。
- ポートが他プロセスで使用中の場合は起動時にエラー表示（フォールバックしない — 固定 URI が必須のため）。

### 起動フロー
1. 開発時: `npm run dev` で Electron main → Next.js dev server をスポーン → BrowserWindow が起動
2. 本番時（将来）: `npm run build` → `npm run start` で Next.js production サーバー起動

### OAuth フロー
```
[+ アカウント追加] クリック
  ↓
モーダル: App ID / App Secret 入力（初回のみ、グローバル保存）
  ↓
[Threads で認可する] クリック
  ↓
main が state パラメータ（ランダム文字列）を生成 → メモリ内に保持
  ↓
OAuth callback サーバー (localhost:47823) を起動（既起動ならスキップ）
  ↓
shell.openExternal() で外部ブラウザ起動 → Threads 認可画面
  ↓
ユーザーが認可
  ↓
localhost:47823/callback?code=...&state=... にリダイレクト
  ↓
main が state を検証（不一致なら 400 エラー + モーダル通知）
  ↓
code → short-lived token 交換 (POST /oauth/access_token)
  ↓
short-lived → long-lived token 交換（60日有効）
  ↓
user_id / username を取得 (GET /me)
  ↓
config/accounts.json に保存（ファイルロック取得後）
  ↓
Renderer に IPC 通知 → UI に追加完了表示
```

#### エラー/タイムアウト処理
- モーダルには **5 分タイムアウト** と `[キャンセル]` ボタンを用意。
- タイムアウト or キャンセル時: callback サーバーの該当 state を invalidate し、モーダルを閉じる。
- ブラウザを閉じて認可を中断した場合も、タイムアウトで検知。
- 複数の OAuth 同時進行: state ごとに管理。完了しなかった state は 5 分後に自動破棄。

---

## 5. 既存システムとの同居

既存の占い自動運用システム（`agents/poster.py`, `.github/workflows/auto-post.yml` など）は継続稼働。本ツールは同じ JSON ファイルを共有する。

| ファイル | Threads Manager 側の扱い | 備考 |
|---------|------------------------|------|
| `config/accounts.json` | R/W（**ロック + mtime 検証**） | アカウント定義・Token 管理 |
| `config/accounts.json` の `groups` | R/W | グループ管理機能で使用 |
| `config/safety.json` | R | 手動投稿時の NG 語チェック用 |
| `state/post-history.json` | **R/W（手動投稿時のみ追記、`source: "manager"` フィールド付き）** | 既存システムの重複検知・分析で手動投稿も捕捉するため |
| `state/post-queue.json` | R のみ | アカウント削除前に関連ジョブ有無をチェック |
| `state/replied-comments.json` | R/W（追記のみ） | 手動返信時に返信済み ID を記録 |
| `state/threads-manager/metrics-cache.json` | R/W | **新設**。メトリクス API キャッシュ（60分 TTL） |
| `state/threads-manager/comments-cache.json` | R/W | **新設**。コメント API キャッシュ |
| `state/threads-manager/app-credentials.json` | R/W | **新設**。App ID / App Secret を保存（§8 セキュリティ要件参照） |
| `state/threads-manager/logs/YYYY-MM-DD.log` | W | **新設**。操作ログ・エラーログ（日次ローテーション） |

### 書き込み競合リスクと対策（Phase 1 から実装必須）

#### mtime ベース楽観的並行制御
1. 読み取り時にファイルの `mtime` を記録
2. 書き込み直前に現在の `mtime` と照合
3. 変化していればエラーを投げて UI に通知（自動リトライはしない — ユーザーに再読み込みを促す）
4. 一致していれば tmp ファイルに書き込み → `fs.rename` で原子的置換

#### 代替案
- `proper-lockfile` npm パッケージを導入してプロセス間ロックを取る（より堅牢）
- Phase 1 時点では mtime チェックを最低限実装、必要に応じて `proper-lockfile` へ移行

### 手動投稿と `post-history.json` の整合性
- Threads Manager から手動投稿した場合、`state/post-history.json` に以下を追記:
  ```json
  {
    "platform_post_id": "...",
    "account_id": "...",
    "text": "...",
    "created_at": "ISO8601",
    "source": "manager"
  }
  ```
- 既存 `agents/poster.py` のフォーマットと整合させる（`source` フィールドは追加だが、既存コードは未知フィールドを無視する想定）。
- これにより既存システムの分析・重複検知・多投稿ペナルティ判定が手動投稿も含めて動作する。

### アカウント削除時の影響チェック
- Threads Manager でアカウントを削除する際、`state/post-queue.json` に該当アカウントの予約投稿が残っていないか確認。
- 残っていれば削除をブロックし、「予約 N 件あり。先にキューを処理するか、該当アカウントを無効化してください」と警告。

### タイムゾーン規定
- 全ての時刻表示・集計は **JST (Asia/Tokyo)** で統一（既存システム準拠）。
- キャッシュ TTL・メトリクス集計の「24時間以内」「直近7日」も JST 基準。

---

## 6. UI 設計

### 3 カラムレイアウト（Xboard 踏襲）

```
┌──────────────────────────────────────────────────────────────────┐
│ 🌙 Threads Manager                             [⚙️] [🌓 テーマ]     │
├──────┬─────────────────┬─────────────────────────────────────┤
│ 左    │ 中央              │ 右メイン                              │
│ サイド │ アカウントリスト    │ 選択中アカウント詳細                   │
│ バー   │                  │                                       │
│ 🏠 全て│ [検索🔍______]   │ ┌ ヘッダ ────────────────────┐    │
│       │ 🟢 月詞メイン    │ │ 月詞メイン @tsukuyomi         │    │
│ 📁    │   @tsukuyomi     │ │ 🟢有効 / 残58日              │    │
│ フォ   │   残58日         │ │ [投稿する][Token更新][⋮]    │    │
│ ルダ   │ ─────────────    │ └───────────────────────────┘    │
│       │ 🟡 凛メイン      │                                       │
│ ▶占い  │   @rin_uranai    │ [📝 投稿][💬 コメント][📊 分析]     │
│  辛口 │   残12日         │ ─────────────                        │
│  (1) │ ─────────────    │                                       │
│ ▶占い  │ 🔴 星猫          │  （選択タブのコンテンツ表示）         │
│  癒し │   期限切れ       │                                       │
│  (1) │                   │                                       │
│       │                   │                                       │
│ 🟢有効│                   │                                       │
│ 🟡警告│                   │                                       │
│ 🔴エラー│                  │                                       │
│       │                   │                                       │
│[+追加] │                  │                                       │
└──────┴─────────────────┴─────────────────────────────────────┘
```

### 各カラムの役割

#### 左サイドバー（フォルダ階層）
- `🏠 全て`: 選択時は右メインがダッシュボード化
- グループごとの折りたたみリスト（`config/accounts.json` の `groups` 配列を利用）
- ステータスフィルタ: 有効 / 警告 / エラー
- 下部: `[+ 追加]` ボタン（アカウント追加 or グループ追加を選択させる）

#### 中央カラム（アカウントリスト）
- 上部: 検索ボックス（表示名 / @username でフィルタ）
- 各行の要素:
  - アバター画像（丸型）
  - 表示名（太字）+ @username
  - ステータスランプ: 🟢有効 / 🟡Token 残 < 14日 / 🔴エラー or 期限切れ
  - Token 残日数（色付き）
  - `⋮` メニュー（編集 / 無効化 / 削除 / トークン更新）

#### 右メインエリア
- **ヘッダ**: 選択アカウント情報 + クイックアクション（投稿する / Token 更新 / オプション）
- **タブ**:
  | タブ | 内容 |
  |------|------|
  | 📝 **投稿** | 最新 50 件、各投稿に view / like / reply 数、`[詳細][削除]` |
  | 💬 **コメント** | 自投稿へのコメント一覧、未返信マーク、`[返信][既読化]` |
  | 📊 **分析** | 直近 7 日 ER 推移、パターン別平均、Token 情報 |
- **「🏠 全て」選択時**: タブの代わりにダッシュボード表示
  - 全アカウントカードグリッド
  - Token 残日数集計（赤/黄/緑の件数）
  - 24 時間以内の全アカウント投稿総数
  - 異常アカウント警告一覧

### ページルーティング

| ルート | 役割 |
|-------|------|
| `/` | 3 カラムレイアウトのシェル。中央・右は状態で動的切替 |
| `/oauth/callback` | OAuth リダイレクト受信専用（UI は最小の「接続完了」メッセージ） |
| `/api/accounts` | アカウント CRUD |
| `/api/groups` | グループ CRUD |
| `/api/oauth/start` | OAuth 開始（認可 URL 生成） |
| `/api/oauth/exchange` | code → token 交換 |
| `/api/token/refresh` | Long-lived token リフレッシュ |
| `/api/threads/posts` | 指定アカウントの投稿一覧取得 |
| `/api/threads/post` | 手動投稿 |
| `/api/threads/delete` | 投稿削除 |
| `/api/threads/comments` | コメント取得 |
| `/api/threads/reply` | 手動返信 |
| `/api/threads/metrics` | メトリクス取得（キャッシュ経由） |

---

## 7. データフロー

### アカウント追加（OAuth）
1. Renderer → `/api/oauth/start?account_name=X` を呼ぶ
2. Main が state パラメータと認可 URL を生成 → Renderer に返す
3. Renderer が `shell.openExternal(authUrl)` で外部ブラウザを起動
4. ユーザー認可 → Threads が `localhost:<port>/oauth/callback?code=...&state=...` にリダイレクト
5. Main が code を受信 → `/api/oauth/exchange` 内部で token 交換
6. `config/accounts.json` に新アカウント追記
7. Renderer に `account-added` IPC 通知 → UI リロード

### 投稿の手動削除
1. ユーザーが投稿一覧の `[削除]` ボタンをクリック
2. 確認モーダル（複数回クリック防止）
3. `/api/threads/delete` を呼ぶ → Main が DELETE リクエストを送信
4. 成功時、該当アカウントのメトリクスキャッシュを invalidate
5. 投稿一覧キャッシュから該当投稿を除外して UI 更新
6. `state/post-history.json` の該当 `platform_post_id` に `deleted_at` フィールドを追記（既存エントリは残したまま削除フラグ）

### 手動投稿（Phase 7）
Threads Graph API の 2 ステップ投稿フローに準拠:
```
1. POST /me/threads
   body: { media_type: "TEXT" | "IMAGE", text: "...", image_url: "..." }
   → creation_id 取得
2. GET /<creation_id>?fields=status
   → 最大 30 秒、1 秒間隔でポーリング
   → status === "FINISHED" を待つ
   → "ERROR" なら失敗通知、30 秒経過でタイムアウト通知
3. POST /me/threads_publish
   body: { creation_id }
   → 公開投稿 ID 取得
4. state/post-history.json に source: "manager" で追記
```

画像付き投稿の場合、`image_url` は公開 URL が必須（Threads 制約）。MVP では:
- ローカル画像は Imgur 等の外部アップロードは実装しない
- 代わりにユーザーが URL を貼り付けるフォームとする（MVP 範囲）
- 将来的に Cloudflare R2 等の一時 URL 発行を検討

### メトリクス取得
1. タブ切替時に `/api/threads/metrics?account_id=X` を呼ぶ
2. Main が `state/threads-manager/metrics-cache.json` を確認
3. アカウントごとの cache_entry の `cached_at` が 60 分以内なら返す
4. 古いか存在しないなら Threads API を呼ぶ
5. 新データを書き込み → Renderer に返す

#### キャッシュ無効化ルール
- **TTL**: アカウント単位で 60 分（JST 基準）
- **手動リフレッシュボタン**: ヘッダの `[更新]` クリックで該当アカウントのキャッシュを強制破棄
- **投稿削除時**: 該当アカウントのキャッシュのみ破棄
- **投稿公開時**: 該当アカウントのキャッシュのみ破棄

### Token リフレッシュ
- **自動トリガ**: アプリ起動時に全アカウントの `token_expires_at` を確認し、残 7 日以下なら自動リフレッシュを試行（並列最大 3 件）
- **手動トリガ**: アカウント行の `⋮` メニュー → `[Token 更新]`、またはヘッダの `[Token 更新]` ボタン
- リフレッシュ結果を `config/accounts.json` の `auth.token_expires_at` に書き込み（ロック取得後）
- バッジの 🟢🟡🔴 判定は UI 側で `token_expires_at` から都度計算（JST 基準）:
  - 🟢 残 14 日以上
  - 🟡 残 1〜13 日
  - 🔴 残 0 日以下 or エラー

---

## 8. セキュリティ要件

### Token の保管
- `config/accounts.json` に平文保存（既存システムと同じ方式）。
- 既存の占い自動運用システムが既に同ファイルを使っているため、新規の暗号化レイヤは MVP 範囲外。
- **将来拡張**: Electron `safeStorage` API で Token 暗号化を検討（OS キーチェーン連携）。

### App ID / App Secret の保管（Phase 1 で実装必須の防御）
- `state/threads-manager/app-credentials.json` に保存。
- **Phase 1 最初のコミットで `.gitignore` に追加必須**:
  ```
  state/threads-manager/app-credentials.json
  state/threads-manager/logs/
  ```
- **プレコミットフック（Phase 1 で実装）**: `app-credentials.json` がステージに含まれていたら commit を中断:
  ```bash
  if git diff --cached --name-only | grep -q "app-credentials.json"; then
    echo "ERROR: app-credentials.json cannot be committed"
    exit 1
  fi
  ```
- **誤コミット時のリカバリ手順**:
  1. 該当コミットを revert（push 済みなら force push は不可→ Secret ローテーション必須）
  2. Threads App コンソール (`https://developers.facebook.com/apps/`) で App Secret をローテーション
  3. `app-credentials.json` を新 Secret で更新
  4. 全アカウントで OAuth 再接続が必要になる場合あり

### Renderer ←→ Main の通信
- Renderer は直接外部 API を叩かない。全て API Routes 経由。
- Electron の `contextIsolation: true`, `nodeIntegration: false` を厳守。

### 危険アクションの UX
- 投稿削除・アカウント削除は **2 ステップ確認**（確認モーダル → クリックで確定）。
- 手動投稿前は NG ワード・長さ・画像形式の自動チェック結果を表示。
- NG ワード検出時: `[投稿できません]` ブロック + 該当 NG 語ハイライト。ただし **`[強制投稿]` ボタンも併置**（ユーザー判断で上書き可能、警告モーダルで最終確認）。

---

## 9. エラーハンドリング

| エラーカテゴリ | 対応 |
|--------------|------|
| Threads API rate limit (429) | リトライせずトースト表示「レート制限。しばらく待ってください」。キャッシュは維持。ログ記録 |
| Token 期限切れ | ステータスランプを 🔴 にし、「Token 更新」ボタンを強調表示 |
| ネットワークエラー | トースト表示 + 再試行ボタン。ログ記録 |
| `config/accounts.json` 書き込み失敗 (mtime 不一致) | モーダル「外部から変更されました。再読み込みしてください」、再読み込みボタン提供 |
| `config/accounts.json` 書き込み失敗 (I/O エラー) | エラーモーダル + ログ出力。既存内容は tmp ファイル経由で保護済み |
| OAuth 認可キャンセル / タイムアウト (5分) | モーダルを閉じて「追加失敗」通知、callback サーバーの state を invalidate |
| OAuth state 不一致 | 「不正なコールバック」警告、callback サーバー 400 応答、該当 state を破棄 |
| 画像投稿ポーリングタイムアウト (30秒) | エラーモーダル「画像処理がタイムアウトしました」+ ログ記録 |
| ポート衝突（3050 or 47823 使用中） | 起動時に明確なエラー「ポート XXXX が使用中です」+ 原因プロセス検出のヒント表示 |
| 既存システムの投稿タイミングと競合 | 書き込み時の mtime 検証で自動的に検知 → 再読み込み要求 |

### ログ出力
- 全てのエラー・OAuth 操作・API 呼び出しを `state/threads-manager/logs/YYYY-MM-DD.log` に追記。
- フォーマット: `[ISO8601 JST] [LEVEL] [component] message`
- 日次ローテーション、30 日経過で自動削除。
- エラーは**ログ + トースト両方**に出力（UI だけでは見逃すため）。

---

## 10. テスト戦略

### MVP で必須
- Threads API 呼び出しロジックの単体テスト（OAuth 交換、メトリクス取得）をモックで実施
- JSON ファイル R/W のユニットテスト（原子性・リカバリ）
- 少なくとも 1 アカウントで end-to-end での OAuth 接続確認

### 省略する範囲（MVP 外）
- E2E UI テスト（Playwright 等）
- ビジュアルリグレッションテスト

---

## 11. 実装フェーズ分割

各 Phase は**開始直後に skeleton コミット**、**完了時に機能コミット**（前回のような未コミット消失を防ぐ）。

### Phase 1: 土台（1日）
**完了基準（全てチェック）**:
- [ ] `apps/threads-manager/` ディレクトリ作成、package.json + tsconfig.json + next.config.js + tailwind.config.ts 配置
- [ ] `.gitignore` に `app-credentials.json` と `logs/` 追加
- [ ] プレコミットフック (`.git/hooks/pre-commit` または `husky`) で `app-credentials.json` コミット阻止
- [ ] `npm run dev` で Electron が起動し、`localhost:3050` の Next.js dev server が BrowserWindow に表示される
- [ ] 3 カラムレイアウト (サイドバー / アカウントリスト / メイン) が描画される（中身は空でよい）
- [ ] `config/accounts.json` の既存 `threads_accounts` を読み込んで中央カラムに表示
- [ ] ファイル R/W ユーティリティに mtime 楽観ロック実装、ユニットテスト通過

### Phase 2: OAuth + Token 管理（1日）
**完了基準**:
- [ ] OAuth callback HTTP サーバー (`localhost:47823`) が起動・停止できる
- [ ] `[+アカウント追加]` → App ID/Secret 入力 → 外部ブラウザ → コールバック受信 → `config/accounts.json` に追記 の完全フロー成功
- [ ] state 検証が動作（不一致時に 400 を返す）
- [ ] 5 分タイムアウト・キャンセルボタンが動作
- [ ] 起動時に Token 残 7 日以下のアカウントを検出してリフレッシュ試行
- [ ] `⋮` メニューの「Token 更新」が動作
- [ ] ステータスランプ (🟢🟡🔴) が `token_expires_at` から正しく計算される

### Phase 3: 投稿タブ（0.5日）
**完了基準**:
- [ ] `/api/threads/posts?account_id=X` が `GET /me/threads` を呼んで最新 50 件を返す
- [ ] UI に表示されるフィールド: text / created_at / view_count / like_count / reply_count
- [ ] `[削除]` ボタンで `DELETE /{post_id}` が呼ばれ、成功時にリストから除外 + `post-history.json` に deleted_at 追記
- [ ] 削除は 2 ステップ確認モーダル経由
- [ ] Rate limit エラー時にトースト表示

### Phase 4: コメントタブ（0.5日）
**完了基準**:
- [ ] 選択アカウントの自投稿に対するコメントを `GET /{post_id}/replies` で取得
- [ ] `state/replied-comments.json` を参照して未返信マークを表示
- [ ] `[返信]` で `POST /me/threads` (reply_to_id 指定) → `replied-comments.json` に追記
- [ ] 返信本文に NG 語チェックを適用（警告のみ、強制送信可）

### Phase 5: 分析タブ（0.5日）
**完了基準**:
- [ ] 直近 7 日の ER 推移を折れ線グラフで表示（ライブラリ: recharts 等）
- [ ] パターン別の平均エンゲージメント表（`post-history.json` の pattern フィールドを集計）
- [ ] Token 情報カード: `token_expires_at` / 残日数 / 最終リフレッシュ時刻
- [ ] メトリクスキャッシュが 60 分 TTL で動作、手動更新ボタンでキャッシュ破棄

### Phase 6: グループ管理（0.5日）
**完了基準**:
- [ ] `/groups` ページで `config/accounts.json` の `groups` 配列の CRUD
- [ ] アカウント追加/編集モーダルのグループ欄がドロップダウン（既存グループから選択）
- [ ] グループ削除時に所属アカウントがいれば警告モーダル

### Phase 7: 手動投稿（0.5日）
**完了基準**:
- [ ] モーダルからテキスト投稿 (`media_type: "TEXT"`) が成功
- [ ] 画像 URL 貼り付けで画像投稿 (`media_type: "IMAGE"`) が成功（2 ステップフロー + ポーリング実装）
- [ ] NG 語チェック警告 + `[強制投稿]` ボタン動作
- [ ] 文字数チェック（500 文字超で警告）
- [ ] 投稿成功時に `post-history.json` に `source: "manager"` で追記

### アカウント削除時の安全チェック（Phase 2 で併せて実装）
- `state/post-queue.json` に該当 account_id のジョブが残っていれば削除ブロック
- 警告モーダル「予約 N 件あり。先にキューを処理するか、無効化のみしてください」

**Phase 1-2 で最小動作**、Phase 3-5 で **MVP 完成**、Phase 6-7 は追加機能。

---

## 12. ディレクトリ構造

```
apps/threads-manager/
├── electron/
│   ├── main.ts              # Electron main プロセス
│   ├── oauth-server.ts      # OAuth callback HTTP サーバー
│   └── ipc-bridge.ts        # IPC ハンドラ定義
├── src/
│   ├── app/
│   │   ├── layout.tsx       # 3 カラムシェル
│   │   ├── page.tsx         # ルート（状態で中央・右を切替）
│   │   ├── oauth/
│   │   │   └── callback/
│   │   │       └── page.tsx # OAuth 完了表示
│   │   └── api/
│   │       ├── accounts/route.ts
│   │       ├── groups/route.ts
│   │       ├── oauth/{start,exchange}/route.ts
│   │       ├── token/refresh/route.ts
│   │       └── threads/{posts,post,delete,comments,reply,metrics}/route.ts
│   ├── components/
│   │   ├── sidebar.tsx
│   │   ├── account-list.tsx
│   │   ├── account-card.tsx
│   │   ├── tabs/
│   │   │   ├── posts-tab.tsx
│   │   │   ├── comments-tab.tsx
│   │   │   └── metrics-tab.tsx
│   │   ├── modals/
│   │   │   ├── add-account-modal.tsx
│   │   │   ├── compose-post-modal.tsx
│   │   │   └── confirm-delete-modal.tsx
│   │   └── dashboard.tsx
│   └── lib/
│       ├── accounts.ts      # config/accounts.json R/W
│       ├── threads-api.ts   # Threads API クライアント
│       ├── cache.ts         # state/threads-manager/*-cache.json R/W
│       └── types.ts
├── package.json
├── tsconfig.json
├── next.config.js
└── tailwind.config.ts
```

---

## 13. 既知のリスクと対処

| リスク | 対処 |
|-------|------|
| 未コミットでの消失再発 | **Phase 1 の開始直後**に skeleton commit。以降各 Phase の開始＋完了の 2 回コミット |
| Threads API 仕様変更 | API 呼び出しは `src/lib/threads-api.ts` に集約し、差分吸収しやすくする |
| 既存 GitHub Actions との JSON 競合 | **Phase 1 から mtime 楽観ロック**実装。ロック違反時はユーザーに再読み込みを促す |
| App Secret 誤コミット | `.gitignore` + プレコミットフックで二重防御、ローテーション手順をドキュメント化 |
| 既存 Token が無いレガシーアカウント | `auth.access_token` が空のアカウントは `🟡 要 OAuth 接続` バッジ表示、投稿/削除等のアクションボタンは無効化 |
| OAuth ポート衝突 | 起動時チェック、明確なエラーメッセージ、固定ポート 47823 のみ使用 |

### 削除アカウントの後処理
- Threads Manager でアカウントを削除 → `config/accounts.json` から該当エントリを除去
- `state/post-history.json` の過去レコードは残す（分析データとして価値あり）
- `state/threads-manager/metrics-cache.json` の該当キャッシュは破棄
- 削除アカウントの Token は無効化せず放置（Meta 側で期限切れになる想定）

---

## 14. 次のステップ

1. ✅ この設計ドキュメントをレビュー
2. `writing-plans` スキルへ移行し、Phase 1 の詳細実装プランを作成
3. Phase 1 実装 → コミット → Phase 2 ... と段階的に進行

---

## 15. 設計決定事項（FAQ 形式）

### Q1. Manager が動作中に GitHub Actions の auto-post が走ったら安全か？
**A.** 安全。`config/accounts.json` は読み取り専用的に使われる（Token 読むのみ）。`post-history.json` は auto-poster が追記するが、Manager は読み取り + 手動投稿時のみ追記で、同じレコードを同時に書き換えない。mtime 検証でさらに保護。

### Q2. Token が無いレガシーアカウントはどう扱う？
**A.** `auth.access_token` が空 or 未設定の場合、リスト表示はするが 🟡 バッジ「要 OAuth 接続」を出し、投稿系アクションは無効化する。`⋮` メニューから OAuth 接続フローに入れる。

### Q3. タイムゾーンは？
**A.** 全て **JST (Asia/Tokyo)**。キャッシュ TTL、「直近 7 日」、「24 時間以内」、ログのタイムスタンプ全て JST。既存システム準拠。

### Q4. 複数の OAuth フローが同時進行したら？
**A.** state ごとに独立管理。state が一致しない callback は 400 で拒否。5 分以内に完了しない state は自動破棄。ユーザーが短時間に 2 つ開始しても衝突しない。

### Q5. 500 文字を超える投稿はどうなる？
**A.** Threads の本文上限は 500 文字（2026-04 時点）。超過時は UI で警告 + 送信ブロック。`[強制投稿]` で上書き可能だが、API からエラーが返る可能性をユーザーに周知。

### Q6. 失敗した投稿の扱いは？
**A.** `post-history.json` には書き込まない（成功時のみ追記）。エラーはログ + トーストで通知。モーダルは閉じず、ユーザーが編集して再送できる状態を維持。

---

## 付録 A: 参照

- Threads Graph API 公式: https://developers.facebook.com/docs/threads
- Xboard LP: https://x-board.net/lp
- 既存 `config/accounts.json` 構造: `threads_accounts` 配列、各要素に id/name/username/persona/group/enabled/auth/otp_url/limits
- 前セッションの累積知見は `C:\Users\fkgyo\.claude\projects\C--Users-fkgyo-OneDrive--------AI-------------\memory\project_threads_manager.md` に保存済み
