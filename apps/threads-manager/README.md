# Threads Manager

マルチ Threads アカウント管理デスクトップアプリ（Electron + Next.js 14）。

## 起動

```
cd apps/threads-manager
npm install
npm run dev
```

Electron ウィンドウが開き、`config/accounts.json` の `threads_accounts` を読み込んで表示します。

## テスト

```
npm test
```

## 備考

- **本番ビルド (`npm run start`) は MVP 範囲外**。開発モード (`npm run dev`) のみ動作保証。
- OAuth 接続、投稿、メトリクス等は Phase 2 以降で実装。
- 設計書: `docs/superpowers/specs/2026-04-22-threads-manager-design.md`
