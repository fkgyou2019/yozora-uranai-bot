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
| OAuth コールバック | Electron main プロセス内で localhost サーバー起動 | - | Threads Graph API のリダイレクト URI を受信 |

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
│  ├─ Next.js サーバー起動 (localhost:<port>)             │
│  ├─ OAuth callback HTTP サーバー (localhost:<port>)    │
│  ├─ ファイル I/O (config/, state/)                     │
│  └─ IPC ブリッジ（将来の内蔵ブラウザ用に確保）          │
└─────────────────┬────────────────────────────────────┘
                  │ BrowserWindow.loadURL
┌─────────────────▼────────────────────────────────────┐
│ Electron Renderer Process (Chromium)                  │
│  └─ Next.js UI (React)                                │
│     ├─ API 呼び出しは内部 API Routes 経由              │
│     └─ Threads API は触らない（mainプロセスが代行）     │
└──────────────────────────────────────────────────────┘
```

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
shell.openExternal() で外部ブラウザ起動 → Threads 認可画面
  ↓
ユーザーが認可
  ↓
localhost:<port>/oauth/callback にリダイレクト
  ↓
Electron main が code を受信
  ↓
code → short-lived token 交換 (POST /oauth/access_token)
  ↓
short-lived → long-lived token 交換（60日有効）
  ↓
user_id / username を取得 (GET /me)
  ↓
config/accounts.json に保存
  ↓
Renderer に IPC 通知 → UI に追加完了表示
```

---

## 5. 既存システムとの同居

既存の占い自動運用システム（`agents/poster.py`, `.github/workflows/auto-post.yml` など）は継続稼働。本ツールは同じ JSON ファイルを共有する。

| ファイル | Threads Manager 側の扱い | 備考 |
|---------|------------------------|------|
| `config/accounts.json` | R/W | アカウント定義・Token 管理（既存構造の `threads_accounts` 配列を利用） |
| `config/accounts.json` の `groups` | R/W | グループ管理機能で使用 |
| `config/safety.json` | R | 手動投稿時の NG 語チェック用 |
| `state/post-history.json` | R のみ | 閲覧表示用（書き込みしない） |
| `state/replied-comments.json` | R/W（追記のみ） | 手動返信時に返信済み ID を記録 |
| `state/threads-manager/metrics-cache.json` | R/W | **新設**。メトリクス API キャッシュ（60分 TTL） |
| `state/threads-manager/comments-cache.json` | R/W | **新設**。コメント API キャッシュ |
| `state/threads-manager/app-credentials.json` | R/W | **新設**。App ID / App Secret を保存（後述のセキュリティ要件参照） |

### 書き込み競合リスクと対策
- `config/accounts.json` は既存システムも読み書きする可能性があるため、書き込み時はファイル全体を原子的に書き換える（`fs.writeFileSync` で一気に）。
- 既存システムの書き込み頻度はスケジュール投稿時のみで、アカウント定義の変更はしない前提。手動操作と時間的に競合する確率は低い。
- MVP 後、競合検知ロジック（mtime チェック）を追加検討。

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
4. 成功時、キャッシュから該当投稿を除外して UI 更新
5. `state/post-history.json` は書き換えない（閲覧専用のため、既存システムの履歴には残る）

### メトリクス取得
1. タブ切替時に `/api/threads/metrics?account_id=X` を呼ぶ
2. Main が `state/threads-manager/metrics-cache.json` を確認
3. キャッシュが 60 分以内なら返す、古ければ Threads API を呼ぶ
4. 新データを書き込み → Renderer に返す

---

## 8. セキュリティ要件

### Token の保管
- `config/accounts.json` に平文保存（既存システムと同じ方式）。
- 既存の占い自動運用システムが既に同ファイルを使っているため、新規の暗号化レイヤは MVP 範囲外。
- **将来拡張**: Electron `safeStorage` API で Token 暗号化を検討（OS キーチェーン連携）。

### App ID / App Secret の保管
- `state/threads-manager/app-credentials.json` に平文保存。
- このファイルは `.gitignore` に追加必須。
- **警告**: Secret を含むため、誤コミット防止のプレコミットフック追加を推奨。

### Renderer ←→ Main の通信
- Renderer は直接外部 API を叩かない。全て API Routes 経由。
- Electron の `contextIsolation: true`, `nodeIntegration: false` を厳守。

### 危険アクションの UX
- 投稿削除・アカウント削除は **2 ステップ確認**（確認モーダル → クリックで確定）。
- 手動投稿前は NG ワード・長さ・画像形式の自動チェック結果を表示。

---

## 9. エラーハンドリング

| エラーカテゴリ | 対応 |
|--------------|------|
| Threads API rate limit (429) | リトライせずトースト表示「レート制限。しばらく待ってください」。キャッシュは維持 |
| Token 期限切れ | ステータスランプを 🔴 にし、「Token 更新」ボタンを強調表示 |
| ネットワークエラー | トースト表示 + 再試行ボタン |
| `config/accounts.json` 書き込み失敗 | エラーモーダル + ログ出力。既存システムの読み取りに影響しないよう、失敗時は既存内容を壊さない |
| OAuth 認可キャンセル / タイムアウト | モーダルを閉じて「追加失敗」通知 |

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

| Phase | 内容 | 工数目安 | 完了基準 |
|-------|------|---------|---------|
| **1** | Electron + Next.js 土台、3 カラムレイアウトの空実装 | 1日 | アプリが起動して空のサイドバー・リスト・メインが表示される |
| **2** | OAuth 接続 + Token 管理 + リフレッシュ | 1日 | 1 アカウント追加でき、Token 情報が正しく保存される |
| **3** | 投稿タブ（履歴取得・表示・削除） | 0.5日 | 投稿一覧が表示され、削除ボタンが機能する |
| **4** | コメントタブ（取得・手動返信） | 0.5日 | 自投稿へのコメントが一覧され、返信投稿ができる |
| **5** | 分析タブ（メトリクス・グラフ） | 0.5日 | 直近 7 日の ER グラフが表示される |
| **6** | グループ管理（前回ロジック再実装） | 0.5日 | グループ CRUD + アカウント割り当て選択 |
| **7** | 手動投稿モーダル（NG 語チェック付き） | 0.5日 | テキスト＋画像の投稿ができ、safety.json の NG 語で警告が出る |

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
| 未コミットでの消失再発 | Phase 1 完了直後に初回コミット、以降 Phase ごとにコミット |
| Threads API 仕様変更 | API 呼び出しは `src/lib/threads-api.ts` に集約し、差分吸収しやすくする |
| 既存 GitHub Actions との JSON 競合 | 書き込み時は fs.writeFileSync で原子的に、将来 mtime チェック追加 |
| App Secret 誤コミット | `.gitignore` に `state/threads-manager/app-credentials.json` を追加、プレコミット確認 |

---

## 14. 次のステップ

1. ✅ この設計ドキュメントをレビュー
2. `writing-plans` スキルへ移行し、Phase 1 の詳細実装プランを作成
3. Phase 1 実装 → コミット → Phase 2 ... と段階的に進行

---

## 付録 A: 参照

- Threads Graph API 公式: https://developers.facebook.com/docs/threads
- Xboard LP: https://x-board.net/lp
- 既存 `config/accounts.json` 構造: `threads_accounts` 配列、各要素に id/name/username/persona/group/enabled/auth/otp_url/limits
- 前セッションの累積知見は `C:\Users\fkgyo\.claude\projects\C--Users-fkgyo-OneDrive--------AI-------------\memory\project_threads_manager.md` に保存済み
